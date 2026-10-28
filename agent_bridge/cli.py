"""Small, model-independent JSON client."""
import argparse
import json
import os
from pathlib import Path
import sys
from .client import BridgeClient, BridgeError

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['state','register','heartbeat','message','task','claim','renew','release','complete'])
    p.add_argument('--agent', default=os.environ.get('AGENT_BRIDGE_AGENT', 'operator'))
    p.add_argument('--url', default=os.environ.get('AGENT_BRIDGE_URL', 'http://127.0.0.1:8765'))
    p.add_argument('--token-file', default=os.environ.get('AGENT_BRIDGE_TOKEN_FILE', '.agent-bridge/token'))
    group = p.add_mutually_exclusive_group()
    group.add_argument('--data', default='{}', help='JSON object with action fields')
    group.add_argument('--data-file', help='JSON file, or - to read stdin; avoids tokens in shell history')
    args = p.parse_args()
    try:
        raw = sys.stdin.read() if args.data_file == '-' else Path(args.data_file).read_text() if args.data_file else args.data
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('--data must be a JSON object')
        data.pop('agent', None)
        client = BridgeClient(args.agent, args.token_file, args.url)
        print(json.dumps(client.call(args.action, **data), indent=2))
    except BridgeError as e:
        print(json.dumps({'error': str(e), 'status': e.status}), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
