"""Provision only the dedicated local cluster created for this project."""
import json
import os
import secrets
import sys
from pathlib import Path
import psycopg
from psycopg import sql

ROOT=Path(__file__).resolve().parents[1]
STATE=Path(os.environ.get(
    'CTR_STATE_ROOT',
    Path.home()/'.local'/'share'/'china-travel-reddit-research',
)).expanduser().resolve()
CONFIG=Path(os.environ.get(
    'CTR_CONFIG_DIR',
    Path.home()/'.config'/'china-travel-reddit-research',
)).expanduser().resolve()
PRIVATE=Path(os.environ.get('CTR_PRIVATE_ROOT', STATE/'private')).expanduser().resolve()
PGDATA=Path(os.environ.get('CTR_PGDATA', STATE/'runtime'/'pgdata')).expanduser().resolve()
PGSOCKET=os.environ.get('CTR_PGSOCKET','/tmp/ctr-pg')
PGPORT=int(os.environ.get('CTR_PGPORT','55483'))
ADMIN=f'host={PGSOCKET} port={PGPORT} user=ctr_admin dbname=postgres'

def main():
    with psycopg.connect(ADMIN,autocommit=True) as c:
        identity=c.execute('SELECT current_user,current_setting(\'data_directory\')').fetchone()
        expected=str(PGDATA)
        if identity!=('ctr_admin',expected):raise RuntimeError('dedicated_cluster_identity_mismatch')
        CONFIG.mkdir(parents=True,exist_ok=True,mode=0o700)
        creds={}
        for role in ('ctr_owner','ctr_writer','ctr_reader'):
            password=secrets.token_urlsafe(32)
            if c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                raise RuntimeError('already_provisioned_use_existing_config')
            c.execute(sql.SQL('CREATE ROLE {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}').format(sql.Identifier(role),sql.SQL('NOLOGIN' if role=='ctr_owner' else 'LOGIN'),sql.Literal(password)))
            creds[role]=password
        for name in ('ctr_reddit','ctr_fixture'):
            c.execute(sql.SQL('CREATE DATABASE {} OWNER ctr_owner TEMPLATE template0').format(sql.Identifier(name)))
            c.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
            c.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO ctr_writer,ctr_reader').format(sql.Identifier(name)))
            with psycopg.connect(ADMIN.replace('dbname=postgres',f'dbname={name}')) as db:
                db.execute('SET ROLE ctr_owner')
                for migration in sorted((ROOT/'sql').glob('*.sql')):db.execute(migration.read_text())
            cfg={'database':name,'private_root':str(PRIVATE/name),'reddit':{}}
            for mode,role in [('writer','ctr_writer'),('reader','ctr_reader')]:
                cfg[mode+'_dsn']=f'host=127.0.0.1 port={PGPORT} dbname={name} user={role} password={creds[role]}'
            path=CONFIG/('local.json' if name=='ctr_reddit' else 'fixture.json')
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(cfg,f)
            Path(cfg['private_root']).mkdir(parents=True,exist_ok=True,mode=0o700)
    print(json.dumps({'provisioned':['ctr_reddit','ctr_fixture'],'roles':['ctr_owner','ctr_writer','ctr_reader'],'secrets_printed':False}))
if __name__=='__main__':main()
