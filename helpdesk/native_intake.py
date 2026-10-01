"""Stage verified native acquisition artifacts; never infer message metadata."""
from hashlib import sha256
from datetime import datetime
import json
from pathlib import Path
import re

from .storage import encode, now


MISSING_METADATA = ('verified_group_identity', 'original_sender', 'original_message_time',
                    'verified_student_identity', 'question_material_and_counting_unit',
                    'original_attachment_references', 'actual_answer_delivery_evidence',
                    'history_coverage_and_duplicate_review')
TABLE = 'native_chat_staging'


def ensure_schema(store):
    store.execute(f'''CREATE TABLE IF NOT EXISTS {TABLE} (
      acquisition_id TEXT PRIMARY KEY, observed_group_label TEXT NOT NULL,
      original_text TEXT NOT NULL, source_path TEXT NOT NULL,
      raw_text_sha256 TEXT NOT NULL, result_sha256 TEXT NOT NULL,
      attempt_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
      evidence_json TEXT NOT NULL, acquired_at TEXT, indexed_at TEXT NOT NULL
    )''')


def _json(path):
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f'Expected an object: {path.name}')
    return value, raw


def verify_native_record(directory):
    """Verify the archive and its original journal, without parsing business facts."""
    directory = Path(directory).resolve(strict=True)
    manifest, manifest_raw = _json(directory / 'manifest.json')
    group = manifest.get('observed_group_label')
    if not isinstance(group, str) or not group.strip():
        raise ValueError('Missing observed group label')
    if (manifest.get('source_kind') != 'WINDOWS_MCP_NATIVE_CLIPBOARD'
            or manifest.get('record_status') != 'RAW_TEXT_AWAITING_MESSAGE_REVIEW'
            or manifest.get('group_identity_verified') is not False
            or manifest.get('formal_statistics_eligible') is not False
            or manifest.get('coverage_complete') is not False
            or manifest.get('message_timestamps', 'MISSING') is not None):
        raise ValueError('Archive must be an unreviewed native acquisition, not a verified message')
    filename = manifest.get('raw_text_file')
    if not isinstance(filename, str) or not filename:
        raise ValueError('Missing raw text file')
    raw_path = (directory / filename).resolve(strict=True)
    if raw_path.parent != directory:
        raise ValueError('Raw text escapes the archive directory')
    raw = raw_path.read_bytes()
    result, result_raw = _json(directory / 'clipboard-result.json')
    attempt, attempt_raw = _json(directory / 'clipboard-attempt.json')
    for key, value in (('raw_text_sha256', raw), ('source_result_sha256', result_raw),
                       ('source_attempt_sha256', attempt_raw)):
        if manifest.get(key) != sha256(value).hexdigest():
            raise ValueError(f'Archive hash mismatch: {key}')
    acquisition = result.get('attempt_id', '')
    if not isinstance(acquisition, str) or not re.fullmatch('[0-9a-f]{32}', acquisition):
        raise ValueError('Missing acquisition attempt identity')
    if (result.get('tool') != 'Clipboard' or result.get('is_error') is not False
            or attempt.get('attempt_id') != acquisition or attempt.get('tool') != 'Clipboard'
            or attempt.get('arguments') != {'mode': 'get'}
            or attempt.get('status') != 'TOOL_RETURNED'
            or manifest.get('acquisition_started_at') != attempt.get('started_at')):
        raise ValueError('Native Clipboard acquisition provenance does not agree')
    acquired_at = attempt.get('started_at')
    if not isinstance(acquired_at, str):
        raise ValueError('Invalid acquisition time; it is not message time')
    acquired = datetime.fromisoformat(acquired_at)
    if acquired.utcoffset() is None:
        raise ValueError('Acquisition time requires a timezone; it is not message time')
    content = result.get('content')
    if (not isinstance(content, list) or len(content) != 1
            or not isinstance(content[0], dict) or content[0].get('type') != 'text'):
        raise ValueError('Ambiguous Clipboard content')
    text = content[0].get('text')
    if not isinstance(text, str) or not text.startswith('Clipboard content:\n'):
        raise ValueError('Unsupported Clipboard format')
    text = text[len('Clipboard content:\n'):]
    if not text.strip() or text.encode('utf-8') != raw:
        raise ValueError('Original text does not match native Clipboard result')
    source_result = Path(attempt.get('result_path', '')).resolve(strict=True)
    source_attempt = source_result.parent / f'attempt-{acquisition}.json'
    if (source_result.read_bytes() != result_raw or source_attempt.read_bytes() != attempt_raw):
        raise ValueError('Original acquisition journal does not match archive evidence')
    return {'acquisition_id': acquisition, 'observed_group_label': group,
            'original_text': text, 'source_path': str(directory),
            'raw_text_sha256': sha256(raw).hexdigest(),
            'result_sha256': sha256(result_raw).hexdigest(),
            'attempt_sha256': sha256(attempt_raw).hexdigest(),
            'manifest_sha256': sha256(manifest_raw).hexdigest(),
            'evidence_json': encode({'original_result_path': str(source_result),
                                     'original_attempt_path': str(source_attempt),
                                     'raw_text_path': str(raw_path),
                                     'manifest_path': str(directory / 'manifest.json')}),
            'acquired_at': acquired_at}


def _exists(store):
    return store.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)) is not None


def index_native_records(store, root, *, dry_run=False):
    """Index English acquisitions (including exited groups), not platform messages.

    Validation is completed before any insertion. A malformed artifact is reported
    and never creates a partial row. Conflicting acquisition IDs fail the batch.
    """
    root = Path(root).resolve(strict=True)
    staged, rejected, excluded = [], [], []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or not (folder / 'manifest.json').is_file():
            continue
        try:
            row = verify_native_record(folder)
            if 'english' not in row['observed_group_label'].casefold():
                excluded.append(str(folder))
            else:
                staged.append(row)
        except (ValueError, OSError, TypeError, KeyError) as exc:
            rejected.append({'source_path': str(folder), 'reason': str(exc)})
    existing = {r['acquisition_id']: dict(r) for r in store.all(f'SELECT * FROM {TABLE}')} if _exists(store) else {}
    insertions, duplicates = [], []
    for row in staged:
        prior = existing.get(row['acquisition_id'])
        if prior:
            if any(prior[key] != row[key] for key in ('observed_group_label', 'original_text',
                                                     'raw_text_sha256', 'result_sha256', 'attempt_sha256')):
                raise ValueError('Conflicting evidence for an existing acquisition ID')
            duplicates.append(row['acquisition_id'])
        else:
            existing[row['acquisition_id']] = row
            insertions.append(row)
    if not dry_run:
        with store.transaction():
            ensure_schema(store)
            for row in insertions:
                keys = tuple(row) + ('indexed_at',)
                store.execute(f"INSERT INTO {TABLE} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                              tuple(row.values()) + (now(),))
    return {'dry_run': dry_run, 'new_records': len(insertions), 'duplicate_acquisitions': len(duplicates),
            'excluded_non_english': len(excluded), 'rejected': rejected,
            'formal_statistics_eligible': False, 'coverage_complete': False,
            'scope_start_date': '2026-09-17', 'source_dates_verified': False}


def list_staged_records(store):
    """Read-only view. Acquisition timestamps never become original message time."""
    if not _exists(store):
        return []
    rows = []
    for record in store.all(f'SELECT * FROM {TABLE} ORDER BY indexed_at,acquisition_id'):
        row = dict(record)
        row['evidence'] = json.loads(row.pop('evidence_json'))
        row.update(record_status='RAW_TEXT_AWAITING_MESSAGE_REVIEW',
                   source_kind='WINDOWS_MCP_NATIVE_CLIPBOARD',
                   group_id=None, sender=None, original_message_time=None,
                   student_identity=None, questions=None, attachments=None,
                   membership=None, platform_message_id=None,
                   reliable_timestamp=False, formal_statistics_eligible=False,
                   coverage_complete=False, missing_metadata=list(MISSING_METADATA))
        rows.append(row)
    return rows
