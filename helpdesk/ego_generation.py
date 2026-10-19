"""Natural-language ego teaching results into the existing Workflow/Outbox.

Requires an already frozen run and verified first-stage receipt. No sending,
new session creation, JSON uploads, or independent completion ledger.
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
from .question_matching import frozen_receipt


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
        self.space_id, self.page = space_id, page
        self.directory = Path(evidence_dir).resolve()
        self.capture_only = capture_only

    def _current(self, snapshot):
        from .service import Helpdesk
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
                raise ValueError('EGO_BOUND_SESSION_REQUIRED')
            return chat['session_url']

    def generate(self, snapshot):
        manifest = verify_frozen_teaching(snapshot)
        url = self._current(snapshot)
        # Never bypass the original version/mapping verification to simplify the UI.
        frozen_receipt(snapshot, session_url=url)
        if manifest.get('format_version') == 3:
            from .lesson_checks import validate_source_check
            validate_source_check(snapshot, manifest=manifest)
        claim_deepseek_chat(snapshot, url)
        skills = snapshot['teaching_skills']
        files = [Path(item['path']).resolve(strict=True) for item in skills]
        for item in snapshot['attachments']:
            path = Path(item['path']).resolve(strict=True)
            if sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('EGO_STUDENT_ATTACHMENT_CHANGED')
            files.append(path)
        words = snapshot.get('student_words') or ''
        prompt = words.strip() or '第' + str(snapshot['student_question']['number']) + '题'
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
            captured = browser.submit(request_id, url, prompt, attachments=files, capture_only=self.capture_only)
        except Exception:
            meter.finish(call_id, 'UNKNOWN', 'EGO_CAPTURE_NOT_CONFIRMED')
            raise
        proof = self.directory / (request_id + '.answer.json')
        evidence = 'sha256:' + sha256(proof.read_bytes()).hexdigest()
        meter.finish(call_id, 'CONFIRMED', evidence)
        meter.close('DEEPSEEK_TEACH', evidence)
        # Recheck while the browser was generating; the old answer must never pass.
        verify_frozen_teaching(snapshot)
        self._current(snapshot)
        frozen_receipt(snapshot, session_url=url)
        if (captured.get('adapter') != 'EGO_LITE' or captured.get('complete') is not True
                or captured.get('session_url') != url or not isinstance(captured.get('text'), str)
                or captured.get('upload_manifest') != [
                    {'path':str(p),'name':p.name,'sha256':sha256(p.read_bytes()).hexdigest()} for p in files]):
            raise ValueError('EGO_CAPTURE_BINDING_CHANGED')
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
    frozen_receipt(snapshot, session_url=chat[0])
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
