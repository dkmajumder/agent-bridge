"""Dependency-free client for trusted, cooperating local agents."""
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

class BridgeError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class BridgeClient:
    def __init__(self, agent, token_file='.agent-bridge/token', url='http://127.0.0.1:8765'):
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
                or parsed.username or parsed.password or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment):
            raise ValueError('url must be a loopback HTTP origin without path, credentials, query or fragment')
        self.agent = agent
        self.url = url.rstrip('/')
        self.token = Path(token_file).read_text().strip()
        # Local credentials must never go through environment proxies or redirects.
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(self, action, **fields):
        state = action == 'state'
        payload = None if state else json.dumps(dict(fields, agent=self.agent)).encode()
        request = Request(self.url+'/api/'+action, data=payload,
                          headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        try:
            with self.opener.open(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as error:
            body = error.read()
            try:
                message = json.loads(body)['error']
            except (ValueError, KeyError, TypeError):
                message = 'HTTP '+str(error.code)
            raise BridgeError(error.code, message) from error
