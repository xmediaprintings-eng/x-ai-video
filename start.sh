#!/usr/bin/env bash
# X Media AI Video - start on macOS / Linux
set -e
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "Please install FFmpeg first (mac: brew install ffmpeg, linux: apt install ffmpeg)"; exit 1; }
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
if ! cmp -s requirements.txt .venv/req.txt; then
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m pip install faster-whisper || echo "auto-captions package failed - app still works"
  cp requirements.txt .venv/req.txt
fi
exec .venv/bin/python -m app.launcher
