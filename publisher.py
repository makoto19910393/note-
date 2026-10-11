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
                if record.get('media_plan'):
                    response=public.request.get('https://note.com/api/v3/notes/'+match.group(1))
                    data=response.json().get('data',{}) if response.ok else {}
                    if data.get('status')!='published' or not data.get('eyecatch') or data.get('price') not in (0,None):
                        raise WorkflowError('無料公開とサムネイルを確認できません。')
                    images=re.findall(r'<img\b[^>]*\bsrc=["\']([^"\']+)',data.get('body',''))
                    expected=record.get('image_urls',[])[1:]
                    public_paths={urlsplit(html_module.unescape(x)).path for x in images}
                    if len(expected)!=4 or not all(urlsplit(x).path in public_paths for x in expected):
                        raise WorkflowError('公開本文の図解4枚を確認できません。')
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


def inspect_editor(output, run_id):
    from playwright.sync_api import sync_playwright
    state = json.loads((output / "state.json").read_text())
    record = pick_article(state, run_id)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=os.environ.get("NOTE_HEADLESS", "true") != "false")
        try:
            context = browser.new_context(storage_state=json.loads(os.environ["NOTE_STORAGE_STATE"]))
            page = context.new_page()
            page.goto(record["note_editor_url"], wait_until="domcontentloaded")
            page.locator("textarea[placeholder='記事タイトル'], input[placeholder='記事タイトル']").first.wait_for(state="visible")
            print("記事編集ボタン:", json.dumps(page.get_by_role("button").all_text_contents(), ensure_ascii=False))
            print("記事編集リンク名:", json.dumps(page.get_by_role("link").all_text_contents(), ensure_ascii=False))
            print("編集ボタンの識別子:", json.dumps(page.get_by_role("button").evaluate_all(
                "els => els.map(el => ({text:el.textContent.trim().slice(0,50),label:el.getAttribute('aria-label'),title:el.getAttribute('title'),className:el.className,svg:el.querySelector('svg')?.getAttribute('class')}))"), ensure_ascii=False))
            if not record.get('note_posted'):
                note_id=re.search(r'/notes/(n[A-Za-z0-9]+)/',record['note_editor_url']).group(1)
                response=context.request.get('https://note.com/api/v3/notes/'+note_id)
                data=response.json().get('data',{}) if response.ok else {}
                print('下書き画像メタデータ:',json.dumps({k:data.get(k) for k in
                      ('status','is_my_note','is_draft','eyecatch','draft_eyecatch','eyecatch_url')},ensure_ascii=False))
                print('画像要素:',json.dumps(page.locator('img').evaluate_all("els=>els.map(el=>({src:el.src,inBody:!!el.closest('.ProseMirror'),parent:el.parentElement.className,grandparent:el.parentElement.parentElement.className}))"),ensure_ascii=False))
                return
            page.goto(record["note_url"], wait_until="domcontentloaded")
            page.get_by_role("heading", name=record["title"], exact=True).first.wait_for(state="visible")
            print("公開記事の管理ボタン:", json.dumps(page.get_by_role("button").evaluate_all(
                "els => els.map(el => ({text:el.textContent.trim().slice(0,80),label:el.getAttribute('aria-label'),title:el.getAttribute('title')}))"), ensure_ascii=False))
            page.goto("https://note.com/notes", wait_until="domcontentloaded")
            note_id = re.search(r'/notes/([^/]+)/', record['note_editor_url']).group(1)
            link = page.locator("a[href*='" + note_id + "']").first
            link.wait_for(state="visible", timeout=30000)
            row = link.locator("xpath=ancestor::li[1]")
            row.get_by_role("button").click()
            print("対象記事のメニュー:", json.dumps(page.get_by_role("menuitem").all_text_contents(), ensure_ascii=False))
            print("メニューのボタン:", json.dumps(page.get_by_role("button").all_text_contents(), ensure_ascii=False))

        finally:
            browser.close()


def withdraw_duplicate(output, config, run_id):
    """Return only our verified accidental duplicate No.2 to draft."""
    from playwright.sync_api import sync_playwright
    state_path = output / "state.json"
    state = json.loads(state_path.read_text())
    record = pick_article(state, run_id)
    if record.get("number") != 2 or record.get("note_url") != "https://note.com/quiet_spirea2390/n/nbf9f9dd8769e":
        raise WorkflowError("重複記事No.2以外の公開状態は変更しません。")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=os.environ.get("NOTE_HEADLESS", "true") != "false")
        try:
            context = browser.new_context(storage_state=json.loads(os.environ["NOTE_STORAGE_STATE"]))
            page = context.new_page()
            page.goto("https://note.com/notes", wait_until="domcontentloaded")
            link = page.locator("a[href*='nbf9f9dd8769e']").first
            link.wait_for(state="visible")
            row = link.locator("xpath=ancestor::li[1]")
            if record["title"] not in row.inner_text():
                raise WorkflowError("重複記事の管理行が一致しません。")
            row.get_by_role("button").click()
            page.get_by_role("menuitem", name="下書きに戻す", exact=True).click()
            try:
                confirm = page.get_by_role("button", name="下書きに戻す", exact=True)
                confirm.wait_for(state="visible", timeout=5000)
                confirm.click()
            except Exception:
                dialog = page.get_by_role("dialog")
                dialog.wait_for(state="visible", timeout=5000)
                dialog.get_by_role("button", name="OK", exact=True).click()
            public = browser.new_context()
            check = public.new_page()
            deadline = time.monotonic() + 30
            unavailable = False
            while time.monotonic() < deadline:
                response = check.goto(record["note_url"], wait_until="domcontentloaded", timeout=15000)
                if response and response.status in (404, 410):
                    unavailable = True
                    break
                check.wait_for_timeout(2000)
            if not unavailable:
                raise WorkflowError("重複記事が非公開になったことを確認できません。状態は変更しません。")
            page.goto(record["note_editor_url"], wait_until="domcontentloaded")
            title = page.locator("textarea[placeholder='記事タイトル'], input[placeholder='記事タイトル']").first
            title.wait_for(state="visible")
            if title.input_value() != record["title"]:
                raise WorkflowError("下書きの保全を確認できません。")
            update_record(state_path, state, record, status="withdrawn_duplicate", note_posted=False,
                          original_run_id=run_id, run_id=run_id + "-withdrawn",
                          withdrawn_at=datetime.now(ZoneInfo(config["timezone"])).isoformat())
            print("重複記事No.2を下書きに戻しました。原稿は保全され、公開URLは404です。")
        finally:
            browser.close()


def publish(output, config, run_id, dry_run=False, before_publish=None):
    if config.get("publisher", {}).get("manual_first_publication"):
        dry_run = True  # Explicit publish options cannot bypass the user review period.
    state_path = output / "state.json"
    state = json.loads(state_path.read_text())
    record = pick_article(state, run_id)
    if record["status"] == "withdrawn_duplicate":
        raise WorkflowError("重複により下書きに戻した記事は再公開しません。")
    if record.get("note_posted"):
        print("この原稿はnoteに公開済みです。再投稿しません。")
        return
    if record["status"] in ("publishing", "publication_uncertain"):
        raise WorkflowError("前回の公開結果が未確認です。noteの公開状況を確認するまで再投稿を止めます。")
    data = (output / record["path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise WorkflowError("原稿が生成後に変更されています。確認が必要です。")
    body = data.decode().split("\n", 1)[1].strip()
    from media import verified_media
    from publisher_images import prepare_body, owner_draft, upload_images, save_draft
    plan=verified_media(output,record)
    body=prepare_body(body,plan)
    if 'ここに図解' in body:
        raise WorkflowError('図解作成の指示が本文に残っています。公開せず修正します。')
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
            if editor_url:
                note_id=re.search(r'/notes/(n[A-Za-z0-9]+)/',editor_url+'/').group(1)
                owner_draft(context,note_id)
            existing_images=content.locator('img').count()
            wanted_hashes=[x['sha256'] for x in plan['items']] if plan else None
            changed_media=plan and record.get('media_hashes')!=wanted_hashes
            if existing_images and not plan:
                raise WorkflowError('予定外の画像があります。既存下書きの上書きを止めます。')
            if existing_images and title.input_value()!=record['title']:
                raise WorkflowError('画像付き下書きのタイトルが一致しません。')
            if not existing_images or changed_media:
                should_fill=True
            else:
                should_fill=False
            title.fill(record["title"])
            stage = "本文入力"
            # Paste through the browser so the editor receives its normal change events.
            if should_fill:
                page.evaluate("""async ({html, text}) => {
                await navigator.clipboard.write([new ClipboardItem({
                    'text/html': new Blob([html], {type: 'text/html'}),
                    'text/plain': new Blob([text], {type: 'text/plain'})
                })]);
            }""", {"html": html, "text": body})
                content.click()
                content.evaluate("""el=>{el.focus();const r=document.createRange();r.selectNodeContents(el);
                    const s=window.getSelection();s.removeAllRanges();s.addRange(r);} """)
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
            save_draft(page)
            update_record(state_path, state, record, status="draft_saved", note_editor_url=editor_url)
            if before_publish:
                before_publish()
            page.goto(editor_url,wait_until='domcontentloaded')
            content.wait_for(state='visible')
            if plan:
                if changed_media:
                    old_hashes=record.get('media_hashes',[])
                    record['cover_needs_refresh']=not old_hashes or old_hashes[0]!=wanted_hashes[0]
                    record['media_hashes']=wanted_hashes
                    update_record(state_path,state,record)
                stage='画像5枚の挿入と保存'
                upload_images(page,context,output,state_path,state,record,plan,before_publish)
            if dry_run:
                print('note下書きの本文と画像を保存・確認しました。公開操作は行っていません。')
                return
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
    parser.add_argument("--inspect-editor", action="store_true", help="記事編集の操作名だけを確認する")
    parser.add_argument("--withdraw-duplicate", action="store_true")
    args = parser.parse_args()
    lock = args.output / ".publisher.lock"
    locked = False
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        locked = True
        config = json.loads((ROOT / "config.json").read_text())
        if args.withdraw_duplicate:
            withdraw_duplicate(args.output, config, args.run_id)
        elif args.inspect_editor:
            inspect_editor(args.output, args.run_id)
        elif args.verify_only:
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
