# clbs-short-skills

ショート動画（YouTube Shorts / TikTok / Instagram Reels）の **リサーチ** と **台本作成** を行う Claude Code 用スキル集です。

## 収録スキル

### 1. `clbs-short-research` — 媒体横断リサーチ＆冒頭フック解剖
- YouTube Shorts / TikTok / Instagram Reels の異常値（バズ動画）を媒体横断でリサーチ。
- **冒頭1〜2秒のフック**（何が写り・何を喋り・どんなテロップか）を最重視して解剖・モデリング。
- 報告書は媒体別に分けて出力。
- YouTube/TikTok は `_tools/`（yt-dlp / ffmpeg を自己セットアップ）で自動取得、Instagram は手動スクショ。
- 出力 `04_short_research_summary.yaml` を `clbs-short-script` に渡して企画・台本の精度を上げる。

### 2. `clbs-short-script` — 企画立案〜台本執筆
- ジャンルを問わない汎用ショート台本スキル。
- 順算思考（冒頭2秒フックから作る）／台本4要素（タイトル・興味づけ・本題・CTA）／冒頭2秒フック5型／視聴維持率基準／守破離モデリング。
- 台本本体はナレーション＋演出タグ（`[ピクチャーN]` / `[スライドN]` / `[BロールN]` / `[カムリターン]`）＋ `<#0.8#>` 沈黙マーカー＋縦型素材注記（1:1 / 4:3 / 9:16）。
- ショートは画面内の見出しテロップを焼かない方針（字幕は編集側に委ねる）。

## 使い方

各スキルフォルダを Claude Code のスキルディレクトリに置きます。

```bash
cp -r clbs-short-research clbs-short-script ~/.claude/skills/
```

- リサーチ: 「ショートリサーチ」「TikTokリサーチ」などで起動。
- 台本: 「ショート台本」「ショート企画」などで起動。
- 推奨フロー: `clbs-short-research`（リサーチ）→ `clbs-short-script`（台本）。

## 依存（リサーチの自動取得を使う場合のみ）
- `yt-dlp`, `ffmpeg`（`clbs-short-research/_tools/setup_tools.py` が未導入なら単体バイナリを自動取得）
- 任意: `faster_whisper`（字幕の無い動画の冒頭発話起こし）
- ※ 手動スクショ方式だけならインストール不要。

## 方法論の出典
`references/` 配下の方法論は、株式会社コンサルタントラボラトリーのショート動画講義資料・常識破壊パターン集を体系化したものです。

## 備考
縦型編集（テロップ焼き込み・素材差し込み）は別スキル（`clbs-short-edit`）が担当します（本リポジトリには含みません）。
