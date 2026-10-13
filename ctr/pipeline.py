import json
import os
import uuid
import urllib.parse
from pathlib import Path
from .core import digest,now,parse_listing,parse_optional_answers,parse_info,write_json
from .db import register,statistics,make_manifest,import_manifest,purge,reconcile_audits
from .network import gate,Fetcher,StopRound,AccessBlocked

STAGES={'smoke':3,'small1':20,'small2':20,'scale':50,'sync':20}

def fingerprint(source,answers=False):
    code=[(p.name,p.read_text()) for p in sorted(Path(__file__).parent.glob('*.py'))]
    return digest([source,code,answers])

def stage_gate(conn,stage,fp):
    previous={'small1':'smoke','small2':'small1','scale':'small2'}.get(stage)
    if previous:
        row=conn.execute('SELECT status,fingerprint FROM runs WHERE stage=%s ORDER BY started_at DESC LIMIT 1',(previous,)).fetchone()
        if not row or row['status']!='passed' or row['fingerprint']!=fp:
            raise AccessBlocked('required_previous_real_stage_not_passed')
    conn.commit()

def finish(conn,cfg,rid,status,summary):
    # PostgreSQL authoritative; files are reconcilable exports.
    with conn.transaction():
        conn.execute('UPDATE runs SET status=%s,ended_at=now(),summary=%s WHERE id=%s',(status,json.dumps(summary,default=str),rid))
        conn.execute('INSERT INTO progress(run_id,status,summary) VALUES(%s,%s,%s)',(rid,status,json.dumps(summary,default=str)))
    export_progress(conn,cfg)

def export_progress(conn,cfg):
    rows=conn.execute('SELECT seq,run_id,status,recorded_at,summary FROM progress ORDER BY seq').fetchall()
    root=Path(cfg['private_root'])
    write_json(root/'state.json',rows[-1] if rows else {})
    temp=root/'progress.tmp'
    with temp.open('w') as f:
        for row in rows:
            f.write(json.dumps(row,default=str)+'\n')
        f.flush();os.fsync(f.fileno())
    temp.chmod(0o600);temp.replace(root/'progress.jsonl')
    for row in conn.execute('SELECT id,status,summary FROM runs WHERE ended_at IS NOT NULL').fetchall():
        write_json(root/'summaries'/f'{row["id"]}.json',dict(row))
    conn.commit()

def expire(conn,cfg):
    # Purge all expired UGC including archives with no eligible question.
    objects=conn.execute('SELECT id FROM objects WHERE expires_at<=now() AND NOT deleted').fetchall()
    for row in objects:
        with conn.transaction():
            purge(conn,row['id'],None,'retention_expired')
    conn.commit()
    for a in conn.execute('SELECT hash,path FROM archives WHERE expires_at<=now() AND purged_at IS NULL').fetchall():
        if a['path']: Path(a['path']).unlink(missing_ok=True)
        for m in conn.execute('SELECT path,payload_path FROM manifests WHERE archive_hash=%s',(a['hash'],)).fetchall():
            for k in ('path','payload_path'):
                if m[k]:Path(m[k]).unlink(missing_ok=True)
        conn.execute('UPDATE archives SET path=NULL,purged_at=now() WHERE hash=%s',(a['hash'],))
        conn.execute('UPDATE manifests SET path=NULL,payload_path=NULL,purged_at=now() WHERE archive_hash=%s',(a['hash'],))
    conn.commit();reconcile_audits(conn,cfg);conn.commit()
    return len(objects)

def run(conn,cfg,source,stage,allow_network=False,answers=False):
    if not allow_network:
        raise AccessBlocked('explicit_allow_network_flag_required')
    register(conn,source)
    fp=fingerprint(source,answers);rid=uuid.uuid4()
    conn.execute('INSERT INTO runs(id,source_id,stage,fingerprint,status) VALUES(%s,%s,%s,%s,%s)',(rid,source['source_id'],stage,fp,'running'));conn.commit()
    conn.execute('INSERT INTO progress(run_id,status,summary) VALUES(%s,%s,%s)',(rid,'running','{}'));conn.commit()
    before=statistics(conn)['qualified_questions'];conn.commit()
    summary=dict(run_id=str(rid),stage=stage,scope='questions_with_optional_answers' if answers else 'questions_only',requests_attempted=0,requests_succeeded=0,archived=0,parsed=0,new_questions=0,raw_content_read_by_model=0,reason=None)
    fetcher=None
    locked=False
    try:
        locked=conn.execute("SELECT pg_try_advisory_lock(68433581) locked").fetchone()['locked'];conn.commit()
        if not locked: raise AccessBlocked('another_project_collector_is_running')
        token,ua=gate(cfg,source)
        if answers and not cfg.get('reddit',{}).get('approved_optional_comments'):
            raise AccessBlocked('optional_comments_not_in_approved_scope')
        stage_gate(conn,stage,fp)
        health=conn.execute('SELECT health,no_new FROM sources WHERE id=%s',(source['source_id'],)).fetchone();conn.commit()
        if stage=='scale' and health['no_new']>=2:
            raise AccessBlocked('two_zero_new_rounds_review_discovery_before_scaling')
        expire(conn,cfg)
        budget=min(STAGES[stage],source['request_budget'])
        fetcher=Fetcher(conn,cfg,source,rid,budget,token,ua)
        cursor=conn.execute('SELECT cursor FROM sources WHERE id=%s',(source['source_id'],)).fetchone()['cursor'];conn.commit()
        if cursor=='EXHAUSTED' and stage!='sync':cursor=None
        listings=0
        while fetcher.count<budget:
            # Smoke = one discovery request + two post rechecks. Small rounds mix discovery and refresh;
            # never collect comments merely to consume budget.
            known=conn.execute("SELECT post_id FROM frontier WHERE state!='terminal' ORDER BY observed_at LIMIT 100").fetchall();conn.commit()
            use_listing=stage!='sync' and (not known or (listings<max(1,budget//2) and (not cursor=='EXHAUSTED')))
            expected=[]
            answer_post=None
            if answers and known and fetcher.count % 5 == 4 and stage!='sync':
                answer_post=known[0]['post_id']
                url=f'https://oauth.reddit.com/r/travelchina/comments/{answer_post}?limit=20&depth=1&sort=top&raw_json=1'
                use_listing=False
            elif use_listing:
                query={'limit':100,'raw_json':1}
                if cursor and cursor!='EXHAUSTED':query['after']=cursor
                url='https://oauth.reddit.com/r/travelchina/new?'+urllib.parse.urlencode(query)
            elif known:
                stored=conn.execute('SELECT id FROM objects WHERE NOT deleted ORDER BY observed_at LIMIT 100').fetchall();conn.commit()
                expected=[x['id'] for x in stored] or ['t3_'+x['post_id'] for x in known]
                url='https://oauth.reddit.com/api/info?'+urllib.parse.urlencode({'id':','.join(expected),'raw_json':1})
            else:
                raise StopRound('frontier_empty')
            request_id,h,body=fetcher.fetch(url)
            summary['requests_succeeded']+=1;summary['archived']+=1
            try:
                if answer_post:
                    records=parse_optional_answers(body,answer_post,source);after=None
                elif expected:
                    qs={x['id']:x for x in conn.execute("SELECT id,post_id,topic,eligible FROM objects WHERE kind='post' AND NOT deleted").fetchall()};conn.commit()
                    records,after=parse_info(body,source,qs)
                else:
                    records,after=parse_listing(body,source)
                if expected and set(x['id'] for x in records)-set(expected):
                    raise ValueError('unexpected_recheck_identity')
            except (ValueError,KeyError,TypeError,RecursionError):
                fetcher.error(url,'parse_failed',True,request_id)
                raise StopRound('parse_failed')
            summary['parsed']+=1
            mid=make_manifest(conn,cfg,rid,request_id,h,records)
            import_manifest(conn,mid)
            with conn.transaction():
                if use_listing:
                    for r in records:
                        size=conn.execute('SELECT count(*) n FROM frontier').fetchone()['n']
                        if size<source['max_frontier']:
                            conn.execute('INSERT INTO frontier(post_id,source_id,discovered_run) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(r['post_id'],source['source_id'],rid))
                    if after and (not __import__('re').fullmatch(r't3_[a-z0-9]+',after) or after==cursor):
                        raise StopRound('invalid_or_repeated_cursor')
                    cursor=after or 'EXHAUSTED'
                    conn.execute('UPDATE sources SET cursor=%s WHERE id=%s',(cursor,source['source_id']))
                    listings+=1
                elif not answer_post:
                    observed={r['id'] for r in records}
                    for oid in set(expected)-observed:
                        # /api/info is explicit reconciliation, unlike missing comments in a truncated tree.
                        purge(conn,oid,rid,'unavailable_on_recheck')
                    conn.execute('UPDATE frontier SET observed_at=now() WHERE post_id=ANY(%s)',([x[3:] for x in expected if x.startswith('t3_')],))
            reconcile_audits(conn,cfg);conn.commit()
            if stage=='scale' and statistics(conn)['qualified_questions']>=500:
                conn.commit();break
            conn.commit()
        stats=statistics(conn);conn.commit()
        summary.update(stats)
        summary['new_questions']=stats['qualified_questions']-before
        success=(summary['requests_succeeded']>=budget or (stage=='scale' and stats['qualified_questions']>=500)) and summary['parsed']==summary['requests_succeeded'] and stats['unresolved_errors']==0 and stats['unsafe_backlog']==0 and (stats['qualified_questions']>0 or stage=='sync')
        status='passed' if success else 'failed'
        if status=='failed':summary['reason']='quality_gate_not_passed'
        no_new=health['no_new']+1 if summary['new_questions']<=0 else 0
        conn.execute('UPDATE sources SET no_new=%s,health=%s WHERE id=%s',(no_new,'cooldown' if no_new>=2 else ('healthy' if success else 'degraded'),source['source_id']));conn.commit()
    except AccessBlocked as e:
        status='blocked';summary['reason']=str(e)
    except StopRound as e:
        conn.rollback();status='stopped';summary['reason']=str(e)
    except Exception:
        conn.rollback();status='failed';summary['reason']='unexpected_internal_error_see_local_debug'
        # No raw exception/HTTP header/credential may enter model output.
        raise
    finally:
        if fetcher:summary['requests_attempted']=fetcher.count
        if locked:
            conn.execute('SELECT pg_advisory_unlock(68433581)');conn.commit()
        if 'status' in locals():
            summary['terminal_status']=status
            finish(conn,cfg,rid,status,summary)
    return summary
