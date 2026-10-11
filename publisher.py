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
import time
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


def verify_public_article(browser, config, record):
    match = re.search(r"/notes/(n[A-Za-z0-9]+)/", record.get("note_editor_url", "") + "/")
    if not match:
        raise WorkflowError("公開記事の確認先を特定できません。")
    username = urlsplit(config["note_profile"]).path.strip("/")
    url = "https://note.com/" + username + "/n/" + match.group(1)
    public = browser.new_context()
    page = public.new_page()
    deadline = time.monotonic() + 60
    try:
        while time.monotonic() < deadline:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.get_by_role("heading", name=record["title"], exact=True).first.wait_for(
                    state="visible", timeout=5000)
                page.wait_for_function("""() => ['30秒で結論', '私の正直な感想', '参考リンク']
                    .every(text => document.body.innerText.includes(text))""", timeout=5000)
                return url
            except Exception:
                continue
        raise WorkflowError("ログインなしで記事のタイトルと本文を確認できません。")
    finally:
        public.close()


def verify_saved_publication(output, config, run_id):
    from playwright.sync_api import sync_playwright
    state_path = output / "state.json"
    state = json.loads(state_path.read_text())
    record = pick_article(state, run_id)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=os.environ.get("NOTE_HEADLESS", "true") != "false")
        try:
            url = verify_public_article(browser, config, record)
        finally:
            browser.close()
    update_record(state_path, state, record, status="published", note_posted=True,
                  note_url=url, public_verified_at=datetime.now(ZoneInfo(config["timezone"])).isoformat())
    print("再投稿せず、noteの公開済み記事を確認しました:", url)


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
        browser = p.chromium.launch(headless=os.environ.get("NOTE_HEADLESS", "true") != "false")
        context = browser.new_context(storage_state=storage,
                                      permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        page.set_default_timeout(30000)
        stage = "ログイン"
        try:
            if storage is None:
                page.goto("https://note.com/login", wait_until="domcontentloaded")
                page.get_by_label("メールアドレス または note ID", exact=True).fill(os.environ["NOTE_EMAIL"])
                page.get_by_label("パスワード", exact=True).fill(os.environ["NOTE_PASSWORD"])
                with page.expect_response(lambda response: urlsplit(response.url).path.endswith("/sessions/sign_in"), timeout=60000) as attempt:
                    page.get_by_role("button", name="ログイン", exact=True).click()
                if attempt.value.status not in (200, 201, 204):
                    raise WorkflowError("noteのログイン処理が拒否されました。ログイン情報または接続方法の確認が必要です。")
                payload = None
                try:
                    payload = attempt.value.json()
                except Exception:
                    pass
                if isinstance(payload, dict) and payload.get("error"):
                    error = payload["error"]
                    code = error.get("code") if isinstance(error, dict) else None
                    if code == "auth":
                        raise WorkflowError("noteが『メールアドレスあるいはパスワードが正しくありません』と返しました。")
                    if code in ("invalid_recaptcha", "required_recaptcha"):
                        raise WorkflowError("noteがロボット確認（reCAPTCHA）を要求しています。本人のブラウザで接続する必要があります。")
                    raise WorkflowError("noteからログインのエラーが返されました。公開せず停止します。")
                try:
                    page.wait_for_url(lambda url: not urlsplit(str(url)).path.startswith("/login"), timeout=10000)
                except BrowserTimeout:
                    pass
                # A successful login need not redirect immediately. The authenticated
                # editor below is the actual verification, including session cookies.
            editor_url = record.get("note_editor_url")
            stage = "編集画面への移動"
            page.goto(editor_url or ui["new_article_url"], wait_until="domcontentloaded")
            title = page.locator(ui["title_selector"])
            content = page.locator(ui["body_selector"])
            stage = "タイトルと本文の入力欄の確認"
            title.wait_for(state="visible")
            content.wait_for(state="visible")
            if title.count() != 1 or content.count() != 1:
                raise WorkflowError("noteの編集画面を一意に特定できません。公開せず停止します。")
            title.fill(record["title"])
            stage = "本文入力"
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
            stage = "公開設定画面の確認"
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
                # note can stay on the editor after posting. Verify the known article
                # in a separate unauthenticated context, independent of redirects.
                published_url = verify_public_article(browser, config, record)
            except Exception:
                update_record(state_path, state, record, status="publication_uncertain")
                raise WorkflowError("公開操作後の確認に失敗しました。重複防止のため自動再投稿を止めています。") from None
            update_record(state_path, state, record, status="published", note_posted=True,
                          note_url=published_url, published_at=datetime.now(ZoneInfo(config["timezone"])).isoformat())
            print("note公開を確認しました:", published_url)
        except BrowserTimeout:
            if stage != "ログイン":
                print("編集画面確認:", json.dumps({"login_page": urlsplit(page.url).path.startswith('/login'),
                      "host": urlsplit(page.url).hostname,
                      "fields": page.locator('textarea, input').evaluate_all(
                          "els => els.map(el => ({tag:el.tagName, type:el.type, placeholder:el.getAttribute('placeholder')}))"),
                      "editable_classes": page.locator('[contenteditable=true]').evaluate_all(
                          "els => els.map(el => el.className)")}, ensure_ascii=False))
            if stage == "ログイン":
                # Only inspect known login messages, never dump page text or credentials.
                messages = ["メールアドレスまたはパスワードが間違っています",
                            "メールアドレスまたはパスワードが正しくありません",
                            "ログインに失敗しました", "認証に失敗しました",
                            "ロボットではない", "しばらく時間をおいて"]
                found = [message for message in messages
                         if page.get_by_text(message, exact=False).count()]
                captcha = page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha']").count() > 0
                print("ログイン確認:", json.dumps({"login_page": urlsplit(page.url).path.startswith('/login'),
                      "known_messages": found,
                      "captcha_frame_present": captcha}, ensure_ascii=False))
            raise WorkflowError(stage + "でタイムアウトしました。ログイン切れや画面変更を確認してください。") from None
        except BrowserError:
            raise WorkflowError(stage + "でブラウザ操作に失敗しました。画面変更、認証、接続状況の確認が必要です。") from None
        finally:
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "content")
    parser.add_argument("--run-id", required=True, help="例: 2026-10-10-morning")
    parser.add_argument("--draft-only", action="store_true")
    parser.add_argument("--verify-only", action="store_true", help="再投稿せず公開済み記事を確認する")
    args = parser.parse_args()
    lock = args.output / ".publisher.lock"
    locked = False
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        locked = True
        config = json.loads((ROOT / "config.json").read_text())
        if args.verify_only:
            verify_saved_publication(args.output, config, args.run_id)
        else:
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
