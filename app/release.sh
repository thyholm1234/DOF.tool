#!/usr/bin/env bash
# Lav en GitHub-release med den byggede Android-APK og iOS-kildepakken fra dist/ (kræver `gh auth login`).
#
#   ./build.sh && ./release.sh            # tag app-v<dato>, fx app-v2026.10.26
#   ./release.sh "Noter til releasen"     # med egne udgivelsesnoter
#
# En release med samme tag erstatter filerne (--clobber). Tag og release oprettes på det nuværende commit på GitHub, så
# push først (git push), hvis commit'et ikke findes dér.
set -euo pipefail
cd "$(dirname "$0")"
command -v gh >/dev/null || { echo "gh mangler – hent den fra https://cli.github.com" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Log ind først: gh auth login" >&2; exit 1; }
for f in dist/DOF-tool.apk dist/DOF-tool-ios.zip; do [ -f "$f" ] || { echo "$f mangler – kør ./build.sh" >&2; exit 1; }; done

VERSION=${DOF_VERSION_NAME:-$(date +%Y.%m.%d)}
TAG="app-v$VERSION"
NOTES=${1:-"Android-APK (signeret) og iOS-kildepakke til Xcode. Appen indlæser portalen fra serveren, så ændringer i web-delen kommer med serveren, ikke med en ny app."}
cp dist/DOF-tool.apk "dist/DOF-tool-$VERSION.apk"
trap 'rm -f "dist/DOF-tool-$VERSION.apk"' EXIT

if gh release view "$TAG" >/dev/null 2>&1; then
  gh release upload "$TAG" "dist/DOF-tool-$VERSION.apk" dist/DOF-tool-ios.zip --clobber
else
  gh release create "$TAG" "dist/DOF-tool-$VERSION.apk" dist/DOF-tool-ios.zip --title "DOF.tool-app $VERSION" --notes "$NOTES" --target "$(git rev-parse HEAD)"
fi
gh release view "$TAG" --json url -q .url
