#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
printf '%s\n' 'note接続の準備をします。記事の公開やパスワード変更は行いません。'
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Pythonが見つかりません。この表示をCodexに伝えてください。'
  read -r -p 'Enterで終了します。'
  exit 1
fi
python3 -m venv .note-connect-venv
.note-connect-venv/bin/python -m pip install 'playwright==1.55.0'
.note-connect-venv/bin/python -m playwright install chromium
umask 077
session_file=$(mktemp "${TMPDIR:-/tmp}/note-session.XXXXXXXX")
rm "$session_file"
trap 'rm -f "$session_file"; printf "%s" "" | pbcopy' EXIT
.note-connect-venv/bin/python login_note.py --output "$session_file" --copy-and-open
read -r -p 'GitHubへの登録が終わったらEnterを押してください。'
printf '%s' '' | pbcopy
printf '%s\n' '一時ファイルとクリップボードを消しました。Codexに「登録した」と伝えてください。'
