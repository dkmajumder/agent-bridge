"""Purpose-level acceptance: independent workers sharing an actual temporary repo."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from agent_bridge.server import Store, Conflict, make_server
from agent_bridge.client import BridgeClient, BridgeError

class CoordinationTests(unittest.TestCase):
    def test_independent_connections_arbitrate_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'state.sqlite')
            stores = [Store(path) for _ in range(8)]
            try:
                tid = stores[0].apply('task',dict(title='Exclusive', resource='repo/src'),'planner')['id']
                def claim(i):
                    try:
                        return stores[i].apply('claim',dict(id=tid),str(i))
                    except Conflict:
                        return None
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    self.assertEqual(sum(v is not None for v in pool.map(claim,range(8))),1)
            finally:
                for store in stores:
                    store.db.close()

    def test_resource_aliases_cannot_bypass_overlap(self):
        s = Store(':memory:')
        try:
            a=s.apply('task',dict(title='A',resource='repo//src/./auth/'),'a')
            b=s.apply('task',dict(title='B',resource='repo/src/auth/file.py'),'b')
            self.assertEqual(a['resource'],'repo/src/auth')
            s.apply('claim',dict(id=a['id']),'a')
            with self.assertRaises(Conflict):
                s.apply('claim',dict(id=b['id']),'b')
            for resource in ['repo/../src', '/repo/src', 'repo\\src', 'C:/repo', './']:
                with self.assertRaises(ValueError):
                    s.apply('task',dict(title='Invalid',resource=resource),'a')
            with patch('agent_bridge.server.time.time',return_value=10**12):
                self.assertEqual(s.apply('snapshot',{},'')['tasks'][a['id']]['status'],'queued')
        finally:
            s.db.close()

    def test_real_worker_processes_and_git_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            token=root/'token'; token.write_text('acceptance-only')
            repo=root/'repo'; repo.mkdir()
            subprocess.run(['git','init','-q',str(repo)],check=True)
            s=make_server(0,str(root/'state.sqlite'),'acceptance-only')
            thread=threading.Thread(target=s.serve_forever,daemon=True); thread.start()
            url=f'http://127.0.0.1:{s.server_port}'
            a=BridgeClient('agent-a',token,url); b=BridgeClient('agent-b',token,url)
            try:
                a.call('register',name='Worker A'); b.call('register',name='Worker B')
                first=a.call('task',title='Edit auth',resource='repo/src/auth.py')
                overlap=b.call('task',title='Competing auth edit',resource='repo/src')
                separate=b.call('task',title='Write tests',resource='repo/tests')
                claim=a.call('claim',id=first['id'])
                worker='''
import sys
from pathlib import Path
from agent_bridge.client import BridgeClient, BridgeError
client=BridgeClient(sys.argv[1],sys.argv[2],sys.argv[3])
try:
    claim=client.call('claim',id=sys.argv[4])
except BridgeError as e:
    sys.exit(23 if e.status==409 else 24)
Path(sys.argv[5]).write_text('coordinated change\\n')
client.call('message',recipient='agent-a',body='Independent worker finished; ready for integration')
client.call('complete',id=sys.argv[4],lease_token=claim['lease_token'])
'''
                def run(task, target):
                    return subprocess.run([sys.executable,'-c',worker,'agent-b',str(token),url,task['id'],str(target)],capture_output=True,text=True)
                blocked=run(overlap,repo/'blocked.txt')
                self.assertEqual(blocked.returncode,23,blocked.stderr)
                self.assertFalse((repo/'blocked.txt').exists())
                allowed=run(separate,repo/'tests.txt')
                self.assertEqual(allowed.returncode,0,allowed.stderr)
                (repo/'auth.py').write_text('validated change\n')
                a.call('complete',id=first['id'],lease_token=claim['lease_token'])
                # Integration itself uses a separate, shared Git resource reservation.
                integration=a.call('task',title='Commit integration',resource='repo/git')
                git_lease=a.call('claim',id=integration['id'])
                subprocess.run(['git','-C',str(repo),'add','.'],check=True)
                subprocess.run(['git','-C',str(repo),'-c','user.name=Acceptance Test','-c','user.email=test@example.invalid','commit','-qm','Integrate coordinated workers'],check=True)
                a.call('complete',id=integration['id'],lease_token=git_lease['lease_token'])
                files=subprocess.check_output(['git','-C',str(repo),'ls-tree','--name-only','HEAD'],text=True)
                self.assertEqual(set(files.splitlines()),{'auth.py','tests.txt'})
                self.assertTrue(a.call('state')['messages'])
                cli = [sys.executable, '-m', 'agent_bridge.cli', 'message', '--agent', 'cli-worker',
                       '--url', url, '--token-file', str(token), '--data-file', '-']
                result = subprocess.run(cli, input=json.dumps({'recipient':'agent-a','body':'stdin handoff'}),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['body'], 'stdin handoff')

            finally:
                s.shutdown(); s.server_close(); thread.join(); s.store.db.close()

    def test_client_does_not_forward_bearer_on_redirect(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302); self.send_header('Location','http://example.invalid/steal'); self.end_headers()
            def log_message(self,*args):
                pass
        s=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
        t=threading.Thread(target=s.serve_forever,daemon=True); t.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                token=Path(d)/'token'; token.write_text('test-only')
                c=BridgeClient('a',token,f'http://127.0.0.1:{s.server_port}')
                with self.assertRaises(BridgeError) as error:
                    c.call('state')
                self.assertEqual(error.exception.status,302)
                for url in ['https://example.com','http://127.0.0.1/path','http://localhost?x=1']:
                    with self.assertRaises(ValueError):
                        BridgeClient('a',token,url)
        finally:
            s.shutdown();s.server_close();t.join()
