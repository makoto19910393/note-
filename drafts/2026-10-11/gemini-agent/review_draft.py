"""Inspect image insertion controls for a user-requested unpublished revision."""
import json
import os
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(storage_state=json.loads(os.environ["NOTE_STORAGE_STATE"]))
    try:
        page = context.new_page()
        page.goto("https://editor.note.com/notes/nbf9f9dd8769e/edit", wait_until="domcontentloaded")
        page.locator(".ProseMirror[contenteditable='true']").wait_for(state="visible")
        print("image_inputs", json.dumps(page.locator("input[type='file']").evaluate_all(
            "els => els.map(x=>({accept:x.accept,multiple:x.multiple,id:x.id,className:x.className}))"),ensure_ascii=False))
        print("buttons",json.dumps(page.get_by_role("button").evaluate_all(
            "els=>els.map(x=>({text:x.textContent.trim(),label:x.getAttribute('aria-label'),title:x.getAttribute('title'),className:x.className}))"),ensure_ascii=False))
        page.locator(".ProseMirror[contenteditable='true']").click()
        menus = page.get_by_role("button",name="メニューを開く",exact=True)
        print("menu_count",menus.count())
        if menus.count():
            menus.first.click()
            print("insert_menu",json.dumps(page.get_by_role("menuitem").all_text_contents(),ensure_ascii=False))
            print("menu_buttons",json.dumps(page.get_by_role("button").all_text_contents(),ensure_ascii=False))
    finally:
        context.close()
        browser.close()
