"""Synthetic billing only; this view never creates tasks or counts performance."""
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest

from helpdesk import call_costs as costs
from helpdesk.storage import encode,now
from tests import test_call_costs as fixtures
from tools.report_task_costs import main


class QuestionCostsTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.CallCostsTests();self.fx.setUp();self.addCleanup(self.fx.doCleanups)
        self.db=self.fx.db
        self.parent,self.outcome,_=self.fx.task()
        self.question=self.outcome.question_id

    def child(self):
        self.db.execute("UPDATE runs SET state='REJECTED' WHERE id=?",(self.parent,))
        child=self.fx.flow.start(self.outcome.turn_id)
        snapshot=json.loads(self.db.one('SELECT input_json FROM runs WHERE id=?',(child,))[0])
        snapshot['ego_revision']={'parent_run_id':self.parent}
        self.db.execute('UPDATE runs SET input_json=? WHERE id=?',(encode(snapshot),child))
        self.db.execute('INSERT INTO audit(run_id,event,details,created_at) VALUES(?,?,?,?)',
            (self.parent,'EGO_REVISION_PREPARED',encode({'run_id':child,'synthetic':True}),now()))
        return child

    def test_counts_parent_and_revision_without_turning_them_into_two_passages(self):
        self.fx.accounted(self.parent,'0.20')
        child=self.child();self.fx.accounted(child,'0.10')
        value=costs.question_cost(self.db,self.question)
        self.assertEqual(value['run_ids'],[self.parent,child])
        self.assertEqual(value['total_cny'],'0.30')
        self.assertEqual(value['observed_call_counts']['SEARCH'],2)
        self.assertEqual(value['scope'],'ALL_RUNS_FOR_QUESTION_NOT_PERFORMANCE_QUANTITY')
        self.assertEqual(value['teaching_revisions'][0]['parent_run_id'],self.parent)
        self.assertTrue(value['injected_evidence'])

    def test_unpriced_revision_and_unmeasured_stages_are_not_free(self):
        self.fx.accounted(self.parent,'0.20');child=self.child()
        call=self.fx.call(child,kind='DEEPSEEK_TEACH')
        costs.finish_call(self.db,child,call,'CONFIRMED','SYNTHETIC captured response')
        value=costs.question_cost(self.db,self.question)
        self.assertEqual(value['observed_call_counts']['DEEPSEEK_TEACH'],1)
        self.assertEqual(value['teaching_revision_calls'],1)
        self.assertFalse(value['measurement_complete']['SCHEDULER'])
        self.assertEqual(value['known_subtotal_cny'],'0.20')
        self.assertIsNone(value['total_cny'])

    def test_invalid_revision_link_does_not_hide_its_costs(self):
        self.fx.accounted(self.parent,'0.20');child=self.child();self.fx.accounted(child,'0.10')
        self.db.execute("DELETE FROM audit WHERE event='EGO_REVISION_PREPARED'")
        value=costs.question_cost(self.db,self.question)
        self.assertEqual(value['known_subtotal_cny'],'0.30')
        self.assertIsNone(value['total_cny'])
        self.assertIn('REVISION_LINK_UNVERIFIED:'+child,value['unverified'])

    def test_cli_repeated_question_report_is_readonly(self):
        before=self.db.connection.total_changes
        expected=costs.question_cost(self.db,self.question)
        for _ in range(2):
            with redirect_stdout(StringIO()) as output:
                main(['--db',str(self.db.path),'--question',self.question])
            self.assertEqual(json.loads(output.getvalue()),expected)
        self.assertEqual(self.db.connection.total_changes,before)
        with self.assertRaisesRegex(ValueError,'QUESTION_NOT_FOUND'):
            costs.question_cost(self.db,'missing')
