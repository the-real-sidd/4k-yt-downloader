# 4K YT Downloader

A small app for **Mac and Windows** that saves YouTube videos in the best quality (up to 4K, with
sound), just the audio, or only part of a video. Paste a link, press Enter, done.

- 4K, 1440p, 1080p, 720p or audio only
- "Works in every app (H.264)": files open in QuickTime, Premiere, Final Cut, iMovie, CapCut, Windows and phones
- Download only part of a video (From 1:30 To 2:45)
- Copy a link on YouTube, switch to the app: it's already filled in
- Also a `ytdl` command for Terminal (Mac)

## Install or update on a Mac (one command)

Open **Terminal** (⌘ Space, type Terminal, Return), paste this and press Return:

```bash
curl -fsSL https://raw.githubusercontent.com/the-real-sidd/4k-yt-downloader/main/install.sh | bash
```

It installs everything it needs (Homebrew, yt-dlp, ffmpeg, deno, Python), installs the app in
Applications and opens it. **Run the same command any time to update.** It closes the app,
replaces it and keeps your settings, so there's nothing to uninstall by hand.

Needs a Mac with Apple Silicon (macOS 12+) or Intel (macOS 14+). The first install of the free
tools asks for your Mac password and can take a few minutes.

Prefer a disk image? Download `4K-YT-Downloader.dmg` from
[Releases](https://github.com/the-real-sidd/4k-yt-downloader/releases/latest) and follow
"Read Me First" inside it.

## Install or update on Windows 10 or 11

**Download the installer:**
[4K-YT-Downloader-Setup.exe](https://github.com/the-real-sidd/4k-yt-downloader/releases/latest/download/4K-YT-Downloader-Setup.exe)
and open it. Windows may say **"Windows protected your PC"** because the app isn't signed with a
paid certificate: click **More info**, then **Run anyway**. No administrator password needed.

**Or with one command** (no warning, and it updates too): open **PowerShell** (Start menu, type
PowerShell, Enter), paste this and press Enter:

```powershell
irm https://raw.githubusercontent.com/the-real-sidd/4k-yt-downloader/main/install.ps1 | iex
```

The first time the app opens, it downloads the free tools it uses (yt-dlp, ffmpeg, deno; about
150 MB, once) and shows the progress. **To update**, run the command again or download the
installer again; your settings stay. yt-dlp keeps itself up to date. To remove the app, use
Settings › Apps.

## Terminal command

```bash
ytdl                                 # asks for a link, saves to the current folder
ytdl ~/Movies <link>                 # into a folder
ytdl --from 1:30 --to 2:45 <link>    # only part of the video
ytdl --audio <link>                  # audio only (.m4a)
ytdl --compat <link>                 # H.264 that opens everywhere
ytdl --web                           # the same page in your browser
```

## If something goes wrong

- Downloads suddenly fail: YouTube changed something. On a Mac, run the install command again; it
  updates yt-dlp. On Windows, close the app and open it again (it updates yt-dlp once a day).
- Details are in `~/Library/Logs/4K YT Downloader.log` (Mac) or
  `%LOCALAPPDATA%\4K YT Downloader\4K YT Downloader.log` (Windows).

Only download videos you have the right to use.

---

Built with ❤️ by [the.real.sidd](https://www.instagram.com/the.real.sidd/). Uses
[yt-dlp](https://github.com/yt-dlp/yt-dlp) and [FFmpeg](https://ffmpeg.org).

## For developers

- `ytdl.py`: the downloader and the `ytdl` command. `ytdl_web.py` + `ytdl_web.html`: the local page.
- `macapp/`: the native Mac app (Swift) that shows the page in its own window.
- `winapp/`: the Windows app (Python + pywebview, packaged with PyInstaller and Inno Setup).
  GitHub builds and tests it on every push (`.github/workflows/windows.yml`).
- Build the Mac app: `bash macapp/build.sh` → `dist/4K YT Downloader.dmg`.
- Publish a version for both: `scripts/release.sh 3.1 "what changed"`.
