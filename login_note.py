"""Run locally to save a note login session. Never send this file through chat."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit
import webbrowser
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("note-session.json"))
    parser.add_argument("--copy-and-open", action="store_true",
                        help="Macのクリップボードに保存し、GitHubの登録画面を開く")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("保存先にファイルがあります。別名を指定してください。")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://note.com/login")
        print("開いたブラウザでnoteにログインしてください。パスワードはこのプログラムには入力しません。")
        input("ログインが完了したら、この画面でEnterを押してください: ")
        page.goto("https://note.com/notes/new", wait_until="domcontentloaded")
        try:
            page.locator("textarea[placeholder='記事タイトル'], input[placeholder='記事タイトル']").first.wait_for(
                state="visible", timeout=60000)
        except Exception:
            raise SystemExit("ログイン完了を確認できません。保存しませんでした。")
        if urlsplit(page.url).path.startswith("/login"):
            raise SystemExit("ログイン完了を確認できません。保存しませんでした。")
        state = context.storage_state()
        # Export only note origins, excluding Google/Apple login cookies.
        state["cookies"] = [cookie for cookie in state["cookies"]
                            if cookie["domain"].lstrip(".") == "note.com" or
                            cookie["domain"].lstrip(".").endswith(".note.com")]
        state["origins"] = [origin for origin in state["origins"]
                            if urlsplit(origin["origin"]).hostname in ("note.com", "editor.note.com")]
        if not state["cookies"]:
            raise SystemExit("noteのログイン情報が見つかりません。保存しませんでした。")
        encoded = json.dumps(state, ensure_ascii=False)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(encoded)
        browser.close()
    if args.copy_and_open:
        subprocess.run(["pbcopy"], input=encoded, text=True, check=True)
        webbrowser.open("https://github.com/makoto19910393/note-/settings/secrets/actions/new")
        print("GitHubのName欄に NOTE_STORAGE_STATE と入力してください。")
        print("Secret欄をクリックし、Command + Vで貼り付け、Add secretを押してください。")
        print("コピーしたものはnoteへのログイン権限です。GitHubのSecret欄だけに貼り付けてください。")
    print("ログイン状態を保存しました。GitHub SecretsのNOTE_STORAGE_STATEに登録してください。")
    print("このファイルはログイン権限を含みます。チャット・Git・共有ドライブには送らないでください。")


if __name__ == "__main__":
    main()
