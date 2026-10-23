"""The free tools the Windows app uses: yt-dlp, ffmpeg (+ ffprobe) and deno.

The first time the app opens, they're downloaded from their official GitHub releases into
%LOCALAPPDATA%\\4K YT Downloader\\tools (about 150 MB, once). After that yt-dlp updates itself
about once a day, because YouTube changes often. Nothing is installed system-wide and no
administrator rights are needed. The page shows the progress (see showSetup in ytdl_web.html).
"""

from __future__ import annotations

import http.client
import os
import shutil
import subprocess
import threading
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
UPDATE_EVERY = 20 * 3600  # seconds between yt-dlp update checks

TOOLS = [
    {"name": "yt-dlp", "files": ["yt-dlp.exe"], "check": ["yt-dlp.exe", "--version"],
     "url": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"},
    # yt-dlp's own ffmpeg builds (recommended by yt-dlp). "shared": ffmpeg and ffprobe share their DLLs.
    {"name": "ffmpeg", "files": ["ffmpeg.exe", "ffprobe.exe"], "check": ["ffmpeg.exe", "-version"], "dlls": True,
     "url": "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip"},
    # yt-dlp needs a JavaScript runtime for YouTube's full-quality formats.
    {"name": "deno", "files": ["deno.exe"], "check": ["deno.exe", "--version"],
     "url": "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"},
]


class Tools:
    def __init__(self, folder: Path, user_agent: str) -> None:
        self.folder = folder
        self.user_agent = user_agent
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.state = {"ready": False, "message": "", "progress": None, "error": None}

    def status(self) -> dict:
        with self.lock:
            return dict(self.state)

    def _set(self, **fields) -> None:
        with self.lock:
            self.state.update(fields)

    def missing(self) -> list[dict]:
        return [tool for tool in TOOLS if not all((self.folder / name).is_file() for name in tool["files"])]

    def start(self) -> None:
        """Gets the tools ready in the background. Also what "Try again" calls."""
        with self.lock:
            if self.state["ready"] or (self.thread and self.thread.is_alive()):
                return
            self.state.update(error=None, message="Starting…", progress=None)
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def run(self) -> bool:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            needed = self.missing()
            for number, tool in enumerate(needed, 1):
                self._install(tool, number, len(needed))
            if needed:
                self._set(message="Checking the tools…", progress=None)
                for tool in TOOLS:
                    self._check(tool)
            self._set(ready=True, message="Ready", progress=1.0, error=None)
            print(f"[tools] ready in {self.folder}")
        except Exception as error:
            traceback.print_exc()
            self._set(error=friendly(error), progress=None)
            return False
        self.update_yt_dlp()
        return True

    def _install(self, tool: dict, number: int, total: int) -> None:
        label = f"Downloading {tool['name']}" + (f" ({number} of {total})" if total > 1 else "")
        self._set(message=f"{label}…", progress=0.0)
        download = self.folder / f"{tool['name']}.download"

        def progress(done: int, size: int) -> None:
            if size:
                self._set(progress=done / size, message=f"{label} · {done / 1e6:.0f} of {size / 1e6:.0f} MB")
            else:
                self._set(progress=None, message=f"{label} · {done / 1e6:.0f} MB")

        print(f"[tools] downloading {tool['url']}")
        self._fetch(tool["url"], download, progress)
        if tool["url"].endswith(".zip"):
            self._set(message=f"Unpacking {tool['name']}…", progress=None)
            with zipfile.ZipFile(download) as archive:
                for member in archive.infolist():
                    name = PurePosixPath(member.filename).name
                    if member.is_dir() or not (name in tool["files"] or (tool.get("dlls") and name.lower().endswith(".dll"))):
                        continue
                    temporary = self.folder / f"{name}.new"
                    with archive.open(member) as source, open(temporary, "wb") as target:
                        shutil.copyfileobj(source, target)
                    os.replace(temporary, self.folder / name)
            download.unlink(missing_ok=True)
        else:
            os.replace(download, self.folder / tool["files"][0])
        absent = [name for name in tool["files"] if not (self.folder / name).is_file()]
        if absent:
            raise RuntimeError(f"The {tool['name']} download didn't contain {', '.join(absent)}.")

    def _fetch(self, url: str, target: Path, progress) -> None:
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(request, timeout=30) as response, open(target, "wb") as out:
                    size = int(response.headers.get("Content-Length") or 0)
                    done = 0
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        progress(done, size)
                if size and done != size:
                    raise OSError("the download was cut off")
                return
            except (OSError, http.client.HTTPException) as error:
                target.unlink(missing_ok=True)
                print(f"[tools] attempt {attempt + 1} failed: {error}")
                if attempt == 2:
                    raise
                time.sleep(3 * (attempt + 1))

    def _check(self, tool: dict) -> None:
        command = [str(self.folder / tool["check"][0]), *tool["check"][1:]]
        try:
            result = subprocess.run(command, capture_output=True, timeout=180, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"{tool['name']} doesn't start on this PC ({error}).") from error
        if result.returncode != 0:
            raise RuntimeError(f"{tool['name']} doesn't start on this PC (exit code {result.returncode}).")
        first_line = (result.stdout or b"").decode("utf-8", "replace").strip().splitlines()[:1]
        print(f"[tools] {tool['name']}: {first_line[0] if first_line else 'ok'}")

    def update_yt_dlp(self, force: bool = False) -> None:
        """yt-dlp -U, at most about once a day. YouTube changes often and yt-dlp keeps up."""
        stamp = self.folder / "yt-dlp-checked"
        try:
            last = stamp.stat().st_mtime
        except OSError:
            last = 0
        if not force and time.time() - last < UPDATE_EVERY:
            return
        try:
            result = subprocess.run([str(self.folder / "yt-dlp.exe"), "-U"], capture_output=True, timeout=300,
                                    creationflags=NO_WINDOW)
            output = (result.stdout + result.stderr).decode("utf-8", "replace").strip().splitlines()
            print(f"[tools] yt-dlp -U (exit {result.returncode}): {' | '.join(output[-3:])}")
            stamp.touch()
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"[tools] yt-dlp update check failed: {error}")


def friendly(error: Exception) -> str:
    if isinstance(error, RuntimeError):
        return str(error)
    if isinstance(error, PermissionError):
        return f"Windows didn't allow saving the tools ({error.filename}). Restart your PC, then click Try again."
    return ("Couldn't download the tools 4K YT Downloader needs. Check your internet connection, "
            f"then click Try again. ({error})")
