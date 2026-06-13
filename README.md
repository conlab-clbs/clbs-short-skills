# clbs-short-skills

ショート動画（YouTube Shorts / TikTok / Instagram Reels）を **リサーチ → 台本作成 → 縦型編集** まで一気通貫で作る [Claude Code](https://claude.com/claude-code) スキル集（3兄弟）です。

```
clbs-short-research   媒体横断リサーチ＆冒頭フック解剖
        ↓ 04_short_research_summary.yaml
clbs-short-script     企画立案〜台本（ナレーション＋演出タグ）
        ↓ script.txt（＋素材プラン）
clbs-short-edit       縦型編集（ジェットカット＋素材差し込み＋テロップ焼き込み）→ final.mp4
```

---

## 特徴

### clbs-short-research — リサーチ＆冒頭フック解剖
- YouTube Shorts / TikTok / Instagram Reels の **異常値（バズ動画）** を媒体横断で抽出し、**媒体別**にレポート。
- **冒頭1〜2秒のフック**（何が写り・何を喋り・どんなテロップか）を最重視して解剖・モデリング。
- YouTube/TikTok は `yt-dlp`＋`ffmpeg` で自動取得（チャンネルURLだけで異常値ランク・冒頭フレーム・字幕・コメント）。Instagram はログインの壁のため手動スクショ → Claude が vision で解析。

### clbs-short-script — 企画〜台本
- ジャンル不問の汎用台本スキル。順算思考（冒頭2秒フックから作る）／台本4要素／冒頭2秒フック5型／視聴維持率基準／守破離モデリング。
- 台本本体は **ナレーション＋演出タグ**（`[ピクチャーN]`/`[スライドN]`/`[BロールN]`/`[カムリターン]`）＋ `<#0.8#>` 沈黙マーカー。**画面内の見出しテロップは焼かない**（字幕は編集側に委ねる）。

### clbs-short-edit — 縦型編集（9:16）
- 顔出しアバター動画（音声入り）＋台本＋縦型素材から **完成MP4（1080x1920）** を書き出す。
- **ジェットカット** — `ffmpeg silencedetect` で無音を検出して詰める（ほぼ喋りっぱなしに）。
- **テロップ焼き込み** — 顔の下（胸元上）に 大きめ2行・白＋黄キーワード・太い黒フチ。**1句ずつ小分け**表示（長い句のみ自動で2行）。
- **素材差し込み** — スライド(1:1)/ピクチャー(4:3) は画面中央付近に表示しテロップはその下（上端UIと被らない）、Bロール(9:16) は全画面。
- テロップ・素材の表示時刻はジェットカット後の新時刻へ自動リマップ。

## 技術スタック（How it works）

意図的に「軽量で再現性の高い」構成にしています。

| 工程 | 使用ツール | 役割 |
|---|---|---|
| 競合動画の取得 | **yt-dlp** | YouTube/TikTok のメタ・字幕・コメント・サムネを取得（要登録なしのCLI） |
| 冒頭フレーム抽出 | **ffmpeg** | 動画の冒頭0〜2秒を静止画化（テロップ＝視覚フックを vision で読む） |
| 冒頭発話の文字起こし | YouTube自動字幕 / **faster-whisper**(任意) | 「何を喋っているか」を取得 |
| 自己セットアップ | `setup_tools.py` | yt-dlp/ffmpeg が無ければ **単体バイナリを `_bin/` に直接DL**（sudo不要・PATHを汚さない） |
| 沈黙ジェットカット | **ffmpeg `silencedetect`** | 無音区間を検出して詰める。NN非依存で環境差なし |
| テロップ描画 | **Pillow (PIL)** | 2行・大きめ・白/黄・太フチのテロップPNGを生成（日本語の折り返しも制御） |
| 素材合成・連結 | **ffmpeg `filter_complex`** | ジェットカット連結＋スライド/ピクチャー/Broll/テロップを合成 |
| 画像理解 | Claude の vision | スクショ/冒頭フレームを読み、テロップ・被写体・フック型を判定 |

---

## 必要環境とインストール

- **Python 3.10+**
- **ffmpeg**（`ffmpeg` が PATH にあること）
- **yt-dlp**（リサーチの自動取得を使う場合）
- **Pillow**（編集スキルのテロップ描画に必須）
- **日本語の太ゴシックフォント**（macOSは標準のヒラギノ／Linuxは `fonts-noto-cjk` 推奨）
- 任意: **faster-whisper**（字幕の無い動画の冒頭発話起こし）

### 1) Python パッケージ

```bash
pip install -r requirements.txt
# 最小構成（編集だけ試す）なら:  pip install Pillow
```

### 2) ffmpeg / yt-dlp

**macOS（Homebrew）**
```bash
brew install ffmpeg yt-dlp
```
**Ubuntu / Debian**
```bash
sudo apt-get update && sudo apt-get install -y ffmpeg fonts-noto-cjk
pip install yt-dlp
```
**Windows（winget）**
```powershell
winget install Gyan.FFmpeg yt-dlp.yt-dlp
```

> **自動セットアップ**: リサーチスキルは `clbs-short-research/_tools/setup_tools.py` を実行すると、
> yt-dlp / ffmpeg が無い環境でも **公式の単体バイナリを `_bin/` に自動ダウンロード**します（管理者権限不要）。
> 受講生など手動インストールが難しい場合はこれだけでOK。
> ```bash
> python3 clbs-short-research/_tools/setup_tools.py --json   # 確認＆不足を自動導入
> ```

### 3) 日本語フォント（編集スキル）

`clbs-short-edit/_tools/build_short.py` は既定で macOS のヒラギノ角ゴ W7 を使います。
- **macOS**: 追加作業なし。
- **Linux/Windows**: 太ゴシックの `.ttf/.ttc` を用意し、`--font /path/to/font.ttc` で指定（または `build_short.py` の `FONT_SRC` を編集）。Linux は `fonts-noto-cjk`（`NotoSansCJK-Bold`）が手軽。

### 4) スキルの設置

各フォルダを Claude Code のスキルディレクトリに置きます。

```bash
cp -r clbs-short-research clbs-short-script clbs-short-edit ~/.claude/skills/
```

---

## 使い方

Claude Code に話しかけるだけで各スキルが起動します。推奨フローは research → script → edit。

### リサーチ
「ショートリサーチして」「@handle のショートを分析して」など。
- 出力: `research/01_landscape.md` / `02_hook_teardown_<platform>.md` / `03_viral_modeling.md` / `04_short_research_summary.yaml`
- YouTube自動取得（手動コマンド例）:
  ```bash
  python3 clbs-short-research/_tools/setup_tools.py
  python3 clbs-short-research/_tools/hook_ingest.py --out research/clips \
    --channel "https://www.youtube.com/@handle" --top 6 --rank-scan 40
  ```

### 台本
「ショート台本作って」など。`04_short_research_summary.yaml` を渡すと精度が上がります。
- 出力: 企画5案 → 構成骨子 → `script.txt`（演出タグ付き）＋縦型素材プラン。

### 編集
プロジェクトフォルダを用意します。

```
プロジェクト/
├── avatar.mp4          # 縦型9:16・音声入りの顔出しアバター動画（台本を喋っている）
├── segments.json       # 文ごとのタイムライン＋テロップ本文
├── plan.json           # 素材の差し込み区間
└── assets/
    ├── slide1.png      # 1:1
    ├── picture1.png    # 4:3
    └── broll1.mp4     # 9:16
```

```bash
python3 clbs-short-edit/_tools/build_short.py --dir /path/to/プロジェクト
# → /path/to/プロジェクト/final.mp4（1080x1920）
```

`segments.json` / `plan.json` の形式:
```json
// segments.json
[{"i":1,"start":0.0,"end":4.04,"text":"あなたが見るまで、現実は決まってない"}]
// plan.json
[{"type":"broll","file":"assets/broll1.mp4","start":0.0,"end":4.0,"fit":"cover"},
 {"type":"image","file":"assets/picture1.png","start":12.5,"end":19.7,"fit":"contain"}]
```

## 主なオプション（clbs-short-edit / build_short.py）

| 引数・定数 | 既定 | 説明 |
|---|---|---|
| `--no-jetcut` | （無効） | ジェットカットを行わず素材＋テロップ合成だけ |
| `--font PATH` | macOSヒラギノ | テロップのフォント（Linux/Windowsで指定） |
| `SIL_MIN` | 0.20 | これ以上の無音を詰める（小さいほどアグレッシブ） |
| `KEEP_PAD` | 0.05 | 各発話の前後に残す余白（プツ切れ防止） |
| `TELOP_CENTER_Y` | 1230 | テロップの縦位置（顔の下・胸元上） |
| `IMG_CENTER_Y` | 640 | スライド/ピクチャーの縦中心（上端UI回避） |

## 主なオプション（clbs-short-research / hook_ingest.py）

| 引数 | 既定 | 説明 |
|---|---|---|
| `--channel URL` | — | YouTubeチャンネルから異常値を自動ランクして取得 |
| `--videos URL...` | — | 動画を明示指定（YouTube/TikTok混在可） |
| `--top` | 8 | ランク上位の取得本数 |
| `--rank-scan` | 40 | 再生数を補完してランクする走査本数 |
| `--always-whisper` / `--no-whisper` | — | 文字起こしの方法 |

---

## 方法論の出典
`references/` 配下の方法論は、株式会社コンサルタントラボラトリーのショート動画講義資料・常識破壊パターン集を体系化したものです。

## 関連
- 素材生成: [Higgsfield](https://higgsfield.ai)（スライド/ピクチャー=Nano Banana、Bロール=Veo3）
- ナレーション音声: Fish Audio 等のクローン音声
- 16:9のYouTube本編編集: [clbs-youtube-edit](https://github.com/conlab-clbs/clbs-youtube-edit)
