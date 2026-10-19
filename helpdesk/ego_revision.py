"""One bounded teaching-check revision in the original question/session.

Keeps the rejected run and draft immutable. Only the existing task's current
run pointer moves; the original Workflow handles the new draft and Outbox.
"""
from hashlib import sha256
import json

from .storage import encode,now


def revision_source(store,snapshot):
    from .ego_generation import _capture_record
    link=snapshot['ego_revision']
    parent=store.one('SELECT * FROM runs WHERE id=?',(link['parent_run_id'],))
    if not parent or parent['state']!='REJECTED' or parent['error']!='ANSWER_DRAFT_CHECK_REQUIRES_REVIEW':
        raise ValueError('EGO_REVISION_REJECTED_PARENT_REQUIRED')
    original=json.loads(parent['input_json'])
    if original.get('ego_revision'):
        raise ValueError('EGO_REVISION_BUDGET_EXHAUSTED')
    keys=('session_id','binding_id','case_id','question_id','question_version','context_revision',
          'turn_id','student_question','student_material','student_words','attachments','teaching_skills')
    if any(encode(original.get(k))!=encode(snapshot.get(k)) for k in keys):
        raise ValueError('EGO_REVISION_SOURCE_CHANGED')
    answer=store.one('SELECT a.* FROM answers a JOIN answer_evidence e ON e.answer_id=a.id WHERE e.run_id=?',
                     (parent['id'],))
    if not answer or answer['state']!='REJECTED' or sha256(answer['text'].encode()).hexdigest()!=link['draft_sha256']:
        raise ValueError('EGO_REVISION_DRAFT_CHANGED')
    proof=_capture_record(store,original,answer['text'])
    if proof['sha256']!=link['capture_sha256'] or proof['session_url']!=snapshot['ego_request_url']:
        raise ValueError('EGO_REVISION_SESSION_CHANGED')
    rows=store.all("SELECT details FROM audit WHERE run_id=? AND event='TEACHING_DRAFT_CHECKED'",(parent['id'],))
    if len(rows)!=1 or sha256(rows[0][0].encode()).hexdigest()!=link['check_sha256']:
        raise ValueError('EGO_REVISION_CHECK_CHANGED')
    if revision_prompt(json.loads(rows[0][0]))!=link['prompt']:
        raise ValueError('EGO_REVISION_PROMPT_CHANGED')
    return original


def revision_prompt(check):
    flags=check.get('output',{}).get('automatic_flags')
    if check.get('status')!='REVIEW_REQUIRED' or not isinstance(flags,list) or not 0<len(flags)<=20:
        raise ValueError('EGO_REVISION_CHECK_FLAGS_REQUIRED')
    lines=['请按本会话已上传的教学原文修订上一份讲解。以下为检查器原文，请逐项对照学生原图处理；不确定的不要编造。',
           '请重新给出本题完整的自然讲解，不输出检查报告，不声称学生已收到旧稿。']
    for flag in flags:
        if not all(isinstance(flag.get(k),str) and 0<len(flag[k])<=4000 for k in ('message','excerpt')):
            raise ValueError('EGO_REVISION_CHECK_FLAGS_REQUIRED')
        lines.extend(['检查提示：'+flag['message'],'原稿片段：'+flag['excerpt']])
    return '\n'.join(lines)


def prepare_revision(store,parent_run_id,*,space_id=None,page=None):
    from .automatic_preparation import _source_review
    from .ego_generation import EgoDeepSeekGenerator
    from .workflow import Workflow
    with store.transaction():
        prior=store.all("SELECT details FROM audit WHERE run_id=? AND event='EGO_REVISION_PREPARED'",(parent_run_id,))
        if prior:
            if len(prior)!=1:raise ValueError('EGO_REVISION_HISTORY_CHANGED')
            child=json.loads(prior[0][0])['run_id']
            row=store.one('SELECT input_json FROM runs WHERE id=?',(child,))
            if not row:raise ValueError('EGO_REVISION_HISTORY_CHANGED')
            snapshot=json.loads(row[0])
            if ((space_id is not None and snapshot['ego_browser']['space_id']!=space_id)
                    or (page is not None and snapshot['ego_browser']['page']!=page)):
                raise ValueError('EGO_FROZEN_BROWSER_CHANGED')
            revision_source(store,snapshot)
            return child
        parent=store.one('SELECT * FROM runs WHERE id=?',(parent_run_id,))
        if not parent or parent['state']!='REJECTED' or parent['error']!='ANSWER_DRAFT_CHECK_REQUIRES_REVIEW':
            raise ValueError('EGO_REVISION_REJECTED_PARENT_REQUIRED')
        original=json.loads(parent['input_json'])
        if original.get('ego_revision'):raise ValueError('EGO_REVISION_BUDGET_EXHAUSTED')
        task=store.one('SELECT * FROM operator_tasks WHERE run_id=?',(parent_run_id,))
        if not task:raise ValueError('EGO_REVIEWED_SOURCE_REQUIRED')
        _source_review(store,task['id'],allow_ego=True,allow_rejected=True)
        checks=store.all("SELECT details FROM audit WHERE run_id=? AND event='TEACHING_DRAFT_CHECKED'",(parent_run_id,))
        if len(checks)!=1:raise ValueError('EGO_REVISION_CHECK_FLAGS_REQUIRED')
        check=json.loads(checks[0][0]);prompt=revision_prompt(check)
        browser=original['ego_browser']
        generator=EgoDeepSeekGenerator(space_id if space_id is not None else browser['space_id'],
            browser['evidence_dir'],page=page if page is not None else browser['page'])
        # Nested transaction support is already used by OperatorTasks.freeze.
        from .operator_tasks import _NestedStore
        workflow=Workflow(_NestedStore(store),generation_adapter=generator,teaching_manifest=task['manifest_path'])
        child=workflow.start(parent['turn_id'])
        snapshot=json.loads(store.one('SELECT input_json FROM runs WHERE id=?',(child,))[0])
        if 'operator_test' in original:snapshot['operator_test']=original['operator_test']
        capture=json.loads(store.one("SELECT details FROM audit WHERE run_id=? AND event='EGO_TEACH_CAPTURED'",(parent_run_id,))[0])
        snapshot['ego_revision']={'parent_run_id':parent_run_id,'draft_sha256':check['draft_sha256'],
            'capture_sha256':capture['sha256'],'check_sha256':sha256(checks[0][0].encode()).hexdigest(),'prompt':prompt}
        revision_source(store,snapshot)
        store.execute('UPDATE runs SET input_json=? WHERE id=?',(encode(snapshot),child))
        store.execute('UPDATE operator_tasks SET run_id=? WHERE id=?',(child,task['id']))
        store.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
            (parent_run_id,'EGO_REVISION_PREPARED',encode({'run_id':child,'maximum_revisions':1,
                'original_browser':browser,'revision_browser':generator.browser_binding()}),now()))
        return child
