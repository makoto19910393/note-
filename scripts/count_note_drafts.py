"""Read the owner's draft list; never edit articles or log browser credentials."""
import json
import os
import re
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(storage_state=json.loads(os.environ['NOTE_STORAGE_STATE']))
        page=context.new_page()
        collections=[]

        def observe(response):
            if '/api/' not in response.url or 'note' not in response.url or not response.ok:
                return
            try:
                data=response.json().get('data')
            except (ValueError,AttributeError):
                return
            if isinstance(data,dict) and any(isinstance(v,list) for v in data.values()):
                collections.append((response.url,data))

        page.on('response',observe)
        try:
            page.goto('https://note.com/notes?status=draft',wait_until='networkidle',timeout=60000)
            page.locator('body').wait_for(state='visible')
            print('DRAFT_PAGE',json.dumps({'url':page.url,'title':page.title(),
                                        'headings':page.get_by_role('heading').all_text_contents()},ensure_ascii=False))
            links=page.get_by_role('link').evaluate_all(
                "els=>els.filter(el=>el.textContent.includes('下書き')).map(el=>({text:el.textContent.trim(),url:el.href}))")
            print('DRAFT_LINKS',json.dumps(links,ensure_ascii=False))
            print('DRAFT_CONTROLS',json.dumps(page.get_by_role('button').all_text_contents(),ensure_ascii=False))
            for url,data in collections:
                lists={k:[{field:x.get(field) for field in ('key','id','status','is_draft') if field in x}
                          for x in value if isinstance(x,dict)] for k,value in data.items() if isinstance(value,list)}
                meta={k:value for k,value in data.items() if isinstance(value,(int,bool))}
                print('DRAFT_LIST_API',json.dumps({'url':url,'metadata':meta,'items':lists},ensure_ascii=False))
            text=page.locator('body').inner_text()
            counts=re.findall(r'下書き\s*[（(]?\s*(\d+)\s*[）)]?',text)
            print('DRAFT_COUNT_LABELS',json.dumps(counts))
            print('DRAFT_EDITOR_LINK_COUNT',page.locator('a[href*="editor.note.com/notes/"]').count())
            for route in ('/api/v2/notes?status=draft&page=1',
                          '/api/v2/notes?filter=draft&page=1',
                          '/api/v2/notes?draft=true&page=1'):
                response=context.request.get('https://note.com'+route)
                print('DRAFT_API_STATUS',route,response.status)
                if not response.ok:
                    continue
                data=response.json().get('data',{})
                if not isinstance(data,dict):
                    print('DRAFT_API_KEYS',type(data).__name__)
                    continue
                metadata={k:v for k,v in data.items() if isinstance(v,(bool,int,str))}
                entries={k:[{field:x.get(field) for field in ('key','id','status','is_draft','isDraft') if field in x}
                           for x in v if isinstance(x,dict)] for k,v in data.items() if isinstance(v,list)}
                print('DRAFT_API_DATA',json.dumps({'keys':list(data),'metadata':metadata,'entries':entries},ensure_ascii=False))
        finally:
            browser.close()


if __name__=='__main__':
    main()
