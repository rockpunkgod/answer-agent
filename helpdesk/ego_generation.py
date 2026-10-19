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


def selected_option(snapshot, text):
    # Read explicit answer claims only; do not solve or infer from explanations.
    affirmative = re.sub(r'(?:不(?:能|要|应(?:该)?|是|会)?|未|别|避免)选(?:择)?\s*[A-D]\b', '', text)
    claims = re.findall(r"(?<![不未])选(?:择)?\s*([A-D])\b|答案(?:是|为)?\s*[:：]?\s*([A-D])\b", affirmative)
    letters = {a or b for a, b in claims}
    matches = [o['id'] for o in snapshot['student_question']['options'] if o['label'] in letters]
    if len(letters) != 1 or len(matches) != 1:
        raise ValueError('EGO_ANSWER_OPTION_REQUIRES_REVIEW')
    numbers = re.findall(r'第\s*(\d+)\s*题', text)
    if any(n != str(snapshot['student_question']['number']) for n in numbers):
        raise ValueError('EGO_ANSWER_QUESTION_REQUIRES_REVIEW')
    return matches[0]


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
        files = [Path(item['path']).resolve(strict=True) for item in skills]
        for item in snapshot['attachments']:
            path = Path(item['path']).resolve(strict=True)
            if sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('EGO_STUDENT_ATTACHMENT_CHANGED')
            files.append(path)
        words = snapshot.get('student_words') or ''
        prompt = words if words.strip() else '第' + str(snapshot['student_question']['number']) + '题'
        if not snapshot['attachments']:
            q = snapshot['student_question']
            prompt = '\n'.join([snapshot.get('student_material') or '', q['verified_stem'],
                *[o['label'] + '. ' + o['verified_text'] for o in q['options']], prompt])
        delivered = delivery_context(snapshot)
        if delivered:
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
        meter.finish(call_id, 'CONFIRMED', evidence)
        meter.close('DEEPSEEK_TEACH', evidence)
        # The approved direct policy makes no search or MATCH model calls.
        meter.close('DEEPSEEK_MATCH', 'direct-original-policy')
        meter.close('SEARCH', 'direct-original-policy')
        # Recheck while the browser was generating; the old answer must never pass.
        verify_frozen_teaching(snapshot)
        current_url = self._current(snapshot)
        validate_direct_source(snapshot)
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
        option = selected_option(snapshot, text)
        details = {'input_fingerprint':input_fingerprint(snapshot), 'session_url':url,
                   'evidence_path':str(proof), 'sha256':evidence[7:], 'correct_option_id':option}
        with closing(Store(snapshot['session_store_path'])) as store, store.transaction():
            previous = store.all("SELECT details FROM audit WHERE run_id=? AND event='EGO_TEACH_CAPTURED'", (snapshot['run_id'],))
            if previous and [json.loads(r[0]) for r in previous] != [details]:
                raise ValueError('EGO_CAPTURE_HISTORY_CHANGED')
            if not previous:
                store.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
                    (snapshot['run_id'], 'EGO_TEACH_CAPTURED', encode(details), now()))
        return dict(adapter=self.identity, simulated=False, run_id=snapshot['run_id'],
            session_id=snapshot['session_id'], complete=True, uploads_confirmed=True,
            uploaded_teaching_hashes={s['path']:s['sha256'] for s in skills},
            web_session_evidence=str(proof), correct_option_id=option, text=text)


def validate_ego_capture(store, snapshot, body, option_id=None):
    """Recheck original ego evidence at Workflow finish and before delivery."""
    verify_frozen_teaching(snapshot)
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
            or captured.get('text') != body or selected_option(snapshot,body) != record['correct_option_id']
            or (option_id is not None and option_id != record['correct_option_id'])):
        raise ValueError('EGO_CAPTURE_EVIDENCE_CHANGED')
    validate_direct_source(snapshot, allow_completed=True)
    return record


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
