"""Upload verified images through note's normal editor controls."""
import hashlib
import json
import re

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


def owner_draft(context, note_id):
    response = context.request.get('https://note.com/api/v3/notes/' + note_id)
    if not response.ok:
        raise WorkflowError('下書きの所有者と公開状態を確認できません。')
    data = response.json().get('data', {})
    if data.get('is_my_note') is not True or data.get('status') != 'draft':
        raise WorkflowError('本人の未公開下書きだけを編集します。')
    return data


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
        page.get_by_role('button', name='下書き保存', exact=True).click()
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
    if not data.get('eyecatch'):
        page.locator('button').filter(has=page.locator("svg[aria-label='画像を追加']")).click()
        with page.expect_file_chooser() as chooser:
            page.get_by_role('button', name='画像をアップロード', exact=False).click()
        chooser.value.set_files(str(directory / plan['items'][0]['filename']))
        dialog = page.get_by_role('dialog').filter(has=page.get_by_role('button', name='保存', exact=True))
        dialog.get_by_role('button', name='保存', exact=True).click()
        page.locator('.CropModal__content').wait_for(state='hidden')
        data, _ = save()
        if not data.get('eyecatch'):
            raise WorkflowError('サムネイルの保存を確認できません。')

    count = uploaded_count(content, plan)
    for item in plan['items'][count + 1:]:
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
