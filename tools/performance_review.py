"""Local operator entry point; never sends a report or edits a remote sheet.

python tools/performance_review.py --db data/demo-ui.db list
python tools/performance_review.py --db data/demo-ui.db apply --input review.json
JSON: {"action":"confirm","arguments":{"unit_id":...,"reviewer":...,"evidence":...}}
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpdesk.storage import Store, encode
from helpdesk.performance import PerformanceLedger
from helpdesk.performance_review import PerformanceReview
from helpdesk.performance_rules import estimate_amount


def apply_review(store, payload, *, ledger=None):
    ledger = ledger or PerformanceLedger(store)
    review = PerformanceReview(store)
    actions = {
        'create_unit': ledger.create_unit, 'record_source_time': ledger.record_source_time,
        'link_activity': ledger.link_activity, 'record_first_response': ledger.record_first_response,
        'record_delivery': ledger.record_delivery, 'request_conversion': ledger.request_conversion,
        'resolve_source_time_conflict': ledger.resolve_source_time_conflict,
        'approve_conversion': ledger.approve_conversion, 'confirm': ledger.confirm,
        'revise': ledger.revise, 'merge': ledger.merge, 'split': ledger.split,
        'configure_night_end': ledger.configure_night_end,
        'preview_night_reclassification': ledger.preview_night_reclassification,
        'flag_anomaly': review.flag, 'review_anomaly': review.resolve, 'timing': review.timing,
        'estimate': estimate_amount,
    }
    action, arguments = payload.get('action'), payload.get('arguments')
    if action not in actions or not isinstance(arguments, dict):
        raise ValueError('Explicit supported action and arguments object required')
    return {'action': action, 'result': actions[action](**arguments), 'local_only': True}


def main():
    parser = argparse.ArgumentParser(description='本地绩效人工核对；无消息发送功能')
    parser.add_argument('--db', required=True)
    parser.add_argument('command', choices=('list', 'pending', 'audit', 'apply'))
    parser.add_argument('--input', help='Local review JSON, with actor/reason/evidence where required')
    args = parser.parse_args()
    if not Path(args.db).is_file():
        parser.error('Existing business database required')
    store = Store(args.db)
    try:
        ledger = PerformanceLedger(store)
        if args.command == 'list':
            result = ledger.list_units()
        elif args.command == 'pending':
            result = {'units': ledger.pending(), 'unlinked_messages': ledger.list_unlinked_messages()}
        elif args.command == 'audit':
            result = [dict(r) for r in store.all('SELECT * FROM performance_events ORDER BY id')]
        else:
            if not args.input:
                parser.error('--input required for apply')
            result = apply_review(store, json.loads(Path(args.input).read_text(encoding='utf-8-sig')), ledger=ledger)
        print(encode(result))
    finally:
        store.close()


if __name__ == '__main__':
    main()
