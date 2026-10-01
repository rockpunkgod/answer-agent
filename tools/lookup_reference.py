"""Local original-question evidence CLI; no WeCom, model, delivery or ledger writes."""
import argparse
from dataclasses import replace
import json
from pathlib import Path

from helpdesk.domain import Option, Question
from helpdesk.reference_fetch import LookupFailure
from helpdesk.reference_lookup import LookupConfig, ReferenceLookup, ROOT
from helpdesk.storage import Store


def demo_snapshot():
    material = ('After the storm, Maya carried warm blankets to the small village library. '
                'The library offered shelter to people who could not return home.')
    options = ('To help people staying there.', 'To prepare a reading competition.',
               'To sell supplies to visitors.', 'To decorate an empty building.')
    question = Question('34', 'Why did Maya carry blankets to the library?',
        'Why did Maya carry blankets to the library?',
        tuple(Option('synthetic-option-' + label, label, index, text, text, 'SELF_AUTHORED_SYNTHETIC_TEST')
              for index, (label, text) in enumerate(zip('ABCD', options))), 'SELF_AUTHORED_SYNTHETIC_TEST')
    # Explicitly synthetic: timestamps are never assigned to real records.
    return {'case_id': 'synthetic-case', 'question_id': 'synthetic-question',
        'question_version': 'synthetic-version', 'context_revision': 1,
        'student_question': question.to_dict(), 'original_student_material': material, 'student_material': material,
        'original_question_time': '2026-09-30T22:58:00+08:00', 'synthetic_test_data': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/reference-lookup.example.toml')
    parser.add_argument('--demo', action='store_true', help='Anonymous self-authored fixture; no business database')
    parser.add_argument('--db', type=Path)
    parser.add_argument('--question-id')
    parser.add_argument('--question-version')
    parser.add_argument('--context-revision', type=int)
    parser.add_argument('--trigger', choices=('blurred', 'missing_material', 'clean_copy', 'version_difference', 'manual_source'))
    parser.add_argument('--url', action='append', default=[], help='Manual candidate URL; still requires source admission')
    parser.add_argument('--fixture', action='append', default=[], help='Self-authored/approved local HTML under fixture_root')
    parser.add_argument('--retry', action='store_true', help='Explicitly retry a failed/interrupted lookup')
    parser.add_argument('--remaining-seconds', type=float, help='Existing task remaining allowance; never increases the 120-second lookup limit')
    parser.add_argument('--apply', help='Verified lookup key to add as reference; shadow must be disabled')
    parser.add_argument('--reviewer', help='Named local reviewer for explicit reference consumption')
    args = parser.parse_args(argv)
    if args.url and args.fixture:
        parser.error('Offline fixtures and network candidate URLs must be separate requests')
    store = None
    try:
        config = LookupConfig.load(args.config)
        if args.demo:
            if args.db or args.url or args.apply or args.fixture:
                parser.error('--demo is isolated and cannot use accounts or real tasks')
            lookup = ReferenceLookup(replace(config, enabled=True, network_enabled=False, shadow=True))
            report = lookup.run(demo_snapshot(), trigger='clean_copy', fixtures=['demo.html'])
        else:
            if not args.db or not args.db.is_file():
                parser.error('An existing local --db is required; no empty business database is created')
            store = Store(args.db)
            lookup = ReferenceLookup(config)
            if args.apply:
                result = lookup.apply(store, args.apply, reviewer=args.reviewer)
                print(json.dumps({'status': 'REFERENCE_ONLY_ADDED', 'comparison': result.reason}, ensure_ascii=False))
                return 0
            if not args.question_id or not args.question_version or args.context_revision is None:
                parser.error('Use the existing question ID, current version and context revision')
            report = lookup.run_for_question(store, args.question_id, args.question_version, args.context_revision,
                trigger=args.trigger, candidate_urls=args.url, fixtures=args.fixture, retry=args.retry,
                remaining_seconds=args.remaining_seconds)
        # Brief output has no student identity, complete chat, key or cookie.
        summary = {key: report.get(key) for key in ('lookup_key', 'retrieval_status', 'match_status', 'next_action',
            'explanation', 'cache_hit', 'stale', 'network_verified', 'delivery_completed', 'performance_changed')}
        report_path = config.cache_root / 'verification' / (report['lookup_key'] + '.json') if report.get('lookup_key') else None
        summary['report_cache'] = str(report_path) if report_path and report_path.is_file() else None
        summary['evidence_storage'] = 'MEMORY_ONLY' if report.get('network_verified') else 'APPROVED_LOCAL_FIXTURE_OR_SEARCH_METADATA'
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, LookupFailure) as exc:
        print(json.dumps({'status': 'INPUT_OR_LOOKUP_ERROR', 'error_type': type(exc).__name__}, ensure_ascii=False))
        return 2
    finally:
        if store:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
