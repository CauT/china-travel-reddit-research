import json
import os
import uuid
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from .core import digest, now, write_json, PARSER_VERSION, CLEANER_VERSION

CONFIG_DEFAULT = Path(os.environ.get(
    'CTR_CONFIG',
    Path.home()/'.config'/'china-travel-reddit-research'/'local.json',
))

def config(path=CONFIG_DEFAULT):
    p = Path(path).expanduser().resolve()
    if p.stat().st_mode & 0o077:
        raise ValueError('private_config_permissions_must_be_0600')
    return json.loads(p.read_text())

def connect(cfg, reader=False):
    connection = psycopg.connect(cfg['reader_dsn' if reader else 'writer_dsn'], row_factory=dict_row)
    identity = connection.execute('SELECT current_database() db,current_user usr').fetchone()
    if identity != {'db':cfg['database'], 'usr':'ctr_reader' if reader else 'ctr_writer'} or cfg['database'] not in ('ctr_reddit','ctr_fixture'):
        connection.close()
        raise ValueError('database_identity_mismatch')
    connection.execute('SET search_path=research,pg_catalog')
    connection.commit()
    return connection

def register(conn, source):
    conn.execute('INSERT INTO sources(id,profile) VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET profile=excluded.profile', (source['source_id'],json.dumps(source)))
    conn.commit()

def make_manifest(conn, cfg, run_id, request_id, archive_hash, records):
    root=Path(cfg['private_root'])
    payload={'schema_version':1,'records':records}
    output_hash=digest(payload)
    mid=digest({'request':str(request_id),'output':output_hash,'parser':PARSER_VERSION,'cleaner':CLEANER_VERSION})
    pp=root/'payloads'/f'{mid}.json'
    mp=root/'manifests'/f'{mid}.json'
    header=dict(id=mid,run_id=str(run_id),request_id=str(request_id),archive_hash=archive_hash,stage='clean_extract',schema_version=1,parser_version=PARSER_VERSION,cleaner_version=CLEANER_VERSION,input_hash=archive_hash,output_hash=output_hash,payload_path=str(pp))
    write_json(pp,payload)
    write_json(mp,header)
    conn.execute('INSERT INTO manifests(id,run_id,request_id,archive_hash,stage,schema_version,parser_version,cleaner_version,input_hash,output_hash,path,payload_path) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING', (mid,run_id,request_id,archive_hash,'clean_extract',1,PARSER_VERSION,CLEANER_VERSION,archive_hash,output_hash,str(mp),str(pp)))
    conn.commit()
    return mid

def import_manifest(conn, mid):
    with conn.transaction():
        m=conn.execute('SELECT * FROM manifests WHERE id=%s FOR UPDATE',(mid,)).fetchone()
        if not m:
            raise ValueError('unknown_manifest')
        if m['purged_at']:
            return {'status':'purged','records':0}
        if m['imported_at']:
            return {'status':'replayed','records':0}
        payload=json.loads(Path(m['payload_path']).read_text())
        if digest(payload)!=m['output_hash'] or payload['schema_version']!=1:
            raise ValueError('manifest_hash_or_schema_mismatch')
        for r in payload['records']:
            conn.execute('INSERT INTO archive_members(request_id,object_id) VALUES(%s,%s) ON CONFLICT DO NOTHING',(m['request_id'],r['id']))
            if conn.execute('SELECT 1 FROM tombstones WHERE object_id=%s',(r['id'],)).fetchone():
                continue
            old=conn.execute('SELECT * FROM objects WHERE id=%s FOR UPDATE',(r['id'],)).fetchone()
            if r['deleted']:
                purge(conn, r['id'], m['run_id'], 'source_deleted')
                continue
            if old and old['observed_at'] >= __import__('datetime').datetime.fromisoformat(r['observed_at']):
                continue
            v=(old['version'] if old else 0)+(0 if old and old['content_hash']==r['content_hash'] else 1)
            cols=['id','kind','post_id','parent_id','canonical_url','published_at','updated_at','observed_at','expires_at','content_hash','deleted','unsafe','topic','eligible','safe_snippet','clean_text']
            values=[r[k] for k in cols]
            source=conn.execute('SELECT source_id FROM runs WHERE id=%s',(m['run_id'],)).fetchone()['source_id']
            conn.execute('INSERT INTO objects('+','.join(cols)+',source_id,version) VALUES('+','.join(['%s']*(len(cols)+2))+') ON CONFLICT(id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in cols if k!='id')+',version=excluded.version', values+[source,v])
            conn.execute('INSERT INTO versions(object_id,version,content_hash,safe_snippet,request_id,archive_hash,manifest_id,observed_at,clean_text) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(r['id'],v,r['content_hash'],r['safe_snippet'],m['request_id'],m['archive_hash'],mid,r['observed_at'],r['clean_text']))
            if r['kind']=='comment':
                conn.execute('DELETE FROM evidence WHERE answer_id=%s',(r['id'],))
                if r['eligible']:
                    conn.execute('INSERT INTO evidence(question_id,answer_id,locator) SELECT id,%s,%s FROM objects WHERE id=%s AND eligible AND NOT deleted ON CONFLICT DO NOTHING',(r['id'],r['id'],r['parent_id']))
        conn.execute('UPDATE manifests SET imported_at=now(),cursor=%s WHERE id=%s',(len(payload['records']),mid))
    return {'status':'imported','records':len(payload['records'])}

def purge(conn, oid, run_id, reason):
    # Pending audit is durable before content removal. DB metadata also records completion.
    cfg_root = getattr(conn, '_ctr_root', None)
    if cfg_root is None:
        raise ValueError('purge_private_root_required')
    root=Path(cfg_root)
    operation=str(uuid.uuid4())
    write_json(root/'deletion-audit'/f'{operation}.pending.json',dict(id=operation,object_id=oid,reason=reason,phase='pending'))
    targets=[oid]
    if oid.startswith('t3_'):
        targets += [x['id'] for x in conn.execute('SELECT id FROM objects WHERE post_id=%s',(oid[3:],)).fetchall()]
    for target in set(targets):
        conn.execute('INSERT INTO tombstones(object_id,reason,observed_at,run_id) VALUES(%s,%s,now(),%s) ON CONFLICT DO NOTHING',(target,reason,run_id))
        hashes=[x['hash'] for x in conn.execute('SELECT DISTINCT ra.hash FROM request_archives ra JOIN archive_members am USING(request_id) WHERE am.object_id=%s',(target,)).fetchall()]
        # New deleted objects may share the current response with living objects.
        for h in set(hashes):
            a=conn.execute('SELECT * FROM archives WHERE hash=%s FOR UPDATE',(h,)).fetchone()
            if a and a['path']:
                Path(a['path']).unlink(missing_ok=True)
            for m in conn.execute('SELECT * FROM manifests WHERE archive_hash=%s',(h,)).fetchall():
                for k in ('payload_path','path'):
                    if m[k]:
                        Path(m[k]).unlink(missing_ok=True)
            conn.execute('UPDATE archives SET path=NULL,purged_at=now() WHERE hash=%s',(h,))
            conn.execute('UPDATE manifests SET path=NULL,payload_path=NULL,purged_at=now() WHERE archive_hash=%s',(h,))
        conn.execute('DELETE FROM evidence WHERE question_id=%s OR answer_id=%s',(target,target))
        conn.execute('UPDATE versions SET safe_snippet=NULL,clean_text=NULL,content_hash=NULL,purged_at=now() WHERE object_id=%s',(target,))
        conn.execute('UPDATE objects SET deleted=true,safe_snippet=NULL,clean_text=NULL,content_hash=NULL,canonical_url=NULL,topic=NULL,eligible=false WHERE id=%s',(target,))
    conn.execute('INSERT INTO deletion_audit(id,object_id,phase,details) VALUES(%s,%s,%s,%s)',(operation,oid,'completed',json.dumps({'reason':reason,'targets':len(set(targets))})))
    # Completion file is reconciled from committed DB by reconcile_audits, never asserted before commit.
    return operation

def reconcile_audits(conn,cfg):
    root=Path(cfg['private_root'])/'deletion-audit'
    for row in conn.execute('SELECT id,object_id,phase FROM deletion_audit').fetchall():
        write_json(root/f'{row["id"]}.completed.json',dict(row))

def bind_root(conn,cfg):
    conn._ctr_root=cfg['private_root']
    return conn

def statistics(conn):
    return dict(conn.execute('SELECT * FROM safe_statistics').fetchone())
