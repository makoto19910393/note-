"""Read current public RSS headlines; publication still requires checked sources."""
from datetime import timezone
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

FEEDS=('https://www.bleepingcomputer.com/feed/',
       'https://www.techradar.com/rss',
       'https://rss.itmedia.co.jp/rss/2.0/aiplus.xml',
       'https://www.bing.com/news/search?q=AI+%E6%97%A5%E6%9C%AC%E4%BC%81%E6%A5%AD&format=rss&mkt=ja-JP',
       'https://www.bing.com/news/search?q=AI&format=rss&mkt=ja-JP')
AI=re.compile(r'AI(?![a-z])|人工知能|生成|Claude|ChatGPT|Anthropic|OpenAI|Gemini|NVIDIA|Copilot|Higgsfield',re.I)
DOMESTIC=re.compile(r'日本|国内|国産|東京|\bJapan(?:ese)?\b|\bTokyo\b|富士通|FUJITSU|日立|NTT|みずほ|三菱|三井|経産省|金融庁|総務省|ソフトバンク|SoftBank|楽天|サイバーエージェント|CyberAgent|NEC|トヨタ|Toyota|ソニー|Sony|パナソニック|Panasonic',re.I)
OVERSEAS=re.compile(r'OpenAI|Anthropic|Claude|Google|Gemini|Microsoft|NVIDIA|Meta\b|Apple|Higgsfield|韓国|中国|米国|アメリカ|英国|欧州|\bKorea|\bChina|\bUS\b|\bUK\b',re.I)


def candidate_region(candidate):
    # Language and publication country do not determine the event's region.
    if DOMESTIC.search(candidate['title']):return 'domestic'
    if OVERSEAS.search(candidate['title']):return 'overseas'
    return 'unknown'


def candidates_for_region(candidates,region):
    if not region:return candidates
    if region not in ('domestic','overseas'):
        raise ValueError('Invalid news region')
    return ([x for x in candidates if candidate_region(x)==region]+
            [x for x in candidates if candidate_region(x)=='unknown'])


def read_feed(feed):
    try:
        request=Request(feed,headers={'User-Agent':'mako-note-news-research/1.0'})
        with urlopen(request,timeout=20) as response:
            return ET.fromstring(response.read(3000000))
    except Exception:
        return None

INTERESTS={
    'OpenAI':r'openai|chatgpt|gpt[-\s]?[0-9]',
    'Anthropic':r'anthropic|claude|クロード',
    'Google':r'google|gemini|グーグル|ジェミニ',
    'Microsoft':r'microsoft|copilot|teams|マイクロソフト',
    'NVIDIA':r'nvidia|エヌビディア',
    'Apple':r'apple|mac\b|iphone|アップル',
    '国内半導体':r'富士通|fujitsu|monaka|半導体|国産|cpu\b',
    'エージェント':r'エージェント|agent|cowork',
    'Higgsfield':r'higgsfield',
    '動画':r'動画|video|sora|映像',
    '音声':r'音声|voice|speech',
    '仕事の実用':r'使い方|how to|副業|kindle|出版|議事録|プロフィール',
    '安全性':r'安全|security|セキュリティ|なりすまし|詐欺|攻撃|attack',
}


def prioritize_candidates(candidates, insights):
    """Use dashboard interests to rank already-fresh news without another AI call."""
    if not insights.get('available'):
        return candidates
    weights={name:0 for name in INTERESTS}
    for field,importance in (('high_view_titles',3),('readthrough_reference_titles',2),
                             ('high_like_titles',1),('commented_titles',1)):
        for rank,title in enumerate(insights.get(field,[])):
            for name,pattern in INTERESTS.items():
                if re.search(pattern,title,re.I):
                    weights[name]+=importance/(rank+1)
    def score(candidate):
        return sum(weight for name,weight in weights.items()
                   if re.search(INTERESTS[name],candidate['title'],re.I))
    # Stable sorting retains newest-first order for equally relevant candidates.
    return sorted(candidates,key=score,reverse=True)


def current_candidates(now,max_age_hours=48):
    candidates=[];seen=set()
    with ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
        roots=list(pool.map(read_feed,FEEDS))
    for root in roots:
        try:
            if root is None:continue
            for item in root.findall('.//item'):
                title=item.findtext('title','').strip()
                if not AI.search(title):continue
                try: published=parsedate_to_datetime(item.findtext('pubDate',''))
                except (TypeError,ValueError):continue
                if published.tzinfo is None:continue
                age=(now-published).total_seconds()/3600
                if not 0<=age<=max_age_hours:continue
                url=item.findtext('link','').strip();parts=urlsplit(url)
                if parts.hostname=='www.bing.com':
                    url=parse_qs(parts.query).get('url',[''])[0];parts=urlsplit(url)
                if parts.scheme!='https' or not parts.hostname or parts.username or parts.password:continue
                if url in seen:continue
                seen.add(url)
                candidates.append({'title':title[:240],'url':url,'feed_published_at':published.astimezone(timezone.utc).isoformat(),
                                   'evidence_note':'RSSの見出しと日時。記事本文・一次資料の裏取りは別途必要。'})
        except Exception:
            continue # A missing feed is optional, never treated as verified news.
    ordered=sorted(candidates,key=lambda x:x['feed_published_at'],reverse=True)
    # Keep domestic candidates even when newer overseas headlines fill the feed.
    chosen=[]
    for region,limit in (('domestic',15),('overseas',15),('unknown',5)):
        chosen.extend([x for x in ordered if candidate_region(x)==region][:limit])
    return sorted(chosen,key=lambda x:x['feed_published_at'],reverse=True)
