"""Read note's own authenticated draft-list API; do not edit or read article bodies."""
import json
import os
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=False)
        context=browser.new_context(storage_state=json.loads(os.environ['NOTE_STORAGE_STATE']))
        try:
            keys=set()
            expected=None
            for page in range(1,201):
                response=context.request.get('https://note.com/api/v2/note_list/contents',
                    params={'limit':20,'page':page,'status':'draft','without_magazines':'true'})
                if not response.ok:
                    raise RuntimeError('下書き一覧を取得できません: HTTP '+str(response.status))
                data=response.json().get('data',{})
                notes=data.get('notes')
                if not isinstance(notes,list) or type(data.get('totalCount')) is not int:
                    raise RuntimeError('下書き総数のデータを確認できません。')
                if expected is None:
                    expected=data['totalCount']
                elif expected!=data['totalCount']:
                    raise RuntimeError('確認中に総数が変わりました。再確認が必要です。')
                for note in notes:
                    key=note.get('key')
                    if not key or key in keys:
                        raise RuntimeError('一覧の重複や保存IDの欠落があり、総数を確定できません。')
                    keys.add(key)
                if data.get('isLastPage') is True:
                    if len(keys)!=expected:
                        raise RuntimeError('総数と全ページの件数が一致しません。')
                    print('DRAFT_COUNT_RESULT',json.dumps({'count':expected,'note_ids':sorted(keys)}))
                    return
                if not notes:
                    raise RuntimeError('一覧の最終ページを確認できません。')
            raise RuntimeError('下書き一覧の取得上限に達しました。')
        finally:
            browser.close()


if __name__=='__main__':
    main()
