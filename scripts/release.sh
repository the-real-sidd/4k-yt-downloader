#!/bin/bash
# Publish a new version of 4K YT Downloader. Everyone's install command picks it up.
#
#   scripts/release.sh 3.1 "What changed, in a sentence or two"
#
# Commit your code changes first. This then: writes VERSION, builds the app and disk image,
# commits + tags v3.1, pushes to GitHub, and creates the GitHub release with the disk image.
# Publishes as the personal account (the-real-sidd) even if another gh account is active.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${1:?Usage: scripts/release.sh <version> \"what changed\"}"
VERSION="${VERSION#v}"
NOTES="${2:-Version $VERSION}"
REPO="the-real-sidd/4k-yt-downloader"
ASSET="dist/4K-YT-Downloader.dmg"

if [ -n "$(git status --porcelain)" ]; then
  git status --short
  echo "✖ Commit (or stash) the changes above first, then run this again." >&2
  exit 1
fi
if git rev-parse "v$VERSION" >/dev/null 2>&1; then
  echo "✖ v$VERSION already exists. Pick a higher version number." >&2
  exit 1
fi
export GH_TOKEN="$(gh auth token --user the-real-sidd)"

echo "$VERSION" > VERSION
bash macapp/build.sh
cp "dist/4K YT Downloader.dmg" "$ASSET"

git add VERSION
git commit -m "Release v$VERSION"
git tag "v$VERSION"
git push origin HEAD
git push origin "v$VERSION"
gh release create "v$VERSION" "$ASSET" --repo "$REPO" --title "4K YT Downloader $VERSION" --notes "$NOTES"
echo "✔ Released v$VERSION: https://github.com/$REPO/releases/tag/v$VERSION"
