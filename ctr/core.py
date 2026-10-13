import hashlib
import html
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

PARSER_VERSION = 'reddit-tree-1'
CLEANER_VERSION = 'privacy-1'
TOPICS = {
    'entry_visa': r'\b(visa|entry|transit|passport|immigration)\b',
    'payments': r'\b(alipay|wechat pay|payment|cash|credit card|bank card|atm)\b',
    'connectivity': r'\b(esim|sim card|vpn|internet|roaming|wifi)\b',
    'transport': r'\b(train|rail|ticket|taxi|didi|metro|flight|airport|bus)\b',
    'accommodation': r'\b(hotel|hostel|accommodation|stay|booking)\b',
    'safety_health': r'\b(safe|safety|allerg|medicine|hospital|scam)\b',
    'itinerary': r'\b(itinerary|visit|travel|trip|tour|days|beijing|shanghai|chengdu|xian|china)\b',
}
INJECTION = re.compile(r'ignore (?:all |the |any |previous )*(?:instructions|prompts)|system\s*prompt|developer\s*message|reveal.{0,20}(?:secret|token)|<\|(?:system|assistant)|BEGIN.{0,8}PRIVATE KEY', re.I)
PII = [
    r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', r'https?://\S+', r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
    r'\b(?:\+?\d[\d ()-]{7,}\d)\b', r'\b(?:u/|/u/)[A-Za-z0-9_-]+',
    r'\b(?:Bearer\s+\S+|(?:api[_ -]?key|token|password|cookie)\s*[:=]\s*\S+)',
    r'\b\d+\s+[\w ]{1,40}\b(?:Street|Road|Avenue|Lane|Drive|St\.|Rd\.)\b',
    r'\b[A-Z]{1,2}\d{6,9}\b',
]

def now():
    return datetime.now(timezone.utc)

def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True, default=str)
        f.flush()
        import os
        os.fsync(f.fileno())
    tmp.chmod(0o600)
    tmp.replace(path)

def clean(text):
    text = html.unescape(text or '')
    # Quotes and URLs are not answer evidence; never concatenate quoted answers.
    text = '\n'.join(x for x in text.splitlines() if not x.lstrip().startswith('>'))
    unsafe = bool(INJECTION.search(text))
    if unsafe:
        return '', True
    text = re.sub(r'<[^>]*>', ' ', text)
    for pattern in PII:
        text = re.sub(pattern, '[redacted]', text, flags=re.I)
    text = re.sub(r'\s+', ' ', text).strip()
    return text, False

def topic(text):
    for label, pattern in TOPICS.items():
        if re.search(pattern, text, re.I):
            return label
    return None

def is_question(text):
    return '?' in text or bool(re.search(r'\b(how|where|which|can i|can we|does|is it|do i|should|need help|advice|recommend|question|help with)\b', text, re.I))

def validate_source(source):
    assert source['allowed_domains'] == ['oauth.reddit.com']
    assert source['subreddits'] == ['travelchina']
    assert 20 <= source['host_rate_limit_seconds'] <= 60
    assert 3 <= source['request_budget'] <= 50
    assert 0 < source['max_file_bytes'] <= 5242880
    assert source['retention_hours'] <= 48
    assert source['max_retries'] <= 2
    assert source['max_frontier'] <= 1200
    return source

def parse_listing(raw, source):
    data=json.loads(raw)
    children=data['data']['children']
    if not isinstance(children,list) or len(children)>100:
        raise ValueError('invalid_listing')
    records=[]
    observed=now()
    for child in children:
        if child['kind']!='t3':
            raise ValueError('only_posts_allowed')
        d=child['data']
        if d['subreddit'].lower() not in source['subreddits'] or not re.fullmatch(r'[a-z0-9]+',d['id']):
            raise ValueError('identity_or_subreddit_mismatch')
        body=d.get('selftext','')
        deleted=body in ('[deleted]','[removed]') or bool(d.get('removed_by_category'))
        text,unsafe=clean(d.get('title','')+'\n'+body)
        if deleted:text=''
        label=topic(text)
        eligible=not deleted and not unsafe and not d.get('over_18') and not d.get('stickied') and bool(label) and is_question(text) and len(text)<=50000
        created=datetime.fromtimestamp(float(d['created_utc']),timezone.utc)
        edited=d.get('edited')
        records.append(dict(id='t3_'+d['id'],kind='post',post_id=d['id'],parent_id=None,
            canonical_url=f'https://www.reddit.com/r/{d["subreddit"].lower()}/comments/{d["id"]}/',
            published_at=created.isoformat(),updated_at=datetime.fromtimestamp(edited,timezone.utc).isoformat() if isinstance(edited,(int,float)) and not isinstance(edited,bool) and edited else None,
            observed_at=observed.isoformat(),expires_at=(observed+timedelta(hours=source['retention_hours'])).isoformat(),
            content_hash=digest({'text':text.casefold(),'deleted':deleted,'unsafe':unsafe}),deleted=deleted,unsafe=unsafe,topic=label,eligible=bool(eligible),safe_snippet=text[:360],clean_text=text[:50000]))
    return records,data['data'].get('after')

def parse_comment(d,q,source,seen=None):
    expected_post=q['post_id'];oid='t1_'+d['id']
    if not re.fullmatch(r't1_[a-z0-9]+',oid) or d.get('subreddit','').lower() not in source['subreddits'] or d.get('link_id')!='t3_'+expected_post or d.get('parent_id')!='t3_'+expected_post:
        raise ValueError('answer_relation_mismatch')
    deleted=d.get('body') in ('[deleted]','[removed]') or bool(d.get('removed_by_category'))
    text,unsafe=clean(d.get('body',''))
    if deleted:text=''
    useful=len(text)>=60 and len(text.split())>=10 and not text.endswith('?') and bool(re.search(r'\b(use|book|buy|take|try|need|must|can|could|should|recommend|worked|accepted|require|check|download|bring|pay|register)\b',text,re.I)) and bool(q['topic'] and re.search(TOPICS[q['topic']],text,re.I)) and not re.search(r'\b(affiliate|promo code|discount code|dm me|subscribe|my channel)\b',text,re.I)
    h=digest({'text':text.casefold(),'deleted':deleted,'unsafe':unsafe})
    eligible=useful and (seen is None or h not in seen) and not deleted and not unsafe and not d.get('stickied') and q['eligible'] and len(text)<=50000
    if seen is not None:seen.add(h)
    observed=now();created=datetime.fromtimestamp(float(d['created_utc']),timezone.utc);edited=d.get('edited')
    return dict(id=oid,kind='comment',post_id=expected_post,parent_id='t3_'+expected_post,
        canonical_url=f'https://www.reddit.com/r/travelchina/comments/{expected_post}/_/{d["id"]}/',
        published_at=created.isoformat(),updated_at=datetime.fromtimestamp(edited,timezone.utc).isoformat() if isinstance(edited,(int,float)) and not isinstance(edited,bool) and edited else None,
        observed_at=observed.isoformat(),expires_at=(observed+timedelta(hours=source['retention_hours'])).isoformat(),content_hash=h,deleted=deleted,unsafe=unsafe,topic=q['topic'],eligible=bool(eligible),safe_snippet=text[:360] if eligible else '',clean_text=text[:50000] if eligible else '')

def parse_optional_answers(raw,expected_post,source):
    data=json.loads(raw)
    if not isinstance(data,list) or len(data)!=2:
        raise ValueError('invalid_answer_tree')
    questions,_=parse_listing(json.dumps(data[0]).encode(),source)
    if len(questions)!=1 or questions[0]['post_id']!=expected_post:
        raise ValueError('answer_question_mismatch')
    if len(data[1]['data']['children'])>100:
        raise ValueError('answer_tree_too_large')
    q=questions[0];records=list(questions);seen=set()
    for child in data[1]['data']['children'][:20]:
        if child['kind']=='more':continue
        if child['kind']!='t1':raise ValueError('invalid_answer_kind')
        records.append(parse_comment(child['data'],q,source,seen))
    return records

def parse_info(raw,source,questions):
    data=json.loads(raw);children=data['data']['children'];records=[]
    if len(children)>100:raise ValueError('info_too_large')
    for child in children:
        if child['kind']=='t3':
            rows,_=parse_listing(json.dumps({'data':{'children':[child]}}).encode(),source)
            records.extend(rows)
        elif child['kind']=='t1':
            q=questions.get(child['data'].get('link_id'))
            if not q:raise ValueError('unknown_answer_question')
            records.append(parse_comment(child['data'],q,source))
        else:raise ValueError('invalid_info_kind')
    return records,None
