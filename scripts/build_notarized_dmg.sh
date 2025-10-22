#!/usr/bin/env bash
set -euo pipefail

# Build + sign + notarize + staple a macOS DMG for distribution.
# Prereqs:
# - Xcode Command Line Tools (xcode-select --install)
# - Developer ID Application certificate in your login keychain
# - notarytool credentials profile (once):
#   xcrun notarytool store-credentials "TRANSCRIBE_NOTARY" \
#     --apple-id YOUR_APPLE_ID --team-id YOUR_TEAM_ID --password APP_SPECIFIC_PASSWORD
# - Python deps installed (pyinstaller)
#
# Usage:
#   CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
#   NOTARY_PROFILE="TRANSCRIBE_NOTARY" \
#   ./scripts/build_notarized_dmg.sh

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Transcription Editor"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/build"
APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
DMG_DIR="$ROOT_DIR/release"
DMG_PATH="$DMG_DIR/Transcription-Editor-macOS.dmg"

IDENTITY="${CODESIGN_IDENTITY:?Set CODESIGN_IDENTITY to your Developer ID Application identity}"
NOTARY_PROFILE="${NOTARY_PROFILE:?Set NOTARY_PROFILE to your notarytool keychain profile name}"

mkdir -p "$DIST_DIR" "$BUILD_DIR" "$DMG_DIR"

echo "[1/6] Building .app with PyInstaller..."
python3 -m PyInstaller \
  --noconfirm \
  --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier com.example.transcriptioneditor \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_DIR" \
  "$ROOT_DIR/app/main.py"

if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "Build failed: $APP_BUNDLE not found" >&2
  exit 1
fi

echo "[2/6] Codesigning app..."
codesign --deep --force --options runtime \
  --sign "$IDENTITY" \
  "$APP_BUNDLE"

# Verify signature
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

echo "[3/6] Creating DMG..."
rm -f "$DMG_PATH"
hdiutil create -volname "$APP_NAME" \
  -srcfolder "$APP_BUNDLE" \
  -ov -format UDZO "$DMG_PATH"

echo "[4/6] Submitting DMG for notarization (this can take a few minutes)..."
xcrun notarytool submit "$DMG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait

echo "[5/6] Stapling ticket to app and DMG..."
xcrun stapler staple "$APP_BUNDLE"
xcrun stapler staple "$DMG_PATH"

echo "[6/6] Gatekeeper assessment..."
spctl --assess --type exec -vv "$APP_BUNDLE" || true

echo "Done. Notarized DMG: $DMG_PATH"
