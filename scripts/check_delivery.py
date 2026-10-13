"""Source-tree audit. Never traverse private archives or inspect secret values."""
from pathlib import Path
import json
import subprocess

root=Path(__file__).resolve().parents[1]
files=[p for p in root.rglob('*') if p.is_file() and not any(x in p.relative_to(root).parts for x in ['.git','__pycache__','.venv','private','work'])]
for p in files:
    assert p.suffix not in ('.pem','.key'),f'forbidden_file:{p.name}'
    assert not p.name.startswith('.env'),f'forbidden_file:{p.name}'
    assert p.name not in ('local.json','fixture.json'),f'private_config:{p.name}'
    s=p.read_text()
    for marker in [
        'postgres'+'ql://',
        'Bearer '+'eyJ',
        'BEGIN '+'PRIVATE KEY',
        '/Users'+'/',
        'github'+'_pat_',
        'gh'+'p_',
        'REDDIT'+'BGDKMDB',
    ]:
        assert marker not in s,f'possible_secret:{p.name}'
    if p.suffix=='.py' and p.name!='check_delivery.py':
        assert 'import sqlite3' not in s
        assert 'import duckdb' not in s
source=json.loads((root/'config'/'source.json').read_text())
assert source['enabled'] is False
assert source['access_review']['operator_approval'] is False
assert source['access_review']['platform_approval']=='missing'
print(json.dumps({'source_files_audited':len(files),'private_files_in_tree':False,'alternate_database_imports':0,'source_enabled':False,'approval_embedded':False}))
