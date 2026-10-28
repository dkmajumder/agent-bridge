"""Run against a local Agent Bridge server; no model provider required."""
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

TOKEN = Path('.agent-bridge/token').read_text().strip()
def call(action, agent, **fields):
    request = Request('http://127.0.0.1:8765/api/'+action,
        data=json.dumps(dict(agent=agent, **fields)).encode(),
        headers={'Authorization':'Bearer '+TOKEN,'Content-Type':'application/json'})
    with urlopen(request, timeout=10) as response:
        return json.load(response)

call('register', 'browser-agent', name='Browser agent · validation')
call('register', 'refactor-agent', name='Refactoring agent · refactoring')
task = call('task', 'browser-agent', title='Demo: refactor authentication', resource='demo/src/auth')
claim = call('claim', 'refactor-agent', id=task['id'])
try:
    call('claim', 'browser-agent', id=task['id'])
    raise AssertionError('Competing claim unexpectedly succeeded')
except HTTPError as e:
    assert e.code == 409, e.code
    print('PASS: overlapping work rejected (HTTP 409)')
call('message', 'refactor-agent', recipient='browser-agent', body='Demo handoff: auth refactor ready for browser validation. No actual files changed.')
call('complete', 'refactor-agent', id=task['id'], lease_token=claim['lease_token'])
print('PASS: two agents registered, task claimed, handoff sent, task completed.')
print('Open http://127.0.0.1:8765 to inspect the workflow.')
