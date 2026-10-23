#!/bin/bash
# 4K YT Downloader: install or update with one command (macOS).
#
#   curl -fsSL https://raw.githubusercontent.com/the-real-sidd/4k-yt-downloader/main/install.sh | bash
#
# What it does, every time it runs:
#   1. Makes sure the free tools are there (Homebrew, yt-dlp, ffmpeg, deno, python) and
#      updates yt-dlp, because YouTube changes often.
#   2. Downloads the latest release from GitHub.
#   3. Closes the app if it's open, replaces it in /Applications (and removes the old
#      "YT Downloader" app from before the rename).
#   4. Installs the `ytdl` terminal command, which runs the copy inside the app.
#   5. Opens the app.
# Run it again any time to update. Nothing has to be uninstalled by hand. Because the app is
# installed by this script (not downloaded in a browser), macOS doesn't block it on first open.
#
# Options (environment variables), mostly for testing:
#   YTD_VERSION=3.0        install that release instead of the latest
#   YTD_DMG=<path or url>  use this disk image instead of the GitHub release
#   YTD_INSTALL_DIR=<dir>  install somewhere other than /Applications
#   YTD_SKIP_TOOLS=1       don't install or update Homebrew tools
#   YTD_NO_OPEN=1          don't open the app at the end
#   YTD_FORCE=1            reinstall even if this version is already installed
set -euo pipefail

# Everything runs inside main(), called on the last line: with `curl … | bash`, bash reads the
# script from the same pipe the commands inherit as input, and a command that reads its input
# (Homebrew does, when it updates something) would swallow the rest of the script, which then
# silently stops. Wrapped in a function, the whole script is read before anything runs.
main() {

REPO="the-real-sidd/4k-yt-downloader"
APP="4K YT Downloader"
OLD_APPS=("YT Downloader")  # names this app had before; removed on update
ASSET="4K-YT-Downloader.dmg"
DEST="${YTD_INSTALL_DIR:-/Applications}"

step() { printf '\033[1;31m→\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m✔\033[0m %s\n' "$*"; }
note() { printf '  %s\n' "$*"; }
die() { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || die "$APP is for macOS."
OS_VERSION="$(sw_vers -productVersion)"
OS_MAJOR="${OS_VERSION%%.*}"
if [ "$(uname -m)" = arm64 ]; then
  [ "$OS_MAJOR" -ge 12 ] || die "$APP needs macOS 12 or newer (this Mac has $OS_VERSION)."
else
  [ "$OS_MAJOR" -ge 14 ] || die "On Intel Macs, $APP needs macOS 14 or newer (this Mac has $OS_VERSION)."
fi

TMP="$(mktemp -d)"
MNT="$TMP/mnt"
cleanup() {
  hdiutil detach -quiet "$MNT" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

printf '\n\033[1m%s installer\033[0m\n\n' "$APP"

# ---------------------------------------------------------------- 1. free tools
if [ -z "${YTD_SKIP_TOOLS:-}" ]; then
  find_brew() {
    for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      [ -x "$candidate" ] && { echo "$candidate"; return; }
    done
  }
  BREW="$(find_brew)"
  if [ -z "$BREW" ]; then
    step "Installing Homebrew, the free tool installer. It asks for your Mac password."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" < /dev/tty
    BREW="$(find_brew)"
    [ -n "$BREW" ] || die "Homebrew didn't install. Install it from https://brew.sh, then run this command again."
    # So `brew` also works in new Terminal windows.
    grep -qs "brew shellenv" "$HOME/.zprofile" || echo "eval \"\$($BREW shellenv)\"" >> "$HOME/.zprofile"
  fi
  eval "$("$BREW" shellenv < /dev/null)"

  missing=()
  for pair in yt-dlp:yt-dlp ffmpeg:ffmpeg ffprobe:ffmpeg deno:deno python3:python; do
    tool="${pair%%:*}"
    formula="${pair##*:}"
    if ! command -v "$tool" >/dev/null 2>&1; then
      case " ${missing[*]-} " in *" $formula "*) ;; *) missing+=("$formula") ;; esac
    fi
  done
  if [ ${#missing[@]} -gt 0 ]; then
    step "Installing ${missing[*]} (this can take a few minutes the first time)"
    "$BREW" install "${missing[@]}" < /dev/null
  fi
  step "Updating yt-dlp (YouTube changes often)"
  "$BREW" upgrade yt-dlp < /dev/null >/dev/null 2>&1 || true
  ok "Tools ready: yt-dlp $(yt-dlp --version < /dev/null 2>/dev/null || echo '?')"
fi

# ---------------------------------------------------------------- 2. the app
if [ -n "${YTD_DMG:-}" ]; then
  case "$YTD_DMG" in
    http://* | https://*) curl -fL --progress-bar -o "$TMP/$ASSET" "$YTD_DMG" || die "Couldn't download $YTD_DMG" ;;
    *) cp "$YTD_DMG" "$TMP/$ASSET" ;;
  esac
else
  if [ -n "${YTD_VERSION:-}" ]; then
    URL="https://github.com/$REPO/releases/download/v${YTD_VERSION#v}/$ASSET"
  else
    URL="https://github.com/$REPO/releases/latest/download/$ASSET"
  fi
  step "Downloading $APP"
  curl -fL --progress-bar -o "$TMP/$ASSET" "$URL" || die "Couldn't download $URL. Check your internet connection and try again."
fi

hdiutil attach -quiet -nobrowse -readonly -noautoopen -mountpoint "$MNT" "$TMP/$ASSET" < /dev/null \
  || die "The download looks damaged. Run the command again."
[ -d "$MNT/$APP.app" ] || die "The download doesn't contain $APP.app."
plist_version() { /usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$1/Contents/Info.plist" 2>/dev/null || true; }
NEW_VERSION="$(plist_version "$MNT/$APP.app")"

mkdir -p "$DEST" 2>/dev/null || true
if [ ! -w "$DEST" ]; then
  note "$DEST isn't writable for this user, so the app goes in ~/Applications instead."
  DEST="$HOME/Applications"
  mkdir -p "$DEST"
fi
OLD_VERSION=""
[ -d "$DEST/$APP.app" ] && OLD_VERSION="$(plist_version "$DEST/$APP.app")"

if [ "$OLD_VERSION" = "$NEW_VERSION" ] && [ -z "${YTD_FORCE:-}" ]; then
  ok "$APP $NEW_VERSION is already installed and up to date."
else
  # Close the copy being replaced (and its older name), only when it runs from $DEST, so
  # another copy elsewhere is left alone. Downloads in progress stop.
  for name in "$APP" "${OLD_APPS[@]}"; do
    running="$DEST/$name.app/Contents/MacOS/"
    if pgrep -f "$running" >/dev/null 2>&1; then
      step "Closing $name to update it"
      pkill -f "$running" || true
    fi
  done
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -f "$DEST/$APP.app/Contents/MacOS/" >/dev/null 2>&1 || break
    sleep 0.5
  done

  step "Installing $APP $NEW_VERSION${OLD_VERSION:+ (replacing $OLD_VERSION)}"
  rm -rf "$DEST/$APP.app.updating"
  ditto "$MNT/$APP.app" "$DEST/$APP.app.updating"
  rm -rf "$DEST/$APP.app"
  mv "$DEST/$APP.app.updating" "$DEST/$APP.app"
  xattr -dr com.apple.quarantine "$DEST/$APP.app" 2>/dev/null || true
  ok "Installed in $DEST"
fi

for name in "${OLD_APPS[@]}"; do
  if [ -d "$DEST/$name.app" ]; then
    rm -rf "$DEST/$name.app"
    ok "Removed the old \"$name\" app (it's now called $APP)"
  fi
done

# ---------------------------------------------------------------- 3. the ytdl command
BIN="$HOME/.local/bin"
TARGET="$DEST/$APP.app/Contents/Resources/ytdl/ytdl.py"
mkdir -p "$BIN"
CURRENT="$(readlink "$BIN/ytdl" 2>/dev/null || true)"
if [ -e "$BIN/ytdl" ] && [ -z "$CURRENT" ]; then
  note "Left your own $BIN/ytdl file alone."
elif [ -z "$CURRENT" ] || [[ "$CURRENT" == *".app/Contents/Resources/ytdl/ytdl.py" ]]; then
  ln -sf "$TARGET" "$BIN/ytdl"
  grep -qs '.local/bin' "$HOME/.zshrc" || printf '\n# 4K YT Downloader: the ytdl command\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"
  ok "Terminal command: ytdl  (open a new Terminal window to use it)"
else
  note "Kept your developer ytdl command ($CURRENT)."
fi

# ---------------------------------------------------------------- 4. open it
if [ -z "${YTD_NO_OPEN:-}" ]; then
  open "$DEST/$APP.app"
fi
printf '\n\033[1;32m%s %s is ready.\033[0m Run the same command again any time to update.\n\n' "$APP" "$NEW_VERSION"

}

main "$@"
