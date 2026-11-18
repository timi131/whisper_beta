#!/bin/bash

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/build-macos-app.sh"

SOURCE="$ROOT/dist/Whisper Beta.app"
TARGET="$HOME/Applications/Whisper Beta.app"

mkdir -p "$HOME/Applications"
rm -rf "$TARGET"
ditto "$SOURCE" "$TARGET"

open "$TARGET"
echo "Whisper Beta установлен в $TARGET"
