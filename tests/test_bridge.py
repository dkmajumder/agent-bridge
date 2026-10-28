import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from agent_bridge.server import Store, Conflict, make_server

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name)/'state.sqlite')
        self.s = Store(self.path)
    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()
    def task(self, resource='repo/src'):
        return self.s.apply('task',dict(title='Refactor',resource=resource),'planner')['id']
    def test_competing_claims_have_one_winner(self):
        tid=self.task()
        def claim(i):
            try:
                return self.s.apply('claim',dict(id=tid),str(i))
            except Conflict:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            winners=[v for v in pool.map(claim,range(20)) if v]
        self.assertEqual(len(winners),1)
    def test_overlapping_resources_and_siblings(self):
        parent=self.task(); child=self.task('repo/src/auth'); sibling=self.task('repo/tests')
        self.s.apply('claim',dict(id=parent),'browser-agent')
        with self.assertRaises(Conflict):
            self.s.apply('claim',dict(id=child),'refactor-agent')
        self.s.apply('claim',dict(id=sibling),'refactor-agent')
    def test_expiry_fencing_and_old_token_rejected(self):
        tid=self.task()
        with patch('agent_bridge.server.time.time',return_value=100):
            old=self.s.apply('claim',dict(id=tid,ttl=5),'browser-agent')
        with patch('agent_bridge.server.time.time',return_value=106):
            new=self.s.apply('claim',dict(id=tid),'refactor-agent')
            self.assertEqual(new['fence'],2)
            with self.assertRaises(Conflict):
                self.s.apply('complete',dict(id=tid,lease_token=old['lease_token']),'browser-agent')
            self.s.apply('renew',dict(id=tid,lease_token=new['lease_token']),'refactor-agent')
            self.s.apply('complete',dict(id=tid,lease_token=new['lease_token']),'refactor-agent')
    def test_wrong_token_and_owner_rejected(self):
        tid=self.task(); claim=self.s.apply('claim',dict(id=tid),'a')
        for actor, token in [('b',claim['lease_token']),('a','invalid')]:
            with self.assertRaises(Conflict):
                self.s.apply('release',dict(id=tid,lease_token=token),actor)
    def test_persistence_and_validation(self):
        self.s.apply('message',dict(body='Handoff',recipient='refactor-agent'),'browser-agent')
        other=Store(self.path)
        self.assertEqual(other.apply('snapshot',{},'')['messages'][0]['body'],'Handoff')
        other.db.close()
        with self.assertRaises(ValueError):
            self.s.apply('claim',dict(id=self.task(),ttl=-1),'a')

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server=make_server(0,':memory:','test-secret')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.server.store.db.close()
    def call(self,path,data=None,token='test-secret',extra=None):
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',**(extra or {})}
        req=Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        with urlopen(req,timeout=5) as response:
            return json.load(response)
    def test_end_to_end(self):
        self.call('/api/register',dict(agent='browser-agent',name='Browser agent'))
        self.call('/api/register',dict(agent='refactor-agent',name='Refactoring agent'))
        task=self.call('/api/task',dict(agent='browser-agent',title='Auth',resource='repo/auth'))
        claim=self.call('/api/claim',dict(agent='refactor-agent',id=task['id']))
        state=self.call('/api/state')
        self.assertNotIn('token_hash',state['tasks'][task['id']])
        self.assertNotIn('lease_token',state['tasks'][task['id']])
        self.call('/api/message',dict(agent='refactor-agent',recipient='browser-agent',body='Ready to review'))
        self.call('/api/complete',dict(agent='refactor-agent',id=task['id'],lease_token=claim['lease_token']))
        self.assertEqual(self.call('/api/state')['tasks'][task['id']]['status'],'done')
    def test_auth_origin_host_and_bad_payload(self):
        for token, headers in [('wrong',{}),('test-secret',{'Origin':'https://evil.example'}),('test-secret',{'Host':'evil.example'})]:
            with self.assertRaises(HTTPError) as cm:
                self.call('/api/task',dict(agent='a',title='x',resource='r'),token,headers)
            self.assertEqual(cm.exception.code,401)
        with self.assertRaises(HTTPError) as cm:
            self.call('/api/task',[])
        self.assertEqual(cm.exception.code,400)
    def test_dashboard_and_health(self):
        with urlopen(self.base) as r:
            self.assertIn(b'Agent Bridge',r.read())
            self.assertIn('frame-ancestors',r.headers['Content-Security-Policy'])
        self.assertEqual(self.call('/health')['status'],'ok')

if __name__=='__main__':
    unittest.main()
