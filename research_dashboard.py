"""Read note analytics without logging private metrics or changing account settings."""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
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
        if not any(part in path.lower() for part in ('stats', 'analytics', 'impression')):
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
