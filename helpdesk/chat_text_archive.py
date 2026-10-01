"""Preserve a native clipboard observation, without inferring chat metadata.

The resulting raw text is an acquisition artifact, not a performance ledger.
Only a reviewed copy operation can establish which chat it came from.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4


def archive_clipboard(result_path, output_root, *, observed_group):
    source = Path(result_path).resolve()
    raw = source.read_bytes()
    result = json.loads(raw)
    if result.get('tool') != 'Clipboard' or result.get('is_error') is not False:
        raise ValueError('A successful native Clipboard result is required; OCR is not accepted')
    attempt_id = result.get('attempt_id', '')
    if not isinstance(attempt_id, str) or len(attempt_id) != 32 or any(c not in '0123456789abcdef' for c in attempt_id):
        raise ValueError('Missing acquisition attempt')
    attempt_path = source.parent / f'attempt-{attempt_id}.json'
    attempt_bytes = attempt_path.read_bytes()
    attempt = json.loads(attempt_bytes)
    if (attempt.get('attempt_id') != attempt_id or attempt.get('tool') != 'Clipboard'
            or attempt.get('arguments') != {'mode': 'get'}
            or attempt.get('status') != 'TOOL_RETURNED'
            or Path(attempt.get('result_path', '')).resolve() != source):
        raise ValueError('Clipboard acquisition provenance does not match')
    content = result.get('content', [])
    if len(content) != 1 or content[0].get('type') != 'text':
        raise ValueError('Ambiguous clipboard result')
    text = content[0].get('text')
    prefix = 'Clipboard content:\n'
    if not isinstance(text, str) or not text.startswith(prefix):
        raise ValueError('Clipboard result format not recognized')
    text = text[len(prefix):]
    if not text.strip():
        raise ValueError('Empty clipboard is not a chat record')
    if not isinstance(observed_group, str) or not observed_group.strip():
        raise ValueError('An observed group label is required')
    # A new directory keeps prior observations intact, including repeated text.
    destination = Path(output_root) / uuid4().hex
    destination.mkdir(parents=True, exist_ok=False)
    text_bytes = text.encode('utf-8')
    (destination / '原始文字记录.txt').write_bytes(text_bytes)
    (destination / 'clipboard-result.json').write_bytes(raw)
    (destination / 'clipboard-attempt.json').write_bytes(attempt_bytes)
    manifest = {
        'source_kind': 'WINDOWS_MCP_NATIVE_CLIPBOARD',
        'observed_group_label': observed_group,
        'group_identity_verified': False,
        'record_status': 'RAW_TEXT_AWAITING_MESSAGE_REVIEW',
        'coverage_complete': False,
        'formal_statistics_eligible': False,
        'message_timestamps': None,
        'acquisition_started_at': attempt.get('started_at'),
        'archived_at': datetime.now(timezone.utc).isoformat(),
        'raw_text_file': '原始文字记录.txt',
        'raw_text_sha256': sha256(text_bytes).hexdigest(),
        'source_result_sha256': sha256(raw).hexdigest(),
        'source_attempt_sha256': sha256(attempt_bytes).hexdigest(),
        'review_required': ['group identity and copy selection', 'original sender and message time',
                            'attachments and question material', 'history coverage and duplicate messages'],
    }
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return destination.resolve()
