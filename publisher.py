"""Publish generated drafts using the user's own authenticated browser session.

Selectors must be verified against the current note UI before scheduled activation.
On an ambiguous publish outcome, stop rather than creating a duplicate post.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import html as html_module
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from automation import ROOT, WorkflowError, atomic_json


def update_record(state_path, state, record, **values):
    record.update(values)
    atomic_json(state_path, state)


def pick_article(state, run_id):
    matches = [a for a in state["articles"] if a["run_id"] == run_id]
    if len(matches) != 1:
        raise WorkflowError("指定された時間枠の原稿が一意に見つかりません。")
    return matches[0]


def publish(output, config, run_id, dry_run=False, before_publish=None):
    state_path = output / "state.json"
    state = json.loads(state_path.read_text())
    record = pick_article(state, run_id)
    if record.get("note_posted"):
        print("この原稿はnoteに公開済みです。再投稿しません。")
        return
    if record["status"] in ("publishing", "publication_uncertain"):
        raise WorkflowError("前回の公開結果が未確認です。noteの公開状況を確認するまで再投稿を止めます。")
    data = (output / record["path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise WorkflowError("原稿が生成後に変更されています。確認が必要です。")
    body = data.decode().split("\n", 1)[1].strip()
    raw_session = os.environ.get("NOTE_STORAGE_STATE")
    storage = None
    if raw_session:
        try:
            storage = json.loads(raw_session)
        except ValueError:
            raise WorkflowError("noteのログイン状態を読み取れません。値はログに出力しません。") from None
    elif not (os.environ.get("NOTE_EMAIL") and os.environ.get("NOTE_PASSWORD")):
        raise WorkflowError("noteログイン用のNOTE_EMAILとNOTE_PASSWORD、またはNOTE_STORAGE_STATEが必要です。")
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout, Error as BrowserError
    import markdown
    html = markdown.markdown(html_module.escape(body), extensions=["tables", "fenced_code"])
    ui = config["publisher"]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=storage,
                                      permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            if storage is None:
                page.goto("https://note.com/login", wait_until="domcontentloaded")
                page.get_by_label("メールアドレス", exact=True).fill(os.environ["NOTE_EMAIL"])
                page.get_by_label("パスワード", exact=True).fill(os.environ["NOTE_PASSWORD"])
                page.get_by_role("button", name="ログイン", exact=True).click()
                page.wait_for_url(lambda url: not urlsplit(str(url)).path.startswith("/login"), timeout=60000)
            editor_url = record.get("note_editor_url")
            page.goto(editor_url or ui["new_article_url"], wait_until="domcontentloaded")
            title = page.locator(ui["title_selector"])
            content = page.locator(ui["body_selector"])
            title.wait_for(state="visible")
            content.wait_for(state="visible")
            if title.count() != 1 or content.count() != 1:
                raise WorkflowError("noteの編集画面を一意に特定できません。公開せず停止します。")
            title.fill(record["title"])
            # Paste through the browser so the editor receives its normal change events.
            page.evaluate("""async ({html, text}) => {
                await navigator.clipboard.write([new ClipboardItem({
                    'text/html': new Blob([html], {type: 'text/html'}),
                    'text/plain': new Blob([text], {type: 'text/plain'})
                })]);
            }""", {"html": html, "text": body})
            content.click()
            page.keyboard.press("ControlOrMeta+A")
            page.keyboard.press("ControlOrMeta+V")
            # Confirm text reaches the editor rather than treating a click as success.
            page.wait_for_function("""({selector, minimum}) => {
                const el = document.querySelector(selector);
                return el && el.innerText.includes('30秒で結論') &&
                    el.innerText.includes('参考リンク') && el.innerText.length >= minimum;
            }""", arg={"selector": ui["body_selector"], "minimum": int(len(body) * 0.7)})
            if title.input_value() != record["title"]:
                raise WorkflowError("タイトルの入力を確認できません。")
            match = re.search(r"/notes/(n?[A-Za-z0-9_-]+)/", page.url + "/")
            if not match or match.group(1) == "new":
                raise WorkflowError("下書きの保存先を確認できません。公開せず停止します。")
            editor_url = "https://editor.note.com/notes/" + match.group(1) + "/edit"
            update_record(state_path, state, record, status="draft_saved", note_editor_url=editor_url)
            page.get_by_role("button", name=ui["proceed_button"], exact=True).click()
            publish_button = page.get_by_role("button", name=ui["publish_button"], exact=True)
            publish_button.wait_for(state="visible")
            # If a paid/publication setting is present, it must be the free option.
            paid = page.get_by_role("radio", name="有料", exact=True)
            if paid.count() and paid.is_checked():
                raise WorkflowError("有料設定になっています。今回は無料ニュースのため停止します。")
            if dry_run:
                print("noteの下書き入力と公開設定画面まで確認しました。公開操作は行っていません。")
                return
            # This persisted marker prohibits retrying an uncertain publish click.
            update_record(state_path, state, record, status="publishing")
            if before_publish:
                before_publish()
            try:
                publish_button.click()
                username = urlsplit(config["note_profile"]).path.strip("/")
                page.wait_for_url(re.compile(r"https://note\.com/" + re.escape(username) + r"/n/n[a-zA-Z0-9]+(?:[?#].*)?$"), timeout=60000)
                published_url = page.url
                # A second, unauthenticated context must see the title: a redirect alone is insufficient.
                public = browser.new_context()
                public_page = public.new_page()
                public_page.goto(published_url, wait_until="domcontentloaded")
                public_page.get_by_role("heading", name=record["title"], exact=True).first.wait_for(state="visible")
                public.close()
            except Exception:
                update_record(state_path, state, record, status="publication_uncertain")
                raise WorkflowError("公開操作後の確認に失敗しました。重複防止のため自動再投稿を止めています。") from None
            update_record(state_path, state, record, status="published", note_posted=True,
                          note_url=published_url, published_at=datetime.now(ZoneInfo(config["timezone"])).isoformat())
            print("note公開を確認しました:", published_url)
        except BrowserTimeout:
            raise WorkflowError("noteの画面操作がタイムアウトしました。ログイン切れや画面変更を確認してください。") from None
        except BrowserError:
            raise WorkflowError("noteのブラウザ操作に失敗しました。画面変更、認証、接続状況の確認が必要です。") from None
        finally:
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "content")
    parser.add_argument("--run-id", required=True, help="例: 2026-10-10-morning")
    parser.add_argument("--draft-only", action="store_true")
    args = parser.parse_args()
    lock = args.output / ".publisher.lock"
    locked = False
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        locked = True
        config = json.loads((ROOT / "config.json").read_text())
        publish(args.output, config, args.run_id, args.draft_only)
    except (WorkflowError, OSError, ValueError, KeyError, ImportError) as e:
        print("note投稿を停止しました:", e, file=sys.stderr)
        return 1
    finally:
        if locked:
            lock.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
