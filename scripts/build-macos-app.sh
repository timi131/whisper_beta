#!/bin/bash

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$ROOT/dist"
APP="$DIST_DIR/Whisper Beta.app"
DMG="$DIST_DIR/Whisper-Beta-macOS.dmg"

rm -rf "$DIST_DIR"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp "$ROOT/macos/Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT/macos/WhisperBeta" "$APP/Contents/MacOS/WhisperBeta"
cp "$ROOT/app.py" "$APP/Contents/Resources/app.py"
cp "$ROOT/requirements.txt" "$APP/Contents/Resources/requirements.txt"
chmod +x "$APP/Contents/MacOS/WhisperBeta"

rm -f "$DMG"
hdiutil create -volname "Whisper Beta" -srcfolder "$APP" -ov -format UDZO "$DMG"

echo "Готово:"
echo "  $APP"
echo "  $DMG"
