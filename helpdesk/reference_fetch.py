"""Bounded, admission-gated reference HTTP reads. No browser or account access."""
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import http.client
from hashlib import sha256
import ipaddress
import json
import math
from pathlib import Path
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from .locking import resource_lock


USER_AGENT = 'AnswerAgentReference/1.0'
MAX_BYTES = 1_000_000


class LookupFailure(ValueError):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)  # Never expose request headers or raw exception text.


def public_ip(value):
    ip = ipaddress.ip_address(value)
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def canonical_url(value):
    if not isinstance(value, str) or len(value) > 3000 or re.search(r'[\x00-\x20\\]', value):
        raise LookupFailure('ACCESS_RESTRICTED', 'INVALID_URL')
    try:
        p = urlsplit(value)
        host = (p.hostname or '').rstrip('.').encode('idna').decode('ascii').lower()
        port = p.port
    except (ValueError, UnicodeError):
        raise LookupFailure('ACCESS_RESTRICTED', 'INVALID_URL') from None
    if (p.scheme not in ('http', 'https') or not host or p.username is not None
            or p.password is not None or port not in (None, 80 if p.scheme == 'http' else 443)):
        raise LookupFailure('ACCESS_RESTRICTED', 'UNSAFE_URL')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '%' in host:
        raise LookupFailure('ACCESS_RESTRICTED', 'NONPUBLIC_HOST')
    try:
        is_ip = ipaddress.ip_address(host)
    except ValueError:
        is_ip = None
    if (is_ip is not None and not public_ip(host)) or (is_ip is None and '.' not in host):
        raise LookupFailure('ACCESS_RESTRICTED', 'NONPUBLIC_HOST')
    decoded = unquote(unquote(p.path))
    if '\\' in decoded or any(part == '..' for part in decoded.split('/')):
        raise LookupFailure('ACCESS_RESTRICTED', 'UNSAFE_PATH')
    if re.search(r'(?:^|&)(?:token|access_token|api_key|password|cookie|session)=', p.query, re.I):
        raise LookupFailure('ACCESS_RESTRICTED', 'URL_CONTAINS_SECRET')
    netloc = '[' + host + ']' if ':' in host else host
    return urlunsplit((p.scheme, netloc, p.path or '/', p.query, ''))


def resolve_public(host, port, timeout):
    # DNS is bounded independently; a late DNS result cannot initiate a request.
    result = queue.Queue(maxsize=1)
    def resolve():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception:
            result.put(None)
    threading.Thread(target=resolve, daemon=True, name='reference-dns').start()
    try:
        records = result.get(timeout=max(.001, timeout))
    except queue.Empty:
        raise LookupFailure('TIMEOUT', 'DNS_TIMEOUT') from None
    if not records:
        raise LookupFailure('FETCH_ERROR', 'DNS_UNAVAILABLE')
    addresses = sorted({item[4][0] for item in records})
    if not addresses or any(not public_ip(address) for address in addresses):
        raise LookupFailure('ACCESS_RESTRICTED', 'DNS_NONPUBLIC_ADDRESS')
    return addresses


@dataclass(frozen=True)
class HTTPResult:
    url: str
    status: int
    headers: dict
    body: bytes


class PinnedHTTP:
    """Connect to a checked IP, retaining hostname for Host and TLS verification.

    Does not use environment proxies, cookies, redirects, credentials or browser
    subrequests. Each redirect is a separate admitted and DNS-pinned request.
    """
    def __init__(self, *, resolver=resolve_public):
        self.resolver = resolver

    def request(self, url, *, timeout, headers=None):
        url = canonical_url(url)
        parsed = urlsplit(url)
        started = time.monotonic()
        addresses = self.resolver(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), timeout)
        if any(not public_ip(ip) for ip in addresses):
            raise LookupFailure('ACCESS_RESTRICTED', 'DNS_NONPUBLIC_ADDRESS')
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise LookupFailure('TIMEOUT', 'DNS_TIMEOUT')
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80), timeout=remaining)
        response, net_socket, watchdog = None, None, None
        expired = threading.Event()
        def left():
            remaining = timeout - (time.monotonic() - started)
            if expired.is_set() or remaining <= 0:
                raise LookupFailure('TIMEOUT', 'HTTP_DEADLINE_EXHAUSTED')
            return remaining
        def expire():
            # A socket timeout alone does not stop a server dripping headers or
            # chunk framing. This deadline interrupts the underlying connection.
            expired.set()
            if net_socket is not None:
                try:
                    net_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        try:
            net_socket = socket.create_connection((addresses[0], connection.port), timeout=left())
            if parsed.scheme == 'https':
                net_socket = ssl.create_default_context().wrap_socket(net_socket, server_hostname=parsed.hostname,
                                                                    do_handshake_on_connect=False)
            watchdog = threading.Timer(left(), expire)
            watchdog.daemon = True
            watchdog.start()
            net_socket.settimeout(left())
            if parsed.scheme == 'https':
                net_socket.do_handshake()
            connection.sock = net_socket
            request_headers = {'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity', 'Accept': 'text/html,text/plain'}
            request_headers.update(headers or {})
            connection.request('GET', urlunsplit(('', '', parsed.path or '/', parsed.query, '')), headers=request_headers)
            net_socket.settimeout(left())
            response = connection.getresponse()
            result_headers = {k.lower(): v for k, v in response.getheaders()}
            if int(result_headers.get('content-length', '0')) > MAX_BYTES:
                raise LookupFailure('FETCH_ERROR', 'RESPONSE_TOO_LARGE')
            body = bytearray()
            while True:
                # getresponse() can clear connection.sock for Connection: close.
                net_socket.settimeout(left())
                block = response.read1(min(16384, MAX_BYTES + 1 - len(body)))
                left()
                if not block:
                    break
                body.extend(block)
                if len(body) > MAX_BYTES:
                    raise LookupFailure('FETCH_ERROR', 'RESPONSE_TOO_LARGE')
            if 'content-length' in result_headers and len(body) != int(result_headers['content-length']):
                raise LookupFailure('FETCH_ERROR', 'TRUNCATED_RESPONSE')
            if result_headers.get('content-encoding', 'identity').lower() != 'identity':
                raise LookupFailure('FETCH_ERROR', 'UNSUPPORTED_CONTENT_ENCODING')
            return HTTPResult(url, response.status, result_headers, bytes(body))
        except LookupFailure:
            raise
        except (TimeoutError, socket.timeout):
            raise LookupFailure('TIMEOUT', 'HTTP_TIMEOUT') from None
        except (OSError, ValueError, http.client.HTTPException):
            if expired.is_set() or time.monotonic() - started >= timeout:
                raise LookupFailure('TIMEOUT', 'HTTP_DEADLINE_EXHAUSTED') from None
            raise LookupFailure('FETCH_ERROR', 'HTTP_FAILED') from None
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if response is not None:
                response.close()
            connection.close()
            if net_socket is not None:
                net_socket.close()


@dataclass(frozen=True)
class SourceAdmission:
    domain: str
    aliases: tuple = ()
    automatic_enabled: bool = False
    terms_status: str = 'UNVERIFIED'
    terms_url: str = ''
    robots_status: str = 'UNVERIFIED'
    checked_at: str = ''
    allowed_paths: tuple = ()
    purpose: str = 'internal_question_verification'
    retention_seconds: int = 0
    request_interval_seconds: float = 10
    restrictions: str = '不对外转发；未获得准入前不保存第三方正文'
    fallback: str = '人工提供获准材料或请学生补图'
    business_record_storage_allowed: bool = False

    def require(self, url, *, metadata=False):
        url = canonical_url(url)
        if urlsplit(url).hostname not in (self.domain, *self.aliases):
            raise LookupFailure('ACCESS_RESTRICTED', 'SOURCE_HOST_MISMATCH')
        if (self.automatic_enabled is not True or self.terms_status != 'APPROVED'
                or self.robots_status != 'REVIEWED' or not self.terms_url or not self.checked_at
                or self.purpose != 'internal_question_verification'):
            raise LookupFailure('ACCESS_RESTRICTED', 'SOURCE_ADMISSION_REQUIRED')
        try:
            checked = datetime.fromisoformat(self.checked_at)
            if checked.utcoffset() is None or checked > datetime.now(timezone.utc):
                raise ValueError()
            canonical_url(self.terms_url)
        except (ValueError, LookupFailure):
            raise LookupFailure('ACCESS_RESTRICTED', 'SOURCE_REVIEW_INVALID') from None
        path = unquote(unquote(urlsplit(url).path))
        if not metadata and not any(path == prefix or path.startswith(prefix.rstrip('/') + '/') for prefix in self.allowed_paths):
            raise LookupFailure('ACCESS_RESTRICTED', 'SOURCE_PATH_NOT_ADMITTED')
        return url


def restriction_page(text):
    markers = r'captcha|验证码|安全验证|访问验证|会员专享|付费|登录|sign\s*in|log\s*in|access\s*denied|checking your browser|页面不存在|访问出错|服务不可用|^(?:error|not found|service unavailable)\b|\b(?:404|500|403)\b'
    titles = re.findall(r'<(?:title|h1)\b[^>]*>(.*?)</(?:title|h1)>', text, re.I | re.S)
    if any(re.search(markers, re.sub('<[^>]+>', ' ', title), re.I) for title in titles):
        return True
    return bool(re.search(r'<input\b[^>]*type\s*=\s*[\"\x27]?password|(?:class|id)\s*=\s*[\"\x27][^\"\x27]*(?:captcha|paywall)', text, re.I))


def decode_text(result):
    content_type = result.headers.get('content-type', '').lower()
    if not any(kind in content_type for kind in ('text/html', 'text/plain', 'application/xhtml+xml')):
        raise LookupFailure('UNSUPPORTED_DOCUMENT', 'DOCUMENT_READER_REQUIRED')
    match = re.search(r'charset\s*=\s*([\w-]+)', content_type)
    encoding = match[1] if match else 'utf-8'
    try:
        return result.body.decode(encoding)
    except (UnicodeError, LookupError):
        raise LookupFailure('FETCH_ERROR', 'PAGE_ENCODING_UNCONFIRMED') from None


class Budget:
    def __init__(self, seconds=120, *, clock=time.monotonic, cancelled=None):
        self.clock, self.deadline = clock, clock() + seconds
        self.cancelled = cancelled or (lambda: False)

    def remaining(self):
        if self.cancelled():
            raise LookupFailure('CANCELLED', 'LOOKUP_CANCELLED')
        value = self.deadline - self.clock()
        if value <= 0:
            raise LookupFailure('TIMEOUT', 'LOOKUP_BUDGET_EXHAUSTED')
        return value


class ReferenceFetcher:
    # Serial HTTP requests are below the requested global ceiling of two and
    # per-domain ceiling of one. There is no browser escalation on rejection.
    _network_lock = threading.Lock()
    _last_request = {}
    _blocked_until = {}

    def __init__(self, *, transport=None, timeout=15, interval=10, retries=1, clock=time.monotonic,
                 sleeper=time.sleep, coordination_root=None, wall_clock=time.time):
        self.transport = transport or PinnedHTTP()
        self.timeout, self.interval, self.retries = timeout, interval, retries
        self.clock, self.sleeper = clock, sleeper
        self.wall_clock = wall_clock
        # CLI and workbench share one inexpensive file lock and host timestamps.
        # Injected offline transports remain independent unless explicitly asked.
        self.coordination_root = (Path(coordination_root) if coordination_root else
            Path(__file__).resolve().parents[1] / 'data/private/reference-lookup/network' if transport is None else None)

    @contextmanager
    def _shared_slot(self, budget):
        if self.coordination_root is None:
            yield None
            return
        root = self.coordination_root
        for path in (root, *root.parents, root / 'state.json', root / 'state.tmp', root / 'network.lock'):
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise LookupFailure('ACCESS_RESTRICTED', 'NETWORK_DIRECTORY_REDIRECTED')
        try:
            with resource_lock(root / 'network.lock', timeout=min(self.timeout, budget.remaining())):
                try:
                    state = json.loads((root / 'state.json').read_text(encoding='utf-8'))
                    if (state.get('version') != 1 or not isinstance(state.get('hosts'), dict)
                            or any(not isinstance(value, dict) or set(value) != {'last', 'blocked_until'}
                                   or any(type(t) not in (int, float) or not math.isfinite(t) for t in value.values())
                                   for value in state['hosts'].values())):
                        raise ValueError()
                except FileNotFoundError:
                    state = {'version': 1, 'hosts': {}}
                except (ValueError, TypeError):
                    raise LookupFailure('INTERNAL_ERROR', 'NETWORK_STATE_INVALID') from None
                yield state
        except TimeoutError:
            raise LookupFailure('TIMEOUT', 'NETWORK_BUSY') from None

    def _save_shared(self, state):
        # Called only with the shared network lock held; no identities or headers.
        if state is not None:
            root = self.coordination_root
            temporary = root / 'state.tmp'
            temporary.write_text(json.dumps(state), encoding='utf-8')
            temporary.replace(root / 'state.json')

    def _wait(self, seconds, budget):
        if seconds >= budget.remaining():
            raise LookupFailure('TIMEOUT', 'RATE_WAIT_EXCEEDS_BUDGET')
        while seconds > 0:
            budget.remaining()
            step = min(seconds, .25)
            self.sleeper(step)
            seconds -= step

    def _request(self, url, budget, *, headers=None, interval=None):
        host = urlsplit(canonical_url(url)).hostname
        if not self._network_lock.acquire(timeout=min(self.timeout, budget.remaining())):
            raise LookupFailure('TIMEOUT', 'NETWORK_BUSY')
        try:
            with self._shared_slot(budget) as state:
                interval = self.interval if interval is None else interval
                wait = max(self._blocked_until.get(host, 0), self._last_request.get(host, -1e10) + interval) - self.clock()
                if state is not None:
                    previous = state['hosts'].get(host, {'last': -1e10, 'blocked_until': 0})
                    wait = max(wait, max(previous['blocked_until'], previous['last'] + interval) - self.wall_clock())
                if wait > 0:
                    self._wait(wait, budget)
                self._last_request[host] = self.clock()
                if state is not None:
                    state['hosts'][host] = {'last': self.wall_clock(), 'blocked_until': previous['blocked_until']}
                    self._save_shared(state)
                result = self.transport.request(url, timeout=min(self.timeout, budget.remaining()), headers=headers)
                if result.status == 429:
                    raw = result.headers.get('retry-after', '')
                    try:
                        delay = float(raw)
                        if not math.isfinite(delay):
                            raise ValueError()
                        delay = max(0, delay)
                    except ValueError:
                        try:
                            delay = max(0, (parsedate_to_datetime(raw) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            delay = 60
                    delay = max(self.interval, delay)
                    self._blocked_until[host] = self.clock() + delay
                    if state is not None:
                        state['hosts'][host]['blocked_until'] = self.wall_clock() + delay
                        self._save_shared(state)
                    raise LookupFailure('RATE_LIMITED', 'HTTP_429_RETRY_AFTER')
                if result.status in (401, 402, 403):
                    raise LookupFailure('ACCESS_RESTRICTED', 'HTTP_' + str(result.status))
                return result
        finally:
            self._network_lock.release()

    def search_request(self, url, budget, *, headers):
        # Fixed provider only: no secret-bearing redirects or arbitrary endpoints.
        p = urlsplit(canonical_url(url))
        if p.scheme != 'https' or p.hostname != 'api.search.brave.com' or p.path != '/res/v1/web/search':
            raise LookupFailure('ACCESS_RESTRICTED', 'UNAPPROVED_SEARCH_ENDPOINT')
        result = self._request(url, budget, headers=headers, interval=max(1, self.interval))
        if result.status != 200:
            raise LookupFailure('PROVIDER_UNAVAILABLE', 'SEARCH_HTTP_' + str(result.status))
        return result

    def page(self, url, admission, budget):
        url = admission.require(url)
        for redirect in range(4):
            budget.remaining()
            p = urlsplit(url)
            robots_url = urlunsplit((p.scheme, p.netloc, '/robots.txt', '', ''))
            admission.require(robots_url, metadata=True)
            robots = self._request(robots_url, budget, interval=max(self.interval, admission.request_interval_seconds))
            if robots.status != 200:
                raise LookupFailure('ACCESS_RESTRICTED', 'ROBOTS_UNAVAILABLE')
            robot_text = decode_text(robots)
            if restriction_page(robot_text) or '<html' in robot_text.lower():
                raise LookupFailure('ACCESS_RESTRICTED', 'ROBOTS_NOT_TEXT')
            parser = RobotFileParser(robots_url)
            parser.parse(robot_text.splitlines())
            if not parser.can_fetch(USER_AGENT, url):
                raise LookupFailure('ACCESS_RESTRICTED', 'ROBOTS_DENIED')
            delay = max(self.interval, admission.request_interval_seconds, parser.crawl_delay(USER_AGENT) or 0)
            for attempt in range(self.retries + 1):
                try:
                    result = self._request(url, budget, interval=delay)
                    if result.status in (502, 503, 504):
                        raise LookupFailure('FETCH_ERROR', 'TEMPORARY_HTTP_FAILURE')
                    break
                except LookupFailure as exc:
                    if exc.status not in ('FETCH_ERROR', 'TIMEOUT') or attempt == self.retries:
                        raise
            if result.status in (301, 302, 303, 307, 308):
                if redirect == 3:
                    raise LookupFailure('ACCESS_RESTRICTED', 'REDIRECT_LIMIT')
                url = admission.require(urljoin(url, result.headers.get('location', '')))
                continue  # Recheck robots and DNS at the redirected path/host.
            if result.status != 200:
                raise LookupFailure('FETCH_ERROR', 'PAGE_HTTP_' + str(result.status))
            if admission.require(result.url) != url:
                raise LookupFailure('ACCESS_RESTRICTED', 'TRANSPORT_FINAL_URL_MISMATCH')
            text = decode_text(result)
            if restriction_page(text):
                raise LookupFailure('ACCESS_RESTRICTED', 'LOGIN_CAPTCHA_OR_PAYWALL')
            proof = dict(result.headers)
            proof['x-reference-robots-sha256'] = sha256(robots.body).hexdigest()
            proof['x-reference-robots-checked-at'] = datetime.now(timezone.utc).isoformat()
            return HTTPResult(result.url, result.status, proof, result.body), text
        raise LookupFailure('FETCH_ERROR', 'REDIRECT_FAILED')
