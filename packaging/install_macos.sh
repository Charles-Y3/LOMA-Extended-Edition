#!/bin/bash
# One-command installer for LOMA Extended Edition on macOS (Apple Silicon).
#
#   curl -fsSL https://raw.githubusercontent.com/Charles-Y3/LOMA-Extended-Edition/main/packaging/install_macos.sh | bash
#
# Why this exists: a browser download gets macOS's "quarantine" flag, and because the app
# isn't notarized by Apple, Gatekeeper then blocks it ("unidentified developer" / "damaged").
# Files fetched with curl are never quarantined, so the app opens with no prompt and no
# manual `xattr` step.
set -euo pipefail

REPO="Charles-Y3/LOMA-Extended-Edition"
APP_NAME="LOMA Extended Edition.app"
DEST="${LOMA_INSTALL_DIR:-$HOME/Applications}"

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "This installer is for Apple Silicon Macs (M1/M2/M3/M4)." >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading LOMA Extended Edition (about 1 GB)..."
curl -fL --progress-bar \
  "https://github.com/$REPO/releases/latest/download/LOMA-Extended-Edition-macOS.zip" \
  -o "$TMP/loma.zip"

echo "Installing to $DEST ..."
ditto -x -k "$TMP/loma.zip" "$TMP/unzipped"
mkdir -p "$DEST"
rm -rf "$DEST/$APP_NAME"
mv "$TMP/unzipped/LOMA-release/$APP_NAME" "$DEST/"

# Keep the licence, readme and user guide next to the app.
DOCS="$DEST/LOMA Extended Edition Docs"
mkdir -p "$DOCS"
find "$TMP/unzipped/LOMA-release" -maxdepth 1 -type f -exec mv {} "$DOCS/" \;

# Belt and braces: a file curl wrote has no quarantine flag, but clear any that exists.
xattr -cr "$DEST/$APP_NAME" 2>/dev/null || true

echo "Done: $DEST/$APP_NAME"
if [ -z "${LOMA_NO_OPEN:-}" ]; then
  open "$DEST/$APP_NAME"
fi
