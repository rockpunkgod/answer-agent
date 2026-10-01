"""Section 15: original question time and material-level night quantity."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from helpdesk.__main__ import demo_question
from helpdesk.domain import Intent, new_id
from helpdesk.performance import PerformanceLedger
from helpdesk.performance_reports import PerformanceReports
from helpdesk.service import Helpdesk, Incoming
from helpdesk.storage import Store


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Store(Path(self.tmp.name) / 'ledger.db')
        self.app = Helpdesk(self.db)
        self.person = self.app.bind('group', 'student', 'Student', verified=True)
        self.ledger = PerformanceLedger(self.db, night_end_hour=7, night_end_inclusive=False)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def question(self, at=None, *, kind='阅读理解', material='Passage'):
        outcome = self.app.ingest(Incoming(self.person, '请讲解第12题', Intent.NEW,
                                           verified_question=demo_question(), verified_material=material,
                                           observed_at='2026-10-01T23:05:00+08:00'))
        if at:
            self.ledger.record_source_time(outcome.message_id, at, source='wecom_original',
                                           message_locator='wecom:' + outcome.message_id,
                                           evidence={'raw_message_id': outcome.message_id})
        mid = self.db.one('SELECT material_id FROM questions WHERE id=?', (outcome.question_id,))[0]
        unit = self.ledger.create_unit(outcome.message_id, kind, scope_key=f'{kind}:{mid}',
                                       grouping_reason='同一材料', question_id=outcome.question_id)
        return outcome, unit

    def unit(self, unit_id):
        return self.db.one('SELECT * FROM performance_units WHERE id=?', (unit_id,))

    def real_delivery(self, outcome, unit_id, at):
        oid = new_id()
        question = self.db.one('SELECT current_version,context_revision FROM questions WHERE id=?', (outcome.question_id,))
        turn = self.db.one('SELECT id,message_id FROM turns WHERE question_id=? ORDER BY rowid DESC LIMIT 1',
                           (outcome.question_id,))
        self.db.execute('''INSERT INTO outbox(id,message_id,case_id,turn_id,binding_id,purpose,body,question_version,
            context_revision,idempotency_key,state,created_at,simulated,sent_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (oid, turn['message_id'], outcome.case_id, turn['id'], self.person, 'ANSWER', '详细解答',
             question['current_version'], question['context_revision'], oid, 'SENT_UI_CONFIRMED', at, 0, at))
        self.db.execute('INSERT INTO delivery_checks VALUES(?,?,?,?,?)',
                        (new_id(), oid, 'SENT_UI_CONFIRMED', '{"visible":true}', at))
        self.ledger.record_delivery(unit_id, oid)
        return oid

    def test_completion_after_23_does_not_change_2250_question(self):
        outcome, unit = self.question('2026-09-30T22:50:00+08:00')
        self.real_delivery(outcome, unit, '2026-09-30T23:20:00+08:00')
        self.assertEqual(self.unit(unit)['category'], 'REGULAR')

    def test_night_question_stays_night_across_midnight_and_morning(self):
        for finished in ('2026-10-01T00:20:00+08:00', '2026-10-01T10:00:00+08:00'):
            with self.subTest(finished=finished):
                outcome, unit = self.question('2026-09-30T23:10:00+08:00')
                self.real_delivery(outcome, unit, finished)
                self.ledger.confirm(unit, reviewer='teacher', evidence='正确性与形式人工确认')
                self.assertEqual((self.unit(unit)['category'], self.unit(unit)['measure_unit'],
                                  self.unit(unit)['confirmed_quantity'], self.unit(unit)['timeliness_status']),
                                 ('NIGHT', '篇', 1, 'PENDING'))

    def test_followup_and_multiple_grammar_blanks_keep_one_piece(self):
        first, unit = self.question('2026-09-30T23:10:00+08:00', kind='语法填空')
        for text in ('第1空为什么', '第2空怎么填', '补图', '选项换序', '第3空呢'):
            follow = self.app.ingest(Incoming(self.person, text, Intent.FOLLOWUP,
                                              quote_message_id=first.message_id))
            self.ledger.link_activity(unit, follow.message_id, question_id=first.question_id,
                                      kind='FOLLOWUP', reason='同篇追问')
        self.real_delivery(first, unit, '2026-10-01T00:20:00+08:00')
        self.ledger.confirm(unit, reviewer='teacher', evidence='核对全部空', actual_question_count=5)
        self.assertEqual(self.unit(unit)['confirmed_quantity'], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_links WHERE unit_id=?', (unit,))[0], 6)
        with self.assertRaises(ValueError):
            self.ledger.request_conversion(unit, 6, actor='teacher', reason='详细讲解')

    def test_2258_send_overrides_2305_observation(self):
        _, unit = self.question('2026-09-30T22:58:00+08:00')
        self.assertEqual(self.unit(unit)['category'], 'REGULAR')
        self.assertEqual(self.unit(unit)['question_time'], '2026-09-30T22:58:00+08:00')

    def test_late_followup_does_not_turn_early_piece_into_night(self):
        first, unit = self.question('2026-09-30T22:50:00+08:00')
        follow = self.app.ingest(Incoming(self.person, '为什么不选B', Intent.FOLLOWUP,
                                          quote_message_id=first.message_id))
        self.ledger.record_source_time(follow.message_id, '2026-09-30T23:10:00+08:00',
                                       source='wecom_original', message_locator='wecom:' + follow.message_id,
                                       evidence={'raw_message_id': follow.message_id})
        self.ledger.link_activity(unit, follow.message_id, question_id=first.question_id,
                                  kind='FOLLOWUP', reason='同篇普通追问')
        self.assertEqual(self.unit(unit)['category'], 'REGULAR')
        self.assertEqual(self.unit(unit)['question_time'], '2026-09-30T22:50:00+08:00')
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 1)

    def test_missing_original_time_never_uses_observation(self):
        _, unit = self.question()
        self.assertEqual(self.unit(unit)['category'], 'PENDING')
        self.assertIsNone(self.unit(unit)['question_time'])
        with self.assertRaises(ValueError):
            self.ledger.confirm(unit, reviewer='teacher', evidence='looks fine')

    def test_draft_or_simulated_send_cannot_confirm(self):
        outcome, unit = self.question('2026-09-30T23:10:00+08:00')
        oid = self.db.one("SELECT id FROM outbox WHERE message_id=? AND purpose='ACK'", (outcome.message_id,))[0]
        with self.assertRaises(ValueError):
            self.ledger.record_delivery(unit, oid)
        self.assertEqual(self.unit(unit)['confirmed_quantity'], 0)

    def test_idempotent_scope_and_audited_revision(self):
        outcome, unit = self.question('2026-09-30T23:10:00+08:00')
        same = self.ledger.create_unit(outcome.message_id, '阅读理解', scope_key=self.unit(unit)['scope_key'],
                                       grouping_reason='重复日报', question_id=outcome.question_id)
        self.assertEqual(unit, same)
        self.real_delivery(outcome, unit, '2026-10-01T00:20:00+08:00')
        self.ledger.confirm(unit, reviewer='teacher', evidence='人工核验')
        self.ledger.revise(unit, actor='manager', reason='发现答案错误', evidence='复核记录', status='REVOKED')
        self.assertEqual(self.unit(unit)['confirmed_quantity'], 0)
        self.assertTrue(self.db.one("SELECT id FROM performance_events WHERE unit_id=? AND event='UNIT_REVISED'", (unit,)))

    def test_stale_version_demotes_previous_confirmed_count(self):
        outcome, unit = self.question('2026-09-30T23:10:00+08:00')
        self.real_delivery(outcome, unit, '2026-10-01T00:20:00+08:00')
        self.ledger.confirm(unit, reviewer='teacher', evidence='原解核对')
        self.app.ingest(Incoming(self.person, '选项换序', Intent.CORRECTION,
                                 quote_message_id=outcome.message_id,
                                 verified_question=demo_question(stem='Which option is correct now?')))
        self.assertEqual(self.ledger.reconcile_stale_deliveries(), [unit])
        self.assertEqual(self.unit(unit)['confirmed_quantity'], 0)
        self.assertEqual(self.unit(unit)['status'], 'PENDING')

    def test_different_scope_key_same_material_does_not_double_count(self):
        outcome, unit = self.question('2026-09-30T23:10:00+08:00')
        material = self.unit(unit)['material_id']
        repeated = self.ledger.create_unit(outcome.message_id, '阅读理解', scope_key='alternate-scope',
                                           grouping_reason='同篇另一小题', question_id=outcome.question_id,
                                           material_id=material)
        self.assertEqual(repeated, unit)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 1)

    def test_unapproved_ordinary_extension_stays_pending(self):
        outcome, unit = self.question('2026-09-30T22:10:00+08:00', kind='语法填空')
        self.real_delivery(outcome, unit, '2026-09-30T22:30:00+08:00')
        self.ledger.request_conversion(unit, 6, actor='teacher', reason='扩展讲解申请')
        with self.assertRaises(ValueError):
            self.ledger.confirm(unit, reviewer='teacher', evidence='内容正确')
        self.assertEqual(self.unit(unit)['confirmed_quantity'], 0)
        self.ledger.approve_conversion(unit, 4, approver='lead', authority='教研老师', evidence='教研审批单')
        self.ledger.confirm(unit, reviewer='teacher', evidence='内容正确')
        self.assertEqual(self.unit(unit)['confirmed_quantity'], 4)

    def test_rule_change_is_persisted_and_old_units_unchanged(self):
        _, old = self.question('2026-09-30T06:30:00+08:00')
        old_version = self.unit(old)['rule_version']
        version = self.ledger.configure_night_end(6, end_inclusive=False, actor='manager',
                                                  reason='边界核准', evidence='负责人签字记录')
        self.assertNotEqual(old_version, version)
        self.assertEqual(self.unit(old)['category'], 'NIGHT')
        self.assertEqual(self.unit(old)['rule_version'], old_version)
        preview = self.ledger.preview_night_reclassification(end_hour=6, end_inclusive=False)
        self.assertEqual(preview[0]['unit_id'], old)
        reopened = PerformanceLedger(self.db)
        self.assertEqual((reopened.night_end_hour, reopened.night_end_inclusive, reopened.rule_version),
                         (6, False, version))
        with self.assertRaises(Exception):
            self.db.execute("UPDATE performance_rule_changes SET actor='someone' WHERE new_version=?", (version,))

    def test_verified_split_moves_new_question_evidence_and_is_atomic(self):
        first, unit = self.question('2026-09-30T22:50:00+08:00')
        changed = self.app.ingest(Incoming(self.person, '题干条件改为否定', Intent.CORRECTION,
                                           quote_message_id=first.message_id,
                                           verified_question=demo_question(stem='Why did he NOT return?')))
        self.ledger.record_source_time(changed.message_id, '2026-09-30T23:10:00+08:00',
                                       source='wecom_original', message_locator='wecom:' + changed.message_id,
                                       evidence={'raw_message_id': changed.message_id})
        self.ledger.link_activity(unit, changed.message_id, question_id=changed.question_id,
                                  kind='CORRECTION', reason='待核验条件变化')
        with self.assertRaises(ValueError):
            self.ledger.split(unit, first_message_id=changed.message_id, scope_key='new-condition',
                              actor='lead', reason='条件变化', evidence='题干对照', new_question_verified=False)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 1)
        new_unit = self.ledger.split(unit, first_message_id=changed.message_id, scope_key='new-condition',
                                     actor='lead', reason='核实为新题', evidence='题干对照', new_question_verified=True)
        self.assertNotEqual(new_unit, unit)
        self.assertEqual((self.unit(unit)['category'], self.unit(new_unit)['category']), ('REGULAR', 'NIGHT'))
        self.assertIsNone(self.db.one('SELECT id FROM performance_links WHERE unit_id=? AND message_id=?',
                                      (unit, changed.message_id)))
        self.assertTrue(self.db.one('SELECT id FROM performance_links WHERE unit_id=? AND message_id=?',
                                    (new_unit, changed.message_id)))

    def test_cross_student_same_material_needs_review_disposition(self):
        first, first_unit = self.question('2026-09-30T23:10:00+08:00')
        other = self.app.bind('group', 'student-two', 'Second', verified=True)
        second = self.app.ingest(Incoming(other, '请讲解第12题', Intent.NEW,
                                          verified_question=demo_question(), verified_material='Passage'))
        self.ledger.record_source_time(second.message_id, '2026-09-30T23:12:00+08:00',
                                       source='wecom_original', message_locator='wecom:' + second.message_id,
                                       evidence={'raw_message_id': second.message_id})
        second_unit = self.ledger.create_unit(second.message_id, '阅读理解', scope_key='second-material',
                                              grouping_reason='第二位学生提问', question_id=second.question_id)
        self.assertNotEqual(first_unit, second_unit)
        self.real_delivery(first, first_unit, '2026-10-01T00:10:00+08:00')
        with self.assertRaises(ValueError):
            self.ledger.confirm(first_unit, reviewer='teacher', evidence='答疑正确')
        self.ledger.confirm(first_unit, reviewer='teacher', evidence='答疑正确',
                            cross_student_disposition='负责人核准按学生分别计；审批单A')
        self.assertEqual(self.unit(first_unit)['confirmed_quantity'], 1)

    def test_conflicting_original_time_demotes_until_human_resolution(self):
        first, unit = self.question('2026-09-30T23:10:00+08:00')
        self.real_delivery(first, unit, '2026-10-01T00:10:00+08:00')
        self.ledger.confirm(unit, reviewer='teacher', evidence='已复核')
        with self.assertRaises(ValueError):
            self.ledger.record_source_time(first.message_id, '2026-09-30T22:58:00+08:00',
                                           source='wecom_original', message_locator='wecom:conflicting',
                                           evidence={'original_screen': 'proof'})
        self.assertEqual((self.unit(unit)['category'], self.unit(unit)['confirmed_quantity']), ('PENDING', 0))
        self.ledger.resolve_source_time_conflict(first.message_id, '2026-09-30T22:58:00+08:00',
                                                 actor='manager', reason='核对原始消息', evidence='截图编号A')
        self.assertEqual((self.unit(unit)['category'], self.unit(unit)['status']), ('REGULAR', 'PENDING'))

    def test_four_subquestions_share_one_reading_piece_with_deliveries(self):
        first, unit = self.question('2026-09-30T23:10:00+08:00')
        questions = [first.question_id]
        self.real_delivery(first, unit, '2026-09-30T23:20:00+08:00')
        for number in ('13', '14', '15'):
            sub = self.app.ingest(Incoming(self.person, f'第{number}题', Intent.SUBQUESTION,
                                           quote_message_id=first.message_id,
                                           verified_question=demo_question(number=number)))
            questions.append(sub.question_id)
            self.ledger.link_activity(unit, sub.message_id, question_id=sub.question_id,
                                      kind='SUBQUESTION', reason='同篇阅读不同小题')
            self.real_delivery(sub, unit, '2026-09-30T23:30:00+08:00')
        self.ledger.confirm(unit, reviewer='teacher', evidence='四小题均正确交付', actual_question_count=4)
        self.assertEqual(len(set(questions)), 4)
        self.assertEqual(self.unit(unit)['confirmed_quantity'], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_links WHERE unit_id=?', (unit,))[0], 4)

    def test_five_followups_across_month_keep_original_count(self):
        first, unit = self.question('2026-09-30T23:10:00+08:00')
        self.real_delivery(first, unit, '2026-09-30T23:20:00+08:00')
        self.ledger.confirm(unit, reviewer='teacher', evidence='正确交付')
        for index in range(5):
            follow = self.app.ingest(Incoming(self.person, f'追问{index}', Intent.FOLLOWUP,
                                              quote_message_id=first.message_id))
            self.ledger.record_source_time(follow.message_id, f'2026-10-{index+1:02d}T23:10:00+08:00',
                                           source='wecom_original', message_locator='wecom:' + follow.message_id,
                                           evidence={'raw_message_id': follow.message_id})
            self.ledger.link_activity(unit, follow.message_id, question_id=first.question_id,
                                      kind='FOLLOWUP', reason='同题延伸追问')
        self.assertEqual((self.unit(unit)['confirmed_quantity'], self.unit(unit)['question_time']),
                         (1, '2026-09-30T23:10:00+08:00'))
        self.assertEqual(self.db.one("SELECT COUNT(*) FROM performance_links WHERE unit_id=? AND link_kind='FOLLOWUP'", (unit,))[0], 5)

    def test_number_and_option_order_revision_does_not_create_unit(self):
        first, unit = self.question('2026-09-30T23:10:00+08:00')
        revised = demo_question(number='99')
        from dataclasses import replace
        revised = replace(revised, options=tuple(reversed(revised.options)))
        changed = self.app.ingest(Incoming(self.person, '题号和选项顺序更正', Intent.CORRECTION,
                                           quote_message_id=first.message_id, verified_question=revised))
        self.ledger.link_activity(unit, changed.message_id, question_id=first.question_id,
                                  kind='CORRECTION', reason='题号与选项换序')
        same = self.ledger.create_unit(changed.message_id, '阅读理解', scope_key='different-key',
                                       grouping_reason='同篇', question_id=changed.question_id)
        self.assertEqual(same, unit)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM performance_units')[0], 1)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM question_versions WHERE question_id=?', (first.question_id,))[0], 2)

    def test_send_unknown_cannot_enter_delivered_count(self):
        first, unit = self.question('2026-09-30T23:10:00+08:00')
        oid = new_id()
        q = self.db.one('SELECT current_version,context_revision FROM questions WHERE id=?', (first.question_id,))
        self.db.execute('''INSERT INTO outbox(id,message_id,case_id,turn_id,binding_id,purpose,body,question_version,
            context_revision,idempotency_key,state,created_at,simulated,sent_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (oid, first.message_id, first.case_id, first.turn_id, self.person, 'ANSWER', '详细解答',
             q['current_version'], q['context_revision'], oid, 'SEND_UNKNOWN', '2026-09-30T23:20:00+08:00',
             0, '2026-09-30T23:20:00+08:00'))
        with self.assertRaises(ValueError):
            self.ledger.record_delivery(unit, oid)
        self.assertIsNone(self.unit(unit)['completed_at'])

    def test_night_window_date_tracks_previous_date_after_midnight(self):
        _, unit = self.question('2026-10-01T00:20:00+08:00')
        self.assertEqual((self.unit(unit)['category'], self.unit(unit)['night_window_date']),
                         ('NIGHT', '2026-09-30'))

    def test_whole_paper_request_stays_pending(self):
        outcome, _ = self.question('2026-09-30T23:10:00+08:00')
        unit = self.ledger.create_unit(outcome.message_id, '整套试卷', scope_key='whole-paper',
                                       grouping_reason='整卷批改请求', question_id=outcome.question_id)
        self.assertEqual((self.unit(unit)['category'], self.unit(unit)['measure_unit']), ('PENDING', '待核验'))
        self.assertIn('班主任', self.unit(unit)['category_basis'])

    def test_split_then_manual_merge_revises_report_and_audit(self):
        first, unit = self.question('2026-09-30T22:50:00+08:00')
        self.real_delivery(first, unit, '2026-09-30T22:55:00+08:00')
        self.ledger.confirm(unit, reviewer='teacher', evidence='人工核对交付')
        reports = PerformanceReports(self.db, self.ledger)
        before = reports.build('2026-09-30')
        self.assertEqual(before['summary']['regular_articles:阅读理解'], 1)
        changed = self.app.ingest(Incoming(self.person, '改成否定条件', Intent.CORRECTION,
                                           quote_message_id=first.message_id,
                                           verified_question=demo_question(stem='Why did he NOT return?')))
        self.ledger.record_source_time(changed.message_id, '2026-09-30T23:10:00+08:00',
                                       source='wecom_original', message_locator='wecom:' + changed.message_id,
                                       evidence={'raw_message_id': changed.message_id})
        self.ledger.link_activity(unit, changed.message_id, question_id=changed.question_id,
                                  kind='CORRECTION', reason='候选新题')
        separate = self.ledger.split(unit, first_message_id=changed.message_id, scope_key='new-condition',
                                     actor='lead', reason='核实条件变化', evidence='题干核验', new_question_verified=True)
        self.ledger.merge(unit, separate, actor='lead', reason='复核后认定仍为同篇一次', evidence='复审单B')
        after = reports.build('2026-09-30')
        self.assertEqual(after['summary'].get('regular_articles:阅读理解', 0), 0)
        self.assertEqual(self.unit(separate)['status'], 'REVOKED')
        self.assertEqual(self.unit(unit)['status'], 'PENDING')
        self.assertTrue(any(item['counting_unit_id'] == unit for item in after['details']))
        self.assertTrue(self.db.one("SELECT id FROM performance_events WHERE unit_id=? AND event='MERGED_AWAY'", (separate,)))

    def test_confirmed_default_0700_is_durable_and_attributed_to_previous_day(self):
        default = PerformanceLedger(self.db)
        self.assertEqual((default.night_end_hour, default.night_end_inclusive), (7, False))
        self.assertEqual(self.db.one("SELECT value,status FROM performance_rules WHERE key='night_date_attribution'")[:],
                         ('original_question_business_day_07:00', 'CONFIRMED'))
        self.assertEqual(self.db.one("SELECT value,status FROM performance_rules WHERE key='reporting_cutoff'")[:],
                         ('07:00', 'CONFIRMED'))
        self.assertTrue(self.db.one("SELECT evidence FROM performance_rule_changes WHERE id='user-2026-night-end-0700'"))
        _, before = self.question('2026-09-23T06:59:59+08:00')
        _, at = self.question('2026-09-23T07:00:00+08:00')
        self.assertEqual((self.unit(before)['category'], self.unit(before)['night_window_date']),
                         ('NIGHT', '2026-09-22'))
        self.assertEqual((self.unit(at)['category'], self.unit(at)['night_window_date']),
                         ('REGULAR', None))

    def test_v3_upgrade_preserves_previously_confirmed_other_boundary(self):
        path = Path(self.tmp.name) / 'old-v3.db'
        migrations = Path(__file__).resolve().parents[1] / 'helpdesk' / 'migrations'
        connection = sqlite3.connect(path)
        for name in ('001_initial.sql', '002_workflow.sql', '003_performance.sql'):
            connection.executescript((migrations / name).read_text(encoding='utf-8'))
        connection.execute("UPDATE performance_rules SET value='6',status='CONFIRMED',source='previous-approval' WHERE key='night_end'")
        connection.execute("UPDATE performance_rules SET value='false',status='CONFIRMED',source='previous-approval' WHERE key='night_end_inclusive'")
        connection.execute("UPDATE performance_rules SET value='custom',status='CONFIRMED',source='previous-approval' WHERE key='night_date_attribution'")
        connection.commit()
        connection.close()
        old = Store(path)
        try:
            ledger = PerformanceLedger(old)
            self.assertEqual((ledger.night_end_hour, ledger.night_end_inclusive), (6, False))
            self.assertEqual(old.one("SELECT value FROM performance_rules WHERE key='night_date_attribution'")[0], 'custom')
            self.assertIsNone(old.one("SELECT id FROM performance_rule_changes WHERE id='user-2026-night-end-0700'"))
            self.assertEqual(old.one('SELECT MAX(version) FROM schema_migrations')[0], 6)
            self.assertIn('legacy-confirmed-night-end-06', ledger.rule_version)
        finally:
            old.close()


if __name__ == '__main__':
    unittest.main()
