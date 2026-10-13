import json
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch
from ctr.core import digest
from ctr.db import config,connect,bind_root,register
from ctr.pipeline import run,stage_gate,fingerprint
from ctr.network import AccessBlocked
import test_system as fixtures
SOURCE,FIXTURE,listing=fixtures.SOURCE,fixtures.FIXTURE,fixtures.listing

class FullPipeline(unittest.TestCase):
    setUpClass=classmethod(fixtures.Integration.setUpClass.__func__)
    tearDownClass=classmethod(fixtures.Integration.tearDownClass.__func__)
    setUp=fixtures.Integration.setUp
    # Inherit database isolation and helper only; run a complete fake-transport smoke separately.
    def test_13_fake_transport_smoke_manifest_and_cursor(self):
        c=self.conn;cfg=self.cfg
        source=dict(SOURCE,enabled=True)
        bodies=[listing(),listing(),listing()]
        index=0
        def fake_fetch(fetcher,url):
            nonlocal index
            body=bodies[index];index+=1;fetcher.count+=1
            rid=uuid.uuid4();h=digest(body);p=Path(cfg['private_root'])/'archive'/h;p.write_bytes(body)
            c.execute('INSERT INTO requests(id,run_id,url,outcome,attempted_at,attempt,final_url) VALUES(%s,%s,%s,%s,now(),0,%s)',(rid,fetcher.run_id,url,'success',url))
            c.execute("INSERT INTO archives VALUES(%s,%s,now(),now()+interval '48 hours',NULL) ON CONFLICT DO NOTHING",(h,str(p)))
            c.execute('INSERT INTO request_archives VALUES(%s,%s)',(rid,h));c.commit()
            return rid,h,body
        with patch('ctr.pipeline.gate',return_value=('synthetic','synthetic')),patch('ctr.pipeline.Fetcher.fetch',fake_fetch),patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('external_network_forbidden')):
            result=run(c,cfg,source,'smoke',True)
        self.assertEqual(result['terminal_status'],'passed')
        self.assertEqual(result['requests_attempted'],3);self.assertEqual(result['qualified_questions'],1)
        self.assertEqual(c.execute("SELECT count(*) n FROM manifests WHERE imported_at IS NOT NULL").fetchone()['n'],3)
        self.assertEqual(c.execute("SELECT cursor FROM sources").fetchone()['cursor'],'EXHAUSTED');c.commit()
        stage_gate(c,'small1',fingerprint(source));c.commit()
        with self.assertRaises(AccessBlocked):stage_gate(c,'small2',fingerprint(source))
