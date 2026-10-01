"""On-demand original-question evidence; never delivery or performance authority."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import tomllib
from urllib.parse import urlencode, urljoin, urlsplit

from .domain import Difference, Option, Question, compare
from .locking import resource_lock
from .reference_fetch import Budget, LookupFailure, ReferenceFetcher, SourceAdmission, canonical_url
from .reference_providers import HttpProvider, LocalProvider
from .storage import encode, now


VERSION = 'reference-lookup-v3'
TRIGGERS = {'blurred', 'missing_material', 'clean_copy', 'version_difference', 'manual_source'}
ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return sha256(encode(value).encode('utf-8')).hexdigest()


def approved_path(root, value):
    for part in (Path(root), *Path(root).parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Reference directory must not contain redirects')
    root = Path(root).resolve(strict=True)
    path = Path(value)
    path = path if path.is_absolute() else root / path
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Reference directory must not contain redirects')
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError('Reference files must remain within the configured workspace')
    return path


@dataclass(frozen=True)
class LookupConfig:
    enabled: bool = False
    shadow: bool = True
    network_enabled: bool = False
    provider: str = 'brave'
    key_env: str = 'BRAVE_SEARCH_API_KEY'
    cache_root: Path = ROOT / 'data/private/reference-lookup'
    fixture_root: Path = ROOT / 'tests/fixtures/reference_lookup'
    max_search_requests: int = 6
    max_pages: int = 6
    timeout_seconds: float = 15
    total_seconds: float = 120
    request_interval_seconds: float = 10
    retries: int = 1
    cache_seconds: int = 3600
    phrase_words: int = 16
    admissions: tuple = field(default_factory=tuple)

    def __post_init__(self):
        if any(type(getattr(self, key)) is not bool for key in ('enabled', 'shadow', 'network_enabled')):
            raise ValueError('Lookup switches must be explicit booleans')
        if self.provider != 'brave' or not re.fullmatch(r'[A-Z][A-Z0-9_]{1,80}', self.key_env):
            raise ValueError('Only the configured Brave Web Search adapter is supported')
        for key, lower, upper in (('max_search_requests', 1, 6), ('max_pages', 1, 6),
                                  ('retries', 0, 1), ('cache_seconds', 1, 86400), ('phrase_words', 4, 30)):
            value = getattr(self, key)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError('Invalid reference lookup budget: ' + key)
        for key, lower, upper in (('timeout_seconds', .1, 15), ('total_seconds', .1, 120),
                                  ('request_interval_seconds', 10, 3600)):
            value = getattr(self, key)
            if type(value) not in (int, float) or not lower <= value <= upper:
                raise ValueError('Invalid reference lookup budget: ' + key)
        hosts = set()
        for source in self.admissions:
            if not isinstance(source, SourceAdmission) or type(source.automatic_enabled) is not bool:
                raise ValueError('Invalid source admission')
            if (type(source.retention_seconds) is not int or not 0 <= source.retention_seconds <= 86400
                    or type(source.business_record_storage_allowed) is not bool
                    or type(source.request_interval_seconds) not in (int, float)
                    or not 10 <= source.request_interval_seconds <= 86400
                    or not isinstance(source.domain, str) or not isinstance(source.aliases, tuple)
                    or any(not isinstance(host, str) for host in source.aliases)
                    or not isinstance(source.allowed_paths, tuple)
                    or any(not isinstance(p, str) or not p.startswith('/') for p in source.allowed_paths)):
                raise ValueError('Invalid source storage/scope policy')
            for host in (source.domain, *source.aliases):
                if canonical_url('https://' + host + '/').split('/')[2] != host or host in hosts:
                    raise ValueError('Invalid or duplicate admitted host')
                hosts.add(host)

    @classmethod
    def load(cls, path, *, workspace=ROOT):
        with Path(path).open('rb') as stream:
            value = tomllib.load(stream)
        if not isinstance(value.get('lookup', {}), dict) or not isinstance(value.get('sources', []), list):
            raise ValueError('Lookup and sources must be configuration tables')
        if set(value) - {'lookup', 'sources'}:
            raise ValueError('Unknown reference lookup config section')
        settings = dict(value.get('lookup', {}))
        settings['cache_root'] = approved_path(workspace, settings.get('cache_root', 'data/private/reference-lookup'))
        settings['fixture_root'] = approved_path(workspace, settings.get('fixture_root', 'tests/fixtures/reference_lookup'))
        allowed = set(SourceAdmission.__dataclass_fields__)
        sources = []
        for item in value.get('sources', []):
            if not isinstance(item, dict):
                raise ValueError('Source admission must be a configuration table')
            if set(item) - allowed:
                raise ValueError('Unknown source admission setting')
            item = dict(item)
            for key in ('aliases', 'allowed_paths'):
                if not isinstance(item.get(key, []), list):
                    raise ValueError('Source hosts and paths must be lists')
                item[key] = tuple(item.get(key, ()))
            try:
                sources.append(SourceAdmission(**item))
            except TypeError:
                raise ValueError('Invalid source admission setting') from None
        settings['admissions'] = tuple(sources)
        try:
            return cls(**settings)
        except TypeError:
            raise ValueError('Unknown reference lookup setting') from None

    def source(self, url):
        host = urlsplit(canonical_url(url)).hostname
        for item in self.admissions:
            if host in (item.domain, *item.aliases):
                return item
        raise LookupFailure('ACCESS_RESTRICTED', 'SOURCE_ADMISSION_REQUIRED')


def student_snapshot(store, question_id, question_version, context_revision):
    row = store.one('SELECT * FROM questions WHERE id=?', (question_id,))
    if not row or row['current_version'] != question_version or row['context_revision'] != context_revision:
        raise ValueError('Current question/version/context required')
    version = store.one('SELECT * FROM question_versions WHERE id=? AND question_id=?', (question_version, question_id))
    material = store.one('SELECT * FROM material_versions WHERE id=?', (version['material_version'],))
    if not material:
        raise ValueError('Original student material is unavailable')
    first = store.one('''SELECT m.id,m.source_sent_at,m.observed_at FROM question_versions qv
        JOIN messages m ON m.id=qv.source_message WHERE qv.question_id=? AND qv.parent_id IS NULL
        ORDER BY qv.rowid LIMIT 1''', (question_id,))
    return {'case_id': row['case_id'], 'question_id': question_id, 'question_version': question_version,
            'context_revision': context_revision, 'student_question': json.loads(version['payload']),
            'original_student_material': material['raw_text'], 'student_material': material['verified_text'],
            'material_version': material['id'], 'source_message_id': version['source_message'],
            'original_question_time': first['source_sent_at'] if first else None,
            'initial_question_message_id': first['id'] if first else None,
            'observed_at': first['observed_at'] if first else None}


def query_phrases(snapshot, *, fragments=None, words=16):
    """Only already verified teaching fields or explicitly reviewed exact spans.

    No images, names, messages, uncertain OCR correction or LLM-generated text
    are submitted. An unverified fragment is never made reliable by its score.
    """
    question = Question.from_dict(snapshot['student_question'])
    fields = {'material': snapshot['student_material'], 'stem': question.verified_stem}
    available = []
    if fragments is not None:
        if not isinstance(fragments, list) or len(fragments) > 4:
            raise ValueError('At most four reviewed original fragments are allowed')
        for item in fragments:
            if not isinstance(item, dict) or set(item) != {'field', 'start', 'end', 'text', 'reviewed'}:
                raise ValueError('Reviewed fragments require exact field/span provenance')
            name = item['field']
            text = fields.get(name)
            if name == 'material' and text is None:
                text = snapshot['original_student_material']
            if (type(item['reviewed']) is not bool or type(item['start']) is not int or type(item['end']) is not int
                    or not text or not 0 <= item['start'] < item['end'] <= len(text)
                    or text[item['start']:item['end']] != item['text']):
                raise ValueError('Fragment does not match the original observable span')
            if item['reviewed']:
                available.append(item['text'])
    else:
        for name, text in fields.items():
            if text and not any(name in uncertain or uncertain in ('all', 'ocr') for uncertain in question.uncertain_fields):
                available.extend(re.split(r'(?<=[.!?])\s+|\n+', text))
    phrases = []
    for text in available:
        if re.search(r'@|\b(?:\+?\d[ -]*){8,}\b|https?://|\[.*?\]|[\u4e00-\u9fff]', text):
            continue
        tokens = re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text)
        if len(tokens) < 4:
            continue
        # Use a literal contiguous fragment rather than reconstructing OCR text.
        spans = list(re.finditer(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text))
        phrase = text[spans[0].start():spans[min(words, len(spans)) - 1].end()]
        if phrase not in phrases:
            phrases.append(phrase)
    phrases.sort(key=lambda p: -len(set(p.casefold().split())))
    return phrases[:4]


class BraveSearch:
    identity = 'BRAVE_WEB_SEARCH'
    def __init__(self, fetcher, key_env):
        self.fetcher, self.key_env = fetcher, key_env

    def search(self, query, budget):
        if not isinstance(query, str) or len(query) > 600 or len(query.split()) > 75:
            raise LookupFailure('PROVIDER_UNAVAILABLE', 'SEARCH_QUERY_EXCEEDS_PROVIDER_LIMIT')
        key = os.environ.get(self.key_env, '')
        if not key:
            raise LookupFailure('PROVIDER_UNAVAILABLE', 'SEARCH_KEY_NOT_CONFIGURED')
        url = 'https://api.search.brave.com/res/v1/web/search?' + urlencode({'q': query, 'count': 6})
        result = self.fetcher.search_request(url, budget, headers={'X-Subscription-Token': key, 'Accept': 'application/json'})
        try:
            value = json.loads(result.body)
            web = value['web']
            results = [] if web is None else web['results']
            if not isinstance(results, list) or len(results) > 20:
                raise ValueError()
            if any(not isinstance(r, dict) or not isinstance(r.get('url'), str)
                   or not isinstance(r.get('description', ''), str) for r in results):
                raise ValueError()
            return [{'url': r['url'], 'summary': r.get('description', '')[:2000]} for r in results]
        except (ValueError, KeyError, TypeError):
            raise LookupFailure('PROVIDER_UNAVAILABLE', 'SEARCH_RESPONSE_INVALID') from None


class _HTML(HTMLParser):
    """Conservative generic text extraction, not invented production selectors."""
    block = {'p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'br', 'tr', 'table', 'section', 'article'}
    hidden = {'title', 'script', 'style', 'nav', 'header', 'footer', 'aside'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.current, self.skip, self.images = [], [], 0, []

    def flush(self):
        text = ''.join(self.current).strip()
        if text:
            self.lines.append({'id': 'segment:' + str(len(self.lines) + 1), 'text': text,
                               'html_position': list(self.getpos())})
        self.current = []

    def handle_starttag(self, tag, attrs):
        if tag in self.hidden:
            self.skip += 1
        if self.skip:
            return
        if tag in self.block:
            self.flush()
        if tag == 'td' and self.current:
            self.current.append(' | ')
        if tag == 'img':
            attrs = dict(attrs)
            self.images.append({'src': attrs.get('src', ''), 'alt': attrs.get('alt', ''), 'position': list(self.getpos())})
            self.current.append('[image reference]')

    def handle_endtag(self, tag):
        if tag in self.hidden and self.skip:
            self.skip -= 1
            return
        if not self.skip and tag in self.block:
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.current.append(data)


def extract_document(text, *, url, page_hash):
    parser = _HTML()
    parser.feed(text)
    parser.flush()
    title_match = re.search(r'<title[^>]*>(.*?)</title>', text, re.I | re.S)
    lines = parser.lines
    if '<' not in text:
        lines = [{'id': 'line:' + str(i + 1), 'text': line.strip(), 'html_position': [i + 1, 0]}
                 for i, line in enumerate(text.splitlines()) if line.strip()]
    passage, questions, answers = [], [], []
    current, in_answers = None, False
    for line in lines:
        content = line['text']
        if re.fullmatch(r'(?:参考答案(?:与解析)?|答案(?:与解析)?|解析|Answer key|Answers(?: and explanations)?|Reference answers?)\s*[:：]?', content, re.I):
            in_answers = True
            continue
        if in_answers:
            answers.append(line)
            continue
        if re.fullmatch(r'(?:Passage|Reading passage|Questions?|阅读材料|阅读理解|题目)\s*[:：]?', content, re.I):
            continue
        stem = re.match(r'^(\d{1,3})[.、)]\s*(.+)', content, re.S)
        option = re.match(r'^([A-D])[.、)]\s*(.+)', content, re.S)
        if stem:
            current = {'number': stem[1], 'stem': stem[2], 'options': {},
                       'evidence': {'stem': line, 'options': {}}, 'missing_fields': []}
            questions.append(current)
        elif option and current:
            if option[1] in current['options']:
                current['missing_fields'].append('ambiguous_option:' + option[1])
            current['options'][option[1]] = option[2]
            current['evidence']['options'][option[1]] = line
        elif current:
            # Unlabelled continuation cannot silently be assigned to an option.
            current['missing_fields'].append('unassigned_segment:' + line['id'])
        else:
            passage.append(line)
    for q in questions:
        q['missing_fields'] += ['option:' + label for label in 'ABCD' if label not in q['options']]
        if not passage:
            q['missing_fields'].append('passage')
        if parser.images:
            q['missing_fields'].append('visual_content_not_verified')
    images = []
    for item in parser.images:
        try:
            target = canonical_url(urljoin(url, item['src']))
        except LookupFailure:
            target = None
        images.append({**item, 'src': target, 'downloaded': False})
    return {'parser_version': VERSION, 'extractor': 'GENERIC_TEXT_NOT_SITE_VALIDATED',
            'source_url': url, 'page_sha256': page_hash,
            'claimed_title': re.sub('<[^>]+>', '', title_match[1]).strip() if title_match else None,
            'passage': '\n'.join(p['text'] for p in passage) or None,
            'passage_evidence': passage, 'questions': questions[:64],
            'reference_answers': answers, 'image_references': images,
            'missing_fields': ['question_structure'] if not questions else [],
            'original_publication_verified': False, 'external_content_is_untrusted_data': True}


def _reference(document, item):
    return Question(item['number'], item['stem'], item['stem'],
        tuple(Option(digest([document['page_sha256'], item['number'], label, text]), label, index, text, text,
                     document['source_url'] + '#' + item['evidence']['options'][label]['id'])
              for index, (label, text) in enumerate(sorted(item['options'].items()))),
        document['source_url'], tuple(item['missing_fields']))


def verify_candidate(snapshot, document, item):
    student = Question.from_dict(snapshot['student_question'])
    reference = _reference(document, item)
    material = snapshot['student_material']
    comparison = compare(document['page_sha256'], reference, document['passage'],
                         snapshot['question_version'], student, material,
                         student_raw_material=snapshot['original_student_material'])
    result = {'source_url': document['source_url'], 'page_sha256': document['page_sha256'],
              'reference_number': reference.number, 'match_status': 'NO_MATCH',
              'resolution_status': comparison.resolution_status, 'comparison_result': asdict(comparison),
              'verified_fields': [e['field'] for e in comparison.evidence if e['relation'] == 'EQUAL'],
              'missing_fields': list(item['missing_fields']),
              'differences': list(comparison.field_differences), 'option_mapping': [], 'coverage': 'current_subquestion_only',
              'reference_material': document['passage'], 'reference_question': reference.to_dict(),
              'reference_evidence': {'passage': document['passage_evidence'], **item['evidence']},
              'reference_answers': document['reference_answers']}
    result['missing_fields'] += [e['field'] for e in comparison.evidence if e['relation'] == 'UNKNOWN']
    if comparison.resolution_status == 'INCOMPLETE':
        if 'PARTIAL_OBSERVATION' in comparison.relation:
            result['match_status'] = 'PARTIAL_MATCH'
        result['missing_fields'] += list(student.uncertain_fields)
        if material is None:
            result['missing_fields'].append('verified_student_passage')
        if not student.complete:
            result['missing_fields'].append('verified_student_question')
        return result
    if comparison.option_mapping:
        result['match_status'] = 'OPTION_REORDER_VERIFIED' if Difference.ORDER in comparison.differences else 'MATCH_VERIFIED'
        ref_options = {o.id: o for o in reference.options}
        stu_options = {o.id: o for o in student.options}
        result['option_mapping'] = [{'reference_label': ref_options[a].label, 'student_label': stu_options[b].label,
                                    'student_option_id': b, 'text': ref_options[a].verified_text}
                                   for a, b in comparison.option_mapping]
        result['verified_fields'] = ['passage', 'stem', 'option_contents', 'option_bijection']
        result['differences'] += [{'field': 'number', 'kind': 'NUMBER_ONLY'}] if Difference.NUMBER in comparison.differences else []
    elif any(d['kind'] == 'KEY_CONDITION_CONFLICT' for d in comparison.field_differences):
        result['match_status'] = 'KEY_CONFLICT'
    elif comparison.resolution_status == 'UNKNOWN':
        result['match_status'] = 'AMBIGUOUS'
    elif 'SAME_PASSAGE_DIFFERENT_QUESTION' in comparison.relation:
        result['match_status'] = 'SAME_PASSAGE_DIFFERENT_QUESTION'
    else:
        result['match_status'] = 'NO_MATCH'
    return result


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.pending-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


class ReferenceLookup:
    def __init__(self, config, *, fetcher=None, provider=None):
        self.config = config
        self.fetcher = fetcher or ReferenceFetcher(timeout=config.timeout_seconds,
            interval=config.request_interval_seconds, retries=config.retries)
        self.provider = provider or BraveSearch(self.fetcher, config.key_env)
        self._memory = {}
        self._memory_lock = threading.Lock()

    def _identity(self):
        config = asdict(self.config)
        config['cache_root'], config['fixture_root'] = str(self.config.cache_root), str(self.config.fixture_root)
        return [VERSION, config, self.provider.identity, bool(os.environ.get(self.config.key_env))]

    def _fixture_hashes(self, fixtures):
        result = []
        for filename in fixtures:
            path, content = LocalProvider(self.config.fixture_root).read(filename)
            result.append([str(path), sha256(content).hexdigest()])
        return result

    def _expire_memory(self, key, expires):
        with self._memory_lock:
            current = self._memory.get(key)
            if current and current['expires'] == expires:
                self._memory.pop(key, None)

    def _check_root(self):
        for part in (self.config.cache_root, *self.config.cache_root.parents):
            if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
                raise ValueError('Reference cache directory redirected')

    def purge_expired(self):
        self._check_root()
        with self._memory_lock:
            self._memory = {key: value for key, value in self._memory.items() if value['expires'] > time.time()}
        for namespace in ('search', 'page', 'verification'):
            directory = self.config.cache_root / namespace
            if directory.is_symlink() or getattr(directory, 'is_junction', lambda: False)():
                raise ValueError('Reference cache directory redirected')
            if not directory.exists():
                continue
            for index, path in enumerate(directory.glob('*.json')):
                if index >= 1000:
                    raise LookupFailure('INTERNAL_ERROR', 'CACHE_CAPACITY_REQUIRES_REVIEW')
                try:
                    if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                        raise LookupFailure('ACCESS_RESTRICTED', 'CACHE_FILE_REDIRECTED')
                    item = json.loads(path.read_text(encoding='utf-8'))
                    legacy_external = (namespace == 'page' or namespace == 'verification' and item.get('value', {}).get('network_verified'))
                    if item['version'] in ('reference-lookup-v1', 'reference-lookup-v2', VERSION) and (item['expires'] <= time.time() or legacy_external) and digest(item['value']) == item['sha256']:
                        path.unlink()  # Only this module's expired cache file.
                except FileNotFoundError:
                    continue
                except (ValueError, KeyError, TypeError):
                    raise LookupFailure('INTERNAL_ERROR', 'CACHE_CORRUPT') from None

    def _cache(self, namespace, key, *, value=None, ttl=None):
        if namespace not in ('search', 'page', 'verification') or not re.fullmatch('[0-9a-f]{64}', key):
            raise ValueError('Invalid reference cache key')
        path = self.config.cache_root / namespace / (key + '.json')
        # Caller-configured roots are checked on every use, including after a move.
        for parent in (path, *path.parents):
            if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
                raise ValueError('Reference cache directory redirected')
        if value is not None:
            ttl = self.config.cache_seconds if ttl is None else ttl
            if ttl <= 0:
                return None
            # Third-party page bodies and reports containing them never persist
            # to disk. Expiry removes the memory entry; process exit removes all.
            if namespace == 'page' or namespace == 'verification' and value.get('network_verified'):
                entry = {'expires': time.time() + ttl, 'value': json.loads(encode(value))}
                with self._memory_lock:
                    self._memory[(namespace, key)] = entry
                timer = threading.Timer(ttl, self._expire_memory, args=((namespace, key), entry['expires']))
                timer.daemon = True
                timer.start()
                return None
            _atomic_json(path, {'version': VERSION, 'expires': time.time() + ttl,
                               'sha256': digest(value), 'value': value})
            return None
        with self._memory_lock:
            entry = self._memory.get((namespace, key))
            if entry and entry['expires'] > time.time():
                return json.loads(encode(entry['value']))
            self._memory.pop((namespace, key), None)
        if namespace == 'page':
            return None
        try:
            item = json.loads(path.read_text(encoding='utf-8'))
            if item['version'] == VERSION and item['expires'] > time.time() and digest(item['value']) == item['sha256']:
                return item['value']
        except FileNotFoundError:
            return None
        except (ValueError, KeyError, TypeError):
            raise LookupFailure('INTERNAL_ERROR', 'CACHE_CORRUPT') from None
        return None

    def run(self, snapshot, *, trigger=None, candidate_urls=(), fixtures=(), fragments=None,
            remaining_seconds=None, cancelled=None):
        run_started = time.monotonic()
        question = Question.from_dict(snapshot['student_question'])
        report = {'schema_version': 1, 'module_version': VERSION, 'case_id': snapshot['case_id'],
            'question_id': snapshot['question_id'], 'question_version': snapshot['question_version'],
            'context_revision': snapshot['context_revision'], 'shadow': self.config.shadow,
            'original_student_material': snapshot['original_student_material'],
            'original_question_time': snapshot.get('original_question_time'),
            'retrieval_status': 'DISABLED', 'match_status': 'NOT_VERIFIED', 'next_action': 'CONTINUE_STUDENT_MATERIAL',
            'queries': [], 'candidates': [], 'matches': [], 'errors': [], 'cache_hit': False,
            'network_verified': False, 'student_material_replaced': False, 'delivery_completed': False,
            'performance_changed': False, 'created_at': now(), 'expires_at_epoch': time.time() + self.config.cache_seconds}
        if not self.config.enabled:
            report['explanation'] = '原题检索关闭，沿用现有答疑流程。'
            return report
        if fixtures and candidate_urls:
            raise ValueError('Offline fixtures and network candidate URLs must be separate requests')
        self._check_root()
        self.purge_expired()
        if trigger is None and question.complete and snapshot['student_material'] is not None:
            report.update(retrieval_status='NOT_REQUIRED', explanation='学生题面清晰完整，无需联网检索。')
            return report
        if trigger not in TRIGGERS:
            raise ValueError('An explicit supported lookup trigger is required')
        phrases = query_phrases(snapshot, fragments=fragments, words=self.config.phrase_words)
        if not phrases and not candidate_urls and not fixtures:
            report.update(retrieval_status='INSUFFICIENT_CLUES', next_action='REQUEST_REPHOTO',
                          explanation='没有可靠可辨片段，请补充清晰题面；未猜测英文检索。')
            return report
        if len(candidate_urls) + len(fixtures) > self.config.max_pages:
            raise ValueError('Too many candidate pages')
        fixture_hashes = self._fixture_hashes(fixtures)
        key = digest([self._identity(), snapshot, trigger, phrases, list(candidate_urls), fixture_hashes])
        report['lookup_key'] = key
        with resource_lock(self.config.cache_root / ('task-' + key + '.lock')):
            previous = self._cache('verification', key)
            if previous:
                previous['cache_hit'] = True
                return previous
            seconds = min(self.config.total_seconds, remaining_seconds if remaining_seconds is not None else self.config.total_seconds) - (time.monotonic() - run_started)
            if type(seconds) not in (int, float) or not math.isfinite(seconds):
                raise ValueError('Invalid remaining task budget')
            budget = Budget(seconds, cancelled=cancelled)
            try:
                budget.remaining()
            except LookupFailure as exc:
                report.update(retrieval_status=exc.status, next_action='CONTINUE_STUDENT_MATERIAL' if question.complete
                              and snapshot['student_material'] is not None else 'MANUAL_REVIEW',
                              explanation='剩余时限耗尽或已取消；未开始外部检索。', errors=[{'status': exc.status, 'code': exc.code}])
                return report
            candidates = [{'url': url, 'summary': '', 'provider': 'MANUAL_URL'} for url in candidate_urls]
            if not candidates and not fixtures:
                if not self.config.network_enabled:
                    report.update(retrieval_status='PROVIDER_UNAVAILABLE', next_action='MANUAL_REVIEW')
                    report['errors'].append({'status': 'PROVIDER_UNAVAILABLE', 'code': 'NETWORK_DISABLED'})
                else:
                    first = phrases[0]
                    positions = list(re.finditer(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", first))
                    shortened = first[:positions[max(3, len(positions) // 2) - 1].end()]
                    proposed = ['"' + p + '"' for p in phrases[:2]] + ['"' + shortened + '"']
                    proposed += ['"' + p + '"' for p in phrases[2:3]] + [first, shortened]
                    queries = list(dict.fromkeys(proposed))
                    for query in queries[:self.config.max_search_requests]:
                        record = {'query': query, 'provider': self.provider.identity, 'searched_at': now(), 'cache_hit': False}
                        report['queries'].append(record)
                        try:
                            search_key = digest([VERSION, self.provider.identity, self.config.key_env, query])
                            cached_search = self._cache('search', search_key)
                            if cached_search is None:
                                found = self.provider.search(query, budget)
                                searched_at = now()
                                self._cache('search', search_key, value={'results': found, 'searched_at': searched_at})
                            else:
                                found, searched_at = cached_search['results'], cached_search['searched_at']
                                record['cache_hit'] = True
                            record['searched_at'] = searched_at
                            record['result_count'] = len(found)
                            candidates.extend({**item, 'provider': self.provider.identity} for item in found)
                            if found:
                                break
                        except LookupFailure as exc:
                            report['errors'].append({'status': exc.status, 'code': exc.code})
                            break
                        except Exception:
                            report['errors'].append({'status': 'INTERNAL_ERROR', 'code': 'SEARCH_ADAPTER_ERROR'})
                            break
            documents = []
            seen = set()
            for item in candidates:
                if len(report['candidates']) >= self.config.max_pages:
                    break
                record = None
                try:
                    url = canonical_url(item['url'])
                    if url in seen:
                        continue
                    seen.add(url)
                    record = {**item, 'url': url, 'fetched_at': None, 'status': 'NOT_FETCHED'}
                    report['candidates'].append(record)
                    if not self.config.network_enabled:
                        raise LookupFailure('ACCESS_RESTRICTED', 'NETWORK_DISABLED')
                    source = self.config.source(url)
                    source.require(url)
                    if source.retention_seconds <= 0:
                        raise LookupFailure('ACCESS_RESTRICTED', 'SOURCE_STORAGE_POLICY_REQUIRED')
                    page_key = digest([VERSION, url, asdict(source)])
                    document = self._cache('page', page_key)
                    if document is None:
                        captured = HttpProvider(self.config, self.fetcher).capture(url, budget)
                        document = extract_document(captured['content'], url=captured['url'], page_hash=captured['hash'])
                        document['fetched_at'] = captured['retrieved_time']
                        document['robots_sha256'] = captured['robots_sha256']
                        document['robots_checked_at'] = captured['robots_checked_at']
                        document['terms_review'] = captured['terms_review']
                        document['expires_at_epoch'] = time.time() + min(source.retention_seconds, self.config.cache_seconds)
                        self._cache('page', page_key, value=document, ttl=min(source.retention_seconds, self.config.cache_seconds))
                    record.update(status='FETCHED', fetched_at=document['fetched_at'], page_sha256=document['page_sha256'])
                    record['retention_seconds'] = source.retention_seconds
                    documents.append(document)
                    report['expires_at_epoch'] = min(report['expires_at_epoch'], document['expires_at_epoch'])
                    record.update(robots_sha256=document['robots_sha256'], robots_checked_at=document['robots_checked_at'], terms_review=document['terms_review'])
                except LookupFailure as exc:
                    if record is not None:
                        record.update(status=exc.status, code=exc.code)
                    report['errors'].append({'status': exc.status, 'code': exc.code})
                except Exception:
                    if record is not None:
                        record.update(status='INTERNAL_ERROR', code='FETCH_ADAPTER_ERROR')
                    report['errors'].append({'status': 'INTERNAL_ERROR', 'code': 'FETCH_ADAPTER_ERROR'})
            for filename in fixtures:
                try:
                    budget.remaining()
                except LookupFailure as exc:
                    report['errors'].append({'status': exc.status, 'code': exc.code})
                    break
                captured = LocalProvider(self.config.fixture_root).capture(filename, budget)
                url = captured['url']
                document = extract_document(captured['content'], url=url, page_hash=captured['hash'])
                document['fetched_at'] = captured['retrieved_time']
                documents.append(document)
                report['candidates'].append({'url': url, 'provider': 'SELF_AUTHORED_FIXTURE', 'status': 'OFFLINE_FIXTURE',
                    'fetched_at': document['fetched_at'], 'page_sha256': document['page_sha256']})
            for document in documents:
                for item in document['questions']:
                    report['matches'].append(verify_candidate(snapshot, document, item))
            plausible = [m for m in report['matches'] if m['match_status'] in ('MATCH_VERIFIED', 'OPTION_REORDER_VERIFIED', 'PARTIAL_MATCH')]
            if len(plausible) > 1:
                report['match_status'], report['next_action'] = 'AMBIGUOUS', 'MANUAL_REVIEW'
            elif plausible:
                report['match_status'] = plausible[0]['match_status']
                report['next_action'] = 'REVIEW_CANDIDATE' if report['match_status'] != 'PARTIAL_MATCH' else 'REQUEST_CLEAN_ORIGINAL'
            elif any(m['match_status'] == 'KEY_CONFLICT' for m in report['matches']):
                report['match_status'], report['next_action'] = 'KEY_CONFLICT', 'MANUAL_REVIEW'
            elif any(m['match_status'] == 'AMBIGUOUS' for m in report['matches']):
                report['match_status'], report['next_action'] = 'AMBIGUOUS', 'MANUAL_REVIEW'
            elif any(m['match_status'] == 'SAME_PASSAGE_DIFFERENT_QUESTION' for m in report['matches']):
                report['match_status'], report['next_action'] = 'SAME_PASSAGE_DIFFERENT_QUESTION', 'MANUAL_REVIEW'
            elif documents:
                report['match_status'] = 'NO_MATCH'
            if documents:
                report['retrieval_status'] = 'OFFLINE_FIXTURE' if fixtures else 'CANDIDATES_FOUND'
            elif report['errors']:
                report['retrieval_status'] = report['errors'][0]['status']
            else:
                report['retrieval_status'], report['match_status'] = 'NO_RESULTS', 'NO_MATCH'
            report['network_verified'] = bool(documents and not fixtures)
            report['student_can_continue'] = question.complete and snapshot['student_material'] is not None
            if report['student_can_continue'] and report['next_action'] not in ('REVIEW_CANDIDATE', 'MANUAL_REVIEW'):
                report['next_action'] = 'CONTINUE_STUDENT_MATERIAL'
            if report['student_can_continue'] and not documents:
                report['next_action'] = 'CONTINUE_STUDENT_MATERIAL'
            elif report['match_status'] == 'NOT_VERIFIED':
                report['next_action'] = 'MANUAL_REVIEW'
            report['explanation'] = {'MATCH_VERIFIED': '待答小题必要题面一致；当前仍是候选，确认后才可辅助答疑。',
                'OPTION_REORDER_VERIFIED': '候选选项文本已一一映射，待确认；解答必须使用学生版字母。',
                'PARTIAL_MATCH': '仅局部对应，缺失部分未确认为学生版本。', 'AMBIGUOUS': '多个候选仍无法区分，请人工核对。',
                'KEY_CONFLICT': '否定、数字或关键条件冲突，不能沿用外部版本。',
                'SAME_PASSAGE_DIFFERENT_QUESTION': '同一文章但问法或选项不同，不能当作同一道题。',
                'NO_MATCH': '已检查材料未匹配；完整学生题面仍可独立解答。',
                'NOT_VERIFIED': '未完成题面核验；请查看密钥、准入或访问状态。'}[report['match_status']]
            # Failures are not negative search/match cache entries.
            if report['retrieval_status'] in ('OFFLINE_FIXTURE', 'CANDIDATES_FOUND', 'NO_RESULTS'):
                self._cache('verification', key, value=report,
                            ttl=max(0, report['expires_at_epoch'] - time.time()))
            return report

    def run_for_question(self, store, question_id, question_version, context_revision, *, retry=False, **kwargs):
        request_started = time.monotonic()
        snapshot = student_snapshot(store, question_id, question_version, context_revision)
        if not self.config.enabled or kwargs.get('trigger') is None:
            return self.run(snapshot, **kwargs)
        if kwargs.get('trigger') not in TRIGGERS:
            raise ValueError('An explicit supported lookup trigger is required')
        if kwargs.get('fixtures') and kwargs.get('candidate_urls'):
            raise ValueError('Offline fixtures and network candidate URLs must be separate requests')
        self._check_root()
        self.purge_expired()
        key_kwargs = {k: [str(p) for p in v] if k == 'fixtures' else v for k, v in kwargs.items() if k != 'cancelled'}
        request_key = digest([self._identity(), snapshot, key_kwargs, self._fixture_hashes(kwargs.get('fixtures', ()))])
        with resource_lock(self.config.cache_root / ('request-' + request_key + '.lock')):
            old = store.one("SELECT details FROM audit WHERE event='REFERENCE_LOOKUP_REPORT' AND json_extract(details,'$.request_key')=? ORDER BY id DESC LIMIT 1", (request_key,))
            if old and not retry:
                meta = json.loads(old['details'])
                cached = self._cache('verification', meta.get('lookup_key', '')) if meta.get('lookup_key') else None
                return self._report_state(store, cached or meta, evidence_available=bool(cached), reused=True)
            started = store.one("SELECT id FROM audit WHERE event='REFERENCE_LOOKUP_STARTED' AND json_extract(details,'$.request_key')=?", (request_key,))
            if started and not old and not retry:
                return {'question_id': question_id, 'question_version': question_version,
                        'retrieval_status': 'INTERRUPTED', 'match_status': 'NOT_VERIFIED', 'next_action': 'MANUAL_REVIEW',
                        'explanation': '上次检索中断；核对后可显式重试，不自动重复搜索。'}
            with store.transaction():
                store.execute('INSERT INTO audit(case_id,question_id,event,details,created_at) VALUES(?,?,?,?,?)',
                    (snapshot['case_id'], question_id, 'REFERENCE_LOOKUP_STARTED', encode({'request_key': request_key,
                     'question_version': question_version, 'context_revision': context_revision}), now()))
            allowance = kwargs.get('remaining_seconds')
            allowance = min(self.config.total_seconds, allowance if allowance is not None else self.config.total_seconds)
            kwargs['remaining_seconds'] = allowance - (time.monotonic() - request_started)
            report = self.run(snapshot, **kwargs)
            self.register_candidates(store, report)
            with store.transaction():
                current = store.one('SELECT current_version,context_revision FROM questions WHERE id=?', (question_id,))
                report['stale'] = tuple(current) != (question_version, context_revision)
                if report['stale']:
                    report.update(next_action='MANUAL_REVIEW', explanation='检索期间题目已更新；旧结果仅保留历史，不适用于当前版本。')
                metadata = {key: report.get(key) for key in ('lookup_key', 'case_id', 'question_id', 'question_version',
                    'context_revision', 'retrieval_status', 'match_status', 'next_action', 'explanation', 'errors', 'stale')}
                metadata['request_key'] = request_key
                store.execute('INSERT INTO audit(case_id,question_id,event,details,created_at) VALUES(?,?,?,?,?)',
                    (snapshot['case_id'], question_id, 'REFERENCE_LOOKUP_REPORT', encode(metadata), now()))
            return report

    def register_candidates(self, store, report):
        """Persist allowed candidates, never auto-confirm or overwrite student facts."""
        from .reference_resolution import ReferenceResolution
        current = store.one('SELECT current_version,context_revision FROM questions WHERE id=?', (report['question_id'],))
        if not current or tuple(current) != (report['question_version'], report['context_revision']):
            return
        resolution = ReferenceResolution(store)
        for match in report.get('matches', []):
            offline = report['retrieval_status'] == 'OFFLINE_FIXTURE'
            policy = {'kind': 'SELF_AUTHORED_OFFLINE', 'business_record_storage_allowed': True} if offline else asdict(self.config.source(match['source_url']))
            if policy.get('business_record_storage_allowed') is not True:
                match['candidate_storage'] = 'SOURCE_STORAGE_NOT_APPROVED'
                continue
            candidate = next((c for c in report['candidates'] if c['url'] == match['source_url']), {})
            try:
                candidate_id, _ = resolution.add(report['question_version'], 'lookup:' + report['lookup_key'],
                    Question.from_dict(match['reference_question']), match['reference_material'],
                    'SELF_AUTHORED_FIXTURE' if offline else self.config.source(match['source_url']).domain,
                    expected_context_revision=report['context_revision'], provenance={
                        'source_url': match['source_url'], 'content_hash': match['page_sha256'],
                        'retrieved_at': candidate.get('fetched_at'), 'reference_evidence': match['reference_evidence'],
                        'source_policy': policy})
                match['candidate_id'] = candidate_id
            except ValueError:
                current = store.one('SELECT current_version,context_revision FROM questions WHERE id=?', (report['question_id'],))
                if current and tuple(current) == (report['question_version'], report['context_revision']):
                    raise
                return
        if report.get('lookup_key') and report['retrieval_status'] in ('OFFLINE_FIXTURE', 'CANDIDATES_FOUND', 'NO_RESULTS'):
            self._cache('verification', report['lookup_key'], value=report,
                        ttl=max(0, report['expires_at_epoch'] - time.time()))

    def _report_state(self, store, report, *, evidence_available, reused=False):
        report = dict(report)
        current = store.one('SELECT current_version,context_revision FROM questions WHERE id=?', (report['question_id'],))
        report['stale'] = not current or tuple(current) != (report['question_version'], report['context_revision'])
        report['reused_task'] = reused
        if not evidence_available:
            report['evidence_expired_or_unavailable'] = True
            if report.get('match_status') in ('MATCH_VERIFIED', 'OPTION_REORDER_VERIFIED', 'PARTIAL_MATCH'):
                report.update(previous_match_status=report['match_status'], match_status='NOT_VERIFIED',
                              next_action='MANUAL_REVIEW', explanation='证据已过期或不在当前进程，不能使用旧核验结论；核对后可显式重试。')
        if report['stale']:
            report.update(next_action='MANUAL_REVIEW', explanation='题目已更新；旧报告仅保留历史，不适用于当前版本。')
        return report

    def reports(self, store):
        self.purge_expired()
        results = []
        for row in store.all("SELECT details FROM audit WHERE event='REFERENCE_LOOKUP_REPORT' ORDER BY id DESC LIMIT 50"):
            metadata = json.loads(row['details'])
            report = self._cache('verification', metadata['lookup_key']) if metadata.get('lookup_key') else None
            results.append(self._report_state(store, report or metadata, evidence_available=bool(report)))
        return results

    def _require_consumption_source(self, candidate):
        policy = candidate['source_policy']
        if policy.get('business_record_storage_allowed') is not True:
            raise ValueError('Source has not approved internal reference use')
        if policy.get('kind') == 'SELF_AUTHORED_OFFLINE' and candidate['source'] == 'SELF_AUTHORED_FIXTURE':
            return
        source = self.config.source(candidate['source_url'])
        source.require(candidate['source_url'])
        if source.business_record_storage_allowed is not True:
            raise ValueError('Current source policy does not permit reference use')

    def candidates(self, store):
        from .reference_resolution import ReferenceResolution
        result = ReferenceResolution(store).list()
        for item in result:
            item['can_use'] = False
            if (not self.config.shadow and not item['stale'] and not item['input_pending_review']
                    and item['state'] == 'CONFIRMED' and not item['consumption_enabled']):
                try:
                    self._require_consumption_source(item)
                    item['can_use'] = True
                except ValueError:
                    pass
            if not self.config.shadow and item['can_confirm']:
                try:
                    self._require_consumption_source(item)
                except ValueError:
                    item['can_confirm'] = False
        return result

    def review_candidate(self, store, candidate_id, **review):
        if not self.config.enabled:
            raise ValueError('Reference review is disabled')
        from .reference_resolution import ReferenceResolution, candidate_view
        row = store.one('SELECT * FROM reference_candidates WHERE id=?', (candidate_id,))
        if not row:
            raise ValueError('Unknown candidate')
        consume = review['decision'] == 'confirm' and not self.config.shadow
        if consume:
            self._require_consumption_source(candidate_view(row))
        return ReferenceResolution(store).review(candidate_id, consume=consume, **review)

    def apply(self, store, lookup_key, *, reviewer, reason=None):
        """Explicit existing-review consumption; adds reference only, never fills student fields."""
        if not self.config.enabled or self.config.shadow:
            raise ValueError('Shadow reports cannot be consumed')
        if (not isinstance(reviewer, str) or not 0 < len(reviewer.strip()) <= 80
                or not isinstance(reason, str) or not 0 < len(reason.strip()) <= 2000):
            raise ValueError('Explicit reviewer and actual review rationale required')
        if not isinstance(lookup_key, str) or not re.fullmatch('[0-9a-f]{64}', lookup_key):
            raise ValueError('Invalid lookup key')
        report = self._cache('verification', lookup_key)
        if not report or report['match_status'] not in ('MATCH_VERIFIED', 'OPTION_REORDER_VERIFIED'):
            raise ValueError('Unexpired unambiguous verified reference required')
        current = store.one('SELECT current_version,context_revision FROM questions WHERE id=?', (report['question_id'],))
        if not current or tuple(current) != (report['question_version'], report['context_revision']):
            raise ValueError('Reference result belongs to a stale question/context')
        matches = [m for m in report['matches'] if m['match_status'] in ('MATCH_VERIFIED', 'OPTION_REORDER_VERIFIED')]
        if len(matches) != 1:
            raise ValueError('Ambiguous reference cannot be consumed')
        match = matches[0]
        if report['retrieval_status'] != 'OFFLINE_FIXTURE':
            source = self.config.source(match['source_url'])
            source.require(match['source_url'])
            if source.business_record_storage_allowed is not True:
                raise ValueError('Source has not approved internal reference ledger storage')
        self.register_candidates(store, report)
        if not match.get('candidate_id'):
            raise ValueError('Reference candidate storage is not approved')
        self.review_candidate(store, match['candidate_id'], question_version=report['question_version'],
            context_revision=report['context_revision'], reviewer=reviewer, reason=reason,
            decision='confirm')
        snapshot = student_snapshot(store, report['question_id'], report['question_version'], report['context_revision'])
        return compare('lookup:' + lookup_key, Question.from_dict(match['reference_question']), match['reference_material'],
            report['question_version'], Question.from_dict(snapshot['student_question']), snapshot['student_material'])
