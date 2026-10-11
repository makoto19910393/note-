"""Record token usage and transparent estimates, never requests or credentials."""
from datetime import datetime
import json, os
from pathlib import Path
from zoneinfo import ZoneInfo

# Public standard API rates checked 2026-10-11. Estimates are not billing receipts.
RATES={'gpt-5-mini':(0.25,2.0),'gpt-image-2':(5.0,30.0),
       'gpt-image-2.5-flare':(5.0,30.0),'gpt-image-2.5-sunburst':(5.0,30.0)}

def estimate_cost(model,usage,web_calls=0,image=False):
 if model not in RATES or not isinstance(usage,dict):return None
 inp,out=usage.get('input_tokens'),usage.get('output_tokens')
 if not isinstance(inp,int) or not isinstance(out,int):return None
 input_rate,output_rate=RATES[model]
 return round((inp*input_rate+out*output_rate)/1_000_000+web_calls*0.01,6)

def record_usage(payload,response,kind):
 directory=os.environ.get('NOTE_USAGE_DIR')
 if not directory:return
 model=payload.get('model');usage=response.get('usage')
 searches=sum(x.get('type')=='web_search_call' for x in response.get('output',[]))
 now=datetime.now(ZoneInfo('Asia/Tokyo'))
 record={'at':now.isoformat(),'kind':kind,'model':model,'usage':usage,
         'web_tool_calls':searches,'estimated_usd':estimate_cost(model,usage,searches,kind=='images'),
         'estimate_only':True}
 path=Path(directory)/(now.strftime('%Y-%m-%d')+'.jsonl');path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('a') as f:f.write(json.dumps(record,ensure_ascii=False)+'\n')
