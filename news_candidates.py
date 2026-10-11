"""Read current public RSS headlines; publication still requires checked sources."""
from datetime import timezone
from email.utils import parsedate_to_datetime
import re
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

FEEDS=('https://www.bleepingcomputer.com/feed/',
       'https://www.techradar.com/rss',
       'https://www.bing.com/news/search?q=AI&format=rss&mkt=ja-JP')
AI=re.compile(r'AI(?![a-z])|人工知能|生成|Claude|ChatGPT|Anthropic|OpenAI|Gemini|NVIDIA|Copilot|Higgsfield',re.I)

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
    for feed in FEEDS:
        try:
            request=Request(feed,headers={'User-Agent':'mako-note-news-research/1.0'})
            with urlopen(request,timeout=20) as response:
                root=ET.fromstring(response.read(3000000))
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
    return sorted(candidates,key=lambda x:x['feed_published_at'],reverse=True)[:35]
