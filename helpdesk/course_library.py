"""Read-only retrieval of course evidence; results are not answer authorization."""
from hashlib import sha256
import json
from pathlib import Path


class CourseLibraryError(ValueError):
    pass


def _file(root, relative):
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts or ':' in str(relative):
        raise CourseLibraryError('Unsafe library path')
    path = root / rel
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise CourseLibraryError('Library symlink is not allowed')
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise CourseLibraryError('Library file escapes root')
    if resolved.stat().st_size > 25_000_000:
        raise CourseLibraryError('Library file too large')
    return resolved


def search_course(root, terms, *, limit=8):
    """Return bounded source excerpts, hashes and explicit review requirements.

    Original-machine paths in the catalog are never followed. Originals must be
    in this package and match the supplied catalog. No scripts are executed.
    """
    root = Path(root).resolve(strict=True)
    terms = tuple(dict.fromkeys(t.strip().casefold() for t in terms if t.strip()))
    if not terms or len(terms) > 12 or any(len(t) > 100 for t in terms):
        raise CourseLibraryError('Provide 1 to 12 nonempty short search terms')
    if not isinstance(limit, int) or not 1 <= limit <= 20:
        raise CourseLibraryError('Limit must be between 1 and 20')
    catalog_path = _file(root, 'references/catalog.json')
    catalog_bytes = catalog_path.read_bytes()
    records = json.loads(catalog_bytes.decode('utf-8'))
    if not isinstance(records, list) or len(records) > 1000:
        raise CourseLibraryError('Invalid catalog')
    hits = []
    seen = set()
    for item in records:
        if item['id'] in seen:
            raise CourseLibraryError('Duplicate source identity')
        seen.add(item['id'])
        name = item['name']
        if Path(name).name != name or '/' in name or '\\' in name:
            raise CourseLibraryError('Invalid original filename')
        source = _file(root, item['text'])
        original = _file(root, 'references/originals/' + name)
        if sha256(original.read_bytes()).hexdigest() != item['sha256']:
            raise CourseLibraryError('Original source hash mismatch')
        data = source.read_bytes()
        lines = data.decode('utf-8').splitlines()
        digest = sha256(data).hexdigest()
        for index, line in enumerate(lines):
            score = sum(term in line.casefold() for term in terms)
            if score:
                start, end = max(0, index - 2), min(len(lines), index + 4)
                excerpt = '\n'.join(lines[start:end])
                hits.append({'source_id': item['id'], 'source_name': name,
                             'text_path': str(source), 'original_path': str(original),
                             'original_sha256': item['sha256'], 'text_sha256': digest,
                             'match_line': index + 1, 'excerpt_start_line': start + 1,
                             'excerpt_end_line': end, 'excerpt': excerpt[:8000],
                             'excerpt_truncated': len(excerpt) > 8000,
                             'warnings': item.get('warnings', []), 'score': score,
                             'requires_context_review': True})
    # Image recovery is separate from the catalog: include it when present and
    # label it as transcribed evidence, never a verified original quotation.
    visual = root / 'references/visual-recovery.md'
    if visual.exists():
        visual = _file(root, 'references/visual-recovery.md')
        data = visual.read_bytes()
        lines = data.decode('utf-8').splitlines()
        for index, line in enumerate(lines):
            score = sum(term in line.casefold() for term in terms)
            if score:
                start, end = max(0, index - 2), min(len(lines), index + 4)
                excerpt = '\n'.join(lines[start:end])
                hits.append({'source_id': 'visual-recovery', 'source_name': '图像补录',
                             'text_path': str(visual), 'text_sha256': sha256(data).hexdigest(),
                             'match_line': index + 1, 'excerpt_start_line': start + 1,
                             'excerpt_end_line': end, 'excerpt': excerpt[:8000],
                             'excerpt_truncated': len(excerpt) > 8000,
                             'warnings': ['人工读图转述，精确原句须回原图核实'],
                             'score': score, 'requires_context_review': True})
    hits.sort(key=lambda x: (-x['score'], x['source_id'], x['match_line']))
    return {'catalog_sha256': sha256(catalog_bytes).hexdigest(), 'source_count': len(records),
            'terms': terms, 'total_hits': len(hits), 'hits': hits[:limit],
            'answer_generation_authorized': False,
            'note': '检索结果仅用于定位，需阅读完整上下文并核查课程冲突后再解题。'}
