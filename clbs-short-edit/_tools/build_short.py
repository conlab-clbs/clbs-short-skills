#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_short.py — clbs-short-edit 縦型ショート動画 合成レンダラー（v2: ジェットカット対応）

入力（1フォルダ）:
  avatar.mp4    縦型9:16の顔出しアバター動画（音声入り。台本ナレーションを喋っている）
  segments.json [{"i":1,"start":0.0,"end":4.04,"text":"..."}, ...]  文ごとのタイムライン＋テロップ本文
  plan.json     [{"type":"broll"|"image","file":"assets/x","start":..,"end":..,"fit":"cover"|"contain"}, ...]
出力:
  final.mp4     ジェットカット＋素材差し込み＋テロップ焼き込み済み（1080x1920）

特徴:
  - ジェットカット: 無音区間を検出して詰める（既定でほぼ全ての無音を除去＝常に喋ってる状態）
  - 画像（ピクチャー/スライド）は画面中央付近に表示、テロップはその下（位置B）。Bロールは全画面cover。
  - テロップは小分け（1句ずつ・長い句のみ2行）、位置B（顔の下・胸元上）、白＋黄・太い黒フチ。
依存: ffmpeg / PIL(Pillow)
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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
IMG_CENTER_Y = 640        # 画像の縦中心（上端UI回避＆テロップ(1230)の上に収まる）

# ジェットカット
SIL_NOISE = "-30dB"
SIL_MIN = 0.20            # これ以上の無音を詰める（小さいほどアグレッシブ）
KEEP_PAD = 0.05          # 各発話セグメントの前後に残す余白（プツ切れ防止）

# テロップ分割
MAX_LINE = 10
ENDERS = set("たてだ")
PARTICLES = set("のはをがにもとで")


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


def dur_of(path):
    _rc, _o, err = run(["ffmpeg", "-i", str(path)])
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
    if not m:
        return None
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


# ---------------------------------------------------------------------------
# 日本語テロップ分割
# ---------------------------------------------------------------------------
def _is_content_head(c):
    return ("一" <= c <= "鿿") or ("゠" <= c <= "ヿ")


def segment_phrase(p):
    chunks, cur = [], ""
    for idx, ch in enumerate(p):
        cur += ch
        nxt = p[idx + 1] if idx + 1 < len(p) else ""
        if len(cur) >= 5 and ch in ENDERS and nxt and _is_content_head(nxt):
            chunks.append(cur)
            cur = ""
    if cur:
        chunks.append(cur)
    return chunks


def wrap_phrase(p):
    p = p.strip()
    if len(p) <= MAX_LINE:
        return [p]
    n = len(p)
    mid = (n + 1) // 2
    break_after = PARTICLES | ENDERS
    best = None
    for cut in range(2, n - 1):
        if cut > MAX_LINE or (n - cut) > MAX_LINE:
            continue
        prev, nxt = p[cut - 1], p[cut]
        cand1 = prev in break_after and nxt not in break_after
        cand2 = _is_content_head(nxt) and not _is_content_head(prev)
        if cand1 or cand2:
            d = abs(cut - mid)
            if best is None or d < best[0]:
                best = (d, cut)
    cut = best[1] if best else mid
    return [p[:cut], p[cut:]]


def split_cards(text):
    cards = []
    for ph in re.split(r"[、。，]", text):
        ph = ph.strip()
        if not ph:
            continue
        for chunk in segment_phrase(ph):
            cards.append(wrap_phrase(chunk))
    return cards or [[text]]


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
# ジェットカット（無音検出 → 詰める）
# ---------------------------------------------------------------------------
def detect_silences(path):
    _rc, _o, err = run(["ffmpeg", "-i", str(path), "-af",
                        f"silencedetect=noise={SIL_NOISE}:d={SIL_MIN}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.]+)", err)]
    sils = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        sils.append((s, e))
    return sils


def build_keeps(dur, sils):
    """無音を除いた発話セグメント [(start,end), ...]（前後に KEEP_PAD の余白）。"""
    keeps, prev = [], 0.0
    for (s, e) in sils:
        seg_end = min(dur, s + KEEP_PAD)
        if seg_end > prev:
            keeps.append([prev, seg_end])
        prev = max(prev, (e - KEEP_PAD) if e is not None else dur)
    if prev < dur:
        keeps.append([prev, dur])
    # 重なり/極小セグメント整理
    merged = []
    for k in keeps:
        if merged and k[0] <= merged[-1][1] + 0.01:
            merged[-1][1] = max(merged[-1][1], k[1])
        elif k[1] - k[0] > 0.05:
            merged.append(k)
    return merged


def make_mapper(keeps):
    """元時刻 → ジェットカット後の時刻 への写像。"""
    cum = []
    acc = 0.0
    for (a, b) in keeps:
        cum.append((a, b, acc))
        acc += (b - a)
    total = acc

    def mp(t):
        for (a, b, off) in cum:
            if t <= b:
                return off + max(0.0, t - a)
        return total
    return mp, total


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="clbs-short-edit 縦型ショート合成（ジェットカット対応）")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--avatar", default="avatar.mp4")
    ap.add_argument("--segments", default="segments.json")
    ap.add_argument("--plan", default="plan.json")
    ap.add_argument("--out", default="final.mp4")
    ap.add_argument("--font", default=None)
    ap.add_argument("--no-jetcut", action="store_true", help="ジェットカットを無効化")
    args = ap.parse_args()

    d = Path(args.dir)
    font_path = ensure_font(args.font)
    avatar = d / args.avatar
    segs = json.loads((d / args.segments).read_text(encoding="utf-8"))
    plan = json.loads((d / args.plan).read_text(encoding="utf-8")) if (d / args.plan).exists() else []
    work = d / "_work"
    work.mkdir(exist_ok=True)

    dur = dur_of(avatar)
    computed_total = max((s["end"] for s in segs), default=dur) or dur
    # segments/plan の時刻を実音声尺に合わせて補正（文字起こし長の系統誤差を吸収）
    scale = (dur / computed_total) if (computed_total and dur) else 1.0

    # --- ジェットカット ---
    if args.no_jetcut:
        base = avatar
        mp = lambda t: t  # noqa: E731
        new_total = dur
    else:
        sils = detect_silences(avatar)
        keeps = build_keeps(dur, sils)
        mp, new_total = make_mapper(keeps)
        sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keeps)
        base = work / "base_cut.mp4"
        rc, _o, err = run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(avatar),
            "-vf", f"select='{sel}',setpts=N/FRAME_RATE/TB",
            "-af", f"aselect='{sel}',asetpts=N/SR/TB",
            "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(base)])
        if rc != 0:
            print(err[-1500:], file=sys.stderr)
            sys.exit("ジェットカット失敗")
        print(f"[jetcut] {len(keeps)}セグメント / {dur:.1f}s → {new_total:.1f}s", file=sys.stderr)

    def remap(t):
        return round(mp(t * scale), 3)

    # --- テロップカード（各文の新窓に均等配置）---
    telops = []
    for s in segs:
        text = s.get("text", "").strip()
        if not text:
            continue
        a, b = remap(s["start"]), remap(s["end"])
        cards = split_cards(text)
        n = len(cards)
        step = (b - a) / n if n else 0
        for k, lines in enumerate(cards):
            png = work / f"telop_{s['i']}_{k}.png"
            render_telop_png(lines, png, font_path)
            telops.append({"png": str(png), "start": round(a + k * step, 3),
                           "end": round(a + (k + 1) * step, 3)})

    # --- 素材の新窓 ---
    for p in plan:
        p["_s"], p["_e"] = remap(p["start"]), remap(p["end"])

    # --- ffmpeg 入力 ---
    inputs = ["-i", str(base)]
    idx = 1
    for p in plan:
        f = d / p["file"]
        inputs += (["-i", str(f)] if p["type"] == "broll" else ["-loop", "1", "-i", str(f)])
        p["_idx"] = idx
        idx += 1
    telop_i0 = idx
    for t in telops:
        inputs += ["-loop", "1", "-i", t["png"]]
        idx += 1

    # --- filter_complex ---
    fc, cur = [], "0:v"
    for p in plan:
        i = p["_idx"]
        st, en = p["_s"], p["_e"]
        if p.get("fit") == "cover" or p["type"] == "broll":
            fc.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                      f"crop={W}:{H},setpts=PTS-STARTPTS+{st}/TB[a{i}]")
            ov = "(W-w)/2:(H-h)/2"
        else:
            # 中央付近のボックスに収めて表示（上端UIを避ける）。テロップ(位置B)はこの下に出る
            fc.append(f"[{i}:v]scale={IMG_BOX_W}:{IMG_BOX_H}:force_original_aspect_ratio=decrease,"
                      f"setpts=PTS-STARTPTS[a{i}]")
            ov = f"(W-w)/2:{IMG_CENTER_Y}-h/2"
        nxt = f"v{i}"
        fc.append(f"[{cur}][a{i}]overlay={ov}:enable='between(t,{st},{en})'[{nxt}]")
        cur = nxt
    for j, t in enumerate(telops):
        i = telop_i0 + j
        nxt = f"t{j}"
        fc.append(f"[{cur}][{i}:v]overlay=0:0:enable='between(t,{t['start']},{t['end']})'[{nxt}]")
        cur = nxt

    out_path = d / args.out
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(fc),
           "-map", f"[{cur}]", "-map", "0:a",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
           "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_path)]
    print(f"[build] telop {len(telops)}枚 / 素材 {len(plan)}件 → {out_path}", file=sys.stderr)
    rc, _o, err = run(cmd)
    if rc != 0:
        print(err[-1500:], file=sys.stderr)
        sys.exit("[error] ffmpeg 失敗")
    print(f"[done] {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
