"""Local coordination service. No model credentials or shell execution required."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Conflict(Exception):
    pass

class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
        self.db.execute('INSERT OR IGNORE INTO state VALUES (1, ?)', (json.dumps(dict(agents={}, tasks={}, messages=[], events=[])),))
        self.db.commit()

    def apply(self, action, data, actor):
        # SQLite, rather than only a Python mutex, arbitrates independent connections.
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            return self._apply(action, data, actor)

    def _apply(self, action, data, actor):
        with self.lock:
            state = json.loads(self.db.execute('SELECT value FROM state WHERE id=1').fetchone()[0])
            now = time.time()
            if action == 'snapshot':
                # Present expired claims as available without erasing fencing history.
                for task in state['tasks'].values():
                    if task['status'] == 'running' and task['lease_until'] <= now:
                        task.update(status='queued', owner=None, lease_until=0, token_hash=None)
                return public(state)
            if action == 'register':
                state['agents'][actor] = dict(name=optional_text(data, 'name', actor), status='online', heartbeat=now)
                result = state['agents'][actor]
            elif action == 'heartbeat':
                if actor not in state['agents']:
                    raise ValueError('Register this agent first')
                state['agents'][actor].update(heartbeat=now, status=optional_text(data, 'status', 'online'))
                result = state['agents'][actor]
            elif action == 'message':
                result = dict(id=uuid.uuid4().hex, sender=actor, recipient=optional_text(data, 'recipient', '*'), body=required(data, 'body'), timestamp=now)
                state['messages'].append(result)
            elif action == 'task':
                result = dict(id=uuid.uuid4().hex, title=required(data, 'title'), resource=canonical_resource(required(data, 'resource')), status='queued', owner=None, lease_until=0, fence=0, token_hash=None, created_by=actor)
                state['tasks'][result['id']] = result
            elif action in ('claim', 'renew', 'complete', 'release'):
                task = state['tasks'].get(required(data, 'id'))
                if not task:
                    raise ValueError('Unknown task')
                if action == 'claim':
                    if task['status'] == 'done' or (task['owner'] and task['lease_until'] > now):
                        raise Conflict('Task unavailable')
                    for other in state['tasks'].values():
                        if other['id'] != task['id'] and other['owner'] and other['lease_until'] > now and overlaps(task['resource'], other['resource']):
                            raise Conflict('Resource overlaps an active lease')
                    token = secrets.token_urlsafe(32)
                    task.update(owner=actor, status='running', lease_until=now+lease(data), fence=task['fence']+1, token_hash=hashlib.sha256(token.encode()).hexdigest())
                    result = dict(task, lease_token=token)
                else:
                    supplied = hashlib.sha256(str(data.get('lease_token', '')).encode()).hexdigest()
                    if task['owner'] != actor or task['lease_until'] <= now or not secrets.compare_digest(supplied, task['token_hash'] or ''):
                        raise Conflict('Lease expired or ownership token invalid')
                    if action == 'renew':
                        task['lease_until'] = now+lease(data)
                    else:
                        task.update(status='done' if action == 'complete' else 'queued', owner=None, lease_until=0, token_hash=None)
                    result = dict(task)
            else:
                raise ValueError('Unknown action')
            state['events'].append(dict(action=action, actor=actor, timestamp=now, task_id=data.get('id')))
            # Bounded history; current task and agent state persists separately.
            state['events'] = state['events'][-2000:]
            state['messages'] = state['messages'][-2000:]
            self.db.execute('UPDATE state SET value=? WHERE id=1', (json.dumps(state),))
            self.db.commit()
            return public(result)

def public(value):
    if isinstance(value, dict):
        return {k: public(v) for k,v in value.items() if k != 'token_hash'}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value

def required(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 8000:
        raise ValueError(f'{key} must be a non-empty string, max 8000 characters')
    return value.strip()

def optional_text(data, key, default):
    return required(data, key) if key in data else default

def canonical_resource(value):
    if value.startswith('/') or '\\' in value or ':' in value:
        raise ValueError('resource must be a relative slash-separated coordination key')
    parts = value.split('/')
    if '..' in parts:
        raise ValueError('resource cannot contain parent traversal (..)')
    result = '/'.join(p for p in parts if p not in ('', '.'))
    if not result:
        raise ValueError('resource must identify a resource')
    return result

def lease(data):
    value = data.get('ttl', 300)
    if not isinstance(value, int) or isinstance(value, bool) or not 5 <= value <= 3600:
        raise ValueError('ttl must be an integer between 5 and 3600 seconds')
    return value

def overlaps(a, b):
    return a == b or a.startswith(b.rstrip('/')+'/') or b.startswith(a.rstrip('/')+'/')

def make_server(port=8765, database='bridge.sqlite', token=None):
    store = Store(database)
    token = token or secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)
        def log_message(self, *args):
            pass
        def reply(self, status, value):
            payload = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(payload)
        def do_GET(self):
            if self.path == '/':
                payload = Path(__file__).with_name('dashboard.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(payload)
            elif self.path == '/health':
                self.reply(200, dict(status='ok', protocol='agent-bridge/1'))
            elif self.path == '/api/state' and self.authorized():
                self.reply(200, public(store.apply('snapshot', {}, '')))
            else:
                self.reply(401 if self.path == '/api/state' else 404, dict(error='Unauthorized or unknown route'))
        def authorized(self):
            # Block cross-origin browser writes and DNS rebinding.
            if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
                return False
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
                return False
            return secrets.compare_digest(self.headers.get('Authorization', '').encode('utf-8'), ('Bearer '+token).encode('utf-8'))
        def do_POST(self):
            if not self.authorized():
                return self.reply(401, dict(error='Unauthorized'))
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    return self.reply(413, dict(error='Body must be 1–65536 bytes'))
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('Expected JSON object')
                actor = required(data, 'agent')
                if not self.path.startswith('/api/'):
                    return self.reply(404, dict(error='Unknown route'))
                result = store.apply(self.path[5:], data, actor)
                self.reply(200, result)
            except Conflict as e:
                self.reply(409, dict(error=str(e)))
            except (ValueError, TypeError) as e:
                self.reply(400, dict(error=str(e)))
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.store = store
    server.token = token
    return server

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data-dir', default='.agent-bridge')
    args = parser.parse_args()
    directory = Path(args.data_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    token_file = directory/'token'
    if not token_file.exists():
        fd = os.open(token_file, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(32))
    os.chmod(token_file, 0o600)
    server = make_server(args.port, str(directory/'bridge.sqlite'), token_file.read_text().strip())
    print(f'Agent Bridge: http://127.0.0.1:{server.server_port}\nToken file: {token_file.resolve()}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.store.db.close()

if __name__ == '__main__':
    main()
