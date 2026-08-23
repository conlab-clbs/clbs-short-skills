#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_short.py — clbs-short-edit 縦型ショート動画 合成レンダラー
v3: takumi-youtube-edit のテロップ精度機構を移植（2026-08-17）

入力（1フォルダ）:
  avatar.mp4    縦型9:16の顔出しアバター動画（音声入り。台本ナレーションを喋っている）
  segments.json [{"i":1,"start":0.0,"end":4.04,"text":"..."}, ...]  文ごとのタイムライン＋テロップ本文
                （時刻は実音声タイムライン秒。"cards" で改行の手動上書き可）
  plan.json     [{"type":"broll"|"image","file":"assets/x","start":..,"end":..,"fit":"cover"|"contain"}, ...]
出力:
  final.mp4     ジェットカット＋素材差し込み＋テロップ焼き込み済み（1080x1920）

v2 からの改善（takumi-youtube-edit 移植）:
  1) フレーム量子化ジェットカット: 全カット境界を 1/FPS グリッドへ量子化し、
     映像=半開区間select・音声=asplit+atrim（サンプル精度・映像フレーム数と厳密一致）
     ＋5msマイクロフェード。中間再エンコード（base_cut.mp4）を廃止し1パス合成。
     → select/aselect の量子化誤差蓄積による「テロップ先行・音声遅れ」を根治。
  2) テロップ分割: 文（。！？）> 節（、）> BudouX文節 の階層分割＋禁則辞書
     no_break_words.txt（takumi skill assets / _factory / プロジェクト直下の和集合）
     ＋Knuth-Plass流DP改行（行長ハード制約・文字レベルDPフォールバック）。
  3) カード時刻: stable-ts 強制アライメント（--align auto、台本既知で±20ms級）で
     語時刻からカード開始を割り当て。stable-ts不在/失敗時は文字数比例で文窓に配分。
     短尺カードは隣へ統合（--telop-min-dur）。次カード開始まで表示保持。
  4) word-safeカット: アライメント成功時、語区間に食い込む無音カットを語端でクランプ
     （語尾・語頭切れ防止）。
  5) 冒頭メイン画面ガード: 最初の文が終わるまで素材オーバーレイ（Bロール/画像）を
     出さない（冒頭はアバター本人）。--allow-open-overlay で解除。

依存: ffmpeg / Pillow。budoux・stable-ts は takumi-youtube-edit の .venv を共用推奨:
  ~/.claude/skills/takumi-youtube-edit/.venv/bin/python3 build_short.py --dir <project>
（無くても動作: budoux→文字DP改行 / stable-ts→文字数比例 に自動フォールバック）
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SKILL_DIR = Path(__file__).resolve().parent.parent
TAKUMI_SKILL = Path.home() / ".claude" / "skills" / "takumi-youtube-edit"

W, H = 1080, 1920
JP_FONT = "/tmp/jpfont.ttc"
FONT_SRC = "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc"

# テロップ（位置B＝顔の下・胸元上）
TELOP_CENTER_Y = 1230
TELOP_MAX_W = 1000
TELOP_FS = 96
TELOP_MIN_FS = 54
LINE_RATIO = 1.18
STROKE = 12

# 画像（ピクチャー/スライド）の表示ボックス（中央付近・上端UIを避ける）
IMG_BOX_W, IMG_BOX_H = 1000, 820
IMG_CENTER_Y = 640

# ジェットカット
SIL_NOISE = -30            # dB
AFADE = 0.005              # カット点マイクロフェード秒

# テロップ改行（takumi移植・縦型は1行10字）
MAX_LINE_CHARS = 10
ORPHAN_MIN = 3
ORPHAN_PENALTY = 60
KINSOKU_LINE_HEAD = set("ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶーゝゞヽヾ々〜")
PARTICLE_HEAD = set("のがをにはでとへやも")
PARTICLE_PREV = set("をはがへ")
PARTICLE_PREV2 = {"だけ", "など", "まで", "から", "より", "ほど", "くらい", "ぐらい"}
AUX_NO_BREAK = ["かもしれない", "かもしれません", "ということ", "というのは",
                "んですよね", "ですよね", "んです", "なんです",
                "らしいんです", "らしいです", "らしい", "みたいです", "ようです"]
BREAK_AT_PARTICLE = 300.0
BREAK_IN_TOKEN = 600.0
BREAK_FORBIDDEN = 1e6

CLAUSE_PUNCT = set("、，,")
SENT_PUNCT = set("。．.！!？?")

FPS = 30.0                 # 実測で上書き
NO_BREAK_WORDS: list[str] = []


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def ensure_font(font_path):
    fp = Path(JP_FONT)
    if fp.exists():
        return str(fp)
    src = Path(font_path or FONT_SRC)
    if not src.exists():
        raise SystemExit(f"日本語フォントが見つかりません: {font_path or FONT_SRC}")
    fp.write_bytes(src.read_bytes())
    return str(fp)


def probe_video(path):
    """(fps, duration) を実測（fps決め打ちはズレの原因: takumi改善点5）。"""
    rc, out, _ = run(["ffprobe", "-v", "quiet", "-select_streams", "v:0",
                      "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(path)])
    fps = 0.0
    if rc == 0 and out.strip():
        try:
            num, den = out.strip().split("/")
            fps = float(num) / float(den)
        except ValueError:
            try:
                fps = float(out.strip())
            except ValueError:
                fps = 0.0
    if not (1.0 <= fps <= 240.0):
        print("[warn] fps検出失敗 → 30 と仮定", file=sys.stderr)
        fps = 30.0
    _rc, _o, err = run(["ffmpeg", "-i", str(path)])
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
    if not m:
        raise SystemExit(f"尺を取得できません: {path}")
    h, mn, s = m.groups()
    dur = int(h) * 3600 + int(mn) * 60 + float(s)
    return fps, dur


def qframe(t):
    """時刻をフレームグリッド（1/FPS の倍数）へ量子化（takumi改善点1の要）。"""
    return round(t * FPS) / FPS


def normalize_text(text):
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[\s　]+", "", text)
    text = re.sub(r"[「」『』“”\"'’‘、。，．・：:；;！？!?…—―ー\-〜~（）()\[\]【】<>＜＞/／&＆]", "", text)
    text = re.sub(r"[^0-9a-zぁ-んァ-ン一-龥]", "", text)
    return text


# ---------------------------------------------------------------------------
# 禁則辞書（takumi改善点3）
# ---------------------------------------------------------------------------
def load_no_break_words(pdir):
    """short-edit assets → takumi assets → _factory（親を遡って探索）→ プロジェクト直下 の和集合。"""
    words = set()
    paths = [SKILL_DIR / "assets" / "no_break_words.txt",
             TAKUMI_SKILL / "assets" / "no_break_words.txt"]
    for parent in [pdir, *pdir.parents]:
        cand = parent / "_factory" / "no_break_words.txt"
        if cand.exists():
            paths.append(cand)
            break
    paths.append(pdir / "no_break_words.txt")
    env = os.environ.get("CLBS_NO_BREAK_FILE")
    if env:
        paths.append(Path(env))
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            w = line.split("#", 1)[0].strip()
            if len(w) >= 2:
                words.add(w)
    return sorted(words, key=len, reverse=True)


def merge_tokens_by_dict(toks, words):
    """辞書語の内部に落ちるトークン境界を禁じてマージする。"""
    if not words or len(toks) <= 1:
        return toks
    text = "".join(toks)
    forbidden = set()
    for w in words:
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            forbidden.update(range(i + 1, i + len(w)))
            start = i + 1
    if not forbidden:
        return toks
    out, cur, off = [], "", 0
    for t in toks:
        cur += t
        off += len(t)
        if off in forbidden:
            continue
        out.append(cur)
        cur = ""
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------------------
# テロップ改行（BudouX + DP: takumi改善点2/3 移植）
# ---------------------------------------------------------------------------
_BUDOUX = None


def _budoux_parse(sentence):
    global _BUDOUX
    if _BUDOUX is None:
        try:
            import budoux
            _BUDOUX = budoux.load_default_japanese_parser()
        except Exception:
            _BUDOUX = False
            print("[warn] budoux 不在 → 文字レベル改行にフォールバック"
                  "（takumi venv の python3 で実行すると文節精度が上がります）", file=sys.stderr)
    if _BUDOUX:
        return _BUDOUX.parse(sentence)
    return list(sentence)


def _dp_lines_chars(text, toks, mc):
    """文字レベルDP。トークンが mc を超え、トークン境界だけでは行長を守れない時の受け皿。
    行長 ≤ mc をハード制約とし、ブレーク位置の質はコスト序列で担保する。"""
    n = len(text)
    tok_bounds = set()
    off = 0
    for t in toks[:-1]:
        off += len(t)
        tok_bounds.add(off)
    forbidden = set()
    for w in list(NO_BREAK_WORDS) + AUX_NO_BREAK:
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            forbidden.update(range(i + 1, i + len(w)))
            start = i + 1

    def break_penalty(pos):
        if pos in tok_bounds:
            return 0.0
        if pos in forbidden or text[pos] in KINSOKU_LINE_HEAD:
            return BREAK_FORBIDDEN
        if text[pos] in PARTICLE_HEAD:
            return BREAK_AT_PARTICLE
        if text[pos - 2:pos] in PARTICLE_PREV2 or text[pos - 1] in PARTICLE_PREV:
            return BREAK_AT_PARTICLE
        return BREAK_IN_TOKEN

    INF = float("inf")
    dp = [INF] * (n + 1)
    dp[n] = 0.0
    nxt = [-1] * (n + 1)
    for i in range(n - 1, -1, -1):
        for j in range(i + 1, min(i + mc, n) + 1):
            w = j - i
            if j == n:
                cost = 0.0 if w >= ORPHAN_MIN else (ORPHAN_MIN - w) * ORPHAN_PENALTY
            else:
                cost = float((mc - w) ** 2) + break_penalty(j)
            tot = cost + dp[j]
            if tot < dp[i]:
                dp[i] = tot
                nxt[i] = j
    lines = []
    i = 0
    while i < n and nxt[i] != -1:
        lines.append(text[i:nxt[i]])
        i = nxt[i]
    if i < n:
        lines.append(text[i:])
    return lines


def _dp_lines(text, max_chars=None):
    """BudouX文節（辞書マージ済み）をDPで改行。
    最終行の余白コスト0（Knuth-Plass流）＋孤児最終行ペナルティ＋行長ハード制約。"""
    mc = max_chars or MAX_LINE_CHARS
    if len(text) <= mc:
        return [text]
    toks = merge_tokens_by_dict(_budoux_parse(text), NO_BREAK_WORDS)
    n = len(toks)
    if n == 0:
        return [text]
    if max(len(t) for t in toks) > mc:
        # 禁則語そのものが行長を超える場合（縦型は1行10字なので起こり得る）は
        # 語内で割らず単独行として溢れさせる（フォント自動縮小が幅を吸収する）
        big = [w for w in NO_BREAK_WORDS if len(w) > mc and w in text]
        if big:
            w = max(big, key=len)
            i = text.find(w)
            lines = []
            if i > 0:
                lines += _dp_lines(text[:i], mc)
            lines.append(w)
            if i + len(w) < len(text):
                lines += _dp_lines(text[i + len(w):], mc)
            return lines
        return _dp_lines_chars(text, toks, mc)
    INF = float("inf")
    dp = [INF] * (n + 1)
    dp[n] = 0.0
    choice = [-1] * (n + 1)
    for i in range(n - 1, -1, -1):
        w = 0
        for j in range(i, n):
            w += len(toks[j])
            if w > mc:
                break
            if j + 1 == n:
                cost = 0.0 if w >= ORPHAN_MIN else (ORPHAN_MIN - w) * ORPHAN_PENALTY
            else:
                cost = float((mc - w) ** 2)
            tot = cost + dp[j + 1]
            if tot < dp[i]:
                dp[i] = tot
                choice[i] = j + 1
    lines = []
    idx = 0
    while idx < n:
        j = choice[idx]
        if j == -1:
            lines.append("".join(toks[idx:]))
            break
        lines.append("".join(toks[idx:j]))
        idx = j
    return lines


# ---------------------------------------------------------------------------
# テロップカード構築（文>節>文節の階層: takumi build_cards_for_segment 移植）
# ---------------------------------------------------------------------------
@dataclass
class Card:
    lines: list          # 表示行（1〜2行）
    cs: int              # 文内クリーン文字列での開始/終了オフセット
    ce: int
    sent: int            # 文ID（segments.json 1エントリ内の。区切り）
    seg_i: int = 0       # segments.json のエントリ番号
    head: bool = False   # エントリ先頭カード
    js: float = 0.0      # jetcutタイムライン秒
    je: float = 0.0

    def plain(self):
        return "".join(self.lines)


def clean_units(text):
    """表示対象文字列（句読点・空白を除去）と、各位置の境界強度・文IDを返す。
    戻り値: (clean:str, bounds:list[int], sents:list[int])
      bounds[i] = clean[i] の直後の境界: 0=なし / 2=節(、) / 3=文(。！？)"""
    clean, bounds, sents = [], [], []
    sent = 0
    for ch in text:
        if ch in CLAUSE_PUNCT:
            if bounds:
                bounds[-1] = max(bounds[-1], 2)
            continue
        if ch in SENT_PUNCT or ch == "\n":
            if bounds:
                bounds[-1] = max(bounds[-1], 3)
            continue
        if not ch.strip():
            continue
        clean.append(unicodedata.normalize("NFKC", ch) if len(unicodedata.normalize("NFKC", ch)) == 1 else ch)
        bounds.append(0)
        sents.append(sent)
        if bounds[-1] >= 3:
            sent += 1
    # 文IDは境界確定後に振り直す
    sent = 0
    for i in range(len(clean)):
        sents[i] = sent
        if bounds[i] >= 3:
            sent += 1
    return "".join(clean), bounds, sents


def build_cards(text, seg_i):
    """1エントリのテロップ本文 → 節ごとにDP改行 → 文>節境界を尊重して2行カードへパック。"""
    clean, bounds, sents = clean_units(text)
    if not clean:
        return []
    # 節分割（b>=2 の直後で切る）
    clauses = []          # (start, end) クリーンオフセット
    start = 0
    for i in range(len(clean)):
        if bounds[i] >= 2:
            clauses.append((start, i + 1))
            start = i + 1
    if start < len(clean):
        clauses.append((start, len(clean)))

    cards = []
    cur_lines = []        # [(line_text, cs, ce), ...]

    def flush():
        nonlocal cur_lines
        if cur_lines:
            cs = cur_lines[0][1]
            ce = cur_lines[-1][2]
            cards.append(Card([t for t, _a, _b in cur_lines], cs, ce, sents[cs], seg_i))
            cur_lines = []

    prev_sent_end = True
    for (a, b) in clauses:
        ctext = clean[a:b]
        lines = _dp_lines(ctext)
        line_units = []
        off = a
        for ln in lines:
            line_units.append((ln, off, off + len(ln)))
            off += len(ln)
        if cur_lines and (prev_sent_end or len(cur_lines) + len(line_units) > 2):
            flush()
        idx = 0
        while len(line_units) - idx > 2 - len(cur_lines):
            take = 2 - len(cur_lines)
            cur_lines += line_units[idx:idx + take]
            flush()
            idx += take
        cur_lines += line_units[idx:]
        if len(cur_lines) == 2:
            flush()
        prev_sent_end = bounds[b - 1] >= 3
    flush()
    for j, c in enumerate(cards):
        c.head = (j == 0)
    return cards


def reflow_merge(a, b):
    """短尺カードの統合（同文のみ）。2行に収まらなければ None。"""
    plain = a.plain() + b.plain()
    if len(plain) > MAX_LINE_CHARS * 2:
        return None
    lines = _dp_lines(plain)
    if len(lines) > 2:
        return None
    m = Card(lines, a.cs, b.ce, a.sent, a.seg_i, head=a.head)
    m.js, m.je = min(a.js, b.js), max(a.je, b.je)
    return m


# ---------------------------------------------------------------------------
# stable-ts 強制アライメント（takumi改善点4 の簡約版）
# ---------------------------------------------------------------------------
def align_words(avatar, full_text, work, model_name):
    """avatar音声へ full_text を強制アライメント。語リスト [{word,start,end}] か None。
    結果は _work/align_words.json にキャッシュ（音声と台本のハッシュで無効化）。"""
    try:
        import stable_whisper
    except ImportError:
        print("[align] stable-ts 不在 → 文字数比例タイミングにフォールバック"
              "（takumi venv の python3 で実行すると語精度になります）", file=sys.stderr)
        return None
    st = avatar.stat()
    key = hashlib.md5(f"{st.st_size}:{st.st_mtime_ns}:{model_name}:{full_text}".encode()).hexdigest()
    cache = work / "align_words.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("key") == key:
                return data["words"]
        except Exception:
            pass
    wav = work / "_audio16k.wav"
    rc, _o, err = run(["ffmpeg", "-y", "-i", str(avatar), "-vn", "-acodec", "pcm_s16le",
                       "-ar", "16000", "-ac", "1", str(wav)])
    if rc != 0:
        print(err[-500:], file=sys.stderr)
        return None
    print(f"[align] stable-ts forced alignment (model={model_name})", file=sys.stderr)
    try:
        model = stable_whisper.load_model(model_name)
        text = re.sub(r"[ \t]+", " ", full_text).strip()
        data = model.align(str(wav), text, language="ja").to_dict()
        words = [{"word": w.get("word", ""), "start": float(w["start"]), "end": float(w["end"])}
                 for seg in data.get("segments", []) for w in seg.get("words", []) or []]
        if not words:
            print("[align] 語が得られず → フォールバック", file=sys.stderr)
            return None
    except Exception as e:
        print(f"[align] stable-ts failed ({e}) → フォールバック", file=sys.stderr)
        return None
    finally:
        try:
            wav.unlink()
        except OSError:
            pass
    cache.write_text(json.dumps({"key": key, "words": words}, ensure_ascii=False), encoding="utf-8")
    return words


def clamp_silences_to_words(silences, word_spans):
    """語区間に食い込む無音を語端でクランプ（語尾・語頭切れ防止: takumi word-safe cuts）。"""
    spans = sorted(word_spans)
    out = []
    for s, e in silences:
        segs = [[s, e]]
        for a, b in spans:
            if b <= s:
                continue
            if a >= e:
                break
            nxt = []
            for ss, ee in segs:
                if b <= ss or a >= ee:
                    nxt.append([ss, ee])
                    continue
                if ss < a:
                    nxt.append([ss, a])
                if b < ee:
                    nxt.append([b, ee])
            segs = nxt
        out.extend((ss, ee) for ss, ee in segs if ee - ss > 1e-3)
    return out


# ---------------------------------------------------------------------------
# ジェットカット（フレーム量子化: takumi改善点1）
# ---------------------------------------------------------------------------
def detect_silences(path, min_d):
    _rc, _o, err = run(["ffmpeg", "-i", str(path), "-af",
                        f"silencedetect=noise={SIL_NOISE}dB:d={min_d}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", err)]
    sils = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is not None and e > s:
            sils.append((s, e))
        elif e is None:
            sils.append((s, None))
    return sils


def build_jetcut(silences, total, keep_pad, min_cut):
    """無音を詰める。全境界をフレームグリッドへ量子化。
    戻り値: removed, kept, seg_bounds(フレーム数累積), total_jc"""
    removed = []
    for s, e in silences:
        if e is None:
            e = total
        if e - s < min_cut:
            continue
        rs = qframe(s + keep_pad)
        re_ = qframe(e - keep_pad)
        if re_ - rs >= 1.0 / FPS - 1e-6:
            removed.append((rs, re_))
    removed.sort()
    merged = []
    for rs, re_ in removed:
        if merged and rs <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], re_)
        else:
            merged.append([rs, re_])
    removed = [(a, b) for a, b in merged]
    kept = []
    cur = 0.0
    for rs, re_ in removed:
        if rs > cur + 1e-6:
            kept.append({"start": round(cur, 6), "end": round(rs, 6)})
        cur = max(cur, re_)
    if cur < total - 1e-6:
        kept.append({"start": round(cur, 6), "end": round(total, 6)})
    # フレーム数が0に丸まる極小チャンクを除去（atrim start=end の空ストリームは
    # concat が終端せず ffmpeg がハングする）。除去分は removed に編入して写像を再構築。
    kept = [c for c in kept if int(round((c["end"] - c["start"]) * FPS)) >= 1]
    removed = []
    cur = 0.0
    for c in kept:
        if c["start"] > cur + 1e-6:
            removed.append((cur, c["start"]))
        cur = c["end"]
    if cur < total - 1e-6:
        removed.append((cur, total))
    seg_bounds = [0.0]
    acc_frames = 0
    for c in kept:
        acc_frames += int(round((c["end"] - c["start"]) * FPS))
        seg_bounds.append(round(acc_frames / FPS, 6))
    return removed, kept, seg_bounds, seg_bounds[-1]


def make_src_to_jc(removed):
    def f(t):
        jc = t
        for rs, re_ in removed:
            if t >= re_:
                jc -= (re_ - rs)
            elif t > rs:
                jc -= (t - rs)
            else:
                break
        return max(0.0, round(jc, 6))
    return f


# ---------------------------------------------------------------------------
# テロップPNG（従来どおり）
# ---------------------------------------------------------------------------
def fit_font(draw, line, font_path):
    fs = TELOP_FS
    f = ImageFont.truetype(font_path, fs, index=0)
    bb = draw.textbbox((0, 0), line, font=f, stroke_width=STROKE)
    w = bb[2] - bb[0]
    if w > TELOP_MAX_W:
        fs = max(TELOP_MIN_FS, int(fs * TELOP_MAX_W / w))
        f = ImageFont.truetype(font_path, fs, index=0)
    return f, fs


def render_telop_png(lines, out_path, font_path):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    fonts = [fit_font(d, ln, font_path) for ln in lines]
    line_hs = [int(fs * LINE_RATIO) for _f, fs in fonts]
    block_h = sum(line_hs)
    y = TELOP_CENTER_Y - block_h // 2
    colors = [(255, 255, 255, 255), (255, 238, 0, 255)]
    for idx, (ln, (f, _fs)) in enumerate(zip(lines, fonts)):
        bb = d.textbbox((0, 0), ln, font=f, stroke_width=STROKE)
        tw = bb[2] - bb[0]
        x = (W - tw) // 2 - bb[0]
        d.text((x, y), ln, font=f, fill=colors[idx % 2],
               stroke_width=STROKE, stroke_fill=(0, 0, 0, 255))
        y += line_hs[idx]
    im.save(out_path)


# ---------------------------------------------------------------------------
def main():
    global FPS, NO_BREAK_WORDS, MAX_LINE_CHARS
    ap = argparse.ArgumentParser(description="clbs-short-edit 縦型ショート合成 v3")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--avatar", default="avatar.mp4")
    ap.add_argument("--segments", default="segments.json")
    ap.add_argument("--plan", default="plan.json")
    ap.add_argument("--out", default="final.mp4")
    ap.add_argument("--font", default=None)
    ap.add_argument("--no-jetcut", action="store_true", help="ジェットカットを無効化")
    ap.add_argument("--min-silence", type=float, default=0.20, help="これ以上の無音を詰める")
    ap.add_argument("--keep-pad", type=float, default=0.05, help="発話前後に残す余白秒")
    ap.add_argument("--align", choices=["auto", "stable", "off"], default="auto",
                    help="カード時刻: auto=stable-tsがあれば語精度 / stable=必須 / off=文字数比例")
    ap.add_argument("--model", default="small", help="stable-tsモデル")
    ap.add_argument("--max-line-chars", type=int, default=MAX_LINE_CHARS, help="テロップ1行の最大文字数")
    ap.add_argument("--telop-min-dur", type=float, default=0.8, help="これ未満のカードは同文の隣へ統合")
    ap.add_argument("--allow-open-overlay", action="store_true",
                    help="冒頭メイン画面ガードを解除（最初の文の間も素材を重ねる）")
    args = ap.parse_args()

    d = Path(args.dir)
    MAX_LINE_CHARS = args.max_line_chars
    font_path = ensure_font(args.font)
    avatar = d / args.avatar
    segs = json.loads((d / args.segments).read_text(encoding="utf-8"))
    plan = json.loads((d / args.plan).read_text(encoding="utf-8")) if (d / args.plan).exists() else []
    work = d / "_work"
    work.mkdir(exist_ok=True)

    NO_BREAK_WORDS = load_no_break_words(d)
    FPS, dur = probe_video(avatar)
    hf = 0.5 / FPS
    print(f"[probe] fps={FPS:.3f} dur={dur:.2f}s 禁則語={len(NO_BREAK_WORDS)}", file=sys.stderr)

    # --- アライメント（カード時刻の語精度化＋word-safeカット） ---
    words = None
    if args.align in ("auto", "stable"):
        full_text = "。".join(s.get("text", "").strip() for s in segs if s.get("text", "").strip())
        words = align_words(avatar, full_text, work, args.model)
        if words is None and args.align == "stable":
            raise SystemExit("[error] --align stable 指定だが stable-ts アライメントに失敗")

    # --- ジェットカット計画（フレーム量子化） ---
    if args.no_jetcut:
        removed, kept, seg_bounds, total_jc = [], [{"start": 0.0, "end": dur}], [0.0, dur], dur
    else:
        sils = detect_silences(avatar, args.min_silence)
        sils = [(s, e if e is not None else dur) for s, e in sils]
        if words:
            sils = clamp_silences_to_words(sils, [(w["start"], w["end"]) for w in words])
        removed, kept, seg_bounds, total_jc = build_jetcut(sils, dur, args.keep_pad, args.min_silence)
        print(f"[jetcut] {len(kept)}チャンク / {dur:.1f}s → {total_jc:.1f}s（フレーム量子化）", file=sys.stderr)
    src_to_jc = make_src_to_jc(removed)

    # --- 語ストリーム（正規化1文字ごとの時刻） ---
    stream_chars = []
    if words:
        for w in words:
            nw = normalize_text(w["word"])
            for ch in nw:
                stream_chars.append((ch, w["start"], w["end"]))
    stream_text = "".join(c for c, _s, _e in stream_chars)

    # --- テロップカード（文>節>文節、語時刻 or 文字数比例） ---
    all_cards = []
    cursor = 0
    for seg_i, s in enumerate(segs):
        text = s.get("text", "").strip()
        if not text:
            continue
        if s.get("cards"):
            # 改行の手動上書き: 1要素=1カード（listなら複数行、strなら1行）
            cards = []
            off = 0
            for k, c in enumerate(s["cards"]):
                lines = c if isinstance(c, list) else [c]
                ln = sum(len(x) for x in lines)
                # sent をカード毎に変えて自動統合の対象外にする（手動指定を尊重）
                cards.append(Card(lines, off, off + ln, -1 - k, seg_i))
                off += ln
            if cards:
                cards[0].head = True
        else:
            cards = build_cards(text, seg_i)
        if not cards:
            continue

        a_src, b_src = float(s["start"]), float(s["end"])
        clean_all = "".join(c.plain() for c in cards)
        # 正規化オフセット表（クリーン文字→正規化文字数の累積）
        cum = [0]
        for ch in clean_all:
            cum.append(cum[-1] + len(normalize_text(ch)))
        norm_seg = "".join(normalize_text(ch) for ch in clean_all)
        base = stream_text.find(norm_seg, cursor) if (stream_chars and norm_seg) else -1
        if base >= 0:
            cursor = base + len(norm_seg)
            for c in cards:
                na, nb = base + cum[c.cs], base + cum[c.ce]
                if nb <= na:
                    nb = na + 1
                na = min(na, len(stream_chars) - 1)
                nb = min(nb, len(stream_chars))
                ws = stream_chars[na][1]
                we = stream_chars[nb - 1][2]
                c.js = qframe(src_to_jc(min(a_src, ws) if c.head else ws))
                c.je = qframe(src_to_jc(max(we, ws + 0.3)))
        else:
            if stream_chars:
                print(f"[align] 文{s.get('i', seg_i + 1)}: 語ストリームに一致せず → 文字数比例", file=sys.stderr)
            total_len = max(1, sum(len(c.plain()) for c in cards))
            ja, jb = src_to_jc(a_src), src_to_jc(b_src)
            acc = 0
            for c in cards:
                c.js = qframe(ja + (jb - ja) * acc / total_len)
                acc += len(c.plain())
                c.je = qframe(ja + (jb - ja) * acc / total_len)

        # 短尺カードの統合（同文のみ・2行に収まる時のみ）
        i = 0
        while i < len(cards) - 1:
            a, b = cards[i], cards[i + 1]
            short_a = (a.je - a.js) < args.telop_min_dur
            orphan_b = len(b.plain()) < 4
            if a.sent == b.sent and (short_a or orphan_b):
                m = reflow_merge(a, b)
                if m is not None:
                    cards[i:i + 2] = [m]
                    continue
            i += 1
        all_cards.extend(cards)

    # 開始の単調性＋次カード開始まで表示保持（takumi同様）
    all_cards.sort(key=lambda c: (c.js, c.seg_i))
    for i in range(1, len(all_cards)):
        if all_cards[i].js < all_cards[i - 1].js:
            all_cards[i].js = all_cards[i - 1].js
    for i, c in enumerate(all_cards):
        if i + 1 < len(all_cards):
            c.je = max(c.js + 0.3, all_cards[i + 1].js)
        else:
            c.je = min(total_jc, max(c.je, c.js + 0.6))

    telops = []
    for i, c in enumerate(all_cards):
        png = work / f"telop_{i:03d}.png"
        render_telop_png(c.lines, png, font_path)
        telops.append({"png": str(png), "start": round(c.js, 6), "end": round(c.je, 6)})
    (work / "telop_cards.json").write_text(
        json.dumps([{"text": "\n".join(c.lines), "start": c.js, "end": c.je} for c in all_cards],
                   ensure_ascii=False, indent=2), encoding="utf-8")

    # --- 素材の新窓（＋冒頭メイン画面ガード） ---
    for p in plan:
        p["_s"] = qframe(src_to_jc(float(p["start"])))
        p["_e"] = qframe(src_to_jc(float(p["end"])))
    if not args.allow_open_overlay and segs:
        first_main = qframe(src_to_jc(float(segs[0]["end"])))
        kept_plan = []
        for p in plan:
            if p["_s"] < first_main - 1e-6:
                if p["_e"] <= first_main + 0.3:
                    print(f"[open-guard] 冒頭素材 {p['file']} は最初の文と重なるため外しました"
                          f"（冒頭はメイン画面）。残す場合は --allow-open-overlay", file=sys.stderr)
                    continue
                print(f"[open-guard] {p['file']} の開始を {p['_s']:.2f}s → {first_main:.2f}s へ"
                      f"（最初の文はメイン画面）", file=sys.stderr)
                p["_s"] = first_main
            kept_plan.append(p)
        plan = kept_plan

    # --- ffmpeg 1パス合成 ---
    def esc(t):
        return f"{t:.6f}"

    def en_between(s, e):
        # 半開区間 [s, e): 境界フレームの二重取り/取りこぼし防止
        return f"between(t,{esc(s - hf)},{esc(e - hf)})"

    # 画像は -framerate 1（takumi方式・低負荷）。looped入力は無限ストリームなので、
    # 出力終端は -shortest ではなく -t total_jc で明示的に打ち切る（takumi render.py と同方式。
    # -shortest はoverlayのEOF反復と噛み合わず無限レンダになる）。
    IMG_IN = ["-framerate", "1", "-loop", "1", "-i"]
    inputs = ["-i", str(avatar)]
    idx = 1
    for p in plan:
        f = d / p["file"]
        if p["type"] == "broll":
            need = float(p["end"]) - float(p["start"]) + 0.5
            inputs += ["-t", f"{need:.3f}", "-i", str(f)]
        else:
            inputs += [*IMG_IN, str(f)]
        p["_idx"] = idx
        idx += 1
    telop_i0 = idx
    for t in telops:
        inputs += [*IMG_IN, t["png"]]
        idx += 1

    fc = []
    # 映像ジェットカット（半開区間select・1デコード）
    if len(kept) > 1 or (kept and (kept[0]["start"] > 0 or kept[0]["end"] < dur - 1e-3)):
        keep_expr = "+".join(en_between(c["start"], c["end"]) for c in kept)
        fc.append(f"[0:v]select='{keep_expr}',setpts=N/FRAME_RATE/TB[jc]")
    else:
        fc.append("[0:v]null[jc]")
    # 音声: asplit+atrim（サンプル精度・映像フレーム数と厳密一致）＋5msマイクロフェード
    n_chunks = len(kept)
    if n_chunks > 1:
        fc.append(f"[0:a]asplit={n_chunks}" + "".join(f"[ain{i}]" for i in range(n_chunks)))
        for i, c in enumerate(kept):
            frames = int(round((c["end"] - c["start"]) * FPS))
            dd = frames / FPS
            end = c["start"] + dd
            fo = max(dd - AFADE, 0.0)
            fc.append(f"[ain{i}]atrim=start={esc(c['start'])}:end={esc(end)},asetpts=PTS-STARTPTS,"
                      f"afade=t=in:st=0:d={AFADE},afade=t=out:st={esc(fo)}:d={AFADE}[ac{i}]")
        fc.append("".join(f"[ac{i}]" for i in range(n_chunks)) + f"concat=n={n_chunks}:v=0:a=1[aout]")
    else:
        fc.append("[0:a]anull[aout]")

    cur = "jc"
    for p in plan:
        i = p["_idx"]
        st, en = p["_s"], p["_e"]
        if p.get("fit") == "cover" or p["type"] == "broll":
            fc.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                      f"crop={W}:{H},setpts=PTS-STARTPTS+{esc(st)}/TB[a{i}]")
            ov = "(W-w)/2:(H-h)/2"
        else:
            fc.append(f"[{i}:v]scale={IMG_BOX_W}:{IMG_BOX_H}:force_original_aspect_ratio=decrease,"
                      f"setpts=PTS-STARTPTS[a{i}]")
            ov = f"(W-w)/2:{IMG_CENTER_Y}-h/2"
        nxt = f"v{i}"
        fc.append(f"[{cur}][a{i}]overlay={ov}:enable='{en_between(st, en)}'[{nxt}]")
        cur = nxt
    for j, t in enumerate(telops):
        i = telop_i0 + j
        nxt = f"t{j}"
        fc.append(f"[{cur}][{i}:v]overlay=0:0:enable='{en_between(t['start'], t['end'])}'[{nxt}]")
        cur = nxt

    preset = os.environ.get("CLBS_PRESET", "veryfast")
    crf = os.environ.get("CLBS_CRF", "20")
    out_path = d / args.out
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(fc),
           "-map", f"[{cur}]", "-map", "[aout]",
           "-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p",
           "-r", f"{FPS:.6f}",
           "-c:a", "aac", "-b:a", "192k", "-t", f"{total_jc:.6f}", str(out_path)]
    print(f"[build] telop {len(telops)}枚 / 素材 {len(plan)}件 → {out_path}", file=sys.stderr)
    rc, _o, err = run(cmd)
    if rc != 0:
        print(err[-2000:], file=sys.stderr)
        sys.exit("[error] ffmpeg 失敗")
    print(f"[done] {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
