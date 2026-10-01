"""Save a copied DeepSeek response to the workbench for human review and send."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.answer_review_packets import create_review_packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--packet-id', required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--clipboard-result', type=Path, required=True)
    parser.add_argument('--clipboard-attempt', type=Path, required=True)
    parser.add_argument('--session-url', required=True)
    args = parser.parse_args()
    record = create_review_packet(args.root, args.packet_id, args.request,
                                  args.clipboard_result, args.clipboard_attempt, args.session_url)
    print(json.dumps({key: record[key] for key in ('packet_id', 'answer_sha256', 'status',
          'actual_delivery_confirmed', 'formal_performance_eligible')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
