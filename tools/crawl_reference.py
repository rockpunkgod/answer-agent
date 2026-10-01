"""Capture one admitted reference as a candidate, never as a confirmed answer.

The previous browser capture is not enabled here: it did not share the source
admission, pinned network and complete deadline checks. This entry now reuses
the local/HTTP providers from the on-demand lookup, without a Crawl4AI install.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
import uuid

from helpdesk.reference_fetch import Budget, LookupFailure, ReferenceFetcher, canonical_url, resolve_public
from helpdesk.reference_lookup import LookupConfig, ReferenceLookup, ROOT, approved_path
from helpdesk.reference_providers import HttpProvider, LocalProvider
from helpdesk.storage import Store


DEFAULT_OUTPUT_ROOT = ROOT / 'data/private/reference-crawl'


def validate_public_url(url, *, expected_host=None):
    url = canonical_url(url)
    parsed = urlsplit(url)
    if expected_host is not None and parsed.hostname != expected_host:
        raise ValueError('Cross-host reference request blocked')
    resolve_public(parsed.hostname, 443 if parsed.scheme == 'https' else 80, 5)
    return parsed.hostname


def _offline_self_test():
    # URL syntax/IP checks only; does not make an external request or resolve DNS.
    assert canonical_url('https://8.8.8.8/path') == 'https://8.8.8.8/path'
    for blocked in ('file:///etc/passwd', 'http://localhost/', 'http://127.0.0.1/',
                    'http://10.1.2.3/', 'http://169.254.10.20/', 'https://user:pass@example.com/'):
        try:
            canonical_url(blocked)
        except LookupFailure:
            continue
        raise AssertionError('Unsafe URL was accepted')
    print('offline URL safety checks passed; no network, browser or business database')


def capture_candidate(config, *, url=None, local_file=None):
    if bool(url) == bool(local_file):
        raise ValueError('Choose one candidate URL or approved local file')
    if not config.enabled:
        raise ValueError('Reference capture is disabled')
    budget = Budget(config.total_seconds)
    if local_file:
        return LocalProvider(config.fixture_root).capture(local_file, budget)
    fetcher = ReferenceFetcher(timeout=config.timeout_seconds, interval=config.request_interval_seconds,
                               retries=config.retries)
    return HttpProvider(config, fetcher).capture(url, budget)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/reference-lookup.example.toml')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--url', help='One admitted HTTP(S) page; no browser or linked-page crawl')
    source.add_argument('--local-file', help='UTF-8 HTML/text in the configured approved directory')
    parser.add_argument('--db', type=Path, help='Existing business database; creates a candidate only')
    parser.add_argument('--question-id')
    parser.add_argument('--question-version')
    parser.add_argument('--context-revision', type=int)
    parser.add_argument('--out-dir', type=Path, help='Metadata only, under data/private/reference-crawl')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args(argv)
    if args.self_test:
        _offline_self_test()
        return 0
    if not args.url and not args.local_file:
        parser.error('--url or --local-file is required')
    store = None
    try:
        config = LookupConfig.load(args.config)
        if args.db:
            if (not args.db.is_file() or not args.question_id or not args.question_version
                    or args.context_revision is None):
                parser.error('Existing database, question, version and context are required together')
            store = Store(args.db)
            report = ReferenceLookup(config).run_for_question(store, args.question_id, args.question_version,
                args.context_revision, trigger='manual_source', candidate_urls=[args.url] if args.url else [],
                fixtures=[args.local_file] if args.local_file else [])
            metadata = {'reference_only': True, 'candidate_only': True, 'confirmation_performed': False,
                'retrieval_status': report['retrieval_status'], 'match_status': report['match_status'],
                'candidate_ids': [m['candidate_id'] for m in report.get('matches', []) if m.get('candidate_id')],
                'explanation': report.get('explanation')}
        else:
            if args.question_id or args.question_version or args.context_revision is not None:
                parser.error('Question identity requires an existing --db')
            captured = capture_candidate(config, url=args.url, local_file=args.local_file)
            metadata = {key: captured[key] for key in ('source', 'url', 'retrieved_time', 'hash')}
            metadata.update(reference_only=True, candidate_only=True, confirmed=False,
                content_chars=len(captured['content']), content_saved=False, state='DISCOVERED',
                explanation='只读抓取元数据；提供已有题目身份后才会建立候选并比较。')
        if args.out_dir:
            # Export never stores third-party page bodies or writes outside the approved directory.
            output = approved_path(ROOT, DEFAULT_OUTPUT_ROOT)
            output.mkdir(parents=True, exist_ok=True)
            output = approved_path(output, args.out_dir)
            output.mkdir(parents=True, exist_ok=True)
            path = output / ('candidate-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.json')
            with path.open('x', encoding='utf-8') as stream:
                json.dump(metadata, stream, ensure_ascii=False, indent=2)
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'CAPTURE_UNAVAILABLE', 'error_type': type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
