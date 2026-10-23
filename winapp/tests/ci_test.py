"""Tests the Windows app on a real Windows machine (GitHub Actions, see .github/workflows/windows.yml).
Nobody on the project has a Windows PC, so this does what a person would do and checks the results.

    python winapp/tests/ci_test.py library   the download code: tool download, full video, clips
                                             (exact length, no chapter track), H.264, audio, cancel
    python winapp/tests/ci_test.py app       the installed app: install.ps1 + installer, first launch,
                                             the window, clipboard, folder dialog, closing, one copy
                                             at a time, downloads stop with the app, uninstall

Test videos are made with ffmpeg and served from this machine, so the tests don't depend on
YouTube (which often blocks GitHub's machines). One real YouTube clip is tried for information
only. Screenshots, logs and results go to ci-output/ (uploaded as the run's artifact).
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "winapp")]
OUT = ROOT / "ci-output"
WORK = Path(tempfile.mkdtemp(prefix="ytdl-ci-"))
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
APP_NAME = "4K YT Downloader"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
YOUTUBE_LINK = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"  # Big Buck Bunny (CC-BY)

results: list[dict] = []


def check(name: str, ok: bool, detail="") -> bool:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail if detail != '' else ''}", flush=True)
    return bool(ok)


def info(name: str, detail) -> None:
    results.append({"info": name, "detail": detail})
    print(f"INFO  {name}  {detail}", flush=True)


def finish(phase: str) -> None:
    OUT.mkdir(exist_ok=True)
    (OUT / f"{phase}-results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    failed = [r["check"] for r in results if "check" in r and not r["ok"]]
    print(f"\n{len([r for r in results if 'check' in r]) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("Failed:\n  " + "\n  ".join(failed))
        sys.exit(1)


# ---------------------------------------------------------------- test media + a local web server


def make_media(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    chapters = folder / "chapters.txt"
    chapters.write_text(";FFMETADATA1\ntitle=Chapters test\n"
                        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=30000\ntitle=Part one\n"
                        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=30000\nEND=60000\ntitle=Part two\n", encoding="utf-8")
    main = folder / "Chapters vidéo ünïcode 日本.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                    "-i", str(chapters), "-map", "0:v", "-map", "1:a", "-map_metadata", "2", "-map_chapters", "2",
                    "-t", "60", "-c:v", "libx264", "-preset", "veryfast", "-g", "30", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(main)], check=True)
    old = folder / "old codec.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000",
                    "-t", "8", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-b:a", "96k", str(old)], check=True)
    return {"main": main, "old": old}


class MediaHandler(http.server.BaseHTTPRequestHandler):
    """Serves the test videos with Range support (yt-dlp's clips need it). ?slow=1 throttles."""
    folder: Path

    def log_message(self, *args) -> None:
        pass

    def _send(self, body: bool) -> None:
        parsed = urlparse(self.path)
        path = self.folder / unquote(parsed.path.lstrip("/"))
        if not path.is_file():
            self.send_error(404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        header = self.headers.get("Range")
        if header and header.startswith("bytes="):
            first, _, last = header[6:].split(",")[0].strip().partition("-")
            if first:
                start, end = int(first), min(int(last), size - 1) if last else size - 1
            else:
                start = max(size - int(last), 0)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        if not body:
            return
        slow = "slow" in parsed.query
        with open(path, "rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = source.read(min(16384 if slow else 262144, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except OSError:
                    return
                remaining -= len(chunk)
                if slow:
                    time.sleep(0.08)  # about 200 KB/s

    def do_GET(self) -> None:
        self._send(True)

    def do_HEAD(self) -> None:
        self._send(False)


def serve_media(folder: Path) -> str:
    MediaHandler.folder = folder
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MediaHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/"


# ---------------------------------------------------------------- helpers


def call(port: int, path: str, data: dict | None = None, timeout: float = 30):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return {"httpError": error.code, **json.loads(error.read() or b"{}")}


def wait_job(port: int, job_id: str, timeout: float = 300, until=("done", "error", "cancelled")) -> dict:
    end = time.time() + timeout
    job = {}
    while time.time() < end:
        jobs = {j["id"]: j for j in call(port, "/api/state")["jobs"]}
        job = jobs.get(job_id, {})
        if job.get("status") in until:
            return job
        time.sleep(0.5)
    return {**job, "timedOut": True}


def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "stream=codec_type,codec_name,width,height,duration:format=duration",
                             "-show_chapters", "-of", "json", str(path)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    return json.loads(result.stdout or "{}")


def processes(*names: str) -> list[str]:
    output = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace").stdout
    running = [line.split('","')[0].strip('"') for line in output.splitlines() if line.strip()]
    return [name for name in running if name.lower() in {n.lower() for n in names}]


def wait_until(condition, timeout: float, step: float = 0.5):
    end = time.time() + timeout
    while time.time() < end:
        value = condition()
        if value:
            return value
        time.sleep(step)
    return condition()


# ---------------------------------------------------------------- 1. the download code


def library() -> None:
    os.environ["APPDATA"] = str(WORK / "appdata")  # settings for this test only (read on import)
    import wintools
    import ytdl
    import ytdl_web

    ytdl.hide_windows = True
    tools_dir = WORK / "tools"
    tools = wintools.Tools(tools_dir, "4K-YT-Downloader-CI")
    started = time.time()
    ok = tools.run()
    check("tools download from GitHub (yt-dlp, ffmpeg, deno)", ok and tools.status()["ready"],
          f"{tools.status()} in {time.time() - started:.0f}s")
    if not ok:
        return finish("library")
    check("tools folder has every file", all((tools_dir / f).is_file() for f in
                                             ("yt-dlp.exe", "ffmpeg.exe", "ffprobe.exe", "deno.exe")),
          sorted(p.name for p in tools_dir.iterdir() if p.suffix == ".exe"))
    os.environ["PATH"] = str(tools_dir) + os.pathsep + os.environ["PATH"]
    check("second run is instant (nothing to download)", wintools.Tools(tools_dir, "ci").missing() == [])

    media = make_media(WORK / "media")
    base = serve_media(WORK / "media")
    main_url = base + quote(media["main"].name)
    info("test video", probe(media["main"]))

    server = ytdl_web.create_server(app=True)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    downloads = WORK / "Downloads ü"
    downloads.mkdir()
    folder = call(port, "/api/folder", {"path": str(downloads)})
    check("folder can be changed", folder.get("folderPath") == str(downloads.resolve()), folder)
    state = call(port, "/api/state")
    check("state reports version and Windows", state.get("version") == VERSION and state.get("platform") == "windows",
          {k: state.get(k) for k in ("version", "platform", "folder")})
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/?app=windows").read().decode("utf-8")
    check("page loads with ?app=windows", "4K YT Downloader" in page and "instagram.com/the.real.sidd" in page)

    info("H.264 encoder chosen on this PC", ytdl.h264_encoder())

    # (name, request, expected length, video codec or None for audio only)
    cases = [
        ("full video", {"urls": main_url, "quality": "1080", "compat": False}, 60.0, "h264"),
        ("clip 0:10-0:15", {"urls": main_url, "quality": "1080", "compat": False, "start": "0:10", "end": "0:15"}, 5.0, "h264"),
        ("clip with H.264 re-encode 20-25.5", {"urls": main_url, "quality": "1080", "compat": True, "start": "20", "end": "25.5"}, 5.5, "h264"),
        ("clip to the end 0:52-", {"urls": main_url, "quality": "1080", "compat": True, "start": "0:52"}, 8.0, "h264"),
        ("old codec converted to H.264", {"urls": base + quote(media["old"].name), "quality": "1080", "compat": True}, 8.0, "h264"),
        ("audio only", {"urls": main_url, "quality": "audio", "compat": False}, 60.0, None),
    ]
    for name, request, length, codec in cases:
        response = call(port, "/api/download", request)
        if not check(f"{name}: accepted", "jobs" in response, response):
            continue
        job = wait_job(port, response["jobs"][0]["id"])
        if not check(f"{name}: finished", job.get("status") == "done", {k: job.get(k) for k in ("status", "error", "timedOut")}):
            continue
        file = Path(job["files"][0]["path"])
        info_ = probe(file)
        duration = float(info_.get("format", {}).get("duration") or 0)
        kinds = [s.get("codec_type") for s in info_.get("streams", [])]
        check(f"{name}: length {length}s", abs(duration - length) <= 0.35, f"{duration:.3f}s, {file.name}")
        if "start" in request:
            # A full download keeps the video's chapters; a clip must not (QuickTime showed the full length).
            check(f"{name}: only audio/video tracks", set(kinds) <= {"video", "audio"}, kinds)
            check(f"{name}: no chapters", not info_.get("chapters"))
            check(f"{name}: name has the times", "[" in file.stem and "s-" in file.stem, file.name)
        if codec:
            video = next((s for s in info_.get("streams", []) if s.get("codec_type") == "video"), {})
            check(f"{name}: video is {codec}", video.get("codec_name") == codec, video)
        else:
            check(f"{name}: audio file", "audio" in kinds and "video" not in kinds and file.suffix == ".m4a", (kinds, file.suffix))

    bad = call(port, "/api/download", {"urls": main_url, "start": "1:00", "end": "0:30"})
    check("end before start is refused", "error" in bad, bad)
    past = call(port, "/api/download", {"urls": main_url, "quality": "1080", "start": "2:00", "end": "2:10"})
    job = wait_job(port, past["jobs"][0]["id"]) if "jobs" in past else {}
    check("start past the end is reported", job.get("status") == "error", job.get("error"))

    # Cancel: a slow clip download, cancelled halfway. yt-dlp and its ffmpeg must stop, and the
    # half-written file must go.
    response = call(port, "/api/download", {"urls": main_url + "?slow=1", "quality": "1080", "compat": False,
                                            "start": "0:01", "end": "0:59"})
    job_id = response["jobs"][0]["id"]
    running = wait_job(port, job_id, 60, until=("downloading", "done", "error"))
    time.sleep(3)
    before = processes("yt-dlp.exe", "ffmpeg.exe")
    call(port, "/api/cancel", {"id": job_id})
    job = wait_job(port, job_id, 30)
    check("cancel: status cancelled", job.get("status") == "cancelled", {k: job.get(k) for k in ("status", "error")})
    left = wait_until(lambda: not processes("yt-dlp.exe", "ffmpeg.exe"), 10)
    check("cancel: yt-dlp and ffmpeg stopped", left or not processes("yt-dlp.exe", "ffmpeg.exe"),
          {"while running": before, "after": processes("yt-dlp.exe", "ffmpeg.exe"), "was": running.get("status")})
    check("cancel: no half-written file left", not list(downloads.glob("*.part")), [p.name for p in downloads.iterdir()])

    # Real YouTube, for information only (GitHub's machines are often asked to sign in).
    response = call(port, "/api/download", {"urls": YOUTUBE_LINK, "quality": "1080", "compat": True,
                                            "start": "0:10", "end": "0:15"})
    job = wait_job(port, response["jobs"][0]["id"], 240)
    detail = {k: job.get(k) for k in ("status", "error", "details")}
    if job.get("status") == "done":
        file = Path(job["files"][0]["path"])
        detail["file"] = file.name
        detail["duration"] = probe(file).get("format", {}).get("duration")
    info("real YouTube clip (not required to pass)", detail)
    if job.get("error"):
        check("yt-dlp's messages keep their characters (UTF-8)", "\ufffd" not in job["error"], job["error"][:80])

    ytdl_web.stop_all()
    server.shutdown()
    finish("library")


# ---------------------------------------------------------------- 2. the installed app


LOCAL = Path(os.environ.get("LOCALAPPDATA", "")) / APP_NAME
ROAMING = Path(os.environ.get("APPDATA", "")) / APP_NAME
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / APP_NAME
EXE = INSTALL_DIR / f"{APP_NAME}.exe"
START_MENU = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def powershell(script: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, **env}, timeout=600)


def registry_version() -> str | None:
    result = powershell(r"(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8C5E2D47-3F1B-4C7A-9E64-2B7D41A9C3F5}_is1' -ErrorAction SilentlyContinue).DisplayVersion")
    return result.stdout.strip() or None


def app_port() -> int | None:
    try:
        return int((ROAMING / "port").read_text())
    except (OSError, ValueError):
        return None


def app_env(**extra) -> dict:
    """The environment a person's PC would give the app (without the build machine's UTF-8 switches)."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    return {**env, **extra}


def save_log(name: str) -> None:
    log = LOCAL / f"{APP_NAME}.log"
    if log.exists():
        shutil.copy(log, OUT / name)


def app() -> None:
    OUT.mkdir(exist_ok=True)
    setup = ROOT / "dist" / "4K-YT-Downloader-Setup.exe"
    if not check("installer was built", setup.is_file(), f"{setup.stat().st_size / 1e6:.1f} MB" if setup.exists() else ""):
        return finish("app")

    # Install the way people do: the one-line command, here with the installer just built.
    result = powershell("Get-Content -Raw -LiteralPath install.ps1 | Invoke-Expression",
                        YTD_SETUP=str(setup), YTD_NO_OPEN="1")
    (OUT / "install-output.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    print(result.stdout, result.stderr)
    check("install.ps1 reports success", "is ready" in result.stdout and "went wrong" not in result.stdout)
    check("app installed in %LOCALAPPDATA%\\Programs", EXE.is_file(), str(EXE))
    check("Start menu shortcut", START_MENU.is_file(), str(START_MENU))
    check("installed version is registered", registry_version() == VERSION, registry_version())

    again = powershell("Get-Content -Raw -LiteralPath install.ps1 | Invoke-Expression",
                       YTD_SETUP=str(setup), YTD_NO_OPEN="1", YTD_FORCE="1")
    check("running the command again updates in place", "is ready" in again.stdout and EXE.is_file(), again.stdout[-300:])

    # First launch, checked by the app itself (see run_selftest in app.py).
    link = "https://youtu.be/aqz-KE-bpKQ"
    powershell(f"Set-Clipboard -Value '{link}'")
    selftest = OUT / "selftest"
    started = time.time()
    process = subprocess.Popen([str(EXE)], env=app_env(YTD_SELFTEST=str(selftest)))
    try:
        code = process.wait(timeout=1500)
    except subprocess.TimeoutExpired:
        process.kill()
        code = "timed out"
    save_log("app-log-first-run.txt")
    check("first launch: app closed by itself after the self-test", code == 0, f"exit {code} after {time.time() - started:.0f}s")
    try:
        report = json.loads((selftest / "results.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        check("first launch: self-test results written", False, error)
        return finish("app")
    info("self-test steps", report.get("steps"))
    check("self-test ran without errors", not report.get("error"), report.get("error"))
    page = report.get("page") or {}
    first = report.get("first_run_page") or {}
    info("first launch, 3 s in (the tools may already be done)", {k: first.get(k) for k in ("setupVisible", "setupText")})
    check("tools downloaded by the app", (report.get("tools") or {}).get("ready"), report.get("tools"))
    check("page talks to the app", page.get("bridge") and "win-app" in page.get("bodyClass", ""), page.get("bodyClass"))
    check("footer: credit links to Instagram", page.get("credit") == "Built with ❤️ by the.real.sidd"
          and page.get("creditLink") == "https://www.instagram.com/the.real.sidd/", (page.get("credit"), page.get("creditLink")))
    check("footer: version on the right", page.get("version") == f"Version {VERSION}", page.get("version"))
    check("folder is Downloads, and Change is shown", page.get("folder", "").lower().endswith("downloads") and page.get("changeVisible"),
          page.get("folder"))
    check("ready: setup banner gone, Download enabled", not page.get("setupVisible") and not page.get("downloadDisabled"))
    check("copied YouTube link filled in on launch", page.get("url") == link, page.get("url"))
    check("Paste button pastes", report.get("paste_button") == link, report.get("paste_button"))
    check("right-click menu on for text fields", report.get("context_menu") is True, report.get("context_menu"))
    dialog = report.get("folder_dialog") or {}
    check("Change opens the Windows folder dialog, Cancel closes it", dialog.get("opened") and dialog.get("finished")
          and dialog.get("returned") is None, dialog)
    closing = report.get("close_dialog") or {}
    check("closing during a download asks first", closing.get("quitAsked"), closing.get("quitText"))

    # Normal launch: tools are there now, so it's ready at once.
    for leftover in (ROAMING / "port",):
        leftover.unlink(missing_ok=True)
    first_app = subprocess.Popen([str(EXE)], env=app_env())
    port = wait_until(app_port, 60)
    check("second launch starts", bool(port), port)
    if not port:
        return finish("app")
    state = wait_until(lambda: (call(port, "/api/state").get("setup") or {}).get("ready"), 60)
    check("second launch: ready at once (tools kept)", bool(state))

    second = subprocess.Popen([str(EXE)], env=app_env())
    try:
        second_code = second.wait(timeout=30)
    except subprocess.TimeoutExpired:
        second.kill()
        second_code = "still running"
    check("opening it again doesn't start a second copy", second_code == 0 and first_app.poll() is None,
          f"second exit {second_code}, first {'running' if first_app.poll() is None else 'stopped'}")

    # Closing the app any way (here: Task Manager's End task) stops its downloads.
    os.environ["PATH"] = str(LOCAL / "tools") + os.pathsep + os.environ["PATH"]  # the app's ffmpeg, for test videos
    media = make_media(WORK / "app-media")
    base = serve_media(WORK / "app-media")
    downloads = WORK / "app-downloads"
    downloads.mkdir()
    call(port, "/api/folder", {"path": str(downloads)})
    response = call(port, "/api/download", {"urls": base + quote(media["main"].name) + "?slow=1", "quality": "1080",
                                            "compat": False, "start": "0:01", "end": "0:59"})
    if check("download started in the app", "jobs" in response, response):
        wait_job(port, response["jobs"][0]["id"], 90, until=("downloading", "error", "done"))
        time.sleep(4)
        during = processes("yt-dlp.exe", "ffmpeg.exe")
        subprocess.run(["taskkill", "/PID", str(first_app.pid), "/F"], capture_output=True)
        first_app.wait(30)
        gone = wait_until(lambda: not processes("yt-dlp.exe", "ffmpeg.exe"), 15)
        check("End task on the app stops its downloads", bool(during) and not processes("yt-dlp.exe", "ffmpeg.exe"),
              {"during": during, "after": processes("yt-dlp.exe", "ffmpeg.exe"), "gone": bool(gone)})
    save_log("app-log.txt")
    log_text = (OUT / "app-log.txt").read_text(encoding="utf-8", errors="replace") if (OUT / "app-log.txt").exists() else ""
    info("app log errors", [line for line in log_text.splitlines() if "Traceback" in line or "Error" in line][:20])

    # Uninstall from Settings > Apps (the same uninstaller, silently).
    uninstaller = INSTALL_DIR / "unins000.exe"
    subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=180)
    removed = wait_until(lambda: not EXE.exists() and not LOCAL.exists() and not START_MENU.exists(), 60)
    check("uninstall removes the app, its tools and shortcuts", bool(removed),
          {"exe": EXE.exists(), "tools": LOCAL.exists(), "shortcut": START_MENU.exists()})
    finish("app")


if __name__ == "__main__":
    os.chdir(ROOT)
    try:
        {"library": library, "app": app}[sys.argv[1]]()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        check("test script crashed", False, traceback.format_exc())
        finish(sys.argv[1])
