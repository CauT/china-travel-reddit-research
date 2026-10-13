import argparse
import json
import sys
import time
from pathlib import Path
from .core import validate_source,write_json
from .db import config,connect,bind_root,import_manifest,statistics,CONFIG_DEFAULT
from .pipeline import run,expire,export_progress,fingerprint
from .network import AccessBlocked

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description='Private Reddit travel questions collection; PostgreSQL only.')
    p.add_argument('--config',default=str(CONFIG_DEFAULT))
    p.add_argument('--source',default=str(ROOT/'config/source.json'))
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('validate')
    r=sub.add_parser('collect');r.add_argument('--stage',choices=['smoke','small1','small2','scale','sync'],required=True);r.add_argument('--allow-network',action='store_true');r.add_argument('--answers',action='store_true')
    c=sub.add_parser('campaign');c.add_argument('--allow-network',action='store_true');c.add_argument('--answers',action='store_true');c.add_argument('--max-rounds',type=int,default=10)
    m=sub.add_parser('maintain');m.add_argument('--cycles',type=int,default=24);m.add_argument('--interval-seconds',type=int,default=3600);m.add_argument('--allow-network',action='store_true')
    sub.add_parser('stats');sub.add_parser('audit');sub.add_parser('expire');sub.add_parser('reconcile')
    q=sub.add_parser('query');q.add_argument('--topic');q.add_argument('--limit',type=int,default=10)
    q=sub.add_parser('replay');q.add_argument('manifest_id')
    args=p.parse_args()
    try:
        source=validate_source(json.loads(Path(args.source).read_text()))
        if args.command=='validate':
            print(json.dumps({'source_valid':True,'enabled':source['enabled'],'scope':'questions_with_optional_answers'}));return
        cfg=config(args.config)
        with bind_root(connect(cfg,reader=args.command in ('stats','query')),cfg) as conn:
            if args.command=='maintain':
                if not 1<=args.cycles<=48 or not 60<=args.interval_seconds<=3600:raise ValueError('maintenance_bounds_invalid')
                result=[]
                for index in range(args.cycles):
                    cycle={'expired_objects_purged':expire(conn,cfg)}
                    if args.allow_network:cycle['sync']=run(conn,cfg,source,'sync',True)
                    cycle['expired_objects_purged']+=expire(conn,cfg)
                    result.append(cycle)
                    if index+1<args.cycles:time.sleep(args.interval_seconds)
            elif args.command=='campaign':
                if not 1<=args.max_rounds<=30:raise ValueError('max_rounds_1_to_30')
                result=[]
                for _ in range(args.max_rounds):
                    row=run(conn,cfg,source,'scale',args.allow_network,args.answers);result.append(row)
                    if row['terminal_status']!='passed' or row.get('qualified_questions',0)>=500:break
            elif args.command=='collect':result=run(conn,cfg,source,args.stage,args.allow_network,args.answers)
            elif args.command=='stats':result=statistics(conn)
            elif args.command=='query':
                if not 1<=args.limit<=50:raise ValueError('query_limit_1_to_50')
                result=conn.execute('SELECT * FROM safe_questions WHERE (%s::text IS NULL OR topic=%s) ORDER BY observed_at DESC LIMIT %s',(args.topic,args.topic,args.limit)).fetchall()
            elif args.command=='replay':result=import_manifest(conn,args.manifest_id)
            elif args.command=='expire':result={'expired_objects_purged':expire(conn,cfg)}
            elif args.command=='reconcile':export_progress(conn,cfg);result={'progress_reconciled':True}
            else:result=audit(conn,cfg)
        print(json.dumps(result,ensure_ascii=False,default=str))
        if args.command=='collect' and result['terminal_status']!='passed':sys.exit(2)
        if args.command=='campaign' and (not result or result[-1].get('qualified_questions',0)<500):sys.exit(2)
    except AccessBlocked as e:
        print(json.dumps({'status':'blocked','reason':str(e)}));sys.exit(2)
    except (ValueError,FileNotFoundError):
        print(json.dumps({'status':'error','reason':'configuration_or_validation_failed_no_secrets_logged'}));sys.exit(2)

def audit(conn,cfg):
    stats=statistics(conn)
    base=['sources','runs','host_limits','requests','archives','request_archives','archive_members','errors','frontier','manifests','objects','versions','tombstones','deletion_audit','progress','schema_version','evidence']
    exposures=sum(conn.execute("SELECT has_table_privilege('ctr_reader',%s,'SELECT') permitted",('research.'+t,)).fetchone()['permitted'] for t in base)
    roles=conn.execute("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname IN ('ctr_writer','ctr_reader')").fetchall()
    source=conn.execute('SELECT source_id,count(*) n FROM safe_questions GROUP BY source_id').fetchall()
    times=conn.execute('SELECT min(published_at) earliest,max(published_at) latest FROM safe_questions').fetchone()
    stages=conn.execute('SELECT stage,status,count(*) n FROM runs GROUP BY stage,status').fetchall()
    coverage=conn.execute('SELECT topic,count(*) n FROM safe_questions GROUP BY topic').fetchall()
    lineage=conn.execute('SELECT count(*) n FROM objects o WHERE NOT deleted AND NOT EXISTS(SELECT 1 FROM versions v WHERE v.object_id=o.id)').fetchone()['n']
    result=dict(stats,reader_base_table_exposures=exposures,reader_roles=roles,missing_version_lineage=lineage,source_coverage=source,time_coverage=dict(times),topic_coverage=coverage,stages=stages,raw_content_read_by_model=0)
    latest=[conn.execute('SELECT status,fingerprint FROM runs WHERE stage=%s ORDER BY started_at DESC LIMIT 1',(s,)).fetchone() for s in ['smoke','small1','small2']]
    consistent=all(x and x['status']=='passed' for x in latest) and len({x['fingerprint'] for x in latest if x})==1
    result['answer_coverage']=stats['questions_with_answers']/stats['qualified_questions'] if stats['qualified_questions'] else None
    result['unsafe_rejected']=conn.execute('SELECT count(*) n FROM objects WHERE unsafe AND NOT deleted').fetchone()['n']
    profiles=[x['profile'] for x in conn.execute('SELECT profile FROM sources').fetchall()]
    current_fingerprints={fingerprint(profile,mode) for profile in profiles for mode in (False,True)}
    consistent=consistent and all(x['fingerprint'] in current_fingerprints for x in latest if x)
    result['dataset_kind']='real_collection' if cfg['database']=='ctr_reddit' else 'synthetic_fixture'
    result['acceptance_500_passed']=cfg['database']=='ctr_reddit' and stats['qualified_questions']>=500 and exposures==0 and lineage==0 and stats['unresolved_errors']==0 and stats['unsafe_backlog']==0 and consistent
    return result
if __name__=='__main__':main()
