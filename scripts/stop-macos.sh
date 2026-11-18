#!/bin/bash

set -euo pipefail

PID_FILE="$HOME/Library/Application Support/WhisperBeta/server.pid"

if [[ ! -f "$PID_FILE" ]]; then
    echo "Сервер Whisper Beta не запущен."
    exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
fi
rm -f "$PID_FILE"
echo "Сервер Whisper Beta остановлен."
