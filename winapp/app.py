"""4K YT Downloader for Windows: the download page in its own window.

It uses the same page and download code as the Mac app (ytdl_web.html, ytdl_web.py, ytdl.py).
This file adds the Windows parts:
  - the window: pywebview, which shows the page with the Edge WebView2 built into Windows;
  - the one-time download of yt-dlp, ffmpeg and deno, and yt-dlp's daily update (wintools.py);
  - the folder dialog and reading the clipboard, for the page's "Change" and "Paste";
  - closing: asks first while downloads run, and Windows stops them if the app is closed;
  - one copy at a time: opening it again brings the open window to the front.

build.py turns this into "4K YT Downloader.exe" (PyInstaller) and its installer (Inno Setup).
Run from source on Windows with:  python winapp/app.py   (needs pip install pywebview)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # ytdl.py and ytdl_web.py, when run from source

APP_NAME = "4K YT Downloader"
LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME
LOG_FILE = LOCAL / f"{APP_NAME}.log"
PID_FILE = LOCAL / "app.pid"
TOOLS_DIR = LOCAL / "tools"
WEBVIEW2_DOWNLOAD = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

window = None       # the pywebview window
api = None          # what the page calls (Api below)
close_now = False   # set once the person chose to close while downloads run


def open_log() -> None:
    """The app has no console, so everything printed goes to the log file."""
    LOCAL.mkdir(parents=True, exist_ok=True)
    try:
        if LOG_FILE.stat().st_size > 2_000_000:
            os.replace(LOG_FILE, LOG_FILE.with_suffix(".old.log"))
    except OSError:
        pass
    log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    print(f"\n--- {APP_NAME} started {time.strftime('%Y-%m-%d %H:%M:%S')} (pid {os.getpid()})")


def show_open_window() -> None:
    """Opened a second time: bring the first copy's window to the front instead."""
    import winsys
    try:
        hwnd = winsys.find_window(int(PID_FILE.read_text()), APP_NAME)
    except (OSError, ValueError):
        hwnd = None
    if hwnd:
        winsys.bring_to_front(hwnd)
    print("already open; brought it to the front" if hwnd else "already open (window not found)")


def on_ui_thread(function):
    """Runs function on the window's own thread (dialogs belong there) and returns its result."""
    form = window.native
    if form is None:
        return function()
    from System import Action
    result, failure = [], []

    def run():
        try:
            result.append(function())
        except Exception as error:  # noqa: BLE001 (handed back to the caller)
            failure.append(error)

    form.Invoke(Action(run))
    if failure:
        raise failure[0]
    return result[0] if result else None


class Api:
    """What the page can ask the app for (window.pywebview.api.* in ytdl_web.html)."""

    def __init__(self) -> None:
        self._last_clipboard = -1

    def choose_folder(self, current=None):
        import webview
        ask = lambda: window.create_file_dialog(webview.FileDialog.FOLDER, directory=current or "")  # noqa: E731
        try:
            chosen = on_ui_thread(ask)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            chosen = ask()
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        return str(chosen) if chosen else None

    def read_clipboard(self, auto=False):
        """auto: the check when the window comes to the front. Only reads when something new
        was copied, and only hands over links."""
        import winsys
        if auto:
            sequence = winsys.clipboard_sequence()
            if sequence == self._last_clipboard:
                return None
            self._last_clipboard = sequence
        text = winsys.clipboard_text()
        if auto and text and not text.strip().lower().startswith(("http://", "https://", "youtu", "www.youtu", "m.youtu")):
            return None
        return text

    def quit(self):
        """The page's "Close" button after asking about running downloads."""
        global close_now
        close_now = True
        threading.Timer(0.2, window.destroy).start()  # after this call has answered the page
        return True


def running_downloads() -> int:
    import ytdl_web
    with ytdl_web.lock:
        return sum(job["status"] in ytdl_web.ACTIVE for job in ytdl_web.jobs.values())


def on_closing():
    """Clicking X while downloads run asks first (in the page). Returning False keeps the window."""
    if close_now:
        return True
    running = running_downloads()
    if not running:
        return True
    threading.Thread(target=window.evaluate_js, args=(f"window.ytdlConfirmQuit({running})",), daemon=True).start()
    return False


activated_hooked = False


def on_loaded():
    global activated_hooked
    form = window.native
    if form is None:
        return
    if not activated_hooked:
        activated_hooked = True
        # Coming to the front: let the page fill in a YouTube link that was just copied.
        check = lambda: window.evaluate_js("void (window.ytdlAppActivated && window.ytdlAppActivated())")  # noqa: E731
        form.Activated += lambda sender, args: threading.Thread(target=check, daemon=True).start()

    def enable_right_click():
        # pywebview turns the right-click menu off; the page allows it only in text fields.
        form.browser.webview.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True

    try:
        on_ui_thread(enable_right_click)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def main() -> None:
    global window, api
    open_log()
    import winsys

    if not winsys.first_instance("Local\\4KYTDownloader.SingleInstance"):
        show_open_window()
        return
    PID_FILE.write_text(str(os.getpid()))

    if not winsys.webview2_version():
        print("WebView2 runtime missing")
        answer = winsys.message_box(
            f"{APP_NAME} needs the Microsoft Edge WebView2 Runtime, a free part of Windows that "
            "shows its window.\n\nClick OK to download it from Microsoft. Run it, then open "
            f"{APP_NAME} again.", APP_NAME, 0x41)  # OK/Cancel, information icon
        if answer == 1:
            os.startfile(WEBVIEW2_DOWNLOAD)
        return

    import ytdl
    import ytdl_web
    import wintools

    print(f"version {ytdl.VERSION}, Python {sys.version.split()[0]}, WebView2 {winsys.webview2_version()}")
    ytdl.hide_windows = True
    os.environ["PATH"] = str(TOOLS_DIR) + os.pathsep + os.environ.get("PATH", "")

    tools = wintools.Tools(TOOLS_DIR, user_agent=f"4K-YT-Downloader/{ytdl.VERSION} (Windows)")
    job = winsys.KillOnCloseJob()
    ytdl_web.setup_status = tools.status
    ytdl_web.setup_retry = tools.start
    ytdl_web.process_started = job.add
    tools.start()

    server = ytdl_web.create_server(app=True)
    port = server.server_address[1]
    ytdl_web.APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    ytdl_web.PORT_FILE.write_text(str(port))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"page at http://127.0.0.1:{port}/, saving to {ytdl_web.save_dir}")

    import webview

    # As big as the Mac window, but never taller than the screen above the taskbar (the footer
    # with the credit and version must stay visible on 768-pixel laptop screens).
    screen_width, screen_height = winsys.work_area()
    width, height = min(820, screen_width - 24), min(760, screen_height - 24)
    print(f"screen {screen_width}x{screen_height}, window {width}x{height}")
    api = Api()
    window = webview.create_window(
        APP_NAME, f"http://127.0.0.1:{port}/?app=windows", js_api=api,
        width=width, height=height, min_size=(min(560, width), min(480, height)), text_select=True,
        background_color="#121212" if winsys.dark_mode() else "#F5F4F1")
    window.events.closing += on_closing
    window.events.loaded += on_loaded

    selftest = os.environ.get("YTD_SELFTEST")
    webview.start(func=run_selftest if selftest else None, args=(Path(selftest), tools) if selftest else None,
                  gui="edgechromium", private_mode=False, storage_path=str(LOCAL / "WebView"))

    print("window closed")
    ytdl_web.stop_all()
    server.shutdown()
    for leftover in (ytdl_web.PORT_FILE, PID_FILE):
        try:
            leftover.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- self-test (CI only)
# With YTD_SELFTEST=<folder>, the app checks itself the way a person would use it, saves
# screenshots and results.json there, and then closes through its "downloads running" question.
# Run by winapp/tests/ci_test.py on GitHub's Windows machines.


def screenshot(path: Path, hwnd=None) -> None:
    import winsys
    from System.Drawing import Bitmap, Graphics, Size
    from System.Drawing.Imaging import ImageFormat
    from System.Windows.Forms import Screen

    if hwnd:
        left, top, right, bottom = winsys.window_rect(hwnd)
    else:
        bounds = Screen.PrimaryScreen.Bounds
        left, top, right, bottom = bounds.Left, bounds.Top, bounds.Right, bounds.Bottom
    bitmap = Bitmap(right - left, bottom - top)
    graphics = Graphics.FromImage(bitmap)
    graphics.CopyFromScreen(left, top, 0, 0, Size(right - left, bottom - top))
    bitmap.Save(str(path), ImageFormat.Png)
    graphics.Dispose()
    bitmap.Dispose()


PAGE_STATE = """(() => {
  const $ = (id) => document.getElementById(id);
  return {
    title: document.title, credit: $('credit').textContent.trim(), creditLink: $('credit').href,
    version: $('version').textContent, url: $('url').value, folder: $('folder').textContent,
    changeVisible: !$('change').hidden, setupVisible: !$('setup').hidden, setupText: $('setup').innerText,
    downloadDisabled: $('go').disabled, bodyClass: document.body.className, message: $('msg').textContent,
    bridge: !!(window.pywebview && window.pywebview.api), quitAsked: !$('quit').hidden,
    footerBottom: Math.round(document.querySelector('.credit').getBoundingClientRect().bottom),
    viewportHeight: window.innerHeight,
    quitText: $('quit-title').textContent, jobs: document.querySelectorAll('.job').length
  };
})()"""


def wait_for(condition, timeout: float, step: float = 0.5):
    end = time.time() + timeout
    while time.time() < end:
        value = condition()
        if value:
            return value
        time.sleep(step)
    return None


def run_selftest(out: Path, tools) -> None:
    import winsys
    import ytdl_web

    out.mkdir(parents=True, exist_ok=True)
    results = {"started": time.time(), "steps": []}

    def step(name, **details):
        results["steps"].append({"step": name, "t": round(time.time() - results["started"], 1), **details})
        print(f"[selftest] {name} {details}")

    def page():
        return window.evaluate_js(PAGE_STATE)

    def save():
        (out / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    try:
        step("loaded", ok=window.events.loaded.wait(90))
        hwnd = wait_for(lambda: winsys.find_window(os.getpid(), APP_NAME), 30)
        step("window", found=bool(hwnd))
        winsys.bring_to_front(hwnd)
        time.sleep(3)
        results["first_run_page"] = page()
        screenshot(out / "1-first-run.png", hwnd)

        ready = wait_for(lambda: tools.status()["ready"] or tools.status()["error"], 1200, 1)
        step("tools", status=tools.status(), seconds=round(time.time() - results["started"]))
        results["tools"] = tools.status()
        wait_for(lambda: not page()["setupVisible"], 15)
        time.sleep(2)
        results["page"] = page()
        results["window_rect"] = winsys.window_rect(hwnd)
        results["work_area"] = winsys.work_area()
        results["context_menu"] = on_ui_thread(
            lambda: bool(window.native.browser.webview.CoreWebView2.Settings.AreDefaultContextMenusEnabled))
        winsys.bring_to_front(hwnd)
        time.sleep(1)
        screenshot(out / "2-ready.png", hwnd)

        # Paste button: empties the field, clicks Paste, reads what came in.
        window.evaluate_js("document.getElementById('url').value = ''; document.getElementById('paste').click()")
        time.sleep(1.5)
        results["paste_button"] = page()["url"]
        step("paste button", value=results["paste_button"])

        # Folder dialog: opens it the way "Change" does, then cancels it like the Cancel button.
        picked = {}
        chooser = threading.Thread(target=lambda: picked.update(result=api.choose_folder(str(Path.home()))), daemon=True)
        chooser.start()
        dialog = wait_for(lambda: winsys.find_window(os.getpid(), class_name="#32770"), 15)
        step("folder dialog", opened=bool(dialog))
        if dialog:
            time.sleep(1.5)
            screenshot(out / "3-folder-dialog.png")
            winsys.close_window(dialog)
        chooser.join(15)
        results["folder_dialog"] = {"opened": bool(dialog), "returned": picked.get("result", "no answer"),
                                    "finished": not chooser.is_alive()}

        # Closing while a download runs: asks in the page first.
        with ytdl_web.lock:
            ytdl_web.jobs["selftest"] = {
                "id": "selftest", "url": "https://www.youtube.com/watch?v=selftest", "quality": "2160",
                "compat": True, "start": None, "end": None, "status": "downloading",
                "title": "Self-test download (not real)", "thumbnail": None, "details": "3840x2160 · 30 fps",
                "part": "video", "progress": 0.42, "speed": 12.5e6, "eta": 95, "files": [], "error": None,
                "created": time.time()}
        time.sleep(3.5)
        winsys.close_window(hwnd)
        asked = wait_for(lambda: page()["quitAsked"], 10)
        step("close asked first", asked=bool(asked))
        results["close_dialog"] = page()
        screenshot(out / "4-close-question.png", hwnd)
        results["finished"] = True
        save()
        window.evaluate_js("document.getElementById('quit-yes').click()")
    except Exception:  # noqa: BLE001
        results["error"] = traceback.format_exc()
        print(results["error"])
        save()
        global close_now
        close_now = True
        window.destroy()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        try:
            import winsys
            winsys.message_box(f"{APP_NAME} couldn't start.\n\nDetails are in:\n{LOG_FILE}", APP_NAME, 0x10)
        except Exception:  # noqa: BLE001
            pass
    finally:
        if sys.stdout:
            sys.stdout.flush()
        os._exit(0)  # don't wait for background threads (a tool download, the web view)
