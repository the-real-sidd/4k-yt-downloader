#!/usr/bin/env python3
"""
ytdl: download a YouTube video in the best quality (up to 4K) with sound,
into the folder you run it from, or into a folder you name.

  ytdl                          asks you to paste a link
  ytdl <folder>                 asks for a link and saves into that folder (created if missing)
  ytdl <link> [<link> ...]      downloads those links
  ytdl <folder> <link>          downloads that link into that folder
  ytdl --max 1080 <link>        caps the resolution (default 2160 = 4K)
  ytdl --audio <link>           audio only (.m4a)
  ytdl --compat <link>          converts to H.264 so QuickTime / Final Cut / Premiere open it
  ytdl --from 1:30 --to 2:45 <link>   downloads only that part (either one can be left out)
  ytdl --cookies chrome <link>  uses your browser sign-in (age-restricted or "confirm you're not a bot")
  ytdl --dry-run <link>         shows the quality it would pick and downloads nothing
  ytdl --web                    opens a simple page in Chrome to paste links into
  ytdl --web <folder>           same, saving into that folder
  ytdl --version                shows the version

Needs yt-dlp, ffmpeg and deno (brew install yt-dlp ffmpeg deno). The same code runs inside
the Mac app and the Windows app (winapp/), which bring their own copies of these tools.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

OUTPUT_TEMPLATE = "%(title).150B.%(ext)s"
IS_WINDOWS = os.name == "nt"

# H.264 bitrates for --compat, by output height.
COMPAT_BITRATES = [(2160, "45M"), (1440, "24M"), (1080, "12M"), (720, "7M"), (0, "4M")]

# The Windows app has no console window. Without this, every ffmpeg/ffprobe it runs would
# flash a black window, so the app sets it to True.
hide_windows = False


def _version() -> str:
    """The app version, from the VERSION file shipped next to this file (repo, Mac app, Windows app)."""
    try:
        return (Path(__file__).resolve().parent / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"


VERSION = _version()


def no_window() -> dict:
    """subprocess options that keep a console window from appearing (Windows app only)."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS and hide_windows else {}


def replace_file(source: Path, target: Path) -> None:
    """os.replace, retried for a few seconds: on Windows a virus scanner or Explorer's preview
    can hold a file that was just written."""
    for attempt in range(20):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.5)


def fail(message: str) -> None:
    print(f"\n✖ {message}", file=sys.stderr)
    sys.exit(1)


def check_tools() -> None:
    install = "winget install yt-dlp.yt-dlp Gyan.FFmpeg DenoLand.Deno" if IS_WINDOWS else "brew install yt-dlp ffmpeg deno"
    missing = [tool for tool in ("yt-dlp", "ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        fail(f"Missing {', '.join(missing)}. Install with:  {install}")
    if not shutil.which("deno"):
        print(f"⚠ deno not found. YouTube may only offer low-quality formats. Fix:  {install}")


def looks_like_link(text: str) -> bool:
    if urlparse(text).scheme in ("http", "https"):
        return True
    return text.lower().startswith(("youtube.com/", "www.youtube.com/", "m.youtube.com/", "youtu.be/", "music.youtube.com/"))


def split_targets(items: list[str]) -> tuple[list[str], Path]:
    """Separate links from the folder. Non-link words are joined, so  ytdl Elevator Goods  works without quotes."""
    links = [item if urlparse(item).scheme else "https://" + item for item in items if looks_like_link(item)]
    words = [item for item in items if not looks_like_link(item)]
    folder = Path(" ".join(words)).expanduser().resolve() if words else Path.cwd()
    return links, folder


def prepare_folder(folder: Path) -> None:
    if folder.is_dir():
        return
    if folder.exists():
        fail(f"{folder} is a file, not a folder.")
    try:
        answer = input(f"Folder doesn't exist: {folder}\nCreate it? [Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    if answer not in ("", "y", "yes"):
        sys.exit(0)
    folder.mkdir(parents=True)


def parse_time(text) -> float | None:
    """'1:30', '1:02:03', '90', '1m30s', '1h2m' → seconds. Empty → None. Raises ValueError."""
    text = str(text or "").strip().lower().replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    match = re.fullmatch(r"(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        hours, minutes, seconds = int(match[1] or 0), int(match[2]), float(match[3])
        if (match[1] and minutes >= 60) or seconds >= 60:
            raise ValueError(f"“{text}” isn't a valid time.")
        return hours * 3600 + minutes * 60 + seconds
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?", text)
    if match and any(match.groups()):
        return int(match[1] or 0) * 3600 + int(match[2] or 0) * 60 + float(match[3] or 0)
    raise ValueError(f"“{text}” isn't a time. Use minutes:seconds, like 1:30 or 1:02:45.")


def _split_time(seconds: float) -> tuple[int, int, int, str]:
    seconds = round(seconds, 1)
    whole = int(seconds)
    tenths = round((seconds - whole) * 10)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return hours, minutes, secs, f".{tenths}" if tenths else ""


def format_time(seconds: float) -> str:
    """90 → '1:30', 3723 → '1:02:03'."""
    hours, minutes, secs, fraction = _split_time(seconds)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}{fraction}"
    return f"{minutes}:{secs:02d}{fraction}"


def check_clip(start: float | None, end: float | None) -> None:
    if start is not None and end is not None and end <= start:
        raise ValueError("The end time must be after the start time.")


def clip_name(start: float | None, end: float | None) -> str:
    """Filename tag for a clip, e.g. '[1m30s-2m45s]' (Mac and Windows file names can't hold ':')."""
    def tag(seconds: float) -> str:
        hours, minutes, secs, fraction = _split_time(seconds)
        return (f"{hours}h{minutes:02d}m" if hours else f"{minutes}m") + f"{secs:02d}{fraction}s"
    return f"[{tag(start or 0)}-{tag(end) if end is not None else 'end'}]"


def ask_for_links() -> list[str]:
    try:
        text = input("Paste YouTube link(s): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return text.split()


def is_clip(args) -> bool:
    return getattr(args, "start", None) is not None or getattr(args, "end", None) is not None


def yt_dlp_command(args, links: list[str], filepath_log: str, extra: list[str] = (), folder: Path | None = None) -> list[str]:
    clip = is_clip(args)
    start, end = getattr(args, "start", None), getattr(args, "end", None)
    cmd = [
        "yt-dlp",
        "--no-playlist",
        *(["-P", str(folder)] if folder else []),
        "--concurrent-fragments", "4",
        "--embed-metadata",
        "-o", f"%(title).150B {clip_name(start, end)}.%(ext)s" if clip else OUTPUT_TEMPLATE,
    ]

    if args.audio:
        # /b: some sites only offer one file with sound and picture; the sound is taken from it.
        cmd += ["-f", "ba[ext=m4a]/ba/b", "-x", "--audio-format", "m4a", "--audio-quality", "0"]
    else:
        cap = args.max
        # With --compat, prefer YouTube's own H.264 (it has it up to 1080p), so there is
        # nothing to convert. Otherwise prefer VP9 (M1 decodes VP9 in hardware, not AV1).
        codec = "h264" if getattr(args, "compat", False) else "vp9"
        cmd += [
            "-f", f"bv*[height<={cap}]+ba/b[height<={cap}]/bv*+ba/b",
            # Highest resolution and frame rate first, then SDR, the codec above and AAC audio.
            "-S", f"res:{cap},fps,hdr:sdr,vcodec:{codec},acodec:m4a",
            "--merge-output-format", "mp4",
            # A clip must not get the whole video's chapters: they become a track as long as the
            # video, so QuickTime would show the full length and stop after the clip.
            *(["--no-embed-chapters"] if clip else ["--embed-chapters"]),
        ]

    if clip:
        # ffmpeg seeks inside the stream, so only that part is downloaded. The copy starts at
        # the keyframe before the start and hides the extra frames, so players show the exact
        # cut; --compat re-encodes it so editors do too.
        cmd += ["--download-sections", f"*{start or 0}-{end if end is not None else 'inf'}",
                "--force-overwrites"]

    if args.cookies:
        cmd += ["--cookies-from-browser", args.cookies]

    if args.dry_run:
        details = "audio %(acodec)s" if args.audio else "%(resolution)s @ %(fps)sfps · video %(vcodec)s · audio %(acodec)s"
        cmd += ["--simulate", "--print", f"%(title)s\n   {details} · ~%(filesize_approx)DB"]
    else:
        cmd += ["--print-to-file", "after_move:%(.{filepath,duration})j", filepath_log]

    return cmd + [*extra, "--", *links]


def read_saved(filepath_log: Path, folder: Path) -> list[tuple[Path, float | None]]:
    """Files yt-dlp saved, with the full video's duration, from its --print-to-file log."""
    saved = []
    for line in Path(filepath_log).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        duration = item.get("duration")
        saved.append((folder / item["filepath"], float(duration) if duration else None))
    return saved


def clip_length(start: float | None, end: float | None, duration: float | None) -> float | None:
    if end is not None:
        return end - (start or 0)
    return duration - (start or 0) if duration else None


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,start_time:format=duration",
         "-show_chapters", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **no_window(),
    )
    return json.loads(result.stdout or "{}")


def is_empty(path: Path, expected: float | None = None) -> bool:
    """True for a clip with (almost) nothing in it, e.g. one that starts after the video ends:
    yt-dlp can't catch that when it doesn't know a video's length, as with some non-YouTube
    links, and ffmpeg then leaves a few stray frames (about 0.1 s)."""
    info = probe(path)
    duration = float(info.get("format", {}).get("duration") or 0)
    limit = min(0.25, expected / 2) if expected else 0.25
    return duration < limit or not any(st.get("codec_type") in ("video", "audio") for st in info.get("streams", []))


def describe(path: Path) -> str:
    info = probe(path)
    parts = []
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            parts.append(f"{stream['width']}×{stream['height']} {stream['codec_name']}")
        elif stream.get("codec_type") == "audio":
            parts.append(f"audio {stream['codec_name']}")
    size_mb = path.stat().st_size / 1e6
    parts.append(f"{size_mb / 1000:.2f} GB" if size_mb >= 1000 else f"{size_mb:.0f} MB" if size_mb >= 10 else f"{size_mb:.1f} MB")
    return " · ".join(parts)


class ConversionError(Exception):
    pass


def finalize_clip(path: Path, length: float | None) -> Path:
    """Make a clip file exactly the clip, so every player shows the clip's own length.

    Found with a video that has chapters: the clip carried the whole video's chapter track, so
    QuickTime showed the full length (e.g. 15:00) and stopped after the clip. Some sources also
    keep the whole timeline. This rewrites the file (a copy, no re-encode) without chapter or
    data tracks, starting at 0 and no longer than the clip. Hidden lead-in frames stay hidden."""
    if not path.exists():
        return path
    info = probe(path)
    duration = float(info.get("format", {}).get("duration") or 0)
    streams = info.get("streams", [])
    starts = [float(st["start_time"]) for st in streams if st.get("start_time") not in (None, "N/A")]
    offset = min(starts) if starts else 0.0
    extra_tracks = any(st.get("codec_type") not in ("video", "audio") for st in streams)
    too_long = bool(length) and duration > length + 0.75
    if not (info.get("chapters") or extra_tracks or too_long or offset >= 0.25):
        return path
    temp = path.with_name(path.stem + ".trim-tmp" + path.suffix)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
           "-map", "0:v:0?", "-map", "0:a:0?", "-map_chapters", "-1", "-c", "copy",
           *(["-t", f"{length:.3f}"] if too_long else []),
           "-movflags", "+faststart", str(temp)]
    if subprocess.run(cmd, **no_window()).returncode != 0 or not temp.exists():
        temp.unlink(missing_ok=True)
        return path
    replace_file(temp, path)
    return path


# H.264 encoders for --compat, best first, with the pixel format each one takes.
# The Mac always has Apple's hardware encoder. A Windows PC may have an NVIDIA (nvenc),
# Intel (qsv) or AMD (amf) one; the CPU encoder (libx264) is slower but works everywhere.
MAC_ENCODER = ["-c:v", "h264_videotoolbox", "-pix_fmt", "yuv420p"]
PC_ENCODERS = [
    ["-c:v", "h264_nvenc", "-preset", "p5", "-pix_fmt", "yuv420p"],
    ["-c:v", "h264_qsv", "-preset", "medium", "-pix_fmt", "nv12"],
    ["-c:v", "h264_amf", "-quality", "balanced", "-pix_fmt", "nv12"],
]
CPU_ENCODER = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
_encoder: list[str] | None = None


def h264_encoder() -> list[str]:
    """The encoder settings to use, found once by encoding a tiny test clip with each candidate."""
    global _encoder
    if _encoder is None:
        if sys.platform == "darwin":
            _encoder = MAC_ENCODER
        else:
            _encoder = CPU_ENCODER
            for candidate in PC_ENCODERS:
                test = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=1280x720:r=30:d=0.5", *candidate, "-b:v", "4M", "-f", "null", "-"]
                try:
                    works = subprocess.run(test, capture_output=True, timeout=30, **no_window()).returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    works = False
                if works:
                    _encoder = candidate
                    break
    return _encoder


def convert_to_h264(path: Path, on_progress=None, force: bool = False) -> Path:
    """Re-encode to H.264 MP4. on_progress(fraction) gets 0..1 updates instead of ffmpeg's own
    stats line, and can return False to cancel. force re-encodes files that are already H.264:
    clips need it so they start on a real frame, not on hidden lead-in frames."""
    info = probe(path)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not video:
        return path
    if not force and video.get("codec_name") == "h264" and (not audio or audio.get("codec_name") == "aac"):
        if not on_progress:
            print("   already H.264, nothing to convert")
        return path

    height = video.get("height") or 1080
    bitrate = next(rate for min_height, rate in COMPAT_BITRATES if height >= min_height)
    temp = path.with_name(path.stem + ".h264-tmp.mp4")
    encoder = h264_encoder()
    if not on_progress:
        print(f"→ Converting to H.264 ({bitrate}bps, {encoder[1]}). Long videos take a while…")

    copy_audio = audio and audio.get("codec_name") == "aac" and not force
    audio_args = ["-c:a", "copy"] if copy_audio else ["-c:a", "aac", "-b:a", "192k"]
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *(["-nostats", "-progress", "pipe:1"] if on_progress else ["-stats"]),
        "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?",
        *encoder, "-b:v", bitrate, "-tag:v", "avc1",
        *audio_args,
        "-movflags", "+faststart",
        str(temp),
    ]

    if on_progress:
        duration = float(info.get("format", {}).get("duration") or 0)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                   **no_window())
        for line in process.stdout:
            if line.startswith("out_time_us=") and duration > 0 and line[12:].strip().isdigit():
                if on_progress(min(int(line[12:]) / 1e6 / duration, 1.0)) is False:
                    process.terminate()
        returncode = process.wait()
    else:
        returncode = subprocess.run(cmd, **no_window()).returncode

    if returncode != 0:
        temp.unlink(missing_ok=True)
        raise ConversionError(f"H.264 conversion failed. The original is still at: {path.name}")

    final = path.with_suffix(".mp4")
    replace_file(temp, final)
    if final != path:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            pass  # Windows: something still has the original open; the converted file is saved anyway
    return final


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ytdl",
        description="Download YouTube videos in the best quality (up to 4K) with audio "
                    "into the current folder, or into a folder you name.",
    )
    parser.add_argument("targets", nargs="*", metavar="folder/link",
                        help="a folder to save into and/or YouTube link(s). With no link you'll be asked for one.")
    parser.add_argument("--max", type=int, default=2160, metavar="HEIGHT",
                        help="highest resolution to download (2160 = 4K, 1440, 1080, 720). Default 2160")
    parser.add_argument("--audio", action="store_true", help="download audio only as .m4a")
    parser.add_argument("--compat", action="store_true",
                        help="convert to H.264 MP4 for QuickTime / Final Cut / Premiere")
    parser.add_argument("--from", dest="start", metavar="TIME",
                        help="download only from this time on, e.g. 1:30 or 1:02:45")
    parser.add_argument("--to", dest="end", metavar="TIME",
                        help="download only up to this time, e.g. 2:45")
    parser.add_argument("--cookies", metavar="BROWSER",
                        help="use your sign-in from this browser (chrome, safari, firefox, brave, edge)")
    parser.add_argument("--dry-run", action="store_true", help="show what would be downloaded, download nothing")
    parser.add_argument("--web", action="store_true", help="open a simple page in Chrome to paste links into")
    parser.add_argument("--version", action="version", version=f"4K YT Downloader {VERSION}")
    # Intermixed, so  ytdl <folder> --audio <link>  works as well as options first.
    args = parser.parse_intermixed_args()
    try:
        args.start, args.end = parse_time(args.start), parse_time(args.end)
        check_clip(args.start, args.end)
    except ValueError as error:
        fail(str(error))

    check_tools()
    links, folder = split_targets(args.targets)
    if not args.dry_run:
        prepare_folder(folder)

    if args.web:
        if is_clip(args):
            fail("With --web, type the times into the page.")
        if links:
            fail("With --web, paste links into the page. Give only a folder:  ytdl --web <folder>")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ytdl_web
        ytdl_web.serve(folder=folder, cookies=args.cookies)
        return

    links = links or ask_for_links()
    if not links:
        fail("No link given.")

    print(f"Saving to: {folder}\n", flush=True)

    with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as log:
        filepath_log = log.name
    try:
        result = subprocess.run(yt_dlp_command(args, links, filepath_log, folder=folder))
        saved = [finalize_clip(path, clip_length(args.start, args.end, duration)) if is_clip(args) else path
                 for path, duration in read_saved(Path(filepath_log), folder)]
        if is_clip(args) and saved and all(path.exists() and is_empty(path, clip_length(args.start, args.end, None))
                                           for path in saved):
            for path in saved:
                path.unlink(missing_ok=True)
            fail("The clip starts after the video ends.")
    except KeyboardInterrupt:
        fail("Cancelled. Run the same command again to resume.")
    finally:
        Path(filepath_log).unlink(missing_ok=True)

    if args.dry_run:
        sys.exit(result.returncode)

    if args.compat and not args.audio:
        try:
            saved = [convert_to_h264(path, force=is_clip(args)) for path in saved if path.exists()]
        except ConversionError as error:
            fail(str(error))

    if saved:
        print("\n✔ Saved:")
        for path in saved:
            if path.exists():
                print(f"   {path.name}\n   {describe(path)}")

    if result.returncode != 0:
        fail(f"Some downloads failed. Try:  {'yt-dlp -U' if IS_WINDOWS else 'brew upgrade yt-dlp'}   or add  --cookies chrome")


if __name__ == "__main__":
    main()
