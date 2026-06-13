#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup_tools.py — clbs-short-research 自己セットアップ

YouTube/TikTok 自動取得に必要な yt-dlp（必須）と ffmpeg（任意・冒頭フレーム抽出用）を用意する。
受講生のPCでも、エージェント（Claude）がこれを1回実行すれば、手動インストール無しで自動取得に入れる。

優先順位（sudo不要・PATHを汚さない）:
  1) 既に PATH か _bin/ にある        → そのまま使う
  2) yt-dlp: 公式の単体バイナリを _bin/ に直接DL（最も確実）
  3) ffmpeg: brew があれば brew、無ければ静的ビルドを _bin/ にDL。だめなら「無し」で続行
     （ffmpeg が無くても、YouTubeは字幕＝冒頭セリフ＋サムネ＝カバーまで取れるので自動取得は成立する）

終了コード:
  0 = yt-dlp が使える（ffmpeg は has_ffmpeg で報告）
  1 = yt-dlp すら用意できない → 呼び出し側は手動スクショモードへ降格

使い方:
  python3 setup_tools.py            # 確認→不足を導入
  python3 setup_tools.py --check    # 確認だけ（導入しない）
  python3 setup_tools.py --json     # 結果をJSONで出力（エージェントが読む）
"""

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BIN = (Path(__file__).resolve().parent.parent / "_bin")
BIN.mkdir(parents=True, exist_ok=True)

OS = platform.system().lower()          # darwin / linux / windows
EXE = ".exe" if OS == "windows" else ""


def log(msg):
    print(msg, file=sys.stderr)


def which(name):
    """PATH と _bin/ の両方を探す。"""
    p = shutil.which(name)
    if p:
        return p
    cand = BIN / (name + EXE)
    if cand.exists():
        return str(cand)
    return None


def _download(url, dest):
    log(f"    DL: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def _chmod_x(path):
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# ---------------------------------------------------------------------------
# yt-dlp
# ---------------------------------------------------------------------------
def ensure_ytdlp(do_install):
    found = which("yt-dlp")
    if found:
        return found
    if not do_install:
        return None
    # 公式リリースの単体バイナリ（アカウント不要・Python不要）
    asset = {
        "darwin": "yt-dlp_macos",
        "linux": "yt-dlp_linux",
        "windows": "yt-dlp.exe",
    }.get(OS, "yt-dlp")
    url = f"https://github.com/yt-dlp/yt-dlp/releases/latest/download/{asset}"
    dest = BIN / ("yt-dlp" + EXE)
    try:
        _download(url, dest)
        if OS != "windows":
            _chmod_x(dest)
        log(f"    yt-dlp を {dest} に配置")
        return str(dest)
    except Exception as e:
        log(f"    [warn] yt-dlp バイナリDL失敗: {e}")
        # 最後の手段: pip
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"], check=True)
            return which("yt-dlp")
        except Exception as e2:
            log(f"    [warn] pip 経由も失敗: {e2}")
            return None


# ---------------------------------------------------------------------------
# ffmpeg（任意）
# ---------------------------------------------------------------------------
def _extract_ffmpeg_from(archive, workdir):
    """アーカイブから ffmpeg 実行ファイルを探して _bin/ へ。"""
    target_names = {"ffmpeg", "ffmpeg.exe"}
    if str(archive).endswith((".zip",)):
        with zipfile.ZipFile(archive) as z:
            z.extractall(workdir)
    elif str(archive).endswith((".tar.xz", ".txz", ".tar.gz", ".tgz", ".tar")):
        with tarfile.open(archive) as t:
            t.extractall(workdir)
    else:
        return None
    for root, _dirs, files in os.walk(workdir):
        for fn in files:
            if fn in target_names:
                src = Path(root) / fn
                dest = BIN / ("ffmpeg" + EXE)
                shutil.copy2(src, dest)
                if OS != "windows":
                    _chmod_x(dest)
                return str(dest)
    return None


def ensure_ffmpeg(do_install):
    found = which("ffmpeg")
    if found:
        return found
    if not do_install:
        return None
    # brew があれば最優先（mac）
    if OS == "darwin" and shutil.which("brew"):
        try:
            subprocess.run(["brew", "install", "ffmpeg"], check=True)
            if which("ffmpeg"):
                return which("ffmpeg")
        except Exception as e:
            log(f"    [warn] brew install ffmpeg 失敗: {e}")
    # 静的ビルドを直接DL（ホスト都合で失敗することがある→失敗してもOK）
    urls = {
        "darwin": "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip",
        "linux": "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz",
        "windows": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    }
    url = urls.get(OS)
    if not url:
        return None
    try:
        with tempfile.TemporaryDirectory() as td:
            suffix = ".tar.xz" if url.endswith(".xz") else ".zip"
            arc = Path(td) / ("ffmpeg" + suffix)
            _download(url, arc)
            got = _extract_ffmpeg_from(arc, td)
            if got:
                log(f"    ffmpeg を {got} に配置")
                return got
    except Exception as e:
        log(f"    [warn] ffmpeg 静的ビルドDL失敗（手動導入推奨 or フレーム抽出なしで続行）: {e}")
    return None


def main():
    ap = argparse.ArgumentParser(description="clbs-short-research 自己セットアップ")
    ap.add_argument("--check", action="store_true", help="導入せず確認だけ")
    ap.add_argument("--json", action="store_true", help="結果をJSONで出力")
    args = ap.parse_args()
    do_install = not args.check

    log(f"[setup] OS={OS} _bin={BIN}")
    ytdlp = ensure_ytdlp(do_install)
    ffmpeg = ensure_ffmpeg(do_install)

    status = {
        "os": OS,
        "bin_dir": str(BIN),
        "ytdlp": ytdlp,
        "ffmpeg": ffmpeg,
        "has_ytdlp": bool(ytdlp),
        "has_ffmpeg": bool(ffmpeg),
        # ffmpeg が無くても YouTube は字幕＋サムネで自動取得が成立する
        "auto_ready": bool(ytdlp),
        "mode": ("auto" if ytdlp else "manual"),
    }
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    log("\n=== セットアップ結果 ===")
    log(f"  yt-dlp : {'OK ' + ytdlp if ytdlp else '無し（→手動スクショモードへ）'}")
    log(f"  ffmpeg : {'OK ' + ffmpeg if ffmpeg else '無し（冒頭フレーム抽出なし／字幕＋サムネで代替）'}")
    log(f"  判定   : {status['mode']} モードで実行可能"
        + ("" if ffmpeg else "（フレーム抽出は省略）"))
    sys.exit(0 if ytdlp else 1)


if __name__ == "__main__":
    main()
