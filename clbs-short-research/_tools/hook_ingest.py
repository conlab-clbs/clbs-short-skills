#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hook_ingest.py — clbs-short-research 自動取得（YouTube Shorts / TikTok）

ショート動画の「異常値」と「冒頭1〜2秒フック」を自動で集める。Instagram は対象外（ログインの壁
→ 手動スクショモード）。本ツールは setup_tools.py が用意した _bin/ の yt-dlp/ffmpeg を自動で使う。

できること:
  - チャンネルから Shorts を列挙し、再生数で異常値を自動ランク（--channel）
  - 各動画につき:
      * メタ（タイトル / 再生数 / いいね / 投稿日 / 尺 / 投稿者 / フォロワー）
      * 冒頭セリフ（YouTube字幕優先・無ければWhisper任意）  ＝「何を喋るか」
      * 冒頭フレーム 0.0〜2.0秒（ffmpegがあれば）           ＝「何が写る＋テロップ」
      * カバー（サムネ）画像                                ＝ffmpeg無しでも視覚フックの代替になる
      * 上位コメント
  - 出力は媒体別 out/<platform>/<id>/、index.json に異常値ランクつき

ffmpeg が無くても成立する: YouTubeは「字幕＝冒頭セリフ」＋「サムネ＝カバー」で冒頭フックを読める。
ffmpeg があれば、本当の冒頭0〜2秒フレームまで抜ける（精度↑）。

使い方:
  # まず自己セットアップ（無ければyt-dlp/ffmpegを_bin/に用意）
  python3 setup_tools.py

  # YouTubeチャンネルから上位（異常値）を自動取得
  python3 hook_ingest.py --out research/clips --channel "https://www.youtube.com/@handle" --top 8

  # 動画を明示（YouTube/TikTok混在可）
  python3 hook_ingest.py --out research/clips \
    --videos "https://www.youtube.com/shorts/XXXX" "https://www.tiktok.com/@u/video/YYYY"

オプション:
  --top 8                 --channel時の上位本数
  --hook-secs 2.0         冒頭フレーム抽出秒数
  --frame-step 0.5        フレーム間隔
  --sub-langs ja,en       字幕優先言語
  --max-comments 60       コメント上限
  --always-whisper        字幕があってもWhisper（要 faster_whisper）
  --no-whisper            Whisperを使わない
  --keep-video            DLした動画実体を残す
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# _bin/ を PATH 先頭に追加（setup_tools.py が置いた yt-dlp/ffmpeg を拾う）
BIN = (Path(__file__).resolve().parent.parent / "_bin")
os.environ["PATH"] = str(BIN) + os.pathsep + os.environ.get("PATH", "")

import shutil  # noqa: E402  (PATH設定後に解決させる)


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return p.returncode, p.stdout, p.stderr


def have(name):
    return shutil.which(name) is not None


def detect_platform(url):
    u = url.lower()
    if "tiktok.com" in u:
        return "tiktok"
    if "instagram.com" in u:
        return "instagram"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return "other"


def ytdlp_json(url, extra=None):
    cmd = ["yt-dlp", "-J", "--skip-download", "--no-warnings"]
    if extra:
        cmd += extra
    cmd += [url]
    rc, out, err = run(cmd)
    if rc != 0 or not out.strip():
        return None, err
    try:
        return json.loads(out), None
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"


# ---------------------------------------------------------------------------
# 字幕（YouTube json3）
# ---------------------------------------------------------------------------
def parse_json3(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []
    lines = []
    for ev in data.get("events", []):
        text = "".join(s.get("utf8", "") for s in (ev.get("segs") or [])).strip()
        if text and text != "\n":
            lines.append((ev.get("tStartMs", 0) / 1000.0, text))
    return lines


def dedup_lines(lines):
    out, prev = [], None
    for t, txt in lines:
        norm = re.sub(r"\s+", " ", txt).strip()
        if norm and norm != prev:
            out.append((t, norm))
            prev = norm
    return out


def fmt_ts(sec):
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def write_transcript(lines, vid_dir, source):
    lines = dedup_lines(lines)
    (vid_dir / "transcript.txt").write_text(
        f"# source: {source}\n\n" + "\n".join(f"[{fmt_ts(t)}] {x}" for t, x in lines) + "\n",
        encoding="utf-8")
    (vid_dir / "transcript.plain.txt").write_text(
        " ".join(x for _, x in lines) + "\n", encoding="utf-8")
    opening = [f"[{fmt_ts(t)}] {x}" for t, x in lines if t <= 3.0]
    (vid_dir / "hook_opening_speech.txt").write_text(
        ("\n".join(opening) if opening else "(冒頭3秒の発話が取得できませんでした)") + "\n",
        encoding="utf-8")
    return len(lines), (opening[0] if opening else None)


def fetch_subs(url, vid_dir, sub_langs):
    tmpl = str(vid_dir / "sub.%(ext)s")
    langs = ",".join(sub_langs)
    for flag in (["--write-subs"], ["--write-auto-subs"]):
        run(["yt-dlp", "--skip-download", "--no-warnings", *flag,
             "--sub-langs", langs, "--sub-format", "json3", "-o", tmpl, url])
        for lang in sub_langs:
            for cand in vid_dir.glob(f"sub.{lang}*.json3"):
                if parse_json3(cand):
                    return cand, ("manual" if "--write-subs" in flag else "auto")
        anyj = sorted(vid_dir.glob("sub.*.json3"))
        if anyj and parse_json3(anyj[0]):
            return anyj[0], ("manual" if "--write-subs" in flag else "auto")
    return None, None


# ---------------------------------------------------------------------------
# 動画DL & 冒頭フレーム
# ---------------------------------------------------------------------------
def download_video(url, vid_dir):
    tmpl = str(vid_dir / "video.%(ext)s")
    run(["yt-dlp", "--no-warnings", "-f", "mp4/best[height<=720]/best",
         "--merge-output-format", "mp4", "-o", tmpl, url])
    for ext in ("mp4", "mkv", "webm", "mov"):
        v = next(iter(vid_dir.glob(f"video.{ext}")), None)
        if v:
            return v
    return None


def extract_hook_frames(video_path, vid_dir, hook_secs, step):
    if not have("ffmpeg"):
        return []
    fdir = vid_dir / "hook_frames"
    fdir.mkdir(exist_ok=True)
    frames, t = [], 0.0
    while t <= hook_secs + 1e-6:
        out = fdir / f"f_{int(round(t*1000)):04d}ms.jpg"
        rc, _, _ = run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video_path),
                        "-frames:v", "1", "-q:v", "3", str(out)])
        if rc == 0 and out.exists():
            frames.append(out.name)
        t += step
    return frames


def whisper_transcribe(video_path, model_name):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    if not video_path:
        return None
    print(f"    Whisper ({model_name}) 文字起こし中...", file=sys.stderr)
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segs, _ = model.transcribe(str(video_path), vad_filter=True)
    return [(s.start, s.text.strip()) for s in segs if s.text.strip()]


def fetch_comments(url, vid_dir, max_comments):
    tmpl = str(vid_dir / "c.%(ext)s")
    run(["yt-dlp", "--skip-download", "--no-warnings", "--write-comments",
         "--extractor-args", f"youtube:max_comments={max_comments},all,100;comment_sort=top",
         "--write-info-json", "-o", tmpl, url])
    info = next(iter(vid_dir.glob("c*.info.json")), None)
    if not info:
        return []
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
    except Exception:
        return []
    cleaned = [{"text": c.get("text", "").strip(), "like_count": c.get("like_count") or 0,
                "is_reply": bool(c.get("parent") and c.get("parent") != "root")}
               for c in (data.get("comments") or []) if c.get("text")]
    cleaned.sort(key=lambda x: x["like_count"], reverse=True)
    try:
        info.unlink()
    except OSError:
        pass
    return cleaned[:max_comments]


def fetch_cover(url, vid_dir):
    tmpl = str(vid_dir / "cover.%(ext)s")
    run(["yt-dlp", "--skip-download", "--no-warnings", "--write-thumbnail",
         "--convert-thumbnails", "jpg", "-o", tmpl, url])
    cover = next(iter(vid_dir.glob("cover.jpg")), None)
    return cover.name if cover else None


def write_hook_card(record, vid_dir):
    frames = record.get("hook_frames") or []
    cover = record.get("cover")
    visual_src = (f"hook_frames/（{len(frames)}枚）" if frames
                  else (f"cover.jpg（フレーム無し→カバーで代替）" if cover else "（画像なし）"))
    md = f"""# フックカード — {record.get('platform')} / {record.get('id')}

- タイトル: {record.get('title') or '[要確認]'}
- 投稿者: {record.get('uploader') or '[要確認]'}／フォロワー: {record.get('uploader_followers') if record.get('uploader_followers') is not None else '[要確認]'}
- 再生数: {record.get('view_count') if record.get('view_count') is not None else '[要確認]'}　いいね: {record.get('like_count') if record.get('like_count') is not None else '[要確認]'}
- 尺: {record.get('duration_sec') or '[要確認]'}秒　投稿日: {record.get('upload_date') or '[要確認]'}
- 異常値判定: {record.get('anomaly_label') or '[STEP2で記入]'}
- URL: {record.get('url')}

## 冒頭1〜2秒フック（最重要）
- 視覚ソース: {visual_src}

### 何が写っているか（{('hook_frames/' if frames else 'cover.jpg')} を vision で確認して記述）
- [要記述]

### 焼き込みテロップ（画像上の文字をそのまま）
- [要記述]

### 何を喋っているか（hook_opening_speech.txt）
- {record.get('opening_speech_first') or '[要確認：字幕/音声なし→ユーザーに冒頭セリフを依頼]'}

### フック型の判定（5型＋常識破壊フレーム）
- フック型: [① 常識破壊 / ② ネガティブ / ③ 短期快楽 / ④ 質問 / ⑤ コレをやると◯◯]
- 常識破壊フレーム: [1〜8 / 「不」起点]
- 視覚×言語の分担: [テロップで何を見せ、音声で何を言うか]
"""
    (vid_dir / "hook_card.md").write_text(md, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1動画処理
# ---------------------------------------------------------------------------
def process_video(url, out_root, args, prefetched_meta=None):
    platform = detect_platform(url)

    if platform == "instagram":
        # Instagram は自動対象外 → 手動枠を作って返す
        vid = re.sub(r"\W+", "_", url)[-24:]
        vid_dir = out_root / platform / vid
        vid_dir.mkdir(parents=True, exist_ok=True)
        rec = {"id": vid, "platform": platform, "url": url, "manual_needed": True,
               "manual_reason": "Instagramは自動取得対象外（ログインの壁）→手動スクショモードで"}
        write_hook_card(rec, vid_dir)
        (vid_dir / "meta.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[manual] instagram は手動: {url}", file=sys.stderr)
        return rec

    meta = prefetched_meta
    if meta is None:
        meta, err = ytdlp_json(url)
        if not meta:
            print(f"[skip] メタ取得失敗 ({platform}): {url}\n    {err[:160]}", file=sys.stderr)
            return None
    while meta.get("entries"):
        ents = [e for e in meta["entries"] if e]
        if not ents:
            return None
        meta = ents[0]
    url = meta.get("webpage_url") or meta.get("url") or url
    vid = meta.get("id", re.sub(r"\W+", "_", url)[-24:])
    vid_dir = out_root / platform / vid
    vid_dir.mkdir(parents=True, exist_ok=True)
    print(f"[ingest] {platform}/{vid}  {meta.get('title','')[:48]}", file=sys.stderr)

    rec = {
        "id": vid, "platform": platform, "url": url, "title": meta.get("title", ""),
        "uploader": meta.get("uploader") or meta.get("channel"),
        "uploader_followers": meta.get("channel_follower_count"),
        "view_count": meta.get("view_count"), "like_count": meta.get("like_count"),
        "comment_count": meta.get("comment_count"), "upload_date": meta.get("upload_date"),
        "duration_sec": meta.get("duration"),
        "transcript_source": None, "transcript_lines": 0, "opening_speech_first": None,
        "hook_frames": [], "cover": None, "comments_fetched": 0,
        "anomaly_label": None, "manual_needed": False,
    }

    # 冒頭セリフ（YouTube字幕優先）
    lines, source = None, None
    if not args.always_whisper and platform == "youtube":
        sub, kind = fetch_subs(url, vid_dir, args.sub_langs)
        if sub:
            lines, source = parse_json3(sub), f"subtitle:{kind}"
            try:
                sub.unlink()
            except OSError:
                pass

    # フレーム抽出のための動画DL（ffmpegがある時だけ意味がある）
    video_path = None
    if have("ffmpeg") or (lines is None and not args.no_whisper):
        video_path = download_video(url, vid_dir)

    if lines is None and not args.no_whisper:
        wl = whisper_transcribe(video_path, args.whisper_model)
        if wl:
            lines, source = wl, f"whisper:{args.whisper_model}"

    if lines:
        n, first = write_transcript(lines, vid_dir, source)
        rec.update(transcript_source=source, transcript_lines=n, opening_speech_first=first)

    # 冒頭フレーム（ffmpegがあれば）
    if video_path and have("ffmpeg"):
        rec["hook_frames"] = extract_hook_frames(video_path, vid_dir, args.hook_secs, args.frame_step)

    # カバー（ffmpeg無しでも視覚フックの代替）
    rec["cover"] = fetch_cover(url, vid_dir)

    # コメント
    comments = fetch_comments(url, vid_dir, args.max_comments)
    if comments:
        (vid_dir / "comments.md").write_text(
            "\n".join(f"- ❤{c['like_count']} {'↳' if c['is_reply'] else ''} {c['text']}"
                      for c in comments) + "\n", encoding="utf-8")
        rec["comments_fetched"] = len(comments)

    if video_path and not args.keep_video:
        try:
            video_path.unlink()
        except OSError:
            pass

    if not rec["hook_frames"] and not rec["cover"] and not rec["opening_speech_first"]:
        rec["manual_needed"] = True

    write_hook_card(rec, vid_dir)
    (vid_dir / "meta.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec


# ---------------------------------------------------------------------------
# チャンネル → Shorts 異常値ランク（YouTube）
# ---------------------------------------------------------------------------
def _flat_entries(url):
    meta, _err = ytdlp_json(url, extra=["--flat-playlist"])
    if not meta:
        return None
    return [e for e in (meta.get("entries") or []) if e and e.get("id")]


def video_views(vid_id):
    """再生数だけを軽量取得（DL無し）。flat列挙は再生数を返さないため補完に使う。"""
    rc, out, _err = run(["yt-dlp", "--no-warnings", "--skip-download",
                         "--print", "%(view_count)s",
                         f"https://www.youtube.com/watch?v={vid_id}"])
    try:
        return int((out or "").strip())
    except (ValueError, AttributeError):
        return 0


def channel_shorts(channel_url, scan_limit):
    """Shorts(無ければVideos)を列挙 → 各再生数を補完 → 再生数降順で返す。
    戻り値: ranked(list of {id,title,_vc}), avg_views"""
    root = channel_url.rstrip("/")
    ents = None
    for suffix in ("/shorts", "/videos"):
        if root.endswith(("/shorts", "/videos")):
            ents = _flat_entries(root)
        else:
            ents = _flat_entries(root + suffix)
        if ents:
            break
    if not ents:
        print("[error] チャンネルのShorts/Videos列挙に失敗", file=sys.stderr)
        return [], 0
    if scan_limit and scan_limit > 0:
        ents = ents[:scan_limit]
    # flat に再生数が無ければ補完（DL無し・1本ずつ）
    print(f"[channel] {len(ents)}本の再生数を補完中（DL無し）...", file=sys.stderr)
    for e in ents:
        vc = e.get("view_count")
        e["_vc"] = vc if isinstance(vc, int) and vc > 0 else video_views(e["id"])
    vcs = [e["_vc"] for e in ents if e["_vc"]]
    avg = (sum(vcs) / len(vcs)) if vcs else 0
    ents.sort(key=lambda e: e["_vc"], reverse=True)
    return ents, avg


def main():
    ap = argparse.ArgumentParser(description="clbs-short-research 自動取得（YouTube/TikTok）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--videos", nargs="*", default=[])
    ap.add_argument("--channel", help="YouTubeチャンネルURL（Shorts異常値を自動ランク）")
    ap.add_argument("--top", type=int, default=8, help="ランク上位の取得本数")
    ap.add_argument("--rank-scan", type=int, default=40,
                    help="再生数を補完してランクする走査本数（多いほど正確・遅い）")
    ap.add_argument("--hook-secs", type=float, default=2.0)
    ap.add_argument("--frame-step", type=float, default=0.5)
    ap.add_argument("--sub-langs", default="ja,en")
    ap.add_argument("--max-comments", type=int, default=60)
    ap.add_argument("--always-whisper", action="store_true")
    ap.add_argument("--no-whisper", action="store_true")
    ap.add_argument("--keep-video", action="store_true")
    args = ap.parse_args()
    args.sub_langs = [s.strip() for s in args.sub_langs.split(",") if s.strip()]

    if not have("yt-dlp"):
        print("[error] yt-dlp が見つかりません。先に `python3 setup_tools.py` を実行してください。", file=sys.stderr)
        sys.exit(1)
    if not have("ffmpeg"):
        print("[note] ffmpeg 無し → 冒頭フレーム抽出は省略し、字幕＝冒頭セリフ＋サムネ＝カバーで代替します。", file=sys.stderr)

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    records, channel_stats = [], None
    if args.channel:
        ranked, avg = channel_shorts(args.channel, args.rank_scan)
        channel_stats = {"scanned": len(ranked), "avg_views": round(avg, 1),
                         "ranking": [{"rank": i + 1, "id": e["id"], "views": e["_vc"],
                                      "title": e.get("title", ""),
                                      "ratio": round((e["_vc"] / avg), 2) if avg else None}
                                     for i, e in enumerate(ranked)]}
        print(f"[channel] 走査{len(ranked)}本 / 平均{round(avg,1)}再生 → 上位{args.top}本を取得", file=sys.stderr)
        for e in ranked[:args.top]:
            url = f"https://www.youtube.com/watch?v={e['id']}"
            r = process_video(url, out_root, args, prefetched_meta=None)
            if r:
                vc = r.get("view_count") or e["_vc"] or 0
                ratio = (vc / avg) if avg else 0
                r["anomaly_label"] = (f"{ratio:.1f}x平均" + ("（★異常値）" if ratio >= 1.5 else ""))
                write_hook_card(r, out_root / r["platform"] / r["id"])
                (out_root / r["platform"] / r["id"] / "meta.json").write_text(
                    json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
                records.append(r)

    for u in args.videos:
        r = process_video(u, out_root, args)
        if r:
            records.append(r)

    index = {"channel_stats": channel_stats, "videos": records}
    (out_root / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 取得サマリ（媒体別） ===", file=sys.stderr)
    if channel_stats:
        print(f"  [channel] 走査{channel_stats['scanned']}本 / 平均{channel_stats['avg_views']}再生", file=sys.stderr)
    for plat in ("youtube", "tiktok", "instagram", "other"):
        sub = [r for r in records if r.get("platform") == plat]
        if not sub:
            continue
        print(f"\n[{plat}]", file=sys.stderr)
        for r in sorted(sub, key=lambda x: (x.get("view_count") or 0), reverse=True):
            flag = " ⚠手動" if r.get("manual_needed") else ""
            print(f"  再生{r.get('view_count')} | {r.get('anomaly_label') or ''} | "
                  f"フレーム{len(r.get('hook_frames') or [])} cover{'有' if r.get('cover') else '無'} "
                  f"字幕{r.get('transcript_lines',0)}行 | {(r.get('title') or '')[:34]}{flag}", file=sys.stderr)
    print(f"\n[done] → {out_root/'index.json'}", file=sys.stderr)
    print("次: 各 <platform>/<id>/hook_card.md を、hook_frames/ か cover.jpg を vision で見て埋める。", file=sys.stderr)


if __name__ == "__main__":
    main()
