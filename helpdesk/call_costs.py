"""Observed calls and billing evidence in the existing audit ledger.

No pricing estimates, model confidence, completion decisions or salary rules.
An uninstrumented stage or an unverified charge is unknown, never zero.
"""
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
from pathlib import Path
import re

from .storage import Store, encode, now

KINDS = ('SEARCH', 'DEEPSEEK_MATCH', 'DEEPSEEK_TEACH', 'SCHEDULER', 'OTHER')
START, RESULT, COVERAGE, CHARGE = ('ANSWER_CALL_STARTED', 'ANSWER_CALL_RESULT',
                                  'ANSWER_USAGE_COVERAGE', 'ANSWER_CALL_CHARGE')


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _name(value):
    _require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', value), 'COST_IDENTIFIER_INVALID')
    return value


def _money(value):
    _require(isinstance(value, (str, Decimal)), 'COST_AMOUNT_REQUIRES_EXACT_CNY')
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError('COST_AMOUNT_INVALID') from None
    _require(amount.is_finite() and 0 <= amount <= Decimal('1000000'), 'COST_AMOUNT_INVALID')
    return amount


def _run(store, run_id):
    row = store.one('SELECT * FROM runs WHERE id=?', (run_id,))
    _require(row is not None, 'COST_RUN_NOT_FOUND')
    snapshot = json.loads(row['input_json'])
    owner = store.one('''SELECT t.case_id,c.binding_id FROM turns t
                         JOIN cases c ON c.id=t.case_id WHERE t.id=?''', (row['turn_id'],))
    _require(owner is not None, 'COST_RUN_BINDING_CHANGED')
    binding = {'run_id': row['id'], 'case_id': owner['case_id'], 'binding_id': owner['binding_id'],
               **{k: row[k] for k in ('question_id', 'question_version', 'context_revision', 'session_id')}}
    _require(all(snapshot.get(k) == v for k, v in binding.items()), 'COST_RUN_BINDING_CHANGED')
    return row, binding


def _records(store, run_id, event):
    return [json.loads(row['details']) for row in store.all(
        'SELECT details FROM audit WHERE run_id=? AND event=? ORDER BY id', (run_id, event))]


def _event(store, run_id, event, details):
    _, binding = _run(store, run_id)
    store.execute('INSERT INTO audit(run_id,case_id,question_id,event,details,created_at) VALUES(?,?,?,?,?,?)',
                  (run_id, binding['case_id'], binding['question_id'], event, encode(details), now()))


def start_call(store, run_id, kind, provider, request_key, attempt_key, *, injected=False):
    """Commit immediately before one external call; never authorize its replay."""
    _require(kind in KINDS and type(injected) is bool, 'COST_CALL_KIND_INVALID')
    for value in (provider, request_key, attempt_key):
        _name(value)
    with store.transaction():
        row, binding = _run(store, run_id)
        _require(row['state'] == 'RUNNING', 'COST_RUN_NOT_ACTIVE')
        identity = sha256(encode([run_id, kind, provider, attempt_key]).encode()).hexdigest()
        calls = _records(store, run_id, START)
        _require(not any(c['call_id'] == identity for c in calls), 'COST_CALL_ALREADY_STARTED')
        attempt = 1 + sum(c['kind'] == kind and c['provider'] == provider and c['request_key'] == request_key for c in calls)
        _event(store, run_id, START, {'call_id': identity, 'binding': binding, 'kind': kind, 'provider': provider,
            'request_key': request_key, 'attempt': attempt, 'injected': injected})
    return identity


def finish_call(store, run_id, call_id, status, evidence):
    """Record what was observed. UNKNOWN does not establish acceptance or a fee."""
    _require(status in ('CONFIRMED', 'FAILED', 'UNKNOWN'), 'COST_CALL_STATUS_INVALID')
    _require(isinstance(evidence, str) and 0 < len(evidence) <= 256, 'COST_CALL_EVIDENCE_REQUIRED')
    with store.transaction():
        _require(any(c['call_id'] == call_id for c in _records(store, run_id, START)), 'COST_CALL_NOT_FOUND')
        details = {'call_id': call_id, 'status': status, 'evidence': evidence}
        previous = [r for r in _records(store, run_id, RESULT) if r['call_id'] == call_id]
        _require(not previous or previous == [details], 'COST_CALL_RESULT_CHANGED')
        if not previous:
            _event(store, run_id, RESULT, details)


def close_stage(store, run_id, kind, evidence, *, injected=False):
    """Trusted adapter completed its measurement, including verified cache/reuse."""
    _require(kind in KINDS and type(injected) is bool and isinstance(evidence, str) and 0 < len(evidence) <= 256,
             'COST_COVERAGE_EVIDENCE_REQUIRED')
    with store.transaction():
        calls = [c for c in _records(store, run_id, START) if c['kind'] == kind]
        results = {r['call_id'] for r in _records(store, run_id, RESULT)}
        _require(all(c['call_id'] in results for c in calls), 'COST_CALL_RESULT_PENDING')
        details = {'kind': kind, 'call_ids': sorted(c['call_id'] for c in calls), 'evidence': evidence,
                   'injected': injected}
        previous = [r for r in _records(store, run_id, COVERAGE) if r['kind'] == kind]
        if not previous or previous[-1] != details:
            _event(store, run_id, COVERAGE, details)


def _billing_file(path, approved_root, reviewer):
    root = Path(approved_root).resolve(strict=True)
    path = Path(path).resolve(strict=True)
    _require(root.is_dir() and path.is_file() and path.is_relative_to(root) and path.stat().st_size <= 2_000_000,
             'COST_BILLING_FILE_OUTSIDE_APPROVED_DIRECTORY')
    _require(isinstance(reviewer, str) and reviewer.strip() and len(reviewer) <= 128,
             'COST_BILLING_REVIEWER_REQUIRED')
    return {'method': 'MANUAL_BILLING_VERIFICATION', 'reviewer': reviewer,
            'file': str(path), 'approved_root': str(root), 'sha256': sha256(path.read_bytes()).hexdigest()}


def _proof_valid(proof):
    try:
        root = Path(proof['approved_root']).resolve(strict=True)
        path = Path(proof['file']).resolve(strict=True)
        return (proof['method'] == 'MANUAL_BILLING_VERIFICATION'
                and isinstance(proof['reviewer'], str) and bool(proof['reviewer'].strip())
                and str(root) == proof['approved_root'] and str(path) == proof['file']
                and root.is_dir() and path.is_file() and path.is_relative_to(root)
                and path.stat().st_size <= 2_000_000 and sha256(path.read_bytes()).hexdigest() == proof['sha256'])
    except (OSError, KeyError, ValueError, TypeError):
        return False


def confirm_charge(store, run_id, call_id, amount_cny, receipt_path, approved_root, reviewer, *, item_ref=None):
    """Local human billing verification, never a fabricated provider receipt."""
    amount = _money(amount_cny)
    proof = _billing_file(receipt_path, approved_root, reviewer)
    if item_ref is not None:
        _name(item_ref)  # Human-verified billing line, not a fabricated provider ID.
    with store.transaction():
        _require(any(c['call_id'] == call_id for c in _records(store, run_id, START)), 'COST_CALL_NOT_FOUND')
        details = {'call_id': call_id, 'amount_cny': str(amount), 'proof': proof, 'item_ref': item_ref}
        previous = [c for c in _records(store, run_id, CHARGE) if c['call_id'] == call_id]
        _require(not previous or previous == [details], 'COST_CHARGE_ALREADY_VERIFIED_DIFFERENTLY')
        if not previous:
            for row in store.all('SELECT details FROM audit WHERE event=?', (CHARGE,)):
                charged = json.loads(row[0])
                if charged['proof']['sha256'] == proof['sha256']:
                    _require(item_ref is not None and charged.get('item_ref') is not None
                             and charged['item_ref'] != item_ref, 'COST_BILLING_ITEM_ALREADY_ALLOCATED')
            _event(store, run_id, CHARGE, details)


def confirm_no_calls(store, run_id, kind, receipt_path, approved_root, reviewer):
    """Explicitly reconcile currently uninstrumented scheduler/other costs."""
    _require(kind in ('SCHEDULER', 'OTHER'), 'COST_MANUAL_COVERAGE_SCOPE_INVALID')
    proof = _billing_file(receipt_path, approved_root, reviewer)
    with store.transaction():
        _require(not any(c['kind'] == kind for c in _records(store, run_id, START)), 'COST_CALLS_EXIST')
        details = {'kind': kind, 'no_calls': True, 'proof': proof}
        if details not in _records(store, run_id, 'ANSWER_COST_SCOPE_RECONCILED'):
            _event(store, run_id, 'ANSWER_COST_SCOPE_RECONCILED', details)
        scope = {'kind': kind, 'call_ids': [], 'evidence': 'billing-sha256:' + proof['sha256'], 'injected': False}
        previous = [r for r in _records(store, run_id, COVERAGE) if r['kind'] == kind]
        if not previous or previous[-1] != scope:
            _event(store, run_id, COVERAGE, scope)


def task_cost(store, run_id):
    row, binding = _run(store, run_id)
    calls = _records(store, run_id, START)
    results = {r['call_id']: r for r in _records(store, run_id, RESULT)}
    charges = {c['call_id']: c for c in _records(store, run_id, CHARGE)}
    coverage = {r['kind']: r for r in _records(store, run_id, COVERAGE)}
    reconciled = _records(store, run_id, 'ANSWER_COST_SCOPE_RECONCILED')
    reasons, items, subtotal = [], [], Decimal('0')
    for kind in KINDS:
        ids = sorted(c['call_id'] for c in calls if c['kind'] == kind)
        if kind not in coverage or coverage[kind]['call_ids'] != ids:
            reasons.append('UNMEASURED_STAGE:' + kind)
        elif coverage[kind]['evidence'].startswith('billing-sha256:'):
            digest = coverage[kind]['evidence'].split(':', 1)[1]
            if not any(r['kind'] == kind and r['proof']['sha256'] == digest and _proof_valid(r['proof']) for r in reconciled):
                reasons.append('COVERAGE_PROOF_CHANGED:' + kind)
    for call in calls:
        _require(call['binding'] == binding, 'COST_CALL_BINDING_CHANGED')
        charge = charges.get(call['call_id'])
        amount = None
        if charge:
            proof = charge['proof']
            if _proof_valid(proof):
                amount = _money(charge['amount_cny'])
                subtotal += amount
        if amount is None:
            reasons.append('CHARGE_UNVERIFIED:' + call['call_id'])
        if call['call_id'] not in results:
            reasons.append('CALL_RESULT_UNKNOWN:' + call['call_id'])
        items.append({k: call[k] for k in ('call_id', 'kind', 'provider', 'attempt', 'injected')} |
            {'status': results.get(call['call_id'], {}).get('status', 'UNKNOWN'),
             'amount_cny': str(amount) if amount is not None else None})
    counts = {kind: len([c for c in calls if c['kind'] == kind])
              if kind in coverage or any(c['kind'] == kind for c in calls) else None for kind in KINDS}
    paid = sum(c['amount_cny'] is not None and Decimal(c['amount_cny']) > 0 for c in items)
    return {**binding, 'run_state': row['state'], 'calls': items, 'call_attempts': counts,
        'search_provider_count': len({c['provider'] for c in calls if c['kind'] == 'SEARCH'}) if counts['SEARCH'] is not None else None,
        'confirmed_calls': sum(r['status'] == 'CONFIRMED' for r in results.values()),
        'retry_calls': sum(c['attempt'] > 1 for c in calls),
        'retry_count_scope': 'OBSERVED_ADAPTER_CALLS',
        'paid_calls': None if reasons else paid, 'verified_paid_calls': paid,
        'known_subtotal_cny': str(subtotal), 'total_cny': None if reasons else str(subtotal),
        'cost_status': 'UNVERIFIED' if reasons else 'VERIFIED', 'unverified': reasons,
        'injected_evidence': any(c['injected'] for c in calls) or any(c['injected'] for c in coverage.values())}


def report(store, *, max_average_cny='0.50'):
    """Use only existing confirmed, fully delivered, single-passage units."""
    from .performance import PerformanceLedger
    tasks = [task_cost(store, row['id']) for row in store.all('SELECT id FROM runs ORDER BY rowid')]
    ledger = PerformanceLedger(store)
    units, owners = [], {}
    for row in store.all("SELECT * FROM performance_units WHERE status='CONFIRMED' AND measure_unit='篇'"):
        questions = {r[0] for r in store.all('SELECT question_id FROM performance_links WHERE unit_id=? AND question_id IS NOT NULL', (row['id'],))}
        units.append((dict(row), questions))
        for question in questions:
            owners.setdefault(question, set()).add(row['id'])
    passages = []
    for unit, questions in units:
        related = [t for t in tasks if t['question_id'] in questions]
        reasons = []
        if not ledger.delivery_eligibility(unit)['eligible']:
            reasons.append('DELIVERY_NOT_VERIFIED')
        if unit['confirmed_quantity'] != 1:
            reasons.append('MULTI_PASSAGE_COST_ALLOCATION_UNCONFIRMED')
        if any(len(owners[q]) != 1 for q in questions):
            reasons.append('AMBIGUOUS_COST_OWNERSHIP')
        if not related or any(t['total_cny'] is None for t in related):
            reasons.append('TASK_COST_UNVERIFIED')
        total = None if reasons else sum((Decimal(t['total_cny']) for t in related), Decimal('0'))
        passages.append({'unit_id': unit['id'], 'run_ids': [t['run_id'] for t in related],
            'cost_cny': str(total) if total is not None else None, 'unverified': reasons})
    values = sorted(Decimal(p['cost_cny']) for p in passages if p['cost_cny'] is not None)
    allocated = {r for p in passages for r in p['run_ids']}
    unallocated = [t['run_id'] for t in tasks if t['run_id'] not in allocated]
    complete = bool(passages) and len(values) == len(passages) and not unallocated
    limit = _money(max_average_cny)
    average = sum(values, Decimal('0')) / len(values) if complete else None
    p50 = ((values[(len(values)-1)//2] + values[len(values)//2]) / 2) if complete else None
    p95 = values[math.ceil(len(values) * .95)-1] if complete else None
    return {'tasks': tasks, 'passages': passages, 'passage_count': len(passages), 'unallocated_runs': unallocated,
        'verified_cost_passages': len(values), 'average_cny': str(average) if average is not None else None,
        'p50_cny': str(p50) if p50 is not None else None, 'p95_cny': str(p95) if p95 is not None else None,
        'max_cny': str(values[-1]) if complete else None, 'max_average_cny': str(limit),
        'limit_status': 'UNVERIFIED' if not complete else 'WITHIN_LIMIT' if average <= limit else 'EXCEEDED',
        'high_cost_runs': [t['run_id'] for t in tasks if Decimal(t['known_subtotal_cny']) > limit],
        'injected_evidence': any(t['injected_evidence'] for t in tasks)}


class RunMeter:
    """Light adapter scope; existing SQLite is opened only for a short record."""
    def __init__(self, database, run_id, *, injected=False):
        from .storage import ensure_local_database
        path = Path(ensure_local_database(database)).resolve(strict=True)
        _require(path.is_file(), 'COST_EXISTING_DATABASE_REQUIRED')
        self.database, self.run_id, self.injected = path, run_id, injected

    def _store(self):
        # A missing/moved DB must not silently create a fresh empty ledger.
        _require(self.database.resolve(strict=True) == self.database, 'COST_DATABASE_PATH_CHANGED')
        return Store(self.database)

    def start(self, kind, provider, request_key, attempt_key):
        with_store = self._store()
        try:
            return start_call(with_store, self.run_id, kind, provider, request_key, attempt_key, injected=self.injected)
        finally:
            with_store.close()

    def finish(self, call_id, status, evidence):
        store = self._store()
        try:
            finish_call(store, self.run_id, call_id, status, evidence)
        finally:
            store.close()

    def close(self, kind, evidence):
        store = self._store()
        try:
            close_stage(store, self.run_id, kind, evidence, injected=self.injected)
        finally:
            store.close()
