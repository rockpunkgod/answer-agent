"""Luna desktop executor: put a saved complete answer in a verified group draft.

No recipient search, Enter, send-button click, delivery row or performance update.
Do not execute while the configured desktop actor is unavailable. A fresh native
group pin must be produced from the actual foreground WeCom chat beforehand.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helpdesk.manual_group_draft import ManualGroupDraft
from helpdesk.mcp_transport import MCPProcess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet-root', type=Path, required=True)
    parser.add_argument('--packet', required=True)
    parser.add_argument('--pin', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true', help='Validate saved answer and pin without any desktop operation')
    args = parser.parse_args()
    pin = json.loads(args.pin.read_bytes())
    draft = ManualGroupDraft(None, pin, ROOT, args.packet_root)
    packet = draft._packet(args.packet)
    draft._authorize(packet)
    if args.check_only:
        print(json.dumps({'status': 'SAVED_SOURCE_AND_PIN_VALID', 'desktop_actions': False,
            'answer_sent': False, 'live_group_verified': False}, ensure_ascii=False))
        return
    with MCPProcess() as transport:
        draft.transport = transport
        result = draft.stage(args.packet)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
