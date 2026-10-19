"""Anonymous seven-choice tasks; no real browser, recipient or performance."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from helpdesk.domain import Question, Option, compare, mapped_label, Difference
from helpdesk.ego_generation import selected_option
from helpdesk.lesson_checks import _source, LessonCheckError
from tests import test_operator_tasks as operator_fixture
from tests import test_ego_generation as ego_fixture


TEXTS = ('Show by example.', 'Practise each morning.', 'Prepare for life.',
         'Be patient with them.', 'Adjust expectations.', 'Praise another attempt.',
         'Name the action done well.')


def question():
    return Question('12', 'Choose the sentence for blank 12.',
                    'Choose the sentence for blank 12.',
                    tuple(Option.confirmed(c, t, i, 'anonymous-original')
                          for i, (c, t) in enumerate(zip('ABCDEFG', TEXTS))),
                    'anonymous-original', kind='seven_five_blank')


class SevenFiveTasks(unittest.TestCase):
    def test_intake_keeps_all_seven_choices_and_missing_g_cannot_be_reviewed(self):
        fixture = operator_fixture.OperatorTaskTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        payload = {**fixture.payload, 'question_type': '七选五',
                   'passage': 'Learning takes effort. __12__ Try again.',
                   'options': dict(zip('ABCDEFG', TEXTS))}
        draft = fixture.intake.create_draft(payload)
        task = fixture.review(draft)
        context = helpdesk_context(fixture, task)
        q = Question.from_dict(context['student_question'])
        self.assertTrue(q.complete)
        self.assertEqual(q.kind, 'seven_five_blank')
        self.assertEqual([o.label for o in q.options], list('ABCDEFG'))
        from helpdesk.mcp_preparation import question_text_fields
        self.assertEqual(question_text_fields(context)['options'], payload['options'])
        self.assertEqual(fixture.review(draft)['id'], task['id'])
        self.assertEqual(fixture.db.one('SELECT count(*) FROM performance_units')[0], 0)
        self.assertEqual(fixture.db.one("SELECT count(*) FROM outbox WHERE purpose='ANSWER' OR state='SENT_UI_CONFIRMED'")[0], 0)
        incomplete = {**payload, 'options': {k:v for k,v in payload['options'].items() if k != 'G'}}
        with self.assertRaisesRegex(ValueError, 'option G'):
            fixture.review(fixture.intake.create_draft(incomplete))
        with self.assertRaises(ValueError):
            fixture.intake.create_draft({**payload, 'question_type': '阅读理解'})
        sibling = {**payload,'number':'13','stem':'Choose the sentence for blank 13.',
                   'request_text':'第13题'}
        child_draft = fixture.intake.create_draft(sibling,parent_task_id=task['id'],intent='SUBQUESTION')
        child = fixture.review(child_draft)
        self.assertEqual(child['case_id'],task['case_id'])
        self.assertNotEqual(child['question_id'],task['question_id'])
        changed = {**sibling,'number':'14','options':{**payload['options'],'G':'Changed shared option.'}}
        with self.assertRaisesRegex(ValueError,'shared original options'):
            fixture.intake.create_draft(changed,parent_task_id=task['id'],intent='SUBQUESTION')

    def test_seven_choice_mapping_preserves_student_g_and_rejects_incomplete(self):
        reference = question()
        student = replace(reference, options=tuple(
            Option.confirmed(c, TEXTS[(i+1)%7], i, 'student-reordered')
            for i,c in enumerate('ABCDEFG')))
        comparison = compare('r1', reference, 'same material', 's1', student, 'same material')
        self.assertIn(Difference.ORDER, comparison.differences)
        self.assertEqual(mapped_label(comparison, 'r1', 's1', reference.options[0].id, student), 'G')
        with self.assertRaises(ValueError):
            mapped_label(comparison, 'r1', 's2', reference.options[0].id, student)
        missing = replace(student, options=student.options[:-1])
        self.assertFalse(missing.complete)
        self.assertIn(Difference.UNCERTAIN, compare('r1',reference,'same','s2',missing,'same').differences)
        self.assertFalse(replace(reference, kind='single_choice').complete)

    def test_source_requires_the_actual_selected_blank_and_all_shared_options(self):
        snapshot = {'student_question':question().to_dict(),
                    'student_material':'Learning takes effort. __12__ Try again.'}
        source = _source(snapshot)
        self.assertIn('__12__', source)
        self.assertIn('G. Name the action done well.', source)
        with self.assertRaisesRegex(LessonCheckError, 'BLANK_NOT_IN_STUDENT_MATERIAL'):
            _source({**snapshot, 'student_material':'Only blank __13__ is here.'})

    def test_g_is_an_explicit_claim_not_inferred_from_discussion(self):
        snapshot = {'student_question':question().to_dict()}
        self.assertEqual(selected_option(snapshot,'第12题不选F。答案为G。'), question_id(snapshot,'G'))
        for text in ('第12题在F和G之间。', '第13题选G。', '第12题选H。', '第12题选F，也选G。'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                selected_option(snapshot,text)
        four = replace(question(),kind='single_choice',options=question().options[:4])
        with self.assertRaises(ValueError):
            selected_option({'student_question':four.to_dict()},'第12题选G。')

    def test_mock_web_g_result_returns_to_original_outbox_without_performance(self):
        fixture = ego_fixture.EgoGenerationTests()
        with patch('tests.test_ego_generation.demo_question', side_effect=question):
            fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.body = '第12题选G，因为这句话承接学生原文。'
        generated = fixture.generator.generate(fixture.snapshot)
        result = fixture.workflow.finish(fixture.run_id, generated)
        self.assertEqual(result['state'], 'GENERATED')
        self.assertEqual(len(fixture.calls), 1)
        row = fixture.store.one('SELECT state,body FROM outbox WHERE run_id=?',(fixture.run_id,))
        self.assertEqual(row['state'], 'PENDING')
        self.assertEqual(row['body'], fixture.body)
        self.assertEqual(fixture.store.one('SELECT count(*) FROM performance_units')[0],0)


def helpdesk_context(fixture, task):
    from helpdesk.service import Helpdesk
    return Helpdesk(fixture.db).context(task['turn_id'])


def question_id(snapshot, label):
    return next(o['id'] for o in snapshot['student_question']['options'] if o['label']==label)
