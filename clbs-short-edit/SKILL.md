---
name: clbs-short-edit
description: 縦型ショート動画（9:16）の自動編集スキル。顔出しアバター動画（avatar.mp4・音声入り）＋台本（script.txt のV41タグ）＋縦型素材（1:1スクエア画像スライド / 4:3ピクチャー / 9:16全画面Bロール動画）から、素材の自動差し込みと焼き込みテロップ付与を行い、完成MP4（1080x1920）を書き出す。テロップは既定で「顔の下・胸元上（位置B）／2行／大きめ／白＋黄キーワード／太い黒フチ」。前段は `clbs-short-script`（台本＝script.txt）と素材生成（Higgsfield: Nano Banana 画像 / Veo 動画）。「ショート編集」「ショート動画編集」「縦型動画編集」「リール編集」「Shorts編集」「ショートを編集して」「テロップ焼き込み（縦型）」「short-edit」などのキーワードで使用。16:9のYouTube本編編集は `clbs-youtube-edit`、台本作成は `clbs-short-script` を使う。
---

# clbs-short-edit — 縦型ショート動画 自動編集（9:16）

# v2.0 — アバター動画＋V41タグ台本＋縦型素材 → ジェットカット＋素材差し込み＋テロップ焼き込み → 完成MP4。
#   ジェットカット: 無音を検出して詰める（既定で SIL_MIN=0.20s 超を除去＝ほぼ喋りっぱなし）。--no-jetcut で無効化。
#   テロップ既定位置 = B（顔の下・胸元上）/ 大きめ / 白＋黄 / 太い黒フチ（PIL描画）。
#   3兄弟スキル: clbs-short-research（リサーチ）→ clbs-short-script（台本）→ clbs-short-edit（編集・本スキル）

## 何をするか

| 入力 | 出力 |
|---|---|
| `avatar.mp4`（縦型9:16・音声入り・台本を喋っている） | `final.mp4`（1080x1920・テロップ焼き込み＋素材差し込み済み） |
| `script.txt`（`clbs-short-script` のV41タグ付き台本） | （任意）中間: テロップPNG群・segments.json |
| 縦型素材: スライド(1:1) / ピクチャー(4:3) / Bロール(9:16) | |

## 縦型レイアウト規格

| 要素 | 縦横比 | 配置 |
|---|---|---|
| アバター | 9:16 | ベース（全画面） |
| `[Bロール N]` | 9:16 | 全画面 cover（差し込み区間だけアバターを覆う・クールな映像メタファー） |
| `[ピクチャー N]` | 4:3 | **中央付近のボックス（横1000×縦820に収め、縦中心 y≈640）**。上端のUI/検索欄を避けつつ、テロップは画像の下に出る |
| `[スライド N]` | 1:1 | 同上（中央付近・テロップはその下） |
| テロップ | — | **位置B：顔の下・胸元上（縦中心 y≈1230）／大きめ／1行目 白・2行目 黄／太い黒フチ12px**。画像差し込み中は画像の下に出る（重ねない） |

> **テロップは小分け**：ナレーションを「、。」で句に割り、さらに節の切れ目（〜た/て/だ の後に内容語）で分割し、**1チャンク=1カード**にして各文の時間窓に均等配置する（一度に出す量を減らす）。
> 1チャンクが長い時だけ2行に折り返す（`MAX_LINE=10`／助詞・語尾の後、または ひらがな→漢字・カタカナの語境界で折る＝「でも」「量子」等を割らない）。
> 見出しテロップは台本本体に書かない方針（[[feedback_short_no_telop]]）＝ここで自動字幕的に焼く。

---

## 入力フォルダの作り方

```
project/
  avatar.mp4              # HeyGen等の縦型アバター動画（台本音声入り）
  script.txt              # clbs-short-script 出力（V41タグ＋ナレーション）
  assets/
    slide1.png            # 1:1
    picture1.png          # 4:3
    broll1.mp4            # 9:16
  segments.json           # 文ごとのタイムライン＋テロップ本文（下記の作り方）
  plan.json               # 素材の差し込み区間（下記）
```

### segments.json（文タイムライン＋テロップ本文）
```json
[{"i":1,"start":0.0,"end":4.04,"text":"あなたが見るまで、目の前の現実は、まだ決まってないらしい"}, ...]
```
タイムラインの作り方（精度順）:
1. **音声クリップ長から算出（最確実）**: アバターが `clbs-short-script`→TTS（Fish等）で作った連結音声で作られている場合、各文クリップ長＋文間沈黙(0.8s)で開始終了を厳密に出せる（Whisper不要）。
2. **Whisper整合**: アバター音声を文字起こし→台本ナレーションと difflib 照合して各文・各タグの時刻を逆算（`clbs-youtube-edit` と同方式）。汎用ケースはこちら。
3. 取れない場合は文数で均等割り（粗い）。

### plan.json（素材差し込み区間）
台本のタグ位置 → 次の `[カムリターン]` までを各素材の表示区間にする。
```json
[{"type":"broll","file":"assets/broll1.mp4","start":0.0,"end":4.04,"fit":"cover"},
 {"type":"image","file":"assets/picture1.png","start":12.55,"end":19.7,"fit":"contain"},
 {"type":"image","file":"assets/slide1.png","start":25.8,"end":31.74,"fit":"contain"}]
```

---

## 実行

```bash
# 日本語フォントは初回に /tmp/jpfont.ttc へ自動コピー（ヒラギノ角ゴ W7）。--font で変更可
python3 ~/.claude/skills/clbs-short-edit/_tools/build_short.py --dir <project>
# → <project>/final.mp4
```

`build_short.py` の処理:
1. segments.json の各文テキストを2行カードに分割 → PIL でテロップPNG（位置B・白/黄・黒フチ）を生成
2. plan.json の素材を ffmpeg で時間窓オーバーレイ（Broll=cover / 画像=contain中央）
3. テロップPNGを最前面に時間窓オーバーレイ
4. アバター音声をそのまま使い `final.mp4`（H.264 / AAC / 30fps）を書き出し

### テロップ既定値（`build_short.py` 冒頭の定数）
- `TELOP_CENTER_Y=1230`（顔の下・胸元上）／`TELOP_FS=96`（長い行は自動縮小・最小54）
- 1行目=白、2行目=黄、黒フチ12px。位置・色・サイズはここで調整。

---

## 既知の調整ポイント（v1）
- 画像（ピクチャー/スライド）は横幅いっぱいで上寄せ。差し込み中は顔の上半分が隠れるカットイン。顔も残したい場合は画像を縮小して上部insetにする（`build_short.py` の image overlay の scale/位置）。
- テロップの日本語分割は簡易ヒューリスティック（形態素解析なし）。稀に語の途中で割れる（例「パチンとひと/つ」）。完璧にしたい hero 動画は、segments.json に手書きの telop カードを持たせて上書きするのが確実（将来 fugashi/MeCab 連携で自動精度を上げる余地）。
- ジェットカット（v2で実装）: `silencedetect` で無音を検出し select/aselect で詰める。強さは `SIL_MIN`（既定0.20s）・`KEEP_PAD`（既定0.05s）で調整。詰めすぎてプツ切れる場合は `KEEP_PAD` を増やすか `--no-jetcut`。
- テロップ/素材のタイミングはジェットカット後の新時刻へ自動リマップ（元台本の文字起こし長の系統誤差は実音声尺で補正）。Whisper整合を入れればさらに高精度。

---

## スキル連携（3兄弟）

| ステップ | スキル |
|---|---|
| 競合リサーチ・冒頭フック解剖 | `clbs-short-research` |
| 企画・台本（script.txt・見出しテロップなし） | `clbs-short-script` |
| 素材生成（画像=Nano Banana / 動画=Veo・Higgsfield-takumi） | （Higgsfield MCP・[[reference_fish_higgsfield_accounts]]） |
| ナレーション音声（Takumi声） | Fish（[[reference_fish_higgsfield_accounts]]・要takumi参照ID） |
| **縦型編集・テロップ焼き込み** | **`clbs-short-edit`（本スキル）** |

16:9のYouTube本編は `clbs-youtube-edit`。
