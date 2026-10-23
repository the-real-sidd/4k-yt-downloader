#!/bin/bash
# Publish a new version of 4K YT Downloader. Everyone's install command picks it up.
#
#   scripts/release.sh 3.1 "What changed, in a sentence or two"
#
# Commit your code changes first. This then: writes VERSION, builds the Mac app and disk image,
# commits + tags v3.1, pushes to GitHub and creates the release as a draft with the disk image.
# GitHub then builds and tests the Windows app (.github/workflows/windows.yml, about 15-20 min),
# adds its installer and publishes the release. If the Windows tests fail, the release stays a
# draft, so nobody gets a half-finished version. This script waits and reports the result.
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
gh release create "v$VERSION" "$ASSET" --repo "$REPO" --title "4K YT Downloader $VERSION" --notes "$NOTES" --draft

echo "→ GitHub is building and testing the Windows app (about 15-20 minutes). The release goes out when it passes."
RUN_ID=""
for attempt in $(seq 1 30); do
  RUN_ID="$(gh run list --repo "$REPO" --workflow windows.yml --branch "v$VERSION" --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || true)"
  [ -n "$RUN_ID" ] && break
  sleep 5
done
if [ -n "$RUN_ID" ] && gh run watch "$RUN_ID" --repo "$REPO" --exit-status --interval 30 >/dev/null; then
  echo "✔ Released v$VERSION for Mac and Windows: https://github.com/$REPO/releases/tag/v$VERSION"
else
  echo "✖ The Windows build or its tests didn't pass: https://github.com/$REPO/actions/runs/${RUN_ID:-}" >&2
  echo "  The release is still a draft, so nobody gets it yet. Fix the problem, then re-run that workflow" >&2
  echo "  (or delete the draft and the v$VERSION tag and release again)." >&2
  exit 1
fi
