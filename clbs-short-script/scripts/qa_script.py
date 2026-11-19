#!/usr/bin/env python3
"""
clbs-youtube-script-pro / 台本リンター（出荷前ゲート・汎用版）

日本語台本の「AIっぽさ」「英文和訳調」「タグ規約違反」のうち機械判定できるものを検査する。
FAIL が1件でもあれば exit 1（出荷不可）。WARN は目視判断用。標準ライブラリのみで動く。

使い方:
  python3 scripts/qa_script.py <プロジェクトdir>          # script.txt / heygen_script.txt / media_list.yaml を検査
  python3 scripts/qa_script.py <台本ファイル> [--role auto|script|heygen|draft|short]
  python3 scripts/qa_script.py <ショート台本> --role short --strict   # clbs-short-script の台本（<#0.8#> 形式）
  python3 scripts/qa_script.py <対象> --strict             # 和訳調WARNをFAILに昇格（納品前ゲート）
  python3 scripts/qa_script.py <対象> --json               # 機械可読（ja_style_fix.py が使う）

出力: qa_script_report.md（プロジェクトdir指定時）＋ stdout
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

TAG_RE = re.compile(r"[\[【]\s*(見出し\s*[:：][^\]】]*|スライド\s*\d*|ピクチャー?\s*\d*|Bロール\s*\d*|ビーロール\s*\d*|カムリターン)\s*[\]】]")
BREAK_RE = re.compile(r"<\s*break\b[^>]*>|<#[\d.]+#>")

# ---- 禁止フレーズ（文頭メタコメント。台本・セリフ全般で禁止）----
BANNED_PHRASES = [
    "最初にはっきりと言います", "いきなり結論", "いきなり答え", "言っちゃうんですけど",
    "率直に言うと", "率直に申し上げ", "ズバリ言", "正直に言います", "正直に申し上げ",
    "結論から言うと", "結論から言えば", "一言で言えば", "単刀直入に",
    "想像してみてください",
]
BANNED_HEAD_ONLY = ["端的に言えば"]  # 文頭のみ禁止

# 許可取り・前振り・反語（英訳調）
BANNED_PHRASES += [
    "させてください",       # 許可取り →「〜します」で言い切る
    "では、いきますね",     # 前振り宣言 → 削除して本題へ
    "ではありませんか",     # 反語調 →「〜ですよね」
    "添えておきますね",     # 勿体ぶった前振り →「話しておきます」
    "どうか覚えておいて",   # 懇願調 →「覚えておいてください」
]

# 例外: 冒頭30秒の②「衝撃的な結論を先に言いますけど」は本人指定の型（禁止対象に含めない）
ALLOWED_PHRASES = ["衝撃的な結論を先に言いますけど"]

# 対面セミナー前提の言葉（視聴者は画面の向こうで一人で見ている）
SEMINAR_PHRASES = ["持って帰ってほしい", "持って帰っていただ", "会場の皆さん", "お手元の"]

# 述語で終わっているとみなす文末（体言止めヒューリスティックの除外）
PRED_END_RE = re.compile(
    r"(です|ます|ません|ました|でした|でしょう|しょうか|ですね|ますね|ますよね|ですよね|よね|ですよ|ますよ|よ|"
    r"ですか|ますか|ください|んです|のです|かもしれません|ございます)[。！？]$")
# 同じ語尾の連続を見るための文末の型
ENDING_RE = re.compile(
    r"(んですよね|ですよね|ますよね|んですよ|ですよ|ますよ|んですね|ですね|ますね|んです|でした|ました|ません|ください|です|ます)[。！？]?$")

# 演出比喩・スタンス宣言（会話で口から出ない言い回し。汎用版ではWARN）
DECOR_PHRASES = [
    "ところが、です", "ひびを入れ", "扉を閉ざ", "扉が開", "光を当て", "土俵に乗", "土俵に上が",
    "テーブルの上に乗", "影を落と", "という言い方は、一度もしません", "という言い方を、一度もしません",
    "立ち止まり", "立ち止まって", "立ち止まります", "素通り", "寄り道", "歩みを進め", "ここまで歩いて",
]

AI_COINED_RE = re.compile(r"[ァ-ヶー]{3,}[0-9０-９]+")

# 和訳調（英文和訳っぽさ）の集計検査。1件ずつは自然でも密度が上がると訳文調になるため、文書単位の比率で判定する。
WAYAKU_PATTERNS = [
    ("受け身で行為者を隠す", re.compile(r"(?<!ま)(され(ました|ています|ている|ます|た|る)|られてい(ます|る))"), 0.06,
     "「〜が進められています」→「〈誰〉が進めています」。行為者を主語に立てて能動で言う（尊敬語は対象外）"),
    ("名詞化・迂言", re.compile(r"(ことになり|ことになる|ことになります|という事実|という点|として捉え|する価値のある)"), 0.02,
     "「受け取っていることになるのか」→「受け取っているのか」。名詞に畳まず動詞で言う"),
    ("〜わけです の多用", re.compile(r"わけ(です|ですね|なんです|なんですね)[。！？]?$"), 0.03,
     "説明の後置きが続くと訳文調になる。事実はそのまま言い切る"),
    ("冗長な強調", re.compile(r"(かなり|非常に|決して|明確に|確実に|まさに|そもそも)"), 0.04,
     "強調語を削ったほうが強い。「かなり不思議」→「不思議」"),
    ("〜ですね/ますね で言い切りを弱める", re.compile(r"(です|ます)ね[。！？]?$"), 0.10,
     "進行の宣言（整理していきますね等）は言い切る。共感の場面だけ残す"),
    ("〜のほう の多用", re.compile(r"のほう(が|を|に|で|から|は)?"), 0.04,
     "「Aのほうから見て」→「Aから見て」"),
    ("〜ことができる", re.compile(r"ことができ"), 0.02,
     "「変えることができます」→「変えられます」"),
]


# 話し言葉でまっすぐ言う・段取りやメタの文・飾りの言葉（references/japanese_style.md §5〜§8）。
# 1件でも引っかかる言い回しなので文ごとに WARN（--strict で FAIL）。
PLAIN_WORDS = [
    (re.compile(r"丹念に|徹底的に|興味深い|真剣に|本質的には|数々の|歴史的な"), "飾りの言葉を削る（「丹念に記録した」→「記録し続けた」）"),
    (re.compile(r"における"), "「〜における」→「〜での」"),
    (re.compile(r"一冊の本を|一本の論文|(?<![のがも])ある(患者|研究|実験|調査)(の|を|が|に|結果)"), "英語の a/an の直訳（「一冊の本を発表」→「本を出した」／「ある患者」→どんな患者かを言う）"),
    (re.compile(r"捉え"), "「〜を捉える」→「〜と考える」など普段の動詞に"),
    (re.compile(r"[0-9０-９一二三四五六七八九]つの手順|[0-9０-９一二三四五六七八九]つ目の(手順|ステップ)は"), "「3つの手順」→「3つのステップ」、列挙は「1つ目は」「2つ目は」（同じ名詞を毎回繰り返さない）"),
    (re.compile(r"(?<!目の)(?<!直)(次|前)の(場面|パート|セクション|章)(では|に|へ|で)"), "動画の区切りを名詞にしない（「次の場面では」→「次は」「ここからは」／「前の章で」→「先ほど」）"),
    (re.compile(r"試み(です|は|を|が)"), "「〜の試み」と名詞にしない（「並べてみる試みです」→「並べて考えてみます」）"),
    (re.compile(r"提唱|体系化し|位置づけ|を経て|極めて"), "論文調の漢語動詞を普段の動詞に（提唱した→出した／体系化した→まとめた／位置づける→考える／を経て→が経って／極めて→とても）"),
    (re.compile(r"役割を(果た|担)|働きを担"), "「役割を果たす／担う」→「〜の働きをする」「〜をする」"),
    (re.compile(r"(結果|研究|論文|データ|数字|調査|問い|謎|発見|事実|理論)(は|が|こそが)、?[^。]*(突きつけ|教えてくれ|悩ませ|驚かせ|揺るが|届けてくれ|見せてくれ|もたら|巻き起こ|迫って|影響を与え|許さない)"),
     "無生物を主語にして人に働きかけさせない（「この結果は私たちに現実を突きつけます」→「この結果から、現実が見えてきます」）。人を主語にする"),
    (re.compile(r"(示し(た|ている)|明らかにし(た|ている)|確かめられた|学べる)のは"), "分裂文をやめる（「調査が示したのは〜ということです」→「調査では〜と分かったんです」）"),
    (re.compile(r"(大切|重要|大事)なのは[^。]*(点|こと)です|(分かった|わかった)のは[^。]*(こと|点)です|わけですね"), "「大切なのは〜点です」型で強調を前置きしない。直接言い切る（まとめの列挙・対比の「〜のは、Aではなく」は対象外）"),
    (re.compile(r"こう(なります|言えます|いうことです|なるんです)。$|ある(性質|特徴|共通点|ルール|法則)があります。$"), "予告だけの文で区切らない。次の文とつないで中身を1文で言う"),
    (re.compile(r"(\d|[一二三])段目の答え|答えを出します|が見えてきました|時間を追って見ていきます|を整理して(みます|おきます)|に注目してみます|掘り下げて考えてみます|ここまでにしておきます|について1つ注意があります|ここでは\d+つを紹介します|ここで言う[^。]{1,12}とは"),
     "段取りを実況する文を置かない。削除して中身の文から入る（大きな話題の切り替えを告げる1文はよい）"),
    (re.compile(r"について(解説|説明|お話し|話)(します|していきます)|を(解説|紹介)していきます。$"), "段取りの前置きを置かず、本題の中身から入る（「今日は〜について解説します」→削除）。冒頭の「今日のテーマは『〜』です」はよい"),
    (re.compile(r"はっきりさせておきます|断定していい|正確な言い方になります|線を引いておきます|この場所に戻ってきます|これも大事なところです"), "誠実さ・断定・進行のメタ宣言を置かない（削除するか「ただし、」でつなぐ）"),
    (re.compile(r"掘り当て|波紋を(広げ|呼)|足を踏み入れ|足元を疑|を見つめ|刻まれ|拾い上げ|に挑(ん|み|む)"), "盛り上げるだけの比喩をやめ、直接の動詞で言う（見つめる→調べる／刻まれる→記録される／足を踏み入れた→扱い始めた／挑む→取り組む）"),
    (re.compile(r"(問い|疑問|問題|視点|観点|基準|基準点|条件|前提|焦点|軸|重点|意識|仮説|ゴール|目標|未来|イメージ|価値|意味|期待|希望|テーマ|論点|課題|目的)(を|の両方を)[^。]{0,8}置(い|き|く|か|け)|(心|頭|胸)の中に置"),
     "物理的に存在しない概念を「置く」と言わない（「問いを置いておきます」→削除／「心の中に置いたゴール」→「心に決めたゴール」）。物を置く文は対象外"),
    (re.compile(r"(問い|謎|疑問|論争)を[^。]{0,15}確かめ"), "問い・疑問は「確かめる」ものではない（「疑問を確かめるために」→「疑問に答えるために」）。確かめるのは事実・数字"),
    (re.compile(r"(考えて|並べて|比べて|思い浮かべて)みてください|に戻ってください"), "説明の途中で視聴者に「〜してみてください」と指示しない（「考えてみてください」→「たとえば、〜です」）。CTAの行動指示は対象外"),
    (re.compile(r"名前を、?[^。]{1,20}といいます"), "名乗りの和訳調（「名前を、〜といいます」→「その研究者の名前は、〜です」）"),
    (re.compile(r"(?<![ぁ-ん])あの(感覚|瞬間|人|会社員|患者|実験|研究|調査|話|体験|方法|ノウハウ)"), "指示語「あの」を使わない（「あの会社員」→「先ほどの会社員」／「あの感覚」→「その感覚」）"),
    (re.compile(r"(3D|３Ｄ|CG|ＣＧ)(で|を使って)[^。]{0,10}(再現|作|描)|カメラ(を|が)(近づけ|引い|引き|寄せ)|(黒板|ボード)に[^。]{0,20}(描|書)(いて|き|く|いた)|画面の(左|右|手前|奥)(側)?に"),
     "画面の作りや編集の工夫をナレーションで言わない（「カメラを近づけて」→「近づいて見ると」／「黒板に描いて整理します」→削除）。模式図の断りは画面のテロップで出す"),
]


def split_sentences(text: str) -> list[str]:
    out = []
    for m in re.finditer(r"[^。！？\n]+[。！？]?", text):
        s = m.group(0).strip()
        if s:
            out.append(s)
    return out


class Lint:
    def __init__(self):
        self.fails: list[str] = []
        self.warns: list[str] = []
        self.wayaku_sents: dict[str, list[str]] = {}   # label -> hit sentences
        self.flagged: set[str] = set()                   # FAIL/WARN に該当した文（ja_style_fix.py 用）

    def fail(self, msg: str, sent: str | None = None):
        self.fails.append(msg)
        if sent:
            self.flagged.add(sent)

    def warn(self, msg: str, sent: str | None = None):
        self.warns.append(msg)
        if sent:
            self.flagged.add(sent)


def lint_text(path: Path, role: str, lint: Lint, strict: bool, style: bool = True) -> int:
    """role: script(script.txt=タグ入り) / heygen(heygen_script.txt=タグ除去済み) / draft(下書き・タグ検査なし)
    / short(clbs-short-script のショート台本。<#0.8#> はタグ前後だけ・見出しテロップなし)
    style=False は文体検査を省く（script.txt から作った heygen_script.txt の二重指摘を避ける）"""
    raw = path.read_text(encoding="utf-8")
    name = path.name

    # ---- 行レベル ----
    for i, line in enumerate(raw.splitlines(), 1):
        st = line.strip()
        if st.startswith("#"):
            lint.fail(f"{name}:{i} Markdown見出し行が地の文に残存（アバターが読み上げる）「{st[:25]}」")
        if st == "---":
            lint.fail(f"{name}:{i} 区切り線 --- が残存")
        if "**" in st:
            lint.fail(f"{name}:{i} AI強調「**」残存")
        if re.search(r"——|――|ーー", st):
            lint.fail(f"{name}:{i} 余韻ダッシュ（——/ーー）残存")
        if role == "heygen" and TAG_RE.search(st):
            lint.fail(f"{name}:{i} heygen_script.txt に演出タグが残存「{TAG_RE.search(st).group(0)}」")
        if role == "short" and re.search(r"[【\[]\s*テロップ", st):
            lint.fail(f"{name}:{i} ショート台本に見出しテロップ【テロップ：…】が残存（ショートは焼かない）")

    # ---- 文レベル（タグ・break除去後）----
    speech = BREAK_RE.sub("\n", TAG_RE.sub("\n", raw))
    all_sents = split_sentences(speech)
    run_end, run_len, run_start = None, 0, ""
    for sent in (all_sents if style else []):
        plain = sent.rstrip("。！？")
        check = sent
        for ok in ALLOWED_PHRASES:
            check = check.replace(ok, "")
        for ph in BANNED_PHRASES:
            if ph in check:
                lint.fail(f"{name} 禁止フレーズ「{ph}」:「{sent[:30]}」", sent)
        for ph in BANNED_HEAD_ONLY:
            if sent.startswith(ph):
                lint.fail(f"{name} 文頭禁止フレーズ「{ph}」:「{sent[:30]}」", sent)
        for ph in SEMINAR_PHRASES:
            if ph in sent:
                lint.fail(f"{name} セミナー用語「{ph}」（視聴者は一人で見ている）:「{sent[:30]}」", sent)
        for ph in DECOR_PHRASES:
            if ph in sent:
                lint.warn(f"{name} 演出比喩・宣言メタ「{ph}」（会話で口から出るか）:「{sent[:30]}」", sent)
        if AI_COINED_RE.search(plain):
            lint.warn(f"{name} AI造語疑い（カタカナ+数字の命名）:「{AI_COINED_RE.search(plain).group(0)}」", sent)
        # ---- 語尾（読み上げで抑揚が崩れる型。japanese_style.md §3）----
        if re.search(r"(って。|」と。)$", sent) or re.search(r"(?<![っこ])と。$", sent):
            lint.fail(f"{name} 引用で止める倒置/と止め:「{sent[-25:]}」", sent)
        if sent.endswith("。") and not PRED_END_RE.search(sent):
            last = plain[-1] if plain else ""
            if re.match(r"[一-龥ァ-ヶA-Za-z0-9０-９ー]", last):
                lint.warn(f"{name} 体言止め疑い:「{sent[-28:]}」", sent)
            elif re.search(r"(こと|もの|の)。$", sent):
                lint.warn(f"{name} 体言止め疑い（こと/もの/の止め）:「{sent[-28:]}」", sent)
            elif len(plain) <= 7:
                lint.warn(f"{name} 短い言い切り:「{sent}」（〜んです／〜んですよね で結ぶ）", sent)
        m_end = ENDING_RE.search(sent)
        end = m_end.group(1) if m_end else None
        if end and end == run_end:
            run_len += 1
            if run_len == 3:
                lint.warn(f"{name} 同じ語尾「〜{end}」が3文以上続く（語尾を変えて話し言葉に）:「{run_start[:20]}」から", sent)
        else:
            run_end, run_len, run_start = end, 1, sent
        for rx, advice in PLAIN_WORDS:
            mm = rx.search(sent)
            if mm:
                (lint.fail if strict else lint.warn)(f"{name} 言い回し「{mm.group(0)}」→ {advice}:「{sent[:30]}」", sent)

    # ---- 和訳調の集計検査（比率が閾値を超えたら1件。--strict で FAIL）----
    if all_sents and style:
        for label, rx, ratio, advice in WAYAKU_PATTERNS:
            hits = [x for x in all_sents if rx.search(x)]
            for h in hits:                      # 修正パスの対象には全件を渡す（直すかは Gemini/人が判断）
                lint.flagged.add(h)
            if len(hits) >= 2 and len(hits) / len(all_sents) > ratio:   # 1件だけなら比率が高くても騒がない
                ex = "／".join(h[:26] for h in hits[:2])
                msg = (f"{name} 和訳調「{label}」{len(hits)}件/{len(all_sents)}文"
                       f"（目安{int(ratio*100)}%超）→ {advice}｜例:{ex}")
                lint.wayaku_sents.setdefault(label, []).extend(hits)
                (lint.fail if strict else lint.warn)(msg)

    # ---- ショート台本（<#0.8#> はタグの直前・直後だけ。尺の目安）----
    if role == "short":
        for m in BREAK_RE.finditer(raw):
            before = raw[:m.start()].rstrip(" \t")
            after = raw[m.end():].lstrip(" \t")
            near_tag = (TAG_RE.search(before[-40:]) and re.search(r"[\]】]$", before)) or TAG_RE.match(after)
            if not near_tag and not before.endswith("\n") and not after.startswith("\n") and before and after:
                ctx = (before[-15:] + m.group(0) + after[:10]).replace("\n", " ")
                lint.warn(f"{name} 文中に沈黙マーカー「{m.group(0)}」（タグの直前・直後だけに置く）:「{ctx}」")
        n_chars = len(re.sub(r"\s", "", "".join(all_sents)))
        if n_chars > 450:
            lint.warn(f"{name} ナレーション{n_chars}字（90秒の目安450字を超える。60秒なら250〜350字）")

    # ---- タグ整合（script / short）----
    if role in ("script", "short"):
        tags = [(m.start(), m.end(), m.group(0)) for m in TAG_RE.finditer(raw)]
        for typ, label in (("スライド", "スライド"), ("ピクチャー?", "ピクチャー"), ("Bロール", "Bロール")):
            nums = [int(n) for n in re.findall(rf"[\[【]\s*{typ}\s*(\d+)\s*[\]】]", raw)]
            if nums and sorted(set(nums)) != list(range(1, max(nums) + 1)):
                lint.warn(f"{name} {label}番号に欠番/重複: {nums}")
        closers = re.compile(r"カムリターン|スライド|ピクチャー?|Bロール")
        for pos, end, tag in tags:
            if re.search(r"ピクチャー?\s*\d|Bロール\s*\d", tag):
                rest = raw[end:]
                later = [m2.group(0) for m2 in TAG_RE.finditer(rest)]
                if not any(closers.search(t2) for t2 in later):
                    lint.warn(f"{name} {tag} が閉じられていない（[カムリターン]で閉じる）")
            if re.search(r"Bロール\s*\d", tag):
                # Bロール〜次のタグまでの文数（1〜2文が原則）
                nxt = TAG_RE.search(raw, end)
                seg = raw[end:nxt.start()] if nxt else raw[end:]
                n = len(split_sentences(BREAK_RE.sub("\n", seg)))
                if n > 2:
                    lint.warn(f"{name} {tag} の区間が{n}文（原則1〜2文・最長5秒。1文ごとに[カムリターン]で戻す）")
        if role == "short":
            pass
        elif not BREAK_RE.search(raw):
            lint.warn(f"{name} 沈黙マーカー（<break>）が1つもない（ジェットカット点なし）")
        else:
            # 文末に break が付いていない文の数
            missing = 0
            for m in re.finditer(r"[。！？](?!\s*<)", raw):
                after = raw[m.end():m.end() + 30]
                if not re.match(r"\s*(<\s*break|<#|\[|【|$)", after):
                    missing += 1
            if missing:
                lint.warn(f"{name} 文末の直後に <break> が無い箇所 {missing}件（全文末・全タグ直後に付ける）")
    return len(all_sents)


def check_media_list(project: Path, script: Path, lint: Lint):
    ml = project / "media_list.yaml"
    if not ml.exists():
        lint.warn("media_list.yaml が無い（素材台帳未出力）")
        return
    raw = script.read_text(encoding="utf-8")
    y = ml.read_text(encoding="utf-8")

    def nums_in_yaml(section: str) -> list[int]:
        m = re.search(rf"^{section}:\s*$(.*?)(?=^\S|\Z)", y, re.M | re.S)
        if not m:
            return []
        key = "order" if section == "headings" else "number"
        return sorted(int(x) for x in re.findall(rf"^\s*-?\s*{key}:\s*(\d+)", m.group(1), re.M))

    pairs = (("slides", r"スライド\s*(\d+)"), ("pictures", r"ピクチャー?\s*(\d+)"), ("brolls", r"Bロール\s*(\d+)"))
    for section, pat in pairs:
        in_script = sorted(set(int(n) for n in re.findall(rf"[\[【]\s*{pat}\s*[\]】]", raw)))
        in_yaml = nums_in_yaml(section)
        if in_script != in_yaml:
            lint.fail(f"media_list.yaml {section} と script.txt のタグ番号が不一致: 台本{in_script} / 台帳{in_yaml}")
    n_head = len(re.findall(r"[\[【]\s*見出し\s*[:：]", raw))
    n_yaml = len(nums_in_yaml("headings"))
    if n_yaml and n_head != n_yaml:
        lint.fail(f"media_list.yaml headings {n_yaml}件 と [見出し] タグ {n_head}件 が不一致")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="プロジェクトdir または 台本ファイル")
    ap.add_argument("--role", choices=["auto", "script", "heygen", "draft", "short"], default="auto")
    ap.add_argument("--strict", action="store_true", help="和訳調の比率WARNと言い回しWARNをFAILに昇格（納品前ゲート）")
    ap.add_argument("--json", action="store_true", help="結果をJSONで出力")
    args = ap.parse_args(argv)
    target = Path(args.target).expanduser().resolve()

    lint = Lint()
    counts: dict[str, int] = {}
    report_path = None
    if target.is_dir():
        script = target / "script.txt"
        heygen = target / "heygen_script.txt"
        if not script.exists():
            print(f"ERROR: {target} に script.txt がない", file=sys.stderr)
            return 1
        counts["script.txt"] = lint_text(script, "script", lint, args.strict)
        if heygen.exists():
            counts["heygen_script.txt"] = lint_text(heygen, "heygen", lint, args.strict, style=False)
            if counts["heygen_script.txt"] != counts["script.txt"]:
                lint.fail(f"script.txt と heygen_script.txt の文数不一致: {counts['script.txt']} vs {counts['heygen_script.txt']}")
        else:
            lint.warn("heygen_script.txt が無い（HeyGen貼り付け用未出力）")
        check_media_list(target, script, lint)
        report_path = target / "qa_script_report.md"
    else:
        role = args.role
        if role == "auto":
            role = {"script.txt": "script", "heygen_script.txt": "heygen"}.get(target.name, "draft")
        counts[target.name] = lint_text(target, role, lint, args.strict)

    verdict = "PASS" if not lint.fails else "FAIL"
    if args.json:
        print(json.dumps({"verdict": verdict, "fails": lint.fails, "warns": lint.warns,
                          "flagged": sorted(lint.flagged), "counts": counts}, ensure_ascii=False, indent=2))
        return 1 if lint.fails else 0

    lines = [f"# qa_script Report: {verdict}", "",
             f"- 対象: {', '.join(f'{k}({v}文)' for k, v in counts.items())}",
             f"- FAIL: {len(lint.fails)} / WARN: {len(lint.warns)}", ""]
    if lint.fails:
        lines += ["## FAIL（出荷不可・要修正）", ""] + [f"- {m}" for m in lint.fails] + [""]
    if lint.warns:
        lines += ["## WARN（目視判断）", ""] + [f"- {m}" for m in lint.warns] + [""]
    if not lint.fails and not lint.warns:
        lines += ["問題なし。", ""]
    report = "\n".join(lines)
    print(report)
    if report_path:
        report_path.write_text(report + "\n", encoding="utf-8")
        print(f"- {report_path}")
    return 1 if lint.fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
