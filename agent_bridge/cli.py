"""Small, model-independent JSON client."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['state','register','heartbeat','message','task','claim','renew','release','complete'])
    p.add_argument('--agent', default=os.environ.get('AGENT_BRIDGE_AGENT', 'operator'))
    p.add_argument('--url', default=os.environ.get('AGENT_BRIDGE_URL', 'http://127.0.0.1:8765'))
    p.add_argument('--token-file', default=os.environ.get('AGENT_BRIDGE_TOKEN_FILE', '.agent-bridge/token'))
    p.add_argument('--data', default='{}', help='JSON object with action fields')
    args = p.parse_args()
    try:
        data = json.loads(args.data)
        if not isinstance(data, dict):
            raise ValueError('--data must be a JSON object')
        data['agent'] = args.agent
        token = Path(args.token_file).read_text().strip()
        # Never forward the local bearer credential to a remote service.
        from urllib.parse import urlsplit
        parsed = urlsplit(args.url)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.username or parsed.password:
            raise ValueError('--url must be a loopback HTTP URL')
        path = '/api/state' if args.action == 'state' else '/api/'+args.action
        request = Request(args.url.rstrip('/')+path, data=None if args.action == 'state' else json.dumps(data).encode(), headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json'})
        with urlopen(request, timeout=10) as response:
            print(json.dumps(json.load(response), indent=2))
    except HTTPError as e:
        print(e.read().decode(), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, URLError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
