"""Shared bounded I/O and durable POST intent. API signatures stay in each client."""
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import base64
import http.client
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
import tomllib
from contextlib import nullcontext
from urllib.parse import urlencode

from .locking import resource_lock
from .storage import encode, now

ROOT = Path(__file__).resolve().parents[1]
ENDPOINTS = {'erase': '/ocr_writing_erase', 'search': '/ocrquestionapi'}


class ApiError(ValueError):
    pass


def checked_path(path, root, *, exists=True):
    path, root = Path(path).absolute(), Path(root).absolute()
    if '..' in path.parts or not path.is_relative_to(root):
        raise ApiError('PATH_OUTSIDE_APPROVED_DIRECTORY')
    for item in (path, *path.parents, root, *root.parents):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise ApiError('PATH_REDIRECTED')
    resolved, allowed = path.resolve(strict=exists), root.resolve(strict=True)
    if not resolved.is_relative_to(allowed):
        raise ApiError('PATH_OUTSIDE_APPROVED_DIRECTORY')
    return resolved


@dataclass(frozen=True)
class Settings:
    workspace: Path = ROOT
    approved_image_root: str = 'data/private/youdao/test-images'
    private_root: str = 'data/private/youdao/cache'
    credential_file: str = 'data/private/youdao-credentials.local.json'
    erase_enabled: bool = False
    search_enabled: bool = False
    config_version: str = 'youdao-2026-10-20-v1'
    retention_seconds: int = 604800
    connect_timeout: float = 5
    read_timeout: float = 20
    max_response_bytes: int = 16000000
    budget_cny: str = '0.50'
    warning_cny: str = '1.00'
    erase_reservation_cny: str | None = None
    search_reservation_cny: str | None = None

    @classmethod
    def load(cls, path, *, workspace=ROOT):
        value = tomllib.loads(Path(path).read_text(encoding='utf-8'))
        if set(value) != {'youdao'} or 'workspace' in value['youdao']:
            raise ApiError('CONFIG_INVALID')
        return cls(workspace=Path(workspace), **value['youdao'])

    def paths(self):
        if (type(self.erase_enabled) is not bool or type(self.search_enabled) is not bool
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', self.config_version)
                or type(self.retention_seconds) is not int or not 60 <= self.retention_seconds <= 2592000
                or not 1 <= self.connect_timeout <= 30 or not 1 <= self.read_timeout <= 60
                or not 1024 <= self.max_response_bytes <= 16000000):
            raise ApiError('CONFIG_INVALID')
        for amount in (self.budget_cny, self.warning_cny, self.erase_reservation_cny, self.search_reservation_cny):
            if amount is not None:
                money(amount)
        base = Path(self.workspace).resolve(strict=True)
        private = checked_path(base/'data/private', base, exists=False)
        private.mkdir(parents=True, exist_ok=True)
        cache = checked_path(base/self.private_root, private, exists=False)
        images = checked_path(base/self.approved_image_root, base)
        if not images.is_dir() or not cache.is_relative_to(private) or images == cache or images.is_relative_to(cache):
            raise ApiError('CONFIG_PATH_INVALID')
        cache.mkdir(parents=True, exist_ok=True)
        return images, cache


def money(value):
    try:
        amount = Decimal(value) if isinstance(value, str) else Decimal('NaN')
    except Exception:
        raise ApiError('BUDGET_INVALID') from None
    if not amount.is_finite() or amount <= 0 or amount > 1000:
        raise ApiError('BUDGET_INVALID')
    return amount


def credentials(settings, api):
    prefix = 'YOUDAO_' + api.upper()
    values = {}
    path = checked_path(Path(settings.workspace)/settings.credential_file,
                        Path(settings.workspace)/'data/private', exists=False)
    if path.exists():
        if path.stat().st_size > 8192:
            raise ApiError('CREDENTIAL_CONFIG_INVALID')
        values = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(values, dict):
            raise ApiError('CREDENTIAL_CONFIG_INVALID')
    pair = tuple(os.environ.get(prefix+suffix) or values.get(prefix+suffix) for suffix in ('_APP_KEY', '_APP_SECRET'))
    return pair if all(isinstance(v, str) and v.strip() and len(v) <= 512 for v in pair) else None


def image_bytes(path, root, *, api):
    from PIL import Image
    path = checked_path(path, root)
    maximum = 5000000 if api == 'erase' else 1000000
    if not path.is_file() or not 0 < path.stat().st_size < maximum:
        raise ApiError('IMAGE_SIZE_INVALID')
    raw = path.read_bytes()
    if not 0 < len(raw) < maximum or len(base64.b64encode(raw)) >= maximum:
        raise ApiError('IMAGE_SIZE_INVALID')
    try:
        with Image.open(io.BytesIO(raw)) as img:
            expected = {'.jpg':'JPEG', '.jpeg':'JPEG', '.png':'PNG', '.bmp':'BMP'}.get(path.suffix.lower())
            if img.format != expected or expected is None or getattr(img, 'n_frames', 1) != 1:
                raise ApiError('IMAGE_FORMAT_INVALID')
            width, height = img.size
            if width < 1 or height < 1 or width*height > 20000000:
                raise ApiError('IMAGE_DIMENSIONS_INVALID')
            if api == 'search' and (min(width, height) <= 10 or max(width, height) >= 2048):
                raise ApiError('IMAGE_DIMENSIONS_INVALID')
            img.verify()
    except ApiError:
        raise
    except Exception:
        raise ApiError('IMAGE_DECODE_FAILED') from None
    return raw, sha256(raw).hexdigest(), [width, height]


def post(api, fields, settings):
    """Exactly one official request. http.client ignores environment proxies."""
    if api not in ENDPOINTS:
        raise ApiError('ENDPOINT_NOT_ALLOWED')
    connection = http.client.HTTPSConnection('openapi.youdao.com', 443, timeout=settings.connect_timeout)
    try:
        connection.connect()
        connection.sock.settimeout(settings.read_timeout)
        connection.request('POST', ENDPOINTS[api], body=urlencode(fields).encode('utf-8'),
                           headers={'Content-Type':'application/x-www-form-urlencoded; charset=utf-8'})
        response = connection.getresponse()
        body = response.read(settings.max_response_bytes+1)
        if len(body) > settings.max_response_bytes:
            raise ApiError('RESPONSE_TOO_LARGE')
        return response.status, body
    except ApiError:
        raise
    except Exception:
        raise ApiError('POST_RESULT_UNKNOWN') from None
    finally:
        connection.close()


def _save(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                                         prefix=path.name+'.',suffix='.tmp',delete=False) as stream:
            temporary=Path(stream.name)
            stream.write(encode(value)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()


class Client:
    """Existing cache plus optional original run audit, never a second cost DB."""
    api = None
    protocol = None

    def __init__(self, settings, *, transport=post):
        self.settings, self.transport = settings, transport

    def execute(self, image, *, context, store=None, run_id=None, origin_sha256=None,
                expected_sha256=None, dry_run=False, **parameters):
        settings = self.settings
        root, cache = settings.paths()
        supplied_path = Path(image).absolute()
        image_path = checked_path(supplied_path,cache if supplied_path.is_relative_to(cache) else root)
        if image_path.is_relative_to(cache):
            # Only our recorded successful derivative can widen the input root.
            if not re.fullmatch(r'[0-9a-f]{64}\.png', image_path.name):
                raise ApiError('DERIVATIVE_NOT_BOUND')
            proof = json.loads(checked_path(cache/(image_path.stem+'.json'),cache).read_text(encoding='utf-8'))
            previous = proof.get('result',{})
            if (proof.get('state')!='FINISHED' or previous.get('status')!='CLEANUP_SUCCEEDED'
                    or previous.get('original_sha256')!=origin_sha256
                    or previous.get('processed_sha256')!=sha256(image_path.read_bytes()).hexdigest()
                    or time.time()>proof['expires_at']):
                raise ApiError('DERIVATIVE_NOT_BOUND')
            root = cache
        raw, image_hash, dimensions = image_bytes(image_path, root, api=self.api)
        if expected_sha256 is not None and image_hash!=expected_sha256:
            raise ApiError('ORIGINAL_IMAGE_CHANGED')
        origin = origin_sha256 or image_hash
        if not re.fullmatch('[0-9a-f]{64}', origin):
            raise ApiError('ORIGINAL_HASH_INVALID')
        result = dict(provider='YOUDAO_'+self.api.upper(), original_sha256=origin, input_sha256=image_hash,
                      dimensions=dimensions, actual_call=False, cache_hit=False, charge_status='UNVERIFIED',
                      amount_cny=None, error_code=None, injected=self.transport is not post)
        parameters = self.parameters(parameters)  # Also validate parameters during dry-run.
        if dry_run:
            return result | {'status':'DRY_RUN'}
        if not getattr(settings, self.api+'_enabled'):
            return result | {'status':'PROVIDER_DISABLED'}
        pair = credentials(settings, self.api)
        if not pair:
            return result | {'status':'CREDENTIALS_NOT_CONFIGURED'}
        if (not isinstance(context, dict) or not context.get('question_version')
                or type(context.get('context_revision')) is not int
                or set(context)-{'question_id','question_version','context_revision','synthetic_test'}):
            raise ApiError('QUESTION_CONTEXT_REQUIRED')
        scope = dict(context)
        if store is not None:
            from .call_costs import _run
            row, binding = _run(store, run_id)
            if row['state'] != 'RUNNING' or any(scope.get(k) != binding[k] for k in ('question_id','question_version','context_revision')):
                raise ApiError('STALE_QUESTION_CONTEXT')
            scope = binding
        account = sha256(pair[0].encode()).hexdigest()
        semantic_scope = {k:v for k,v in scope.items() if k not in ('run_id','session_id')}
        key = sha256(encode([origin, image_hash, self.api, self.protocol, account, result['injected'], semantic_scope,
                             parameters, settings.config_version]).encode()).hexdigest()
        journal = cache/(key+'.json')
        blocker = cache/('pending-'+sha256(encode([origin,self.api]).encode()).hexdigest()+'.json')
        # Cross-version calls for this image and all Youdao calls in a passage serialize.
        ledger_lock = resource_lock(str(store.path)+'.youdao.lock') if store is not None else nullcontext()
        with resource_lock(cache/'calls.lock'), ledger_lock:
            if store is not None:
                from .question_matching import _current
                _current(store, json.loads(row['input_json']))
            if journal.exists():
                saved = json.loads(checked_path(journal,cache).read_text(encoding='utf-8'))
                if saved.get('key') != key:
                    raise ApiError('CACHE_IDENTITY_CHANGED')
                if saved.get('state') != 'FINISHED':
                    return result | {'status':'RESULT_UNKNOWN', 'error_code':'PRIOR_POST_NOT_REPLAYED'}
                if time.time() > saved['expires_at']:
                    return result | {'status':'MANUAL_REVIEW_REQUIRED', 'error_code':'CACHE_EXPIRED_NO_AUTOMATIC_RECALL'}
                body_path = checked_path(cache/(key+'.response'), cache)
                with body_path.open('rb') as stream:
                    body = stream.read(settings.max_response_bytes+1)
                if len(body)>settings.max_response_bytes:
                    raise ApiError('CACHE_RESPONSE_CHANGED')
                if sha256(body).hexdigest() != saved['result']['raw_response_sha256']:
                    raise ApiError('CACHE_RESPONSE_CHANGED')
                if saved['result'].get('processed_path'):
                    processed = checked_path(saved['result']['processed_path'], cache)
                    if sha256(processed.read_bytes()).hexdigest()!=saved['result']['processed_sha256']:
                        raise ApiError('CACHE_DERIVATIVE_CHANGED')
                if saved['result']['status'].endswith('FAILED'):
                    return saved['result'] | {'actual_call':False,'cache_hit':True}
                # Parse again, including generated-image hashes, before reuse.
                parsed = self.parse(body, cache, key)
                return saved['result'] | parsed | {'actual_call':False,'cache_hit':True}
            if blocker.exists():
                return result | {'status':'RESULT_UNKNOWN','error_code':'UNRESOLVED_ORIGINAL_POST'}
            reserve = getattr(settings, self.api+'_reservation_cny')
            if reserve is None:
                return result | {'status':'BUDGET_REVIEW_REQUIRED','error_code':'REVIEWED_CALL_CEILING_REQUIRED'}
            reservation = money(reserve)
            used = self._reserved(store, scope, cache, origin)
            if used+reservation > money(settings.budget_cny):
                return result | {'status':'BUDGET_EXCEEDED','budget_warning':used >= money(settings.warning_cny)}
            result['budget_warning']=used+reservation >= money(settings.warning_cny)
            record = dict(key=key, state='INTENT', scope=scope, origin_sha256=origin, reservation_cny=reserve,
                          api=self.api, injected=result['injected'], created_at=now(), expires_at=time.time()+settings.retention_seconds)
            _save(journal, record); _save(blocker, {'key':key})
            call_id = None
            if store is not None:
                from .call_costs import start_call
                with store.transaction():
                    store.execute('INSERT INTO audit(run_id,question_id,event,details,created_at) VALUES(?,?,?,?,?)',
                        (run_id, scope['question_id'], 'YOUDAO_API_INTENT', encode(record), now()))
                call_id = start_call(store, run_id, 'SEARCH' if self.api == 'search' else 'OTHER',
                                     'YOUDAO_'+self.api.upper(), key, key, injected=result['injected'])
            started = time.monotonic()
            try:
                fields = self.sign(base64.b64encode(raw).decode('ascii'), *pair, **parameters)
                status, body = self.transport(self.api, fields, settings)
                result.update(actual_call=True, latency_ms=round((time.monotonic()-started)*1000),
                              raw_response_sha256=sha256(body).hexdigest(), retrieved_at=now())
                checked_path(cache/(key+'.response'),cache,exists=False).write_bytes(body)
                if status != 200:
                    raise ApiError('HTTP_'+str(status))
                parsed = self.parse(body, cache, key)
                result.update(parsed)
            except Exception as exc:
                code = str(exc) if isinstance(exc, ApiError) and re.fullmatch('[A-Z0-9_]{1,80}',str(exc)) else 'RESPONSE_INVALID'
                result.update(actual_call=True, latency_ms=round((time.monotonic()-started)*1000),
                              status='RESULT_UNKNOWN', error_code=code)
                if code.startswith('HTTP_') and code[5:].isdigit() and 300 <= int(code[5:]) < 500:
                    result['status'] = self.api.upper()+'_FAILED'
            if call_id:
                from .call_costs import finish_call
                state = 'UNKNOWN' if result['status'] == 'RESULT_UNKNOWN' else 'FAILED' if result['status'].endswith('FAILED') else 'CONFIRMED'
                finish_call(store, run_id, call_id, state, 'sha256:'+result.get('raw_response_sha256',key))
            # Keep only intent/summary after payload expiry, not duplicate question/answer text.
            record.update(state='FINISHED' if result['status'] != 'RESULT_UNKNOWN' else 'UNKNOWN',
                          result={k:v for k,v in result.items() if k not in ('candidates','ocr_text')})
            _save(journal, record)
            if result['status'] != 'RESULT_UNKNOWN':
                blocker.unlink()
            return result

    def _reserved(self, store, scope, cache, origin):
        if store is None:
            return sum((money(v['reservation_cny']) for p in cache.glob('*.json')
                        if re.fullmatch(r'[0-9a-f]{64}\.json',p.name)
                        and (v:=json.loads(checked_path(p,cache).read_text(encoding='utf-8'))).get('origin_sha256') == origin), Decimal(0))
        # All versions, subquestions and followups of this student's material.
        question = store.one('SELECT case_id,material_id FROM questions WHERE id=?',(scope['question_id'],))
        ids = [r[0] for r in store.all('SELECT id FROM questions WHERE case_id=? AND material_id=?',tuple(question))]
        placeholders = ','.join('?' for _ in ids)
        rows = store.all('SELECT event,details FROM audit WHERE question_id IN ('+placeholders+')',ids)
        reservations = {json.loads(r['details'])['key']:money(json.loads(r['details'])['reservation_cny'])
                        for r in rows if r['event']=='YOUDAO_API_INTENT'}
        from .call_costs import CHARGE, START
        # Billing is still authoritative; a reservation is never recorded as a fee.
        starts = {json.loads(r['details'])['call_id']:json.loads(r['details']) for r in rows if r['event']==START}
        from .call_costs import _proof_valid
        charges = {v['call_id']:Decimal(v['amount_cny']) for r in rows if r['event']==CHARGE
                   and _proof_valid((v:=json.loads(r['details']))['proof'])}
        if any(c['call_id'] not in charges and c['request_key'] not in reservations for c in starts.values()):
            raise ApiError('COST_SCOPE_UNVERIFIED')
        for identity in charges:
            if identity in starts:
                reservations.pop(starts[identity]['request_key'],None)
        return sum(reservations.values(),Decimal(0))+sum(charges.values(),Decimal(0))

    def purge_expired(self):
        """Remove owned payloads only; retain intents so expiry cannot replay a POST."""
        _,cache = self.settings.paths()
        with resource_lock(cache/'calls.lock'):
            for path in cache.glob('*.json'):
                if not re.fullmatch(r'[0-9a-f]{64}\.json',path.name):
                    continue
                record = json.loads(checked_path(path,cache).read_text(encoding='utf-8'))
                if record.get('key')!=path.stem or record.get('api') not in ENDPOINTS:
                    continue
                if time.time() > record['expires_at']:
                    for suffix in ('.response','.png'):
                        item = checked_path(cache/(path.stem+suffix), cache, exists=False)
                        if item.exists():item.unlink()
