#!/bin/bash
# Builds "4K YT Downloader.dmg" into dist/ (version from the VERSION file).
#   bash macapp/build.sh
# To publish it for everyone, use scripts/release.sh instead.
# The app (YTDownloader.swift) shows the page in its own window and bundles
# tools/ytdl.py, ytdl_web.py and ytdl_web.html. It uses the Homebrew tools on
# the Mac it runs on (yt-dlp, ffmpeg, deno, python3).
# Runs on Apple Silicon (macOS 12+) and Intel (macOS 14+).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TOOLS="$(dirname "$HERE")"
DIST="$TOOLS/dist"
NAME="4K YT Downloader"
VERSION="$(tr -d '[:space:]' < "$TOOLS/VERSION")"
BUILD="$(mktemp -d)"
APP="$BUILD/$NAME.app"
trap 'rm -rf "$BUILD"' EXIT

echo "→ Compiling the app (Apple Silicon + Intel)"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/ytdl"
swiftc -swift-version 5 -O -target arm64-apple-macos12 -o "$BUILD/arm64" "$HERE/YTDownloader.swift"
# The Command Line Tools only ship Intel support libraries for macOS 14 and later.
swiftc -swift-version 5 -O -target x86_64-apple-macos14 -o "$BUILD/x86_64" "$HERE/YTDownloader.swift" 2>/dev/null
lipo -create -output "$APP/Contents/MacOS/$NAME" "$BUILD/arm64" "$BUILD/x86_64"

echo "→ Adding the downloader and icon"
cp "$TOOLS/ytdl.py" "$TOOLS/ytdl_web.py" "$TOOLS/ytdl_web.html" "$TOOLS/VERSION" "$APP/Contents/Resources/ytdl/"
cp "$HERE/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleExecutable</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>io.github.the-real-sidd.4k-yt-downloader</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSMinimumSystemVersionByArchitecture</key>
  <dict><key>arm64</key><string>12.0</string><key>x86_64</key><string>14.0</string></dict>
  <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
  <key>NSHumanReadableCopyright</key><string>Built by the.real.sidd. Uses yt-dlp and ffmpeg.</string>
</dict>
</plist>
PLIST
plutil -lint -s "$APP/Contents/Info.plist"

echo "→ Signing (ad-hoc, for this Mac)"
xattr -cr "$APP"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

echo "→ Building the disk image"
mkdir -p "$DIST" "$BUILD/dmg"
cp -R "$APP" "$BUILD/dmg/"
cp "$HERE/Read Me First.txt" "$BUILD/dmg/"
ln -s /Applications "$BUILD/dmg/Applications"
rm -rf "$DIST/$NAME.dmg"
hdiutil create -quiet -volname "$NAME" -srcfolder "$BUILD/dmg" -fs HFS+ -format UDZO -ov "$DIST/$NAME.dmg"

echo "✔ $DIST/$NAME.dmg"
