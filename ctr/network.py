"""Only approved OAuth endpoints; no proxy, redirects, scraping or content logging."""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import timedelta, datetime
from pathlib import Path
from .core import now, digest

class AccessBlocked(Exception):
    pass

class StopRound(Exception):
    pass

def gate(cfg,source):
    access=cfg.get('reddit',{})
    required=['approval_file','approved_purpose','approved_mechanism','approved_subreddits','approval_expires_at','token_file','user_agent']
    if not all(access.get(k) for k in required):
        raise AccessBlocked('reddit_explicit_research_approval_and_oauth_missing')
    if not source['enabled']:
        raise AccessBlocked('source_disabled_pending_approval_review')
    if access['approved_purpose']!='private_china_travel_question_research' or access['approved_mechanism']!='official_oauth':
        raise AccessBlocked('approval_scope_mismatch')
    if not set(source['subreddits']).issubset(access['approved_subreddits']):
        raise AccessBlocked('subreddit_not_approved')
    if datetime.fromisoformat(access['approval_expires_at'])<=now():
        raise AccessBlocked('approval_expired')
    if not Path(access['approval_file']).is_file() or Path(access['approval_file']).stat().st_size==0:
        raise AccessBlocked('approval_evidence_missing')
    token=Path(access['token_file'])
    if token.stat().st_mode & 0o077:
        raise AccessBlocked('token_file_not_private')
    if not re.fullmatch(r'[^\r\n]+:[^\r\n]+:v[^\r\n]+ \(by /u/[A-Za-z0-9_-]+\)',access['user_agent']):
        raise AccessBlocked('descriptive_user_agent_required')
    return token.read_text().strip(),access['user_agent']

def validate_url(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme!='https' or p.hostname!='oauth.reddit.com' or p.port or p.username or p.fragment:
        raise ValueError('url_not_allowed')
    if not (re.fullmatch(r'/r/travelchina/new',p.path) or p.path=='/api/info' or re.fullmatch(r'/r/travelchina/comments/[a-z0-9]+',p.path)):
        raise ValueError('endpoint_not_allowed')
    q=urllib.parse.parse_qs(p.query)
    if set(q)-{'limit','raw_json','after','id','sort','depth'}:
        raise ValueError('query_not_allowed')
    if 'after' in q and not re.fullmatch(r't3_[a-z0-9]+',q['after'][0]):
        raise ValueError('cursor_not_allowed')
    if 'id' in q and not re.fullmatch(r't[13]_[a-z0-9]+(?:,t[13]_[a-z0-9]+){0,99}',q['id'][0]):
        raise ValueError('ids_not_allowed')
    return url

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

class Fetcher:
    def __init__(self,conn,cfg,source,run_id,budget,token,ua):
        self.conn,self.cfg,self.source,self.run_id=conn,cfg,source,run_id
        self.budget=budget
        self.count=0
        self.token,self.ua=token,ua
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

    def reserve(self):
        with self.conn.transaction():
            self.conn.execute("INSERT INTO host_limits(host,next_at) VALUES('oauth.reddit.com',now()) ON CONFLICT DO NOTHING")
            row=self.conn.execute("SELECT next_at FROM host_limits WHERE host='oauth.reddit.com' FOR UPDATE").fetchone()
            due=max(now(),row['next_at'])
            self.conn.execute("UPDATE host_limits SET next_at=%s WHERE host='oauth.reddit.com'",(due+timedelta(seconds=self.source['host_rate_limit_seconds']),))
        delay=(due-now()).total_seconds()
        if delay>0:
            time.sleep(delay)

    def error(self,url,kind,terminal,rid):
        eid=digest([self.source['source_id'],url,kind])
        self.conn.execute('INSERT INTO errors(external_id,source_id,url,kind,terminal,first_at,last_at,latest_request) VALUES(%s,%s,%s,%s,%s,now(),now(),%s) ON CONFLICT(external_id) DO UPDATE SET last_at=now(),retry_count=errors.retry_count+1,latest_request=excluded.latest_request,resolved_at=NULL,resolution_request=NULL',(eid,self.source['source_id'],url,kind,terminal,rid))
        self.conn.commit()

    def fetch(self,url):
        validate_url(url)
        if self.conn.execute('SELECT 1 FROM errors WHERE url=%s AND terminal AND resolved_at IS NULL',(url,)).fetchone():
            self.conn.commit()
            raise StopRound('terminal_url_blocked')
        self.conn.commit()
        for attempt in range(self.source['max_retries']+1):
            if self.count>=self.budget:
                raise StopRound('request_budget_exhausted')
            self.reserve()
            self.count+=1
            rid=uuid.uuid4(); started=now(); status=None; mime=''; body=b''; kind=''; retry_after=0
            try:
                request=urllib.request.Request(url,headers={'Authorization':'Bearer '+self.token,'User-Agent':self.ua,'Accept':'application/json','Accept-Encoding':'identity'})
                with self.opener.open(request,timeout=45) as response:
                    status=response.status
                    mime=response.headers.get_content_type()
                    if response.headers.get('X-Ratelimit-Remaining') is not None and float(response.headers['X-Ratelimit-Remaining'])<=1:
                        retry_after=max(0,float(response.headers.get('X-Ratelimit-Reset','60')))
                    body=response.read(self.source['max_file_bytes']+1)
                if len(body)>self.source['max_file_bytes']:
                    kind='oversize'; body=b''
                elif mime!='application/json':
                    kind='unsupported_mime'; body=b''
                elif not body.lstrip().startswith((b'{',b'[')):
                    kind='invalid_json_signature'
            except urllib.error.HTTPError as e:
                status=e.code; kind=f'http_{status}'
                # Error bodies may contain secrets or UGC and are not logged/archived.
                if status==429:
                    try: retry_after=max(60,float(e.headers.get('Retry-After','60')))
                    except ValueError: retry_after=60
            except (urllib.error.URLError,TimeoutError,OSError):
                kind='transport'
            self.conn.execute('INSERT INTO requests(id,run_id,url,status,outcome,attempted_at,elapsed_ms,mime,bytes,attempt,final_url) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(rid,self.run_id,url,status,kind or 'success',started,int((now()-started).total_seconds()*1000),mime,len(body),attempt,url))
            self.conn.commit()
            if retry_after:
                self.conn.execute("UPDATE host_limits SET next_at=greatest(next_at,%s) WHERE host='oauth.reddit.com'",(now()+timedelta(seconds=retry_after),));self.conn.commit()
            if kind:
                retryable=status in (408,429) or (status is not None and status>=500) or kind=='transport'
                self.error(url,kind,not retryable,rid)
                if status in (401,403,429):
                    raise StopRound(kind) # No same-round retry for access denial or rate limit.
                if retryable and attempt<self.source['max_retries']:
                    time.sleep(2**attempt)
                    continue
                raise StopRound(kind)
            h=digest(body); root=Path(self.cfg['private_root']);path=root/'archive'/h[:2]/h
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            # Purged content must never be resurrected by a repeated response.
            a=self.conn.execute('SELECT purged_at FROM archives WHERE hash=%s',(h,)).fetchone()
            if a and a['purged_at']:
                self.conn.commit();raise StopRound('previously_purged_archive')
            if not path.exists():
                with path.open('xb') as f:
                    f.write(body);f.flush();__import__('os').fsync(f.fileno())
                path.chmod(0o600)
            self.conn.execute('INSERT INTO archives(hash,path,created_at,expires_at) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(h,str(path),started,started+timedelta(hours=self.source['retention_hours'])))
            self.conn.execute('INSERT INTO request_archives VALUES(%s,%s)',(rid,h))
            self.conn.execute('UPDATE errors SET resolved_at=now(),resolution_request=%s WHERE source_id=%s AND url=%s AND resolved_at IS NULL',(rid,self.source['source_id'],url))
            self.conn.commit()
            return rid,h,body
        raise StopRound('retries_exhausted')
