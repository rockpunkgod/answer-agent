"""Read-only handoff of actual copied DeepSeek responses for human delivery.

The configured directory belongs to the local operator. Browser requests cannot
select paths, import responses, approve answers, or record delivery.
"""
from hashlib import sha256
from datetime import datetime
import json
from pathlib import Path
import re
from uuid import uuid4

WINDOWS_MCP_JOURNAL_ROOT = Path(__file__).resolve().parents[1] / 'data/private/windows-mcp'


class ReviewPacketError(ValueError):
    pass


def _read_file(directory, entry):
    if not isinstance(entry, dict) or set(entry) != {'file', 'sha256'}:
        raise ReviewPacketError('Invalid review file descriptor')
    name = entry['file']
    if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
        raise ReviewPacketError('Review file must be local to its packet')
    path = (directory / name).resolve(strict=True)
    if path.parent != directory or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ReviewPacketError('Review file is outside its packet or too large')
    content = path.read_bytes()
    if sha256(content).hexdigest() != entry['sha256']:
        raise ReviewPacketError('Review file changed')
    return content


def _load_packet(directory):
    manifest_path = directory / 'manifest.json'
    if manifest_path.resolve(strict=True).parent != directory or manifest_path.stat().st_size > 16_384:
        raise ReviewPacketError('Invalid review manifest')
    manifest = json.loads(manifest_path.read_bytes())
    if (not isinstance(manifest, dict) or type(manifest.get('version')) is not int
            or manifest.get('version') != 1 or manifest.get('packet_id') != directory.name):
        raise ReviewPacketError('Invalid review packet identity')
    request = json.loads(_read_file(directory, manifest['request']))
    response_bytes = _read_file(directory, manifest['answer'])
    response = response_bytes.decode('utf-8')
    copied = json.loads(_read_file(directory, manifest['clipboard_result']))
    attempt = json.loads(_read_file(directory, manifest['clipboard_attempt']))
    if not all(isinstance(value, dict) for value in (request, copied, attempt)):
        raise ReviewPacketError('Invalid review evidence object')
    acquisition_id = copied.get('attempt_id')
    if (copied.get('tool') != 'Clipboard' or copied.get('is_error') is not False
            or attempt.get('tool') != 'Clipboard' or attempt.get('arguments') != {'mode': 'get'}
            or attempt.get('status') != 'TOOL_RETURNED'
            or not isinstance(acquisition_id, str) or not re.fullmatch(r'[0-9a-f]{32}', acquisition_id)
            or acquisition_id != attempt.get('attempt_id')):
        raise ReviewPacketError('Copied response lacks successful native Clipboard evidence')
    if datetime.fromisoformat(attempt['started_at']).utcoffset() is None:
        raise ReviewPacketError('Clipboard acquisition time must have a timezone')
    journal_root = WINDOWS_MCP_JOURNAL_ROOT.resolve(strict=True)
    journal_attempt = (journal_root / f'attempt-{acquisition_id}.json').resolve(strict=True)
    journal_result = Path(attempt['result_path']).resolve(strict=True)
    if (journal_attempt.parent != journal_root or journal_result.parent != journal_root
            or journal_attempt.stat().st_size > 2_000_000 or journal_result.stat().st_size > 2_000_000
            or journal_attempt.read_bytes() != _read_file(directory, manifest['clipboard_attempt'])
            or journal_result.read_bytes() != _read_file(directory, manifest['clipboard_result'])):
        raise ReviewPacketError('Response evidence differs from the native MCP journal')
    blocks = copied.get('content', [])
    if (not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict)
            or blocks[0].get('type') != 'text'
            or not isinstance(blocks[0].get('text'), str)
            or not blocks[0]['text'].startswith('Clipboard content:\n')
            or blocks[0]['text'][len('Clipboard content:\n'):].encode('utf-8') != response_bytes
            or not response.strip()):
        raise ReviewPacketError('Response differs from the original copied text')
    from .mcp_page_contract import DeepSeekPage
    page = DeepSeekPage(manifest['session_url'])
    for key in ('group_observed', 'student_observed', 'question_number', 'student_words'):
        if not isinstance(request.get(key), str) or not request[key].strip():
            raise ReviewPacketError('Question association is missing')
    return {'packet_id': manifest['packet_id'],
            **{key: request[key] for key in ('group_observed', 'student_observed', 'question_number', 'student_words')},
            'session_url': page.url, 'text': response,
            'answer_sha256': sha256(response_bytes).hexdigest(),
            'native_copy_acquisition_id': acquisition_id,
            'copied_at': attempt['started_at'],
            'status': 'AWAITING_HUMAN_SEND', 'answer_review_required': False,
            'actual_delivery_confirmed': False, 'formal_performance_eligible': False}


def list_review_packets(root):
    if root is None:
        return {'configured': False, 'packets': [], 'count': 0}
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ReviewPacketError('Review packet root must be a directory')
    directories = sorted(p for p in root.iterdir() if not p.name.startswith('.'))
    if len(directories) > 100:
        raise ReviewPacketError('Too many review packets')
    packets = []
    for directory in directories:
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', directory.name)
                or not directory.is_dir() or directory.is_symlink()
                or directory.resolve().parent != root):
            raise ReviewPacketError('Invalid review packet directory')
        packets.append(_load_packet(directory.resolve()))
    return {'configured': True, 'packets': packets, 'count': len(packets)}


def create_review_packet(root, packet_id, request_path, clipboard_result_path,
                         clipboard_attempt_path, session_url):
    """Import a native response once, preserving every character and paragraph.

    Only a local caller supplies paths. No model request or delivery is made.
    Failed imports remain in a hidden staging directory for diagnosis.
    """
    if not isinstance(packet_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', packet_id):
        raise ReviewPacketError('Invalid review packet identity')
    from .mcp_page_contract import DeepSeekPage
    session_url = DeepSeekPage(session_url).url
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    from .locking import resource_lock
    with resource_lock(str(root / '.review-import.lock'), timeout=5):
        destination = root / packet_id
        if destination.exists():
            raise FileExistsError('Review packet already exists; it will not be replaced')
        sources = {}
        for key, path in (('request', request_path), ('clipboard_result', clipboard_result_path),
                          ('clipboard_attempt', clipboard_attempt_path)):
            path = Path(path).resolve(strict=True)
            if not path.is_file() or path.stat().st_size > 2_000_000:
                raise ReviewPacketError('Review source is not a bounded file')
            sources[key] = path.read_bytes()
        result = json.loads(sources['clipboard_result'])
        if (not isinstance(result, dict) or not isinstance(result.get('content'), list)
                or len(result['content']) != 1 or not isinstance(result['content'][0], dict)):
            raise ReviewPacketError('Invalid copied response')
        copied = result['content'][0].get('text')
        if not isinstance(copied, str) or not copied.startswith('Clipboard content:\n'):
            raise ReviewPacketError('Copied response lacks its native prefix')
        sources['answer'] = copied[len('Clipboard content:\n'):].encode('utf-8')
        staging = root / ('.review-import-' + uuid4().hex)
        staging.mkdir()
        manifest = {'version': 1, 'packet_id': staging.name, 'session_url': session_url}
        for key, content in sources.items():
            name = key + ('.txt' if key == 'answer' else '.json')
            (staging / name).write_bytes(content)
            manifest[key] = {'file': name, 'sha256': sha256(content).hexdigest()}
        manifest_path = staging / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        checked = _load_packet(staging.resolve())
        if any(row['native_copy_acquisition_id'] == checked['native_copy_acquisition_id']
               for row in list_review_packets(root)['packets']):
            raise FileExistsError('Copied response already exists in the review queue')
        manifest['packet_id'] = packet_id
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        staging.rename(destination)
        return _load_packet(destination.resolve())
