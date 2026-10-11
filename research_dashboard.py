"""Read note analytics without logging private metrics or changing account settings."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
import math
import subprocess
import tempfile
from urllib.parse import urlsplit


def collect_dashboard(context):
    owner = context.request.get('https://note.com/api/v3/notes/nb987b2314fe7', timeout=45000)
    if not owner.ok or not owner.json().get('data', {}).get('is_my_note'):
        raise RuntimeError('本人のログイン状態を確認できません。')
    page = context.new_page()
    captured = []
    def receive(response):
        path = urlsplit(response.url).path
        if response.request.method != 'GET' or not path.startswith('/api/'):
            return
        if not any(part in path.lower() for part in ('stats','analytics','impression','dashboard')):
            return
        if response.ok and 'json' in response.headers.get('content-type', ''):
            try:
                data = response.json()
                if len(json.dumps(data)) < 1500000:
                    captured.append({'path': path, 'data': data})
            except (ValueError, RuntimeError):
                pass
    page.on('response', receive)
    try:
        page.goto('https://note.com/dashboard', wait_until='domcontentloaded', timeout=60000)
        page.get_by_text('ページビュー', exact=True).filter(visible=True).first.wait_for(timeout=45000)
        page.get_by_text('スキ', exact=True).filter(visible=True).first.wait_for(timeout=45000)
        page.locator('a[href*="/n/"]').filter(visible=True).first.wait_for(timeout=45000)
        page.get_by_text(re.compile(r'^\d[\d,]*$')).filter(visible=True).first.wait_for(timeout=45000)
        from playwright.sync_api import expect
        article_table=page.locator('table').filter(has_text='タイトル').first
        for _ in range(10):
            more=page.get_by_text('もっとみる',exact=True).filter(visible=True)
            if not more.count():
                break
            count=article_table.locator('tbody tr').count()
            more.first.click()
            expect(article_table.locator('tbody tr')).not_to_have_count(count,timeout=30000)
        else:
            raise RuntimeError('記事実績の最終ページを確認できません。')
        text = page.locator('body').inner_text()
        tables = [{'headers':table.locator('thead th').all_inner_texts(),
                   'rows':[row.locator('td').all_inner_texts() for row in table.locator('tbody tr').all()]}
                  for table in page.locator('table').all()]
        return {'captured_at': datetime.now(timezone.utc).isoformat(), 'page': urlsplit(page.url).path,
                'text': text, 'tables': tables, 'responses': captured,
                'screenshot_png': base64.b64encode(page.screenshot(full_page=True)).decode()}
    finally:
        page.close()


def export_encrypted(payload):
    output = Path('/tmp/private-note-dashboard'); output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        public = work/'public.pem'; public.write_text(os.environ['RESEARCH_PUBLIC_KEY'])
        source = work/'dashboard.json'; source.write_text(json.dumps(payload, ensure_ascii=False)); source.chmod(0o600)
        secret = os.urandom(32).hex().encode()
        password = work/'password'; password.write_bytes(secret); password.chmod(0o600)
        subprocess.run(['openssl','enc','-aes-256-cbc','-pbkdf2','-iter','100000','-pass','file:'+str(password),
                        '-in',str(source),'-out',str(output/'dashboard.enc')], check=True, capture_output=True)
        subprocess.run(['openssl','pkeyutl','-encrypt','-pubin','-inkey',str(public),'-pkeyopt','rsa_padding_mode:oaep',
                        '-pkeyopt','rsa_oaep_md:sha256','-in',str(password),'-out',str(output/'key.enc')],check=True,capture_output=True)
        (output/'integrity.json').write_text(json.dumps({'hmac_sha256':hmac.new(secret,(output/'dashboard.enc').read_bytes(),hashlib.sha256).hexdigest()}))


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=json.loads(os.environ['NOTE_STORAGE_STATE']), viewport={'width':1440,'height':1000})
        try:
            export_encrypted(collect_dashboard(context))
            print('本人のアクセス状況を確認し、暗号化して保存しました。私的な数値はログに出していません。')
        finally:
            context.close(); browser.close()

if __name__ == '__main__':
    main()


def reader_insights(dashboard):
    """Turn aggregate counts into planning context; never publish private metrics."""
    rows=[]
    for table in dashboard.get('tables',[]):
        headers=[x.strip() for x in table['headers']]
        if 'タイトル' not in headers or 'ページビュー' not in headers:
            continue
        for cells in table['rows']:
            if len(cells)!=len(headers):continue
            row=dict(zip(headers,cells))
            if '公開中' not in row['タイトル']:continue
            title=row['タイトル'].strip().split('\n',1)[0].strip()
            def count(name):
                value=row.get(name,'').strip().replace(',','')
                return int(value) if value.isdecimal() else 0
            views,likes,comments,impressions=map(count,['ページビュー','スキ','コメント','インプレッション'])
            # Page views, likes and comments express different actions. Likes can
            # happen without opening a post, so likes/PV is not a conversion rate.
            score=math.log1p(views)+0.5*math.log1p(likes)+0.5*math.log1p(comments)
            rows.append({'title':title,'score':score,'views':views,'likes':likes,'comments':comments,'impressions':impressions})
    if not rows:raise RuntimeError('読者実績を読み取れません。')
    ranked=sorted(rows,key=lambda r:r['score'],reverse=True)
    return {'available':True,'captured_at':dashboard['captured_at'],
            'scope':'画面で選択されている過去28日間の公開記事。集計時刻は画面表示に従う。',
            'articles_checked':len(rows),
            'strong_article_titles':[r['title'] for r in ranked[:8]],
            'high_view_titles':[r['title'] for r in sorted(rows,key=lambda r:r['views'],reverse=True)[:5]],
            'high_like_titles':[r['title'] for r in sorted(rows,key=lambda r:r['likes'],reverse=True)[:5]],
            'commented_titles':[r['title'] for r in ranked if r['comments']>0][:5],
            'high_impression_titles':[r['title'] for r in sorted(rows,key=lambda r:r['impressions'],reverse=True)[:5]],
            'readthrough_reference_titles':[r['title'] for r in sorted(
                [r for r in rows if r['impressions']>=100 and r['views']>=10],
                key=lambda r:r['views']/r['impressions'],reverse=True)[:5]],
            'rate_interpretation':'PV÷インプレッションやスキ÷PVは参考値。外部流入・繰り返し閲覧・本文を開かないスキがあるので厳密なクリック率や購入率として扱わない。',
            'editorial_actions':[
                'PVの多い記事に近い企業・用途の新しい出来事を優先する。同じ事件は再利用しない。',
                'スキやコメントの多い記事の、読者にとって身近な疑問・具体例・感想の見せ方を参考にする。',
                '表示機会と本文閲覧の指標を区別し、会社・サービス名と具体的な変化が伝わるタイトルにする。',
                '数回の反応だけで断定せず、次回もダッシュボードを読み、選定を更新する。'],
            'interpretation':'露出と本文閲覧とスキとコメントは別々の指標。PVやスキ数を売上・購入率とみなさない。少数の反応だけで好みを断定しない。'}


def current_reader_insights():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=os.environ.get('NOTE_HEADLESS','true')!='false')
        context=browser.new_context(storage_state=json.loads(os.environ['NOTE_STORAGE_STATE']),viewport={'width':1440,'height':1000})
        try:
            return reader_insights(collect_dashboard(context))
        finally:
            context.close();browser.close()
