"""Saved teacher replies are anonymous raw records, not real platform receipts."""
from dataclasses import replace
from http.client import HTTPConnection
import json
from pathlib import Path
from threading import Thread
import unittest
from unittest.mock import Mock, patch

from helpdesk.collector_storage import CollectorStore
from helpdesk.demo_server import DemoHTTPServer
from helpdesk.manual_delivery import ManualDeliveries, completion_for_turn
from helpdesk.message_sources import MessageBatch, NormalizedMessage, SyncMode, normalize_sent_time
from helpdesk.performance import PerformanceLedger
from helpdesk.performance_reports import PerformanceReports
from helpdesk.semantic_decisions import SharedSemanticDecisions
from helpdesk.service import Helpdesk
from tests import test_source_question_tasks as source_fixture


class SavedTeacherDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fx = source_fixture.SourceQuestionTasksTests()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        original_connect = CollectorStore.connect
        def bounded_fixture_connection(collector):
            connection = original_connect(collector)
            if str(collector.path) == str(self.fx.raw.path):
                connection.execute('PRAGMA busy_timeout=100')
            return connection
        short_wait = patch.object(CollectorStore, 'connect', bounded_fixture_connection)
        short_wait.start()
        self.addCleanup(short_wait.stop)
        self.fx.message, self.fx.receipt, self.fx.outcome = self.fx.receive(
            'saved-reply-question', sent='2026-09-30T23:10:00+08:00')
        self.db = self.fx.db
        self.filters = {'teacher_sender_ids': ('anonymous-teacher',)}
        self.shared = SharedSemanticDecisions(self.db, self.fx.raw, **self.filters)
        decision = self.shared.confirm(self.fx.receipt['id'], question_type='阅读理解',
            actor='anonymous resolver fixture', evidence='Synthetic verified material and question association')
        self.fx.draft = self.fx.tasks.create_from_semantic(self.fx.raw, decision, **self.filters)
        self.unit_id = self.shared.counting_unit(decision)
        self.registry = ManualDeliveries(self.db)
        self.args = dict(turn_id=self.fx.outcome.turn_id, question_version=self.fx.draft['base_version'],
            context_revision=self.fx.draft['base_context_revision'], reviewer='anonymous human verifier',
            verification_evidence='Synthetic human check of original teacher reply, full content, time and question',
            source_verified=True, part_number=1, total_parts=1)

    def teacher(self, name='reply-1', *, content='Actual anonymous teacher explanation for this question.',
                sent='2026-10-01T10:00:00+08:00', **changes):
        utc, local = normalize_sent_time(sent)
        message = NormalizedMessage(source_type='wecom_archive', source_message_id=name,
            room_id='fixture-room', sender_id='anonymous-teacher', sender_display_name='匿名老师',
            raw_content=content, normalized_text=content, sent_at_raw=sent, sent_at_utc=utc, sent_at_local=local,
            ingested_at='2026-10-01T10:05:00+08:00', reply_to_message_id=self.fx.message.source_message_id)
        if changes:
            message = replace(message, **changes)
        self.persist(message)
        return message

    def persist(self, message):
        state = self.fx.raw.get_sync_state('fixture-source', SyncMode.LIVE)
        self.fx.raw.persist_batch('fixture-source', SyncMode.LIVE, MessageBatch((message,), 'teacher-cursor'), state['cursor'])

    def select(self, message):
        result = self.registry.saved_replies(turn_id=self.args['turn_id'])
        return next(record for record in result['replies'] if record['collector_message_id'] == message.message_id)

    def register(self, message, **changes):
        selected = self.select(message)
        return self.registry.register_saved_reply(collector_message_id=selected['collector_message_id'],
            source_evidence_sha256=selected['source_evidence_sha256'], **(self.args | changes))

    def unit(self):
        return self.db.one('SELECT * FROM performance_units WHERE id=?', (self.unit_id,))

    def test_saved_actual_reply_returns_to_original_turn_and_counts_student_night_time(self):
        message = self.teacher()
        selected = self.select(message)
        self.assertEqual(selected['association'], 'QUOTED_ORIGINAL')
        self.assertEqual(selected['delivered_at'], '2026-10-01T02:00:00+00:00')
        self.assertNotIn('collector_path', selected)
        result = self.register(message)
        self.assertEqual(result['counting_status'], 'CONFIRMED')
        actual = self.db.one('SELECT * FROM outbox WHERE id=?', (result['recorded_outbox_id'],))
        proof = json.loads(self.db.one('SELECT evidence FROM delivery_checks WHERE outbox_id=?', (actual['id'],))[0])
        self.assertEqual(actual['body'], message.raw_content)
        self.assertEqual(actual['turn_id'], self.args['turn_id'])
        self.assertEqual(actual['question_version'], self.args['question_version'])
        self.assertEqual(proof['source_record']['collector_message_id'], message.message_id)
        self.assertEqual(proof['verification_method'], 'MANUAL_ATTESTATION')
        self.assertFalse(proof['automatic_receipt'])
        self.assertIsNone(proof['platform_message_id'])
        self.assertIsNone(proof['recipient_read'])
        self.assertEqual((self.unit()['category'], self.unit()['measure_unit'], self.unit()['confirmed_quantity']), ('NIGHT','篇',1))
        self.assertEqual(self.unit()['question_time'], '2026-09-30T23:10:00+08:00')
        self.assertEqual(Helpdesk(self.db).context(self.args['turn_id'])['previous_sent_answer'], message.raw_content)
        self.persist(message)
        again = self.register(message)
        self.assertTrue(again['replayed'])
        self.assertEqual(again['recorded_outbox_id'], result['recorded_outbox_id'])
        reports = PerformanceReports(self.db, PerformanceLedger(self.db))
        self.assertEqual(reports.build('2026-09-30')['summary'], reports.build('2026-09-30')['summary'])
        self.assertEqual(reports.build('2026-09-30')['summary']['night_articles'], 1)

    def test_partial_package_and_reused_source_cannot_count_twice(self):
        first, second = self.teacher('first'), self.teacher('second', sent='2026-10-01T10:01:00+08:00')
        result = self.register(first, total_parts=2)
        self.assertEqual(result['state'], 'PARTIAL_DELIVERY')
        self.assertEqual(self.unit()['confirmed_quantity'], 0)
        with self.assertRaisesRegex(ValueError, '已关联其他交付部分'):
            self.register(first, total_parts=2, part_number=2)
        self.assertIsNone(completion_for_turn(self.db, self.args['turn_id']))
        final = self.register(second, total_parts=2, part_number=2)
        self.assertEqual(final['counting_status'], 'CONFIRMED')
        self.assertEqual(self.unit()['confirmed_quantity'], 1)
        self.assertEqual(len(Helpdesk(self.db).context(self.args['turn_id'])['sent_history']), 2)

    def test_saved_reply_can_replace_generated_draft_without_claiming_generated_text_was_sent(self):
        from helpdesk.reviewed_question_queue import advance
        self.fx.enqueue()
        for kwargs in ({'session_creator':lambda **_:source_fixture.URL},
                       {'preparer':self.fx.prepare},{'generator':self.fx.generate}):
            advance(self.db,self.fx.task['id'],executor='LUNA',**kwargs)
        origin = self.db.one("SELECT * FROM outbox WHERE purpose='ANSWER'")
        message = self.teacher(content='Actual human text differs from generated fixture.')
        args = dict(self.args); args.pop('turn_id'); args['original_outbox_id']=origin['id']
        result = self.registry.register_saved_reply(collector_message_id=message.message_id,
            source_evidence_sha256=self.select(message)['source_evidence_sha256'],**args)
        self.assertEqual(result['counting_status'],'CONFIRMED')
        old = self.db.one('SELECT * FROM outbox WHERE id=?',(origin['id'],))
        self.assertEqual(old['body'],origin['body']); self.assertEqual(old['state'],'CANCELLED')
        actual = self.db.one('SELECT * FROM outbox WHERE id=?',(result['recorded_outbox_id'],))
        self.assertEqual(actual['body'],message.raw_content)
        self.assertNotEqual(actual['body'],origin['body'])
        self.assertEqual(self.unit()['confirmed_quantity'],1)

    def test_source_content_change_after_selection_requires_new_human_review(self):
        message = self.teacher()
        selected = self.select(message)
        with self.fx.raw.connect() as source:
            source.execute('UPDATE messages SET raw_content=? WHERE message_id=?', ('Changed synthetic reply',message.message_id))
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.registry.register_saved_reply(collector_message_id=message.message_id,
                source_evidence_sha256=selected['source_evidence_sha256'], **self.args)
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0], 0)
        self.assertEqual(self.unit()['confirmed_quantity'], 0)

    def test_conflicting_repeat_observation_cannot_reuse_the_preserved_first_record(self):
        message = self.teacher()
        self.register(message)
        self.persist(replace(message,raw_content='Conflicting observation of the same source ID',
                             normalized_text='Conflicting observation of the same source ID'))
        self.assertEqual(self.registry.saved_replies(turn_id=self.args['turn_id'])['replies'],[])
        self.assertIsNone(completion_for_turn(self.db,self.args['turn_id']))
        self.assertFalse(PerformanceLedger(self.db).delivery_eligibility(self.unit())['eligible'])

    def test_changed_record_after_registration_is_not_a_verified_completion(self):
        message = self.teacher()
        self.register(message)
        with self.fx.raw.connect() as source:
            source.execute('UPDATE messages SET raw_content=? WHERE message_id=?', ('Changed after verification',message.message_id))
        self.assertIsNone(completion_for_turn(self.db, self.args['turn_id']))
        self.assertFalse(PerformanceLedger(self.db).delivery_eligibility(self.unit())['eligible'])
        self.assertEqual(PerformanceReports(self.db, PerformanceLedger(self.db)).build('2026-09-30')['summary']['night_articles'], 0)

    def test_unknown_identity_time_media_and_wrong_quote_never_become_delivery(self):
        variants = [dict(sender_id='other-student'), dict(room_id='other-room'),
            dict(source_confidence='low'), dict(time_confidence='low'), dict(sent_at_utc=None,sent_at_local=None),
            dict(sent_at_raw=None), dict(sent_at_raw='2026-10-01T10:02:00+08:00'),
            dict(message_type='image',media_id='unverified-media'), dict(reply_to_message_id='other-question'),
            dict(quoted_message_id='other-student'), dict(parse_status='needs_review')]
        for index, changes in enumerate(variants):
            self.teacher('invalid-'+str(index), **changes)
        self.assertEqual(self.registry.saved_replies(turn_id=self.args['turn_id'])['replies'], [])
        self.assertEqual(self.db.one('SELECT COUNT(*) FROM delivery_checks')[0], 0)
        self.assertEqual(self.unit()['confirmed_quantity'], 0)

    def test_unquoted_reply_requires_explicit_human_association_and_ack_is_not_answer(self):
        message = self.teacher(reply_to_message_id=None)
        selected = self.select(message)
        self.assertEqual(selected['association'], 'MANUAL_LINK_REQUIRED')
        with self.assertRaisesRegex(ValueError, '请核验'):
            self.register(message, source_verified=False)
        ack = self.teacher('teacher-ack',content='收到')
        with self.assertRaisesRegex(ValueError, '收到或致谢'):
            self.register(ack)
        self.assertEqual(self.unit()['confirmed_quantity'], 0)
        self.assertEqual(self.register(message)['counting_status'], 'CONFIRMED')

    def test_historical_reply_after_correction_keeps_fact_but_not_current_completion(self):
        message = self.teacher()
        question = self.db.one('SELECT * FROM questions WHERE id=?', (self.fx.outcome.question_id,))
        Helpdesk(self.db).correct_material(question['material_id'], self.fx.outcome.message_id,
            'Changed condition in anonymous material.', 'Changed condition in anonymous material.')
        result = self.register(message)
        self.assertEqual(result['state'], 'SENT_UI_CONFIRMED')
        self.assertNotEqual(result['counting_status'], 'CONFIRMED')
        self.assertEqual(self.unit()['confirmed_quantity'], 0)
        self.assertTrue(completion_for_turn(self.db, self.args['turn_id'])['stale'])
        current = self.db.one('SELECT status FROM questions WHERE id=?', (self.fx.outcome.question_id,))[0]
        self.assertNotEqual(current, 'WAITING_FOLLOWUP')

    def test_task_without_configured_teacher_scope_is_unavailable_not_empty_group(self):
        turn = self.db.one('SELECT id FROM turns WHERE message_id=(SELECT message_id FROM source_question_drafts WHERE draft_id!=?)',
            (self.fx.draft['id'],))[0]
        with self.assertRaisesRegex(ValueError, '未配置老师身份'):
            self.registry.saved_replies(turn_id=turn)

    def test_missing_source_database_invalidates_completion_without_hiding_manual_records(self):
        message = self.teacher()
        self.register(message)
        Path(self.fx.raw.path).unlink()
        self.assertIsNone(completion_for_turn(self.db, self.args['turn_id']))
        row = next(row for row in self.registry.list() if row.get('outbox_id'))
        self.assertFalse(row['completed'])
        self.assertEqual(len(row['parts']), 1)
        self.assertEqual(row['parts'][0]['actual_content'], message.raw_content)


class SavedTeacherDeliveryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.fx = SavedTeacherDeliveryTests()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.message = self.fx.teacher()
        self.actor = Mock(side_effect=AssertionError('No real desktop, upload or generation'))
        self.server = DemoHTTPServer(('127.0.0.1',0),Path(self.fx.db.path),processing_mode='ACK_ONLY',
            transport_factory=self.actor, desktop_factory=self.actor, real_generator=self.actor)
        self.thread = Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.token = self.request('GET','/api/state')[1]['csrf_token']
        self.payload = dict(self.fx.args, collector_message_id=self.message.message_id,
            source_evidence_sha256=self.fx.select(self.message)['source_evidence_sha256'])

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    def request(self,method,path,payload=None,headers=None):
        connection = HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        fields = {'Origin':self.origin,'X-CSRF-Token':getattr(self,'token',''),'Content-Type':'application/json'} if payload is not None else {}
        fields.update(headers or {})
        connection.request(method,path,body=json.dumps(payload) if payload is not None else None,headers=fields)
        response = connection.getresponse(); raw = response.read(); connection.close()
        return response.status,json.loads(raw)

    def test_readonly_saved_reply_and_human_registration_return_to_original_task(self):
        before = self.fx.db.one('SELECT COUNT(*) FROM audit')[0]
        code,data = self.request('GET','/api/manual-delivery-replies?turn_id='+self.fx.args['turn_id'])
        self.assertEqual(code,200); self.assertEqual(data['status'],'READY')
        self.assertEqual(len(data['replies']),1)
        self.assertFalse(data['continuous_listener']); self.assertFalse(data['sends_messages'])
        self.assertNotIn('collector_path',json.dumps(data))
        self.assertEqual(self.fx.db.one('SELECT COUNT(*) FROM audit')[0],before)
        code,result = self.request('POST','/api/manual-deliveries',self.payload)
        self.assertEqual(code,200); self.assertEqual(result['result']['counting_status'],'CONFIRMED')
        self.assertTrue(self.request('POST','/api/manual-deliveries',self.payload)[1]['result']['replayed'])
        self.assertEqual(self.fx.unit()['confirmed_quantity'],1)
        self.actor.assert_not_called()

    def test_browser_cannot_replace_content_time_source_path_target_or_attestation(self):
        for change in ({'content':'Forged content'},{'delivered_at':'2026-10-01T10:03:00+08:00'},
                       {'collector_path':'C:/private.db'},{'target_group':'other-room'},
                       {'command':'anything'},{'source_verified':False},{'source_verified':1},
                       {'source_evidence_sha256':'0'*64}):
            self.assertEqual(self.request('POST','/api/manual-deliveries',self.payload|change)[0],400)
        for headers in ({'Origin':'https://other.invalid'},{'X-CSRF-Token':'wrong'},{'Host':'other.invalid'}):
            self.assertEqual(self.request('POST','/api/manual-deliveries',self.payload,headers)[0],403)
        self.assertEqual(self.fx.db.one('SELECT COUNT(*) FROM delivery_checks')[0],0)
        self.actor.assert_not_called()

    def test_unavailable_source_and_success_without_candidates_are_distinct(self):
        original = self.fx.db.one('SELECT id FROM turns WHERE message_id=(SELECT message_id FROM source_question_drafts WHERE draft_id!=?)',
            (self.fx.fx.draft['id'],))[0]
        self.assertEqual(self.request('GET','/api/manual-delivery-replies?turn_id='+original)[1]['status'],'UNAVAILABLE')
        with self.fx.fx.raw.connect() as source:
            source.execute("UPDATE messages SET time_confidence='low' WHERE message_id=?",(self.message.message_id,))
        data = self.request('GET','/api/manual-delivery-replies?turn_id='+self.fx.args['turn_id'])[1]
        self.assertEqual(data['status'],'READY'); self.assertEqual(data['replies'],[])
        self.assertIn('不代表群内没有消息',data['message'])
        for query in ('','?turn_id=','?turn_id=x&turn_id=y','?turn_id=x&collector_path=C:/private.db'):
            self.assertEqual(self.request('GET','/api/manual-delivery-replies'+query)[0],400)
        self.actor.assert_not_called()


if __name__ == '__main__':
    unittest.main()
