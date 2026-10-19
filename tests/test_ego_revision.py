"""Anonymous SQLite + fake webpage. No real generation or delivery."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from helpdesk.ego_generation import EgoDeepSeekGenerator,run_existing
from helpdesk.ego_revision import prepare_revision,revision_source
from helpdesk.storage import encode,now
from helpdesk.storage import Store
from helpdesk.workflow import Workflow
from tests import test_ego_direct_queue as queue_fixtures


def crash_revision(database,parent,manifest_file,phase):
    """Real hard exit against an anonymous existing DB; no browser adapter."""
    manifest=json.loads(Path(manifest_file).read_text(encoding='utf-8'))
    with patch('helpdesk.automatic_preparation.verify_bundle',return_value=manifest), \
            patch('helpdesk.workflow.verify_bundle',return_value=manifest), \
            patch('helpdesk.ego_generation.verify_frozen_teaching',return_value=manifest):
        store=Store(database)
        original=store.execute
        def execute(sql,args=()):
            result=original(sql,args)
            if phase=='BEFORE_COMMIT' and 'EGO_REVISION_PREPARED' in args:
                os._exit(86)
            return result
        store.execute=execute
        prepare_revision(store,parent)
        os._exit(86)


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.fx=queue_fixtures.DirectQueueTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.fx.admit();self.db=self.fx.db
        task=self.fx.fx.tasks.get_task(self.fx.task['id']);self.task=task
        self.parent=task['run_id']
        self.snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(self.parent,))[0])
        browser=self.snapshot['ego_browser']
        self.generator=EgoDeepSeekGenerator(browser['space_id'],browser['evidence_dir'],page=browser['page'])
        result=self.generator.generate(self.snapshot)
        self.body=result['text']
        result['error']='ANSWER_DRAFT_CHECK_REQUIRES_REVIEW'
        self.assertEqual(Workflow(self.db).finish(self.parent,result)['state'],'REJECTED')
        check={'status':'REVIEW_REQUIRED','draft_sha256':sha256(self.body.encode()).hexdigest(),
            'output':{'automatic_flags':[{'message':'Anonymous checker: quote the source.','excerpt':self.body}]}}
        self.db.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
            (self.parent,'TEACHING_DRAFT_CHECKED',encode(check),now()))

    def test_retry_keeps_history_and_session_no_reupload_no_delivery(self):
        child=prepare_revision(self.db,self.parent)
        self.assertEqual(prepare_revision(self.db,self.parent),child)
        self.assertEqual(self.db.one('SELECT state FROM runs WHERE id=?',(self.parent,))[0],'REJECTED')
        snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(child,))[0])
        self.assertEqual(snapshot['session_id'],self.snapshot['session_id'])
        self.assertEqual(snapshot['question_version'],self.snapshot['question_version'])
        result=run_existing(self.db,child,1,self.task['evidence_dir'],page='p1')
        self.assertEqual(result['state'],'GENERATED',result)
        self.assertEqual(self.fx.calls[-1][3],[])
        self.assertIn('Anonymous checker: quote the source.',self.fx.calls[-1][2])
        self.assertNotIn('JSON',self.fx.calls[-1][2])
        self.assertFalse(self.fx.calls[-1][-1])
        self.assertEqual(self.db.one('SELECT count(*) FROM answers')[0],2)
        self.assertEqual(self.db.one("SELECT count(*) FROM outbox WHERE purpose='ANSWER'")[0],1)
        self.assertEqual(self.db.one('SELECT count(*) FROM performance_units')[0],0)

    def test_changed_source_or_pause_cannot_create_revision(self):
        self.db.execute("UPDATE settings SET value='true' WHERE key='stop_requested'")
        with self.assertRaisesRegex(ValueError,'STOPPED'):prepare_revision(self.db,self.parent)
        self.assertEqual(self.db.one('SELECT count(*) FROM runs')[0],1)
        self.db.execute("UPDATE settings SET value='false' WHERE key='stop_requested'")
        self.db.execute('UPDATE questions SET context_revision=context_revision+1')
        with self.assertRaises(ValueError):prepare_revision(self.db,self.parent)
        self.assertEqual(self.db.one('SELECT count(*) FROM runs')[0],1)

    def test_tampered_parent_check_stops_revision_and_retry_budget_is_one(self):
        child=prepare_revision(self.db,self.parent)
        self.db.execute("UPDATE runs SET state='REJECTED',error='ANSWER_DRAFT_CHECK_REQUIRES_REVIEW' WHERE id=?",(child,))
        with self.assertRaisesRegex(ValueError,'BUDGET_EXHAUSTED'):prepare_revision(self.db,child)
        self.db.execute("UPDATE audit SET details='{}' WHERE run_id=? AND event='TEACHING_DRAFT_CHECKED'",(self.parent,))
        snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(child,))[0])
        with self.assertRaisesRegex(ValueError,'CHECK_CHANGED'):revision_source(self.db,snapshot)

    def test_rebuilt_space_is_frozen_once_without_changing_original_session(self):
        child=prepare_revision(self.db,self.parent,space_id=8,page='p2')
        snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(child,))[0])
        self.assertEqual(snapshot['ego_browser']['space_id'],8)
        self.assertEqual(snapshot['session_id'],self.snapshot['session_id'])
        self.assertEqual(json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(self.parent,))[0]),self.snapshot)
        with self.assertRaisesRegex(ValueError,'FROZEN_BROWSER_CHANGED'):
            prepare_revision(self.db,self.parent,space_id=9,page='p2')

    def test_missing_checker_evidence_does_not_create_new_run(self):
        self.db.execute("DELETE FROM audit WHERE run_id=? AND event='TEACHING_DRAFT_CHECKED'",(self.parent,))
        with self.assertRaisesRegex(ValueError,'CHECK_FLAGS_REQUIRED'):
            prepare_revision(self.db,self.parent)
        self.assertEqual(self.db.one('SELECT count(*) FROM runs')[0],1)

    def test_abrupt_exit_rolls_back_or_reuses_original_revision_without_browser(self):
        manifest=self.fx.base/'restart-revision-manifest.json'
        manifest.write_text(encode(self.fx.fx.manifest),encoding='utf-8')
        for phase in ('BEFORE_COMMIT','AFTER_COMMIT'):
            with self.subTest(phase=phase):
                process=subprocess.run([sys.executable,'-X','utf8','-B','-m','tests.test_ego_revision',
                    '--crash',str(self.db.path),self.parent,str(manifest),phase],
                    cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=30,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertEqual(process.returncode,86,process.stderr.decode('utf-8',errors='replace'))
                self.assertEqual(self.db.one('SELECT state FROM runs WHERE id=?',(self.parent,))[0],'REJECTED')
                if phase=='BEFORE_COMMIT':
                    self.assertEqual(self.db.one('SELECT count(*) FROM runs')[0],1)
                    self.assertEqual(self.db.one('SELECT run_id FROM operator_tasks WHERE id=?',(self.task['id'],))[0],self.parent)
                else:
                    child=self.db.one('SELECT run_id FROM operator_tasks WHERE id=?',(self.task['id'],))[0]
                    self.assertNotEqual(child,self.parent)
                    calls=len(self.fx.calls)
                    self.assertEqual(prepare_revision(self.db,self.parent),child)
                    self.assertEqual(prepare_revision(self.db,self.parent),child)
                    self.assertEqual(len(self.fx.calls),calls)
                    self.assertEqual(self.db.one('SELECT count(*) FROM runs')[0],2)
                    self.assertEqual(self.db.one('SELECT count(*) FROM answers')[0],1)
                    self.assertEqual(self.db.one("SELECT count(*) FROM outbox WHERE purpose='ANSWER'")[0],0)
                    self.assertEqual(self.db.one('SELECT count(*) FROM performance_units')[0],0)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--crash':
        crash_revision(*sys.argv[2:])
    else:
        unittest.main()
