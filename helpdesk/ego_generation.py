"""Natural-language ego teaching results into the existing Workflow/Outbox.

Requires a frozen DIRECT_ORIGINAL run and its original source clarity review.
One native request creates/binds its chat if needed. No sending, JSON uploads,
or independent completion ledger.
"""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import re

from .call_costs import RunMeter
from .ego_browser import EgoDeepSeek
from .mcp_generation import input_fingerprint, delivery_context
from .session_isolation import claim_deepseek_chat
from .storage import Store, encode, now
from .teaching_bundle import verify_frozen_teaching


def validate_direct_source(snapshot, *, allow_completed=False):
    """One existing source review and local version checks; no webpage MATCH."""
    if snapshot.get('ego_policy') != 'DIRECT_ORIGINAL':
        raise ValueError('EGO_DIRECT_POLICY_REQUIRED')
    from .automatic_preparation import _source_review
    with closing(Store(Path(snapshot['session_store_path']).resolve(strict=True))) as store:
        task = store.one('SELECT id FROM operator_tasks WHERE run_id=?',(snapshot['run_id'],)) if store.one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_tasks'") else None
        if not task:
            raise ValueError('EGO_REVIEWED_SOURCE_REQUIRED')
        _, frozen, _ = _source_review(store, task[0], allow_ego=True, allow_completed=allow_completed)
        if frozen != snapshot:
            raise ValueError('EGO_SOURCE_SNAPSHOT_CHANGED')


def selected_option(snapshot, text, inherited_option_id=None):
    # Read explicit answer claims only; do not solve or infer from explanations.
    affirmative = re.sub(r'(?:不(?:能|要|应(?:该)?|是|会)?|未|别|避免)选(?:择)?\s*[A-G]\b', '', text)
    claims = re.findall(r"(?<![不未])选(?:择)?\s*([A-G])\b|答案(?:是|为)?\s*[:：]?\s*([A-G])\b", affirmative)
    letters = {a or b for a, b in claims}
    # Natural replies can explicitly conclude “第2题落在C。” without “选/答案”.
    # Require the question and a terminal separator, not discussion of C/D.
    letters.update(re.findall(r'第\s*\d+\s*题\s*(?:最终)?落在\s*([A-G])(?=[。.!！；;，,\s]|$)', affirmative))
    matches = [o['id'] for o in snapshot['student_question']['options'] if o['label'] in letters]
    if not letters and inherited_option_id in {o['id'] for o in snapshot['student_question']['options']}:
        matches = [inherited_option_id]
    elif len(letters) != 1 or len(matches) != 1:
        raise ValueError('EGO_ANSWER_OPTION_REQUIRES_REVIEW')
    if inherited_option_id is not None and matches[0] != inherited_option_id:
        raise ValueError('EGO_FOLLOWUP_SELECTION_CHANGED_REVIEW')
    numbers = re.findall(r'第\s*(\d+)\s*题', text)
    if any(n != str(snapshot['student_question']['number']) for n in numbers):
        raise ValueError('EGO_ANSWER_QUESTION_REQUIRES_REVIEW')
    return matches[0]


def actual_delivery_files(snapshot):
    """Use only registered images inside the existing local delivery directory."""
    rows = snapshot.get('sent_history', [])
    if not any(row.get('attachments') for row in rows):
        return []
    try:
        configured_root = Path(snapshot['session_store_path']).resolve(strict=True).parent / 'delivery-attachments'
        root = configured_root.resolve(strict=True)
        # A replaced directory must not expand the already approved local scope.
        if root != configured_root or not root.is_dir():
            raise ValueError('EGO_DELIVERY_ATTACHMENT_CONTEXT_REQUIRES_REVIEW')
        files = []
        for row in rows:
            for item in row.get('attachments', []):
                path = Path(item['path']).resolve(strict=True)
                if (row.get('delivery_method') != 'MANUAL_ATTESTATION'
                        or not path.is_relative_to(root) or not path.is_file()
                        or path.name != item.get('name')
                        or path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.gif')
                        or type(item.get('bytes')) is not int
                        or not 0 < item['bytes'] <= 20_000_000
                        or path.stat().st_size != item['bytes']
                        or sha256(path.read_bytes()).hexdigest() != item.get('sha256')):
                    raise ValueError('EGO_DELIVERY_ATTACHMENT_CONTEXT_REQUIRES_REVIEW')
                if path not in files:
                    files.append(path)
        return files
    except (OSError, KeyError, TypeError):
        raise ValueError('EGO_DELIVERY_ATTACHMENT_CONTEXT_REQUIRES_REVIEW') from None


class EgoDeepSeekGenerator:
    identity = 'EGO_LITE_DEEPSEEK'
    simulated = False

    def __init__(self, space_id, evidence_dir, *, page='p1', capture_only=False):
        if type(space_id) is not int or space_id < 1 or not re.fullmatch(r'p[1-9][0-9]*', page):
            raise ValueError('An existing ego space and page are required')
        self.space_id, self.page = space_id, page
        self.directory = Path(evidence_dir).resolve()
        self.capture_only = capture_only

    def browser_binding(self):
        return {'space_id':self.space_id,'page':self.page,'evidence_dir':str(self.directory)}

    def _current(self, snapshot):
        from .service import Helpdesk
        if snapshot.get('ego_browser') != self.browser_binding():
            raise ValueError('EGO_FROZEN_BROWSER_CHANGED')
        with closing(Store(Path(snapshot['session_store_path']).resolve(strict=True))) as store:
            run = store.one('SELECT state,input_json FROM runs WHERE id=?', (snapshot['run_id'],))
            stop = store.one("SELECT value FROM settings WHERE key='stop_requested'")
            if stop and stop[0] == 'true':
                raise ValueError('STOPPED')
            if (not run or run['state'] != 'RUNNING' or json.loads(run['input_json']) != snapshot
                    or snapshot.get('generation_adapter') != self.identity or snapshot.get('simulated') is not False):
                raise ValueError('EGO_FROZEN_RUN_CHANGED')
            try:
                current = Helpdesk(store).context(snapshot['turn_id'])
            except ValueError:
                raise ValueError('EGO_CURRENT_QUESTION_CHANGED') from None
            if input_fingerprint(current) != input_fingerprint(snapshot):
                raise ValueError('EGO_CURRENT_QUESTION_CHANGED')
            chat = store.one('SELECT session_url FROM deepseek_chats WHERE session_id=?', (snapshot['session_id'],))
            if not chat:
                if snapshot.get('ego_request_url') != 'https://chat.deepseek.com/':
                    raise ValueError('EGO_BOUND_SESSION_REQUIRED')
                return 'https://chat.deepseek.com/'
            return chat['session_url']

    def generate(self, snapshot):
        manifest = verify_frozen_teaching(snapshot)
        url = self._current(snapshot)
        validate_direct_source(snapshot)
        if manifest.get('format_version') == 3:
            from .lesson_checks import validate_source_check
            validate_source_check(snapshot, manifest=manifest)
        new_session = snapshot['ego_request_url'] == 'https://chat.deepseek.com/'
        claim_deepseek_chat(snapshot, None if url == 'https://chat.deepseek.com/' else url,
                            reserve=url != 'https://chat.deepseek.com/')
        skills = snapshot['teaching_skills']
        origin = None
        if snapshot.get('ego_material_reuse'):
            with closing(Store(snapshot['session_store_path'])) as store:
                origin = material_origin(store, snapshot)
        revision = None
        if snapshot.get('ego_revision'):
            from .ego_revision import revision_source
            with closing(Store(snapshot['session_store_path'])) as store:
                revision = revision_source(store,snapshot)
        files = [] if origin or revision else [Path(item['path']).resolve(strict=True) for item in skills]
        for item in snapshot['attachments']:
            path = Path(item['path']).resolve(strict=True)
            if sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('EGO_STUDENT_ATTACHMENT_CHANGED')
            if not revision and (not origin or item['sha256'] not in {a['sha256'] for a in origin['attachments']}):
                files.append(path)
        words = snapshot.get('student_words') or ''
        prompt = words if words.strip() else '第' + str(snapshot['student_question']['number']) + '题'
        if revision:
            prompt=snapshot['ego_revision']['prompt']
        if not snapshot['attachments'] and not origin and not revision:
            q = snapshot['student_question']
            prompt = '\n'.join([snapshot.get('student_material') or '', q['verified_stem'],
                *[o['label'] + '. ' + o['verified_text'] for o in q['options']], prompt])
        delivered = delivery_context(snapshot)
        if delivered:
            for path in actual_delivery_files(snapshot):
                if path not in files:
                    files.append(path)
            prompt += '\n此前实际回复：\n' + '\n'.join(row['body'] for row in delivered['sent_history'])
        request_id = snapshot['run_id'] + '-teach'
        browser = EgoDeepSeek(self.space_id, self.directory, page=self.page, allowed_files=files)
        meter = RunMeter(snapshot['session_store_path'], snapshot['run_id'])
        with closing(Store(snapshot['session_store_path'])) as store:
            calls = [json.loads(row[0]) for row in store.all(
                "SELECT details FROM audit WHERE run_id=? AND event='ANSWER_CALL_STARTED'", (snapshot['run_id'],))]
        calls = [c for c in calls if c['kind'] == 'DEEPSEEK_TEACH' and c['provider'] == self.identity]
        if len(calls) > 1:
            raise ValueError('EGO_CALL_HISTORY_REQUIRES_REVIEW')
        call_id = calls[0]['call_id'] if calls else meter.start('DEEPSEEK_TEACH', self.identity, request_id, request_id)
        try:
            captured = browser.submit(request_id, snapshot['ego_request_url'], prompt, attachments=files,
                                      capture_only=self.capture_only, **({'new_session':True} if new_session else {}))
        except Exception:
            meter.finish(call_id, 'UNKNOWN', 'EGO_CAPTURE_NOT_CONFIRMED')
            raise
        proof = self.directory / (request_id + '.answer.json')
        evidence = 'sha256:' + sha256(proof.read_bytes()).hexdigest()
        with closing(Store(snapshot['session_store_path'])) as store:
            prior = [json.loads(r[0]) for r in store.all(
                "SELECT details FROM audit WHERE run_id=? AND event='ANSWER_CALL_RESULT'", (snapshot['run_id'],))]
        if any(r['call_id'] == call_id and r['status'] == 'UNKNOWN' for r in prior):
            meter.confirm_ego_capture(call_id,proof)
        else:
            meter.finish(call_id, 'CONFIRMED', evidence)
        meter.close('DEEPSEEK_TEACH', evidence)
        # The approved direct policy makes no search or MATCH model calls.
        meter.close('DEEPSEEK_MATCH', 'direct-original-policy')
        meter.close('SEARCH', 'direct-original-policy')
        # Recheck while the browser was generating; the old answer must never pass.
        verify_frozen_teaching(snapshot)
        current_url = self._current(snapshot)
        validate_direct_source(snapshot)
        if origin:
            with closing(Store(snapshot['session_store_path'])) as store:
                material_origin(store,snapshot)
        resolved_url = captured.get('session_url')
        if new_session and current_url == 'https://chat.deepseek.com/':
            if not isinstance(resolved_url,str) or not re.fullmatch(r'https://chat\.deepseek\.com/a/chat/s/[A-Za-z0-9-]+',resolved_url):
                raise ValueError('EGO_CREATED_SESSION_UNCONFIRMED')
        elif resolved_url != current_url:
            raise ValueError('EGO_CAPTURE_SESSION_CHANGED')
        url = resolved_url
        if (captured.get('adapter') != 'EGO_LITE' or captured.get('complete') is not True
                or captured.get('session_url') != url or not isinstance(captured.get('text'), str)
                or captured.get('upload_manifest') != [
                    {'path':str(p),'name':p.name,'sha256':sha256(p.read_bytes()).hexdigest()} for p in files]):
            raise ValueError('EGO_CAPTURE_BINDING_CHANGED')
        claim_deepseek_chat(snapshot, url)
        text = captured['text']
        validation_error=None
        try:
            option = selected_option(snapshot, text, snapshot['ego_material_reuse']['correct_option_id'] if origin else None)
        except ValueError as exc:
            if str(exc) not in ('EGO_ANSWER_OPTION_REQUIRES_REVIEW','EGO_ANSWER_QUESTION_REQUIRES_REVIEW',
                                'EGO_FOLLOWUP_SELECTION_CHANGED_REVIEW'):
                raise
            # The webpage completed, but its answer is not deliverable. Preserve
            # the exact text through Workflow.finish instead of leaving RUNNING.
            option=''
            validation_error=str(exc)
        details = {'input_fingerprint':input_fingerprint(snapshot), 'session_url':url,
                   'evidence_path':str(proof), 'sha256':evidence[7:], 'correct_option_id':option,
                   **({'reused_materials':snapshot['ego_material_reuse']} if origin else {}),
                   **({'validation_error':validation_error} if validation_error else {})}
        event='EGO_TEACH_CAPTURE_REJECTED' if validation_error else 'EGO_TEACH_CAPTURED'
        with closing(Store(snapshot['session_store_path'])) as store, store.transaction():
            previous = store.all("SELECT details FROM audit WHERE run_id=? AND event=?", (snapshot['run_id'],event))
            if previous and [json.loads(r[0]) for r in previous] != [details]:
                raise ValueError('EGO_CAPTURE_HISTORY_CHANGED')
            if not previous:
                store.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
                    (snapshot['run_id'], event, encode(details), now()))
        return dict(adapter=self.identity, simulated=False, run_id=snapshot['run_id'],
            session_id=snapshot['session_id'], complete=True, uploads_confirmed=True,
            uploaded_teaching_hashes={s['path']:s['sha256'] for s in skills},
            web_session_evidence=str(proof), correct_option_id=option, text=text,
            **({'error':validation_error} if validation_error else {}),
            **({'reused_teaching_hashes':{s['path']:s['sha256'] for s in skills},
                'material_origin_run_id':origin['run_id']} if origin else {}))


def _capture_record(store, snapshot, body, option_id=None):
    """Read immutable capture evidence; this never approves a current source."""
    verify_frozen_teaching(snapshot)
    actual_delivery_files(snapshot)
    if snapshot.get('ego_revision'):
        from .ego_revision import revision_source
        revision_source(store,snapshot)
    rows = store.all("SELECT details FROM audit WHERE run_id=? AND event='EGO_TEACH_CAPTURED'", (snapshot['run_id'],))
    if len(rows) != 1:
        raise ValueError('EGO_CAPTURE_EVIDENCE_MISSING')
    record = json.loads(rows[0][0])
    chat = store.one('SELECT session_url FROM deepseek_chats WHERE session_id=?', (snapshot['session_id'],))
    raw = Path(record['evidence_path']).read_bytes()
    captured = json.loads(raw)
    if (sha256(raw).hexdigest() != record['sha256'] or not chat
            or record['session_url'] != chat[0] or captured.get('session_url') != chat[0]
            or record['input_fingerprint'] != input_fingerprint(snapshot)
            or captured.get('adapter') != 'EGO_LITE' or captured.get('complete') is not True
            or captured.get('text') != body
            or record.get('reused_materials') != snapshot.get('ego_material_reuse')
            or selected_option(snapshot,body,(snapshot.get('ego_material_reuse') or {}).get('correct_option_id')) != record['correct_option_id']
            or (option_id is not None and option_id != record['correct_option_id'])):
        raise ValueError('EGO_CAPTURE_EVIDENCE_CHANGED')
    return record


def validate_ego_capture(store, snapshot, body, option_id=None):
    """Recheck original ego evidence at Workflow finish and before delivery."""
    record = _capture_record(store, snapshot, body, option_id)
    validate_direct_source(snapshot, allow_completed=True)
    if snapshot.get('ego_material_reuse'):
        material_origin(store, snapshot)
    return record


def material_origin(store, snapshot):
    """Validate one frozen original upload, never an unsent-draft delivery claim."""
    link = snapshot['ego_material_reuse']
    if snapshot.get('intent') != 'FOLLOWUP' or not isinstance(link,dict):
        raise ValueError('EGO_REUSE_REQUIRES_ORDINARY_FOLLOWUP')
    row = store.one("SELECT input_json FROM runs WHERE id=? AND state='GENERATED'", (link.get('origin_run_id'),))
    if not row:
        raise ValueError('EGO_ORIGINAL_MATERIALS_UNCONFIRMED')
    origin = json.loads(row[0])
    keys = ('case_id','binding_id','question_id','question_version','session_id',
            'student_question','student_material','teaching_skills','generation_adapter','ego_policy')
    if (origin.get('ego_material_reuse') or origin.get('run_id') == snapshot['run_id']
            or any(encode(origin.get(k)) != encode(snapshot.get(k)) for k in keys)
            or origin.get('references',[]) != snapshot.get('references',[])):
        raise ValueError('EGO_REUSE_QUESTION_OR_TEACHING_CHANGED')
    answer = store.one('SELECT text FROM answers a JOIN answer_evidence e ON e.answer_id=a.id WHERE e.run_id=?',
                       (origin['run_id'],))
    if not answer:
        raise ValueError('EGO_ORIGINAL_MATERIALS_UNCONFIRMED')
    proof = _capture_record(store,origin,answer[0])
    if (proof['sha256'] != link.get('evidence_sha256') or proof['session_url'] != snapshot['ego_request_url']
            or proof['correct_option_id'] != link.get('correct_option_id')):
        raise ValueError('EGO_REUSE_UPLOAD_EVIDENCE_CHANGED')
    delivered=delivery_context(snapshot)
    if delivered:
        actual_delivery_files(snapshot)
        for item in delivered['sent_history']:
            if item['question_version'] == snapshot['question_version']:
                # A teacher correction must not inherit a contradictory old selection.
                selected_option(snapshot,item['body'],link['correct_option_id'])
    return origin


def freeze_material_reuse(store, snapshot):
    """Called inside the original Workflow.start transaction; no webpage action."""
    if snapshot.get('intent') != 'FOLLOWUP' or snapshot['ego_request_url'] == 'https://chat.deepseek.com/':
        return
    rows = store.all("SELECT id,input_json FROM runs WHERE session_id=? AND question_version=? AND state='GENERATED' ORDER BY rowid",
                     (snapshot['session_id'],snapshot['question_version']))
    for row in rows:
        origin=json.loads(row['input_json'])
        if origin.get('ego_material_reuse'):
            continue
        proofs=store.all("SELECT details FROM audit WHERE run_id=? AND event='EGO_TEACH_CAPTURED'", (row['id'],))
        if len(proofs) != 1:
            continue
        snapshot['ego_material_reuse']={'origin_run_id':row['id'],
            'evidence_sha256':json.loads(proofs[0][0])['sha256'],
            'correct_option_id':json.loads(proofs[0][0])['correct_option_id']}
        material_origin(store,snapshot)
        return
    raise ValueError('EGO_FOLLOWUP_MATERIALS_UNVERIFIED')


def run_existing(store, run_id, space_id, evidence_dir, *, page='p1', capture_only=False):
    """Resume one frozen ego run into the original Workflow, never dispatch."""
    from .locking import resource_lock
    from .workflow import Workflow
    with resource_lock(str(Path(store.path).resolve())+'.ego-run-'+sha256(run_id.encode()).hexdigest()+'.lock'):
        row=store.one('SELECT * FROM runs WHERE id=?',(run_id,))
        if row is None:
            raise ValueError('EGO_FROZEN_RUN_REQUIRED')
        if row['state'] != 'RUNNING':
            return {'state':row['state'],'resubmitted':False}
        generator=EgoDeepSeekGenerator(space_id,evidence_dir,page=page,capture_only=capture_only)
        snapshot=json.loads(row['input_json'])
        verify_frozen_teaching(snapshot)
        workflow=Workflow(store,generation_adapter=generator,
                          teaching_manifest=snapshot['teaching_skills'][0]['manifest_path'])
        workflow._require_confirmed_ack(row['turn_id'])
        result=generator.generate(snapshot)
        return workflow.finish(run_id,result)
