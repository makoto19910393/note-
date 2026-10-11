"""Upload verified images through note's normal editor controls."""
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from automation import WorkflowError, atomic_json


def prepare_body(body, plan):
    if not plan:
        return body
    body = re.sub(r'!\[[^\]]*\]\([^\n]*\)\n?', '', body)
    for item in plan['items'][1:]:
        body = body.replace(item['anchor'], caption(item))
    return body.strip()


def caption(item):
    return '図解' + '①②③④'[item['number'] - 1] + '｜' + item['theme']


def stock_markers(body):
    """Only standalone company commands are stock widgets; prices are never commands."""
    names=re.findall(r'^\$([^\n$]{1,80})$', body, re.M)
    return list(dict.fromkeys(name for name in names
                            if re.match(r'^[A-Za-z\u3040-\u30ff\u3400-\u9fff]',name)))


def insert_stock_embeds(page, content, companies):
    """Use note's own $ company + Enter command and require a native chart."""
    from playwright.sync_api import TimeoutError as BrowserTimeout
    selector=('[data-src^="https://money.note.com/"],'
              'iframe[src^="https://money.note.com/"]')
    for company in companies:
        command='$'+company
        paragraph=content.locator('p').filter(has_text=re.compile('^'+re.escape(command)+'$'))
        if not paragraph.count():
            continue  # A previously saved native widget no longer contains its command.
        if paragraph.count()!=1:
            raise WorkflowError('株価の挿入位置が重複しています。')
        count=content.locator(selector).count()
        paragraph.click()
        paragraph.evaluate('''el => {
            const range=document.createRange(); range.selectNodeContents(el);
            const selection=window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
        }''')
        # Normal key events activate note's command; pasting plain text alone does not.
        page.keyboard.type(command,delay=40)
        page.keyboard.press('Enter')
        try:
            page.wait_for_function('''({selector, count}) => {
                const body=document.querySelector('.ProseMirror');
                return body && body.querySelectorAll(selector).length>count;
            }''',arg={'selector':selector,'count':count},timeout=12000)
        except BrowserTimeout:
            raise WorkflowError('note標準の株価埋め込みを確認できません。数値や自作画像で代用しません。') from None
    urls=content.locator(selector).evaluate_all(
        'els=>Array.from(new Set(els.map(el=>el.dataset.src || el.src)))')
    if len(urls)<len(companies):
        raise WorkflowError('指定した株価チャートの保存前確認ができません。')
    return urls


def owner_draft(context, note_id):
    response = context.request.get('https://note.com/api/v3/notes/' + note_id)
    if not response.ok:
        raise WorkflowError('下書きの所有者と公開状態を確認できません。')
    data = response.json().get('data', {})
    if data.get('is_my_note') is not True or data.get('status') != 'draft':
        raise WorkflowError('本人の未公開下書きだけを編集します。')
    return data


def save_draft(page):
    # A navigation immediately after the click can cancel the asynchronous save.
    from playwright.sync_api import TimeoutError as BrowserTimeout
    try:
        with page.expect_response(lambda r: r.request.method in ('POST','PUT','PATCH') and
                                  re.search(r'/api/[^/]+/(?:text_notes|notes)(?:/|$)',urlsplit(r.url).path),
                                  timeout=10000) as saved:
            page.get_by_role('button', name='下書き保存', exact=True).click()
    except BrowserTimeout:
        # note can finish autosaving before the click and send no second request.
        # Callers still reload the editor and verify the server-saved body/images.
        return
    if not saved.value.ok:
        raise WorkflowError('noteが下書き保存を拒否しました。')


def uploaded_count(content, plan):
    """Every existing image must occur beneath the corresponding caption."""
    result = content.evaluate('''(el, captions) => {
        const images = Array.from(el.querySelectorAll('img'));
        const paragraphs = Array.from(el.querySelectorAll('p'));
        return images.map(img => {
            const previous = paragraphs.filter(p => p.compareDocumentPosition(img) & Node.DOCUMENT_POSITION_FOLLOWING);
            const labels = previous.filter(p => captions.includes(p.innerText.trim()));
            return labels.length ? captions.indexOf(labels.at(-1).innerText.trim()) : -1;
        });
    }''', [caption(x) for x in plan['items'][1:]])
    if result != list(range(len(result))) or len(result) > 4:
        raise WorkflowError('画像の配置が不明です。二重挿入せず停止します。')
    return len(result)


def upload_images(page, context, output, state_path, state, record, plan, checkpoint):
    directory = (output / record['media_plan']).parent
    content = page.locator('.ProseMirror[contenteditable=true]')
    note_id = re.search(r'/notes/(n[A-Za-z0-9]+)/', record['note_editor_url'] + '/').group(1)

    def save():
        save_draft(page)
        # Reload to verify a server-saved draft, not merely local editor contents.
        page.goto(record['note_editor_url'], wait_until='domcontentloaded')
        content.wait_for(state='visible')
        data = owner_draft(context, note_id)
        count = uploaded_count(content, plan)
        record['media_upload'] = {'cover_uploaded': bool(data.get('eyecatch')),
                                  'diagrams': count}
        atomic_json(state_path, state)
        if checkpoint:
            checkpoint()
        return data, count

    data = owner_draft(context, note_id)
    pending=record.get('pending_cover_url')
    if data.get('eyecatch') and pending and urlsplit(data['eyecatch']).path==urlsplit(pending).path:
        record['cover_needs_refresh']=False
        record.pop('pending_cover_url',None)
    if not data.get('eyecatch') or record.get('cover_needs_refresh'):
        before=page.locator('img').evaluate_all("els=>els.filter(el=>!el.closest('.ProseMirror')).map(el=>el.src)")
        cover = page.locator('img[alt="eyecatch"]')
        if cover.count() > 1:
            raise WorkflowError('差し替えるサムネイルを一意に確認できません。')
        if cover.count() == 1:
            # Existing covers expose only a remove control. Remove this cover in
            # the native editor, then use its normal add/upload/crop controls.
            container = cover.locator('xpath=ancestor::div[@data-dragging][1]')
            remove = container.locator('button').filter(has=page.locator('svg[aria-label="削除"]'))
            if remove.count() != 1:
                raise WorkflowError('サムネイルの差し替え操作を確認できません。')
            cover.hover()
            remove.click()
            cover.wait_for(state='hidden')
        page.locator('button').filter(has=page.locator("svg[aria-label='画像を追加']")).click()
        with page.expect_file_chooser() as chooser:
            page.get_by_role('button', name='画像をアップロード', exact=False).click()
        chooser.value.set_files(str(directory / plan['items'][0]['filename']))
        dialog = page.get_by_role('dialog').filter(has=page.get_by_role('button', name='保存', exact=True))
        dialog.get_by_role('button', name='保存', exact=True).click()
        page.locator('.CropModal__content').wait_for(state='hidden')
        page.wait_for_function('''previous => Array.from(document.querySelectorAll('img'))
            .some(img => !img.closest('.ProseMirror') && img.src.startsWith('https:') &&
                !previous.includes(img.src) && img.complete && img.naturalWidth>0)''',arg=before)
        candidates=page.locator('img').evaluate_all("els=>els.filter(el=>!el.closest('.ProseMirror') && el.src.includes('assets.st-note.com')).map(el=>el.src)")
        if len(candidates)!=1:
            raise WorkflowError('見出し画像の保存先を一意に確認できません。')
        record['pending_cover_url']=candidates[0]
        atomic_json(state_path,state)
        if checkpoint:checkpoint()
        data, _ = save()
        deadline=time.monotonic()+15
        expected_cover_path=urlsplit(record['pending_cover_url']).path
        while (urlsplit(data.get('eyecatch') or '').path != expected_cover_path
               and time.monotonic()<deadline):
            time.sleep(1)
            data=owner_draft(context,note_id)
        if urlsplit(data.get('eyecatch') or '').path != expected_cover_path:
            raise WorkflowError('サムネイルの保存を確認できません。')
        record['cover_needs_refresh']=False
        record.pop('pending_cover_url',None)
        atomic_json(state_path,state)
        if checkpoint:checkpoint()

    count = uploaded_count(content, plan)
    for item in plan['items'][count + 1:]:
        print('図解の挿入開始:',record['number'],item['number'])
        marker = content.locator('p').filter(has_text=caption(item))
        if marker.count() != 1 or marker.inner_text().strip() != caption(item):
            raise WorkflowError('図解の挿入位置を一意に確認できません。')
        marker.click()
        marker.evaluate('''el => {const r=document.createRange();r.selectNodeContents(el);r.collapse(false);
            const s=window.getSelection();s.removeAllRanges();s.addRange(r);}''')
        page.keyboard.press('Enter')
        page.get_by_role('button', name='メニューを開く', exact=True).click()
        option = page.get_by_text('画像', exact=True)
        option.wait_for(state='visible')
        with page.expect_file_chooser() as chooser:
            option.dispatch_event('click')
        chooser.value.set_files(str(directory / item['filename']))
        print('図解ファイルを送信:',record['number'],item['number'])
        page.wait_for_function('n => document.querySelectorAll(".ProseMirror img").length === n',
                               arg=item['number'])
        page.wait_for_function('''() => Array.from(document.querySelectorAll('.ProseMirror img'))
            .every(img => img.complete && img.naturalWidth > 0 && img.src.startsWith('https:'))''')
        data, count = save()
        if count != item['number']:
            raise WorkflowError('図解の保存を確認できません。')
        print('図解の保存確認:', record['number'], count)

    data, count = save()
    text = content.inner_text()
    if count != 4 or not data.get('eyecatch') or text.count('30秒で結論') != 1:
        raise WorkflowError('画像5枚と本文の保存確認に失敗しました。')
    record['media_upload']['verified'] = True
    record['image_urls'] = [data['eyecatch']] + content.locator('img').evaluate_all('els=>els.map(el=>el.src)')
    atomic_json(state_path, state)
    # A separate preview uses note's saved image URLs; original draft hashes stay intact.
    source = (output / record['path']).read_text()
    for item, url in zip(plan['items'], record['image_urls']):
        source = source.replace('../../media/' + str(record['number']).zfill(4) + '/' + item['filename'], url)
    (output / record['path']).with_suffix('.preview.md').write_text(source)
    if checkpoint:
        checkpoint()
