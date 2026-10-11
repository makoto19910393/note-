"""Save this user-requested revision as a separate draft. Never publish it."""
import json
import os
from pathlib import Path
import re
import markdown
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
MANIFEST = ROOT / 'note-draft.json'
source = (ROOT / 'draft.md').read_text()
title_text, body = source.split('\n', 1)
title_text = title_text.removeprefix('# ')
body = re.sub(r'!\[[^\]]*\]\([^\n]*\)\n?', '', body).strip()
html = markdown.markdown(body)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(storage_state=json.loads(os.environ['NOTE_STORAGE_STATE']),
                                  permissions=['clipboard-read', 'clipboard-write'])
    try:
        page = context.new_page()
        saved = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
        page.goto(saved.get('editor_url', 'https://note.com/notes/new'), wait_until='domcontentloaded')
        title = page.locator("textarea[placeholder='記事タイトル'], input[placeholder='記事タイトル']").first
        content = page.locator(".ProseMirror[contenteditable='true']")
        content.wait_for(state='visible')
        if not saved:
            title.fill(title_text)
            page.evaluate("""async ({html,text}) => navigator.clipboard.write([new ClipboardItem({
                'text/html':new Blob([html],{type:'text/html'}),
                'text/plain':new Blob([text],{type:'text/plain'})})])""",{'html':html,'text':body})
            content.click()
            page.keyboard.press('ControlOrMeta+A')
            page.keyboard.press('ControlOrMeta+V')
            page.wait_for_function("() => document.querySelector('.ProseMirror').innerText.includes('この記事について')")
            match = re.search(r'/notes/(n[A-Za-z0-9]+)/', page.url + '/')
            if not match:
                raise RuntimeError('新規下書きのIDを確認できません。')
            saved = {'title':title_text,'editor_url':f'https://editor.note.com/notes/{match.group(1)}/edit',
                     'note_id':match.group(1),'status':'draft_body_saved','published':False}
            MANIFEST.write_text(json.dumps(saved,ensure_ascii=False,indent=2)+'\n')
            page.get_by_role('button',name='下書き保存',exact=True).click()
            page.goto(saved['editor_url'],wait_until='domcontentloaded')
            content.wait_for(state='visible')
        if title.input_value() != title_text:
            raise RuntimeError('対象の下書きタイトルが一致しません。')
        print('draft',json.dumps(saved,ensure_ascii=False))
        page.locator('button').filter(has=page.locator("svg[aria-label='画像を追加']")).click()
        print('cover_menu',json.dumps(page.get_by_role('menuitem').all_text_contents(),ensure_ascii=False))
        print('cover_buttons',json.dumps(page.get_by_role('button').all_text_contents(),ensure_ascii=False))
        print('cover_inputs',json.dumps(page.locator("input[type='file']").evaluate_all("els=>els.map(x=>({accept:x.accept,multiple:x.multiple}))")))
        page.keyboard.press('Escape')
        content.locator('p').filter(has_text='【ここに図解①を挿入').click()
        page.keyboard.press('End')
        page.keyboard.press('Enter')
        page.get_by_role('button',name='メニューを開く',exact=True).click()
        print('body_menu',json.dumps(page.get_by_role('menuitem').all_text_contents(),ensure_ascii=False))
        print('body_buttons',json.dumps(page.get_by_role('button').all_text_contents(),ensure_ascii=False))
        page.screenshot(path='/tmp/review-editor.png')
    finally:
        context.close()
        browser.close()
