"""Check model availability without generation or exposing credentials."""
import json, os, sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError
key=os.environ.get('OPENAI_API_KEY')
if not key:
 raise SystemExit('APIキーが届いていません。')
try:
 with urlopen(Request('https://api.openai.com/v1/models',headers={'Authorization':'Bearer '+key}),timeout=30) as r:
  ids={x['id'] for x in json.load(r)['data']}
except HTTPError as e:
 raise SystemExit('モデル確認がHTTP '+str(e.code)+'で停止しました。') from None
print(json.dumps({'available_image_models':sorted(x for x in ids if x.startswith('gpt-image-')),'text_model_available':'gpt-5-mini' in ids},ensure_ascii=False))
