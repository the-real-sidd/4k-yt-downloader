"""
ytdl --web: a small page in Chrome for pasting YouTube links.

It runs only on this Mac (127.0.0.1), uses the same download settings as the
`ytdl` command, and saves into the folder it was started from (or the one
given as  ytdl --web <folder>).

The Mac app (tools/macapp) runs this file directly with --app --no-open and shows
the page in its own window: the folder then comes from the app's saved settings,
the app's native folder panel sets it, and the server stops when the app quits.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import sys
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import ytdl

PAGE = Path(__file__).resolve().parent / "ytdl_web.html"
FIRST_PORT = 8765
QUALITIES = {"2160": 2160, "1440": 1440, "1080": 1080, "720": 720}
ACTIVE = {"starting", "downloading", "finishing", "converting"}
PUBLIC_FIELDS = ("id", "url", "quality", "compat", "start", "end", "status", "title", "thumbnail",
                 "details", "part", "progress", "speed", "eta", "files", "error")

PROGRESS_TEMPLATE = (
    "download:PROG %(info.vcodec)s %(progress.downloaded_bytes)s %(progress.total_bytes)s "
    "%(progress.total_bytes_estimate)s %(progress.speed)s %(progress.eta)s"
)

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "4K YT Downloader"
OLD_APP_SUPPORT = Path.home() / "Library" / "Application Support" / "YT Downloader"  # before 3.0
SETTINGS_FILE = APP_SUPPORT / "settings.json"
PORT_FILE = APP_SUPPORT / "port"

jobs: dict[str, dict] = {}
lock = threading.Lock()
save_dir = Path.cwd()
cookies_browser = None
app_mode = False


def load_settings() -> dict:
    # Falls back to the settings saved before the app was renamed (3.0), so the folder is kept.
    for settings in (SETTINGS_FILE, OLD_APP_SUPPORT / "settings.json"):
        try:
            return json.loads(settings.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def save_settings(settings: dict) -> None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


def display_folder() -> str:
    home, folder = str(Path.home()), str(save_dir)
    return "~" + folder[len(home):] if folder.startswith(home) else folder


def update(job: dict, **fields) -> None:
    with lock:
        job.update(fields)


def number(text: str):
    try:
        return float(text)
    except ValueError:
        return None


def clean_error(line: str) -> str:
    message = line.removeprefix("ERROR:").strip()
    # "[youtube] abc123: Video unavailable" → "Video unavailable"
    if message.startswith("[") and ": " in message:
        message = message.split(": ", 1)[1]
    return message.split(" (caused by ")[0]


def stop(process) -> None:
    """Stop yt-dlp and the ffmpeg it started (each download runs in its own process group)."""
    if process and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()


def run_job(job: dict) -> None:
    folder = save_dir  # the folder can change while this download runs
    args = SimpleNamespace(
        audio=job["quality"] == "audio",
        max=QUALITIES.get(job["quality"], 2160),
        compat=job["compat"],
        start=job["start"],
        end=job["end"],
        cookies=cookies_browser,
        dry_run=False,
    )
    clip = ytdl.is_clip(args)
    with tempfile.NamedTemporaryFile("r", suffix=".txt", delete=False) as log:
        filepath_log = Path(log.name)
    extra = [
        "--newline", "--progress", "--progress-delta", "0.5",
        "--progress-template", PROGRESS_TEMPLATE,
        "--print", "before_dl:META %(.{title,thumbnail,resolution,fps,duration,section_start,section_end,_filename})j",
    ]
    if clip:
        # yt-dlp hands clips to ffmpeg, which reports how far it got as out_time_us=… lines.
        extra += ["--downloader-args", "ffmpeg_o:-progress pipe:1 -nostats"]
    cmd = ytdl.yt_dlp_command(args, [job["url"]], str(filepath_log), extra, folder=folder)

    errors, problem, clip_length, target = [], None, None, None
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1, cwd=folder, start_new_session=True)
    update(job, _process=process)
    for line in process.stdout:
        line = line.rstrip()
        if line.startswith("PROG "):
            fields = line.split(" ")
            if len(fields) < 7:
                continue
            vcodec, done, total, estimate, speed, eta = fields[1:7]
            done, size = number(done), number(total) or number(estimate)
            fraction = min(done / size, 1.0) if done is not None and size else None
            update(job,
                   status="finishing" if fraction is not None and fraction >= 0.999 else "downloading",
                   part="audio" if vcodec == "none" or args.audio else "clip" if clip else "video",
                   progress=fraction, speed=number(speed), eta=number(eta))
        elif line.startswith("out_time_us=") and clip_length:
            position = number(line[12:])
            if position is not None and position >= 0:
                fraction = min(position / 1e6 / clip_length, 1.0)
                update(job, status="finishing" if fraction >= 0.999 else "downloading",
                       part="audio" if args.audio else "clip", progress=fraction)
        elif line.startswith("META "):
            try:
                meta = json.loads(line[5:])
            except json.JSONDecodeError:
                continue
            details = "Audio only" if args.audio else " · ".join(
                part for part in (meta.get("resolution"), f"{meta['fps']:g} fps" if meta.get("fps") else None) if part)
            update(job, title=meta.get("title") or job["title"], thumbnail=meta.get("thumbnail"), details=details)
            target = meta.get("_filename")
            if clip:
                start, end, duration = meta.get("section_start") or 0, meta.get("section_end"), meta.get("duration")
                if end is not None and end <= start:
                    # yt-dlp trims the end to the video's length, so the start is past the end.
                    problem = f"The clip starts after the video ends (it's {ytdl.format_time(end)} long)."
                    stop(process)
                    continue
                clip_length = end - start if end is not None else duration - start if duration else None
                # ffmpeg takes a moment to report its first position.
                update(job, status="downloading", part="audio" if args.audio else "clip", progress=None)
        elif line.startswith("ERROR:"):
            errors.append(clean_error(line))
    returncode = process.wait()

    saved = ytdl.read_saved(filepath_log, folder)
    filepath_log.unlink(missing_ok=True)
    if clip and not job.get("_cancelled") and not problem and returncode == 0:
        # Make sure the file is exactly the clip, however the video was served.
        update(job, status="finishing", progress=None)
        saved = [(ytdl.finalize_clip(path, ytdl.clip_length(args.start, args.end, duration)), duration)
                 for path, duration in saved]
    saved = [path for path, _ in saved]

    if job.get("_cancelled"):
        if clip and target:
            # A clip can't be resumed, so don't leave the half-written file behind.
            Path(target + ".part").unlink(missing_ok=True)
        update(job, status="cancelled", progress=None)
        return
    if problem or returncode != 0 or not saved:
        message = problem or (errors[-1] if errors else "Download failed.")
        update(job, status="error", error=message, progress=None)
        return

    if job["compat"] and not args.audio:
        update(job, status="converting", progress=0.0, speed=None, eta=None)
        try:
            saved = [
                ytdl.convert_to_h264(
                    path,
                    on_progress=lambda fraction: (update(job, progress=fraction), not job.get("_cancelled"))[1],
                    force=clip,
                )
                for path in saved if path.exists()
            ]
        except ytdl.ConversionError as error:
            if job.get("_cancelled"):
                update(job, status="cancelled", progress=None)
            else:
                update(job, status="error", error=str(error), progress=None)
            return

    files = [{"name": path.name, "path": str(path), "info": ytdl.describe(path)} for path in saved if path.exists()]
    update(job, status="done", progress=1.0, files=files, speed=None, eta=None)


def start_job(url: str, quality: str, compat: bool, start: float | None = None, end: float | None = None) -> dict:
    job = {
        "id": uuid.uuid4().hex[:10], "url": url, "quality": quality if quality in {*QUALITIES, "audio"} else "2160",
        "compat": bool(compat), "start": start, "end": end,
        "status": "starting", "title": url, "thumbnail": None, "details": "",
        "part": None, "progress": None, "speed": None, "eta": None, "files": [], "error": None,
        "created": time.time(),
    }
    with lock:
        jobs[job["id"]] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return job


def public(job: dict) -> dict:
    return {key: job.get(key) for key in PUBLIC_FIELDS}


class Handler(BaseHTTPRequestHandler):
    server_version = "ytdl-web"

    def log_message(self, *args) -> None:
        pass

    def allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def guard(self, post: bool = False) -> bool:
        # Only answer this page: blocks other websites and DNS-rebinding tricks.
        if self.headers.get("Host") not in self.allowed_hosts():
            self.send_error(403)
            return False
        if post:
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc not in self.allowed_hosts():
                self.send_error(403)
                return False
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self.send_error(415)
                return False
        return True

    def do_GET(self) -> None:
        if not self.guard():
            return
        if self.path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            with lock:
                listed = [public(job) for job in sorted(jobs.values(), key=lambda j: j["created"], reverse=True)]
            self.send_json({"folder": display_folder(), "folderPath": str(save_dir),
                            "canChange": app_mode, "jobs": listed})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        global save_dir
        if not self.guard(post=True):
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(min(length, 100_000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "Bad request"}, 400)
            return

        if self.path == "/api/download":
            urls = [u for u in str(data.get("urls", "")).split() if urlparse(u).scheme in ("http", "https")]
            if not urls:
                self.send_json({"error": "That doesn't look like a link."}, 400)
                return
            try:
                # 0:00 as the start is the same as no start.
                start, end = ytdl.parse_time(data.get("start")) or None, ytdl.parse_time(data.get("end"))
                ytdl.check_clip(start, end)
            except ValueError as error:
                self.send_json({"error": str(error)}, 400)
                return
            if (start is not None or end is not None) and len(urls) > 1:
                self.send_json({"error": "Clip times work with one link at a time."}, 400)
                return
            created = [public(start_job(url, str(data.get("quality")), data.get("compat"), start, end))
                       for url in urls]
            self.send_json({"jobs": created})
            return

        if self.path == "/api/folder":
            # The app's folder panel picks the path; the server only checks it and remembers it.
            if not app_mode:
                self.send_json({"error": "Pick the folder when starting:  ytdl --web <folder>"}, 400)
                return
            chosen = Path(str(data.get("path", ""))).expanduser()
            if not chosen.is_absolute() or not chosen.is_dir():
                self.send_json({"error": "That folder doesn't exist."}, 400)
                return
            save_dir = chosen.resolve()
            save_settings({**load_settings(), "folder": str(save_dir)})
            self.send_json({"folder": display_folder(), "folderPath": str(save_dir)})
            return

        with lock:
            job = jobs.get(str(data.get("id")))
        if not job:
            self.send_json({"error": "Unknown download"}, 404)
        elif self.path == "/api/cancel":
            update(job, _cancelled=True)
            stop(job.get("_process"))
            self.send_json({"ok": True})
        elif self.path == "/api/reveal":
            for file in job["files"]:
                if Path(file["path"]).exists():
                    subprocess.run(["open", "-R", file["path"]])
                    break
            self.send_json({"ok": True})
        elif self.path == "/api/remove":
            if job["status"] not in ACTIVE:
                with lock:
                    jobs.pop(job["id"], None)
            self.send_json({"ok": True})
        else:
            self.send_error(404)


def stop_on_sigterm(*_) -> None:
    raise KeyboardInterrupt


def watch_parent(pid: int, server: ThreadingHTTPServer) -> None:
    """Stop the server when the app that started it has gone (even after a Force Quit)."""
    while True:
        time.sleep(2)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            server.shutdown()
            return
        except PermissionError:
            pass


def serve(folder: Path | None = None, cookies: str | None = None, app: bool = False,
          watch_pid: int | None = None, open_browser: bool = True) -> None:
    global cookies_browser, save_dir, app_mode
    cookies_browser = cookies
    app_mode = app
    if app:
        saved = Path(load_settings().get("folder") or Path.home() / "Downloads")
        save_dir = saved if saved.is_dir() else Path.home() / "Downloads"
    else:
        save_dir = folder or Path.cwd()

    server = None
    for port in range(FIRST_PORT, FIRST_PORT + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if not server:
        ytdl.fail("No free port between 8765 and 8784.")

    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"ytdl is open at {url}")
    print(f"Saving to: {save_dir}")
    if app:
        APP_SUPPORT.mkdir(parents=True, exist_ok=True)
        PORT_FILE.write_text(str(server.server_address[1]))
        signal.signal(signal.SIGTERM, stop_on_sigterm)
        if watch_pid:
            threading.Thread(target=watch_parent, args=(watch_pid, server), daemon=True).start()
    else:
        print("Keep this window open while downloading. Press Ctrl+C to stop.\n")
    # The Mac app reads this line to know where to point its window.
    print(f"PORT {server.server_address[1]}")
    sys.stdout.flush()

    if open_browser and subprocess.run(["open", "-a", "Google Chrome", url], capture_output=True).returncode != 0:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        with lock:
            running = [job.get("_process") for job in jobs.values()]
        for process in running:
            stop(process)
        server.server_close()
        if app:
            PORT_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local download page used by 4K YT Downloader.app")
    parser.add_argument("--app", action="store_true", help="use the app's saved folder and settings")
    parser.add_argument("--watch", type=int, metavar="PID", help="stop when this process exits")
    parser.add_argument("--no-open", action="store_true", help="don't open the page in a browser")
    options = parser.parse_args()
    serve(app=options.app, watch_pid=options.watch, open_browser=not options.no_open)
