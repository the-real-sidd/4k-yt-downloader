# 4K YT Downloader

A Mac and Windows app (plus a `ytdl` terminal command on the Mac) for saving YouTube videos (up to
4K, with sound), audio only, or just part of a video. It wraps **yt-dlp** and **ffmpeg** and has a
friendly UI. It's built and maintained by **Siddhant (the.real.sidd)** and shared with friends through
one-command installers.

- **Repo:** https://github.com/the-real-sidd/4k-yt-downloader (public, owned by the personal account `the-real-sidd`)
- **Mac, install or update:** `curl -fsSL https://raw.githubusercontent.com/the-real-sidd/4k-yt-downloader/main/install.sh | bash`
- **Windows, install or update:** `irm https://raw.githubusercontent.com/the-real-sidd/4k-yt-downloader/main/install.ps1 | iex` (PowerShell),
  or the installer link https://github.com/the-real-sidd/4k-yt-downloader/releases/latest/download/4K-YT-Downloader-Setup.exe

## Working on this project

- The maintainer is a beginner developer and a video creator. Explain things in plain words, not jargon. He tests the app himself and reports issues in everyday terms (e.g. "QuickTime shows 15 minutes").
- **Free tools only.** No paid APIs or services unless he asks.
- **Ask before** anything outward-facing: pushing, creating releases, publishing, or downloading files (say the file, source and size). A release reaches every user through the install command.
- **Publish only as `the-real-sidd`.** Nothing goes to the GoGrowth account. `scripts/release.sh` uses `GH_TOKEN="$(gh auth token --user the-real-sidd)"`, so it works even when another GitHub CLI account is active. Personal machine and account notes live in `CLAUDE.local.md` (not in git).
- **Nobody on the project has a Windows PC.** The Windows app is built and tested only on GitHub Actions (see "Windows app" below). Read a run's results and screenshots before telling him something works on Windows.

## How it fits together

```
ytdl.py          CLI + the shared download logic (yt-dlp command, H.264 conversion, clip handling)
ytdl_web.py      local HTTP server (127.0.0.1:8765+) used by both the browser page and the Mac app;
                 runs downloads as jobs, reports progress, imports ytdl.py
ytdl_web.html    the UI (single file, no build step). Works in a browser (ytdl --web) and inside both apps
macapp/
  YTDownloader.swift   native AppKit app: window with a WKWebView showing the page, menus, folder
                       panel, clipboard reading, quit check. Starts ytdl_web.py --app --no-open
  build.sh             compiles a universal binary, assembles the .app, ad-hoc signs, makes the DMG
  Read Me First.txt    shipped inside the DMG
  AppIcon.icns/.png, make_icon.py (draws the icon with Pillow)
winapp/
  app.py               Windows app: pywebview window (Edge WebView2) showing the page, runs the server in a thread
  wintools.py          first-run download of yt-dlp, ffmpeg (+ffprobe, DLLs) and deno; daily yt-dlp -U
  winsys.py            ctypes helpers: single instance, clipboard, kill-on-close job object, WebView2/dark checks
  build.py             PyInstaller (one folder) + Inno Setup → dist/4K-YT-Downloader-Setup.exe (Windows only)
  installer.iss        Inno Setup script (per-user install, no admin; AppId must never change)
  requirements.txt     pinned pywebview / pythonnet / PyInstaller
  tests/ci_test.py     the Windows tests GitHub Actions runs (library + installed app)
  AppIcon.ico, make_ico.py (crops macapp/AppIcon.png)
install.sh       the Mac one-command installer/updater (see below)
install.ps1      the Windows one-command installer/updater (irm … | iex)
.github/workflows/windows.yml   builds + tests Windows on every push; publishes tagged releases
scripts/release.sh   publishes a version for both (see below)
VERSION          the single source of the version number (build.sh/build.py read it; shipped next to ytdl.py)
dist/, build/, ci-output/   build and test output (gitignored)
```

The page footer shows "Built with ❤️ by **the.real.sidd**" (left, links to https://www.instagram.com/the.real.sidd/)
and "Version X" (right, from `/api/state` → `ytdl.VERSION`, read from the VERSION file). Links with
`target=_blank` open in the default browser in both apps.

### The app (macapp/YTDownloader.swift)
- On launch, it finds `python3`, `yt-dlp`, `ffmpeg`, `ffprobe` and `deno` in `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin` and `/bin`. Apps opened from Finder don't inherit Homebrew's PATH, so it checks these folders directly. If any are missing, it shows an alert with "Copy Command" (`brew install …`) and quits.
- It runs `python3 Resources/ytdl/ytdl_web.py --app --no-open --watch <app pid>` with `PYTHONDONTWRITEBYTECODE=1`, so Python never writes into the signed bundle.
  - It reads the server's `PORT <n>` stdout line, then loads `http://127.0.0.1:<n>/` in the web view.
  - Server output goes to `~/Library/Logs/4K YT Downloader.log`.
- **The page talks to the app** through `window.webkit.messageHandlers.ytdl.postMessage(...)`:
  - `{action: "chooseFolder", current}` opens an NSOpenPanel. The result comes back via `window.ytdlFolderChosen(path)`, and the page then POSTs `/api/folder`.
  - `{action: "readClipboard", id, auto}` reads the clipboard. The result comes back via `window.ytdlClipboard(id, text, denied)`.
- **Clipboard on macOS 15.4+:** reading the clipboard without a user paste can trigger an "Allow Paste?" prompt.
  - The *automatic* check (run when the app becomes active, via `window.ytdlAppActivated`) only reads when `changeCount` changed and `detectPatterns` says the content is probably a web URL.
  - The Paste button reads directly.
  - The Edit menu exists so ⌘V works in the web view.
- **Quitting:** closing the window quits. If downloads are active, it asks first.
  - `applicationWillTerminate` terminates the server.
  - The server also watches the app's PID and stops by itself after a Force Quit.
- Only the local page may load in the web view. Any other URL opens in the default browser.

### The server (ytdl_web.py)
- **Endpoints:**
  - `GET /api/state`: jobs, folder, and `canChange` (true in app mode).
  - `POST /api/download {urls, quality, compat, start, end}`
  - `POST /api/cancel`, `/api/reveal` (`open -R`), `/api/remove`, and `/api/folder {path}` (app mode only).
- **Security:** it only answers when `Host` is `127.0.0.1:<port>` or `localhost:<port>`. POSTs must be `application/json` with no foreign `Origin`. This blocks other websites and DNS rebinding.
- **Jobs** run yt-dlp in their own process group (`start_new_session=True`), so Cancel kills yt-dlp together with its ffmpeg (`os.killpg`).
  - Progress comes from a `--progress-template` line (`PROG …`). For clips it comes from ffmpeg's `-progress pipe:1` (`out_time_us=`), passed via `--downloader-args ffmpeg_o:`.
  - Metadata comes from `--print before_dl:META {json}`.
  - Saved files come from `--print-to-file after_move:%(.{filepath,duration})j`, read with `ytdl.read_saved()`.
- **App mode** (`--app`):
  - The folder is stored in `~/Library/Application Support/4K YT Downloader/settings.json`. It falls back to the pre-3.0 `…/YT Downloader/settings.json`, so the folder survives the rename.
  - The default folder is `~/Downloads`.
  - It writes a `port` file there.

### The Windows app (winapp/)
- **One process:** `app.py` imports `ytdl_web`, runs `create_server(app=True)` in a thread and shows
  `http://127.0.0.1:<port>/?app=windows` in a pywebview window (`gui="edgechromium"`, `private_mode=False`,
  storage in `%LOCALAPPDATA%\4K YT Downloader\WebView`). The page detects `?app=windows`.
- **Page ↔ app:** `window.pywebview.api.choose_folder(current)`, `.read_clipboard(auto)`, `.quit()`
  (available after the `pywebviewready` event). The folder dialog is opened on the window's UI thread.
  The app calls `window.ytdlAppActivated()` when its window is activated, and `window.ytdlConfirmQuit(n)`
  instead of closing while downloads run (an in-page dialog; the Mac app uses a native sheet).
- **Tools:** downloaded on first launch from their official GitHub releases into
  `%LOCALAPPDATA%\4K YT Downloader\tools` (yt-dlp.exe; yt-dlp/FFmpeg-Builds `ffmpeg-master-latest-win64-gpl-shared.zip`;
  deno). The server's `/api/state` has `setup: {ready, message, progress, error}`; the page shows a banner and
  blocks Download until ready; "Try again" POSTs `/api/setup`. `yt-dlp -U` runs at most every 20 h.
  Not bundled in the installer, so the installer stays small and ffmpeg (GPL) isn't redistributed.
- **Downloads:** each yt-dlp runs with `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` and UTF-8 output, and is
  added to a kill-on-close job object, so closing the app any way (even End task) stops it. Cancel uses
  `taskkill /T /F`. H.264 conversion tries nvenc → qsv → amf, then libx264 (`ytdl.h264_encoder()`).
- **Other:** settings in `%APPDATA%\4K YT Downloader\settings.json` (default folder = Windows' Downloads known
  folder); log in `%LOCALAPPDATA%\4K YT Downloader\4K YT Downloader.log`; one copy at a time (named mutex; the
  second copy brings the first window forward); right-click menu only in text fields; WebView2 missing → a
  message box with Microsoft's download link.
- **Installer:** Inno Setup, per user (`%LOCALAPPDATA%\Programs\4K YT Downloader`), Start menu + desktop
  shortcut, closes the running app on update, uninstall removes the tools/log/settings. `install.ps1` downloads
  the latest Setup.exe (skips if that version is installed, via the uninstall registry key), runs it with
  `/VERYSILENT`, opens the app. Downloads made by PowerShell carry no "mark of the web", so no SmartScreen
  warning; a browser-downloaded Setup.exe shows "Windows protected your PC" (More info → Run anyway) because
  it isn't code-signed.
- **Windows CLI:** not shipped (the app has no console). `ytdl.py` itself runs on Windows with Python.

### Download logic (ytdl.py)
- **Format choice:** `-f "bv*[height<=CAP]+ba/b[height<=CAP]/bv*+ba/b"` and `-S "res:CAP,fps,hdr:sdr,vcodec:<c>,acodec:m4a"`, merging to mp4.
  - Without "compat", `<c>` is `vp9`: the M1 decodes VP9 in hardware, not AV1.
  - With "compat" (H.264, on by default in the UI), `<c>` is `h264`, so up to 1080p YouTube's own H.264 is used and nothing is converted.
  - 1440p and 4K are re-encoded with `h264_videotoolbox` (bitrates in `COMPAT_BITRATES`).
- **YouTube** needs `deno` (yt-dlp's JS runtime) for full-quality formats.
- **Clips** (`--from/--to`, or the page's "Download only part of the video"):
  - `--download-sections "*START-END" --force-overwrites --no-embed-chapters`, with output name `Title [1m30s-2m45s].mp4`.
  - Then **`finalize_clip()`**. A yt-dlp clip carried the *whole video's chapters* as a chapter/text track, so QuickTime showed the full length (e.g. 15:00) and stopped playing after the clip. Some sources also keep the whole timeline.
  - `finalize_clip` rewrites the file (stream copy) without chapter or data tracks, starting at 0 and no longer than the clip. Hidden lead-in frames stay hidden, because the edit list is preserved; don't add `-avoid_negative_ts make_zero`, which exposes them.
  - With compat on, clips are then re-encoded (`convert_to_h264(force=True)`).
  - Measured in QuickTime (AVFoundation), clips land within a few frames of the selection.
- **The CLI** uses `parse_intermixed_args()`, so `ytdl <folder> --audio <link>` works. Non-link words are joined into the folder name, so `ytdl Elevator Goods` works without quotes.

## Build, release, install

- **Build the Mac app locally:** `bash macapp/build.sh` → `dist/4K YT Downloader.dmg`.
  - Universal: arm64 for macOS 12+, x86_64 for macOS 14+. The Command Line Tools only ship Intel support libraries for macOS 14 and later.
  - The app is ad-hoc signed. The bundle ID is `io.github.the-real-sidd.4k-yt-downloader`, and the version comes from `VERSION`.
- **The Windows app is built only on GitHub** (`.github/workflows/windows.yml`, windows-2025, Python 3.13): every
  push builds it, runs `winapp/tests/ci_test.py library` and `… app`, and uploads `windows-test-results`
  (results JSON, app logs, self-test screenshots) and `windows-installer` as artifacts.
- **Publish (both platforms):** commit your changes on main, then run `scripts/release.sh 3.1 "what changed"`. It writes
  `VERSION`, builds the DMG, commits, tags `v3.1`, pushes, and creates the release **as a draft** with the DMG (as
  the-real-sidd). The tag's Windows workflow builds and tests, uploads `4K-YT-Downloader-Setup.exe` to the draft and
  publishes it. release.sh waits for that run. If the Windows tests fail, the release stays a draft: nobody gets
  it (the Mac installer uses `releases/latest`, which ignores drafts). Everyone updates by re-running their
  install command (or, on Windows, downloading the installer again).
- **`install.sh`** (what users run):
  1. Installs Homebrew if missing (reading the password prompt from `/dev/tty`), installs any missing tools (yt-dlp, ffmpeg, deno, python), and always upgrades yt-dlp.
  2. Downloads `releases/latest/download/4K-YT-Downloader.dmg`, mounts it and reads its version. If the same version is already installed, it skips reinstalling.
  3. Otherwise it closes the app (`pkill -x`), installs via a staged `ditto` and `mv`, and removes the old "YT Downloader.app".
  4. Links `~/.local/bin/ytdl` to the app's bundled `ytdl.py` and opens the app.
  - It's written for macOS's bash 3.2 (`curl | bash` may run /bin/bash), so avoid bash 4+ features and empty-array expansions under `set -u`.
  - Test overrides: `YTD_DMG` (a local path or URL), `YTD_INSTALL_DIR`, `YTD_SKIP_TOOLS`, `YTD_NO_OPEN`, `YTD_FORCE`, `YTD_VERSION`.
  - Apps installed by curl get no quarantine flag, so **Gatekeeper doesn't block them**. That fixes the "Apple could not verify…" problem friends hit with the DMG.
- **Not notarized** (that needs a $99/yr Apple Developer account). DMG users must use Privacy & Security → Open Anyway, or `xattr -dr com.apple.quarantine`.

## Testing (what has worked)

- **Windows:** push a branch to the-real-sidd and read the run (`GH_TOKEN=… gh run watch <id>`, then
  `gh run download <id> -n windows-test-results`). The app's self-test (`YTD_SELFTEST=<folder>`, see
  `run_selftest` in app.py) checks the page, footer, clipboard auto-fill, Paste, the folder dialog, right-click and
  the close question, and saves screenshots. Test videos are generated with ffmpeg and served locally; a real
  YouTube clip is tried for information only (GitHub's machines are often blocked by YouTube).
- **The same download checks on the Mac:** import `winapp/tests/ci_test.py` helpers (`make_media`, `serve_media`,
  `call`, `wait_job`, `probe`) in a scratch script; point `ytdl_web.APP_SUPPORT`/`SETTINGS_FILE` at a scratch
  folder first so his real settings aren't touched.

- **Python:** compile and import on both Homebrew python3 (3.15) and `/usr/bin/python3` (3.9). Keep `from __future__ import annotations`, since 3.9 must work.
- **Time parsing:** `ytdl.parse_time` accepts `1:30`, `1:02:45`, `90`, `1m30s` and `2:45.5`, and rejects `1:75`.
- **Server without the app:** a scratch harness imports `ytdl_web`, stubs out `open -a`, and calls `serve(folder=…)`. Drive it with `curl`/`urllib` against `/api/*`.
- **Clips without YouTube:** serve a local mp4 from a **range-capable** HTTP server. Python's plain `http.server` doesn't support Range, which breaks section downloads and gives misleading results.
- **Measure clip length the way QuickTime does:** a tiny Swift script using `AVURLAsset.load(.duration)` and the tracks' time ranges. ffprobe's format duration can ignore the chapter/data track that QuickTime counts.
- **Swift:** `swiftc -swift-version 5 -O -target arm64-apple-macos12 …` and `-target x86_64-apple-macos14`.
- **Installer:** run with `YTD_DMG=dist/4K\ YT\ Downloader.dmg YTD_INSTALL_DIR=/tmp/x YTD_SKIP_TOOLS=1 YTD_NO_OPEN=1 /bin/bash install.sh`. Also test under `/bin/bash`.
- Real YouTube test downloads need Siddhant's OK. Big Buck Bunny (`aqz-KE-bpKQ`, CC-BY) has been used before.

## History

- **1.x (2026-10-19):** `ytdl` CLI, then the `ytdl --web` page in Chrome, then folder arguments.
- **2.0:** native Swift window instead of Chrome. 2.1 added the footer credit and the DMG Read Me.
- **2.2 (2026-10-23, built by Siddhant):** clipboard auto-fill + Paste button (macOS 15.4 aware), H.264 on by default ("Works in every app"), part-of-video downloads.
- **3.0 (2026-10-23):**
  - Renamed to **4K YT Downloader**.
  - Published on GitHub with the one-command installer/updater and `scripts/release.sh`.
  - Fixed clips showing the full video length in QuickTime (chapters → `--no-embed-chapters` + `finalize_clip`).
  - Fixed `ytdl <folder> --options <link>` argument order.

- **3.1 (2026-10-23):** Windows app (installer + `install.ps1`), built and tested on GitHub Actions. Footer credit
  links to his Instagram; version shown on the right. Audio-only falls back to the combined file on sites without
  separate audio.

## Open ideas / known gaps

- In-app update check (e.g. a "New version available" banner comparing `CFBundleShortVersionString` with the latest GitHub release) that runs the install command.
- For non-YouTube links whose length yt-dlp doesn't know, a start time past the end isn't reported as an error.
- No licence file yet, so the code is "all rights reserved" by default. Ask Siddhant if he wants MIT or similar.
- Notarization would remove the DMG Gatekeeper step but costs $99/yr. A Windows code-signing certificate
  (~$100–300/yr) would remove the SmartScreen warning for the downloaded installer.
- Windows: no `ytdl` command, no in-app update banner (people re-run the installer or the command).
