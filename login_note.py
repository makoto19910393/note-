"""Run locally to save a note login session. Never send this file through chat."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("note-session.json"))
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
        if page.url.startswith("https://note.com/login"):
            raise SystemExit("ログイン完了を確認できません。保存しませんでした。")
        context.storage_state(path=str(args.output))
        args.output.chmod(0o600)
        browser.close()
    print("ログイン状態を保存しました。GitHub SecretsのNOTE_STORAGE_STATEに登録してください。")
    print("このファイルはログイン権限を含みます。チャット・Git・共有ドライブには送らないでください。")


if __name__ == "__main__":
    main()
