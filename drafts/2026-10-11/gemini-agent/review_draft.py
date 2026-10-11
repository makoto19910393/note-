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
        if not saved.get('uploaded_diagrams'):
            # Correct the plain hashtag line before adding media; never touch a published article.
            page.evaluate("""async ({html,text}) => navigator.clipboard.write([new ClipboardItem({
                'text/html':new Blob([html],{type:'text/html'}),
                'text/plain':new Blob([text],{type:'text/plain'})})])""",{'html':html,'text':body})
            content.click()
            page.keyboard.press('ControlOrMeta+A')
            page.keyboard.press('ControlOrMeta+V')
            page.wait_for_function("() => document.querySelector('.ProseMirror').innerText.includes('この記事について')")
        if not saved.get('cover_uploaded'):
            page.locator('button').filter(has=page.locator("svg[aria-label='画像を追加']")).click()
            with page.expect_file_chooser() as chooser:
                page.get_by_role('button',name='画像をアップロード',exact=False).click()
            chooser.value.set_files(str(ROOT / 'assets' / 'thumbnail.png'))
            dialog = page.get_by_role('dialog')
            dialog.get_by_role('button',name='保存',exact=True).wait_for(state='visible')
            dialog.get_by_role('button',name='保存',exact=True).click()
            dialog.wait_for(state='hidden')
            page.get_by_role('button',name='下書き保存',exact=True).click()
            saved['cover_uploaded']=True
            MANIFEST.write_text(json.dumps(saved,ensure_ascii=False,indent=2)+'\n')
        for i in range(saved.get('uploaded_diagrams',0),4):
            marker = content.locator('p').filter(has_text=f'【ここに図解{"①②③④"[i]}を挿入')
            marker.click()
            page.keyboard.press('End')
            page.keyboard.press('Enter')
            page.get_by_role('button',name='メニューを開く',exact=True).click()
            image_option=page.get_by_text('画像',exact=True)
            image_option.wait_for(state='visible')
            with page.expect_file_chooser() as chooser:
                image_option.click()
            chooser.value.set_files(str(ROOT/'assets'/f'diagram{240+i}.png'))
            page.wait_for_function("n => document.querySelectorAll('.ProseMirror img').length >= n",arg=i+1)
            page.get_by_role('button',name='下書き保存',exact=True).click()
            saved['uploaded_diagrams']=i+1
            MANIFEST.write_text(json.dumps(saved,ensure_ascii=False,indent=2)+'\n')
            print('body_image_saved',i+1)
        page.goto(saved['editor_url'],wait_until='domcontentloaded')
        content.wait_for(state='visible')
        assert content.locator('img').count()==4
        assert 'この記事について' in content.inner_text()
        response=context.request.get('https://note.com/api/v3/notes/'+saved['note_id'])
        if response.ok:
            data=response.json().get('data',{})
            print('saved_owner_metadata',json.dumps({k:data.get(k) for k in
                ('status','is_my_note','can_read','is_draft','eyecatch','publish_at')},ensure_ascii=False))
            if data.get('is_my_note') is not True:
                raise RuntimeError('本人の下書きであることを確認できません。')
            if not data.get('eyecatch'):
                raise RuntimeError('見出し画像の保存を確認できません。')
        public=browser.new_context()
        try:
            check=public.request.get('https://note.com/quiet_spirea2390/n/'+saved['note_id'])
            if check.status not in (404,410):
                raise RuntimeError('確認用記事の非公開状態を確認できません。')
        finally:
            public.close()
        saved['status']='draft_images_verified'
        MANIFEST.write_text(json.dumps(saved,ensure_ascii=False,indent=2)+'\n')
        print('verified_draft',json.dumps(saved,ensure_ascii=False))
    finally:
        if 'page' in locals():
            page.screenshot(path='/tmp/review-editor.png')
        context.close()
        browser.close()
