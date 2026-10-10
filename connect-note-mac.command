#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
printf '%s\n' 'note接続を準備しています。この文字の画面への入力は不要です。'
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Pythonが見つかりません。この表示をCodexに伝えてください。'
  exit 1
fi
python3 -m venv .note-connect-venv
.note-connect-venv/bin/python -m pip install --quiet 'playwright==1.55.0'
.note-connect-venv/bin/python -m playwright install chromium
umask 077
note_connect_dir="$HOME/Library/Application Support/note-connect"
mkdir -p "$note_connect_dir"
chmod 700 "$note_connect_dir"
.note-connect-venv/bin/python login_note.py --output "$note_connect_dir/note-session.json" --guided-mac
