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


def _ego_capture_proof(store, run_id, call_id, capture):
    row, _ = _run(store, run_id)
    snapshot = json.loads(row['input_json'])
    calls = [c for c in _records(store, run_id, START) if c['call_id'] == call_id]
    _require(len(calls) == 1 and calls[0]['provider'] == 'EGO_LITE_DEEPSEEK'
             and calls[0]['kind'] == 'DEEPSEEK_TEACH'
             and calls[0]['request_key'] == run_id + '-teach', 'COST_RECOVERY_CALL_MISMATCH')
    expected = Path(snapshot['ego_browser']['evidence_dir']) / (run_id + '-teach.answer.json')
    path = Path(capture).resolve(strict=True)
    _require(path == expected.resolve(strict=True), 'COST_RECOVERY_CAPTURE_PATH_CHANGED')
    raw = path.read_bytes()
    digest = sha256(raw).hexdigest()
    value = json.loads(raw)
    state = json.loads(path.with_name(run_id + '-teach.state.json').read_text(encoding='utf-8'))
    _require(snapshot['generation_adapter'] == 'EGO_LITE_DEEPSEEK'
             and value.get('adapter') == 'EGO_LITE' and value.get('complete') is True
             and value.get('space_id') == snapshot['ego_browser']['space_id']
             and value.get('page') == snapshot['ego_browser']['page']
             and isinstance(value.get('text'),str) and bool(value['text'].strip())
             and re.fullmatch(r'https://chat\.deepseek\.com/a/chat/s/[A-Za-z0-9-]+',value.get('session_url',''))
             and state.get('phase') == 'CAPTURED' and state.get('output_sha256') == digest,
             'COST_RECOVERY_CAPTURE_UNCONFIRMED')
    return {'call_id':call_id,'status':'CONFIRMED','evidence':'sha256:'+digest,'capture':str(path)}


def confirm_ego_capture(store, run_id, call_id, capture):
    """Append a verified recovery observation; never rewrite the UNKNOWN fact."""
    with store.transaction():
        details = _ego_capture_proof(store, run_id, call_id, capture)
        previous = [r for r in _records(store, run_id, RESULT) if r['call_id'] == call_id]
        _require(len(previous) == 1 and previous[0]['status'] == 'UNKNOWN', 'COST_RECOVERY_REQUIRES_UNKNOWN')
        recovered = [r for r in _records(store,run_id,'ANSWER_EGO_CALL_RECOVERED') if r['call_id'] == call_id]
        _require(not recovered or recovered == [details], 'COST_RECOVERY_RESULT_CHANGED')
        if not recovered:
            _event(store,run_id,'ANSWER_EGO_CALL_RECOVERED',details)


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
    for recovered in _records(store, run_id, 'ANSWER_EGO_CALL_RECOVERED'):
        try:
            proof = _ego_capture_proof(store,run_id,recovered['call_id'],recovered['capture'])
            _require(proof == recovered, 'COST_RECOVERY_RESULT_CHANGED')
            _require(results.get(recovered['call_id'],{}).get('status') == 'UNKNOWN','COST_RECOVERY_RESULT_CHANGED')
            results[recovered['call_id']] = recovered
        except (OSError,ValueError,KeyError,TypeError):
            reasons.append('RECOVERY_PROOF_CHANGED:'+str(recovered.get('call_id')))
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


def question_cost(store,question_id):
    """Read all versions, followups and revisions of one existing question.

    This is not a performance unit or an allocation for per-passage statistics.
    Do not replace report's existing all-run passage allocation with this view.
    """
    _require(store.one('SELECT id FROM questions WHERE id=?',(question_id,)) is not None,
             'COST_QUESTION_NOT_FOUND')
    rows=store.all('SELECT id,input_json FROM runs WHERE question_id=? ORDER BY rowid',(question_id,))
    tasks=[task_cost(store,row['id']) for row in rows]
    by_id={task['run_id']:task for task in tasks}
    revisions=[];reasons=[]
    for row in rows:
        link=json.loads(row['input_json']).get('ego_revision')
        if link is None:continue
        parent=link.get('parent_run_id') if isinstance(link,dict) else None
        child=by_id[row['id']]
        matching=[] if parent not in by_id or parent==row['id'] else [
            event for event in _records(store,parent,'EGO_REVISION_PREPARED') if event.get('run_id')==row['id']]
        keys=('question_id','question_version','context_revision','case_id','binding_id','session_id')
        if len(matching)!=1 or any(child[k]!=by_id[parent][k] for k in keys):
            reasons.append('REVISION_LINK_UNVERIFIED:'+row['id'])
            continue
        revisions.append({'parent_run_id':parent,'run_id':row['id'],
            'observed_teaching_calls':sum(call['kind']=='DEEPSEEK_TEACH' for call in child['calls'])})
    for task in tasks:
        reasons.extend(task['run_id']+':'+reason for reason in task['unverified'])
    if not tasks:reasons.append('NO_OBSERVED_RUNS')
    known=sum((Decimal(task['known_subtotal_cny']) for task in tasks),Decimal('0'))
    return {'question_id':question_id,'scope':'ALL_RUNS_FOR_QUESTION_NOT_PERFORMANCE_QUANTITY',
        'run_ids':[task['run_id'] for task in tasks],'runs':tasks,
        'observed_call_counts':{kind:sum(call['kind']==kind for task in tasks for call in task['calls']) for kind in KINDS},
        'measurement_complete':{kind:bool(tasks) and all(task['call_attempts'][kind] is not None for task in tasks) for kind in KINDS},
        'within_run_retry_calls':sum(task['retry_calls'] for task in tasks),
        'teaching_revisions':revisions,'teaching_revision_calls':sum(r['observed_teaching_calls'] for r in revisions),
        'known_subtotal_cny':str(known),'total_cny':None if reasons else str(known),
        'cost_status':'UNVERIFIED' if reasons else 'VERIFIED','unverified':reasons,
        'injected_evidence':any(task['injected_evidence'] for task in tasks)}


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

    def confirm_ego_capture(self, call_id, capture):
        store = self._store()
        try:
            confirm_ego_capture(store,self.run_id,call_id,capture)
        finally:
            store.close()

    def close(self, kind, evidence):
        store = self._store()
        try:
            close_stage(store, self.run_id, kind, evidence, injected=self.injected)
        finally:
            store.close()
