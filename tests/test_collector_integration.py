import tempfile
from base64 import b64decode
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from dataclasses import replace
from hashlib import sha256
from concurrent.futures import ThreadPoolExecutor
from helpdesk.collector_storage import CollectorStore
from helpdesk.collector_dispatch import CollectorDispatcher
from helpdesk.collector_cli import status
from helpdesk.gui_message_source import GUIMessageSource
from helpdesk.message_sources import MessageBatch, NormalizedMessage, SyncMode, normalize_sent_time
from helpdesk.message_sync import SyncEngine
from helpdesk.storage import Store
from helpdesk.service import Helpdesk, Incoming
from helpdesk.domain import Intent, Question, Option
from helpdesk.performance import PerformanceLedger


class CollectorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.collector = CollectorStore(Path(self.tmp.name) / "messages.db")
        self.business = Store(Path(self.tmp.name) / "business.db")
        self.binding = Helpdesk(self.business).bind("room", "student", "学生", verified=True)
        self.dispatch = CollectorDispatcher(self.collector, self.business, processing_mode="CASE_RESOLUTION")

    def tearDown(self):
        self.business.close()
        self.tmp.cleanup()

    def ingest(self, source_id, sent="2026-09-30T22:58:00+08:00", mode=SyncMode.LIVE, text="题目"):
        utc, local = normalize_sent_time(sent)
        message = NormalizedMessage(source_type="wecom_archive", source_message_id=source_id,
            room_id="room", sender_id="student", raw_content=text, normalized_text=text,
            sent_at_raw=sent, sent_at_utc=utc, sent_at_local=local,
            ingested_at="2026-09-30T23:05:00+08:00")
        state = self.collector.get_sync_state("archive", mode)
        self.collector.persist_batch("archive", mode, MessageBatch((message,), source_id), state["cursor"])
        self.dispatch.drain()
        return message, self.business.one("SELECT * FROM collector_answer_tasks WHERE collector_message_id=?", (message.message_id,))

    def decision(self, intent=Intent.NEW, **kwargs):
        q = Question("12", "stem", "stem", tuple(Option.confirmed(label, text, i, "verified")
            for i, (label, text) in enumerate(zip("ABCD", ("first", "second", "third", "fourth")))), "verified")
        return Incoming(self.binding, "untrusted replacement", intent=intent,
            verified_question=q if intent == Intent.NEW else None, verified_material="passage", **kwargs)

    def resolve(self, task, decision):
        return self.dispatch.resolve(task["id"], decision, reviewer="case-resolver", rationale="source and case reviewed", confidence=1)

    def test_live_is_real_pending_task_no_collector_reply_and_send_time_preserved(self):
        message, task = self.ingest("live")
        self.assertEqual(task["state"], "PENDING_RESOLUTION")
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 0)
        outcome = self.resolve(task, self.decision())
        business = self.business.one("SELECT * FROM messages WHERE id=?", (outcome.message_id,))
        self.assertEqual(business["source_sent_at"], message.sent_at_local)
        self.assertEqual(business["raw_text"], "题目")
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM turns")[0], 1)
        unit = PerformanceLedger(self.business).create_unit(outcome.message_id, "语法填空", scope_key="before-23",
            grouping_reason="verified source time", question_id=outcome.question_id)
        self.assertEqual(self.business.one("SELECT category FROM performance_units WHERE id=?", (unit,))[0], "REGULAR")

    def test_backfill_resolution_never_creates_ack_or_other_outbox(self):
        _, task = self.ingest("history", mode=SyncMode.BACKFILL)
        self.assertEqual(task["state"], "HISTORY_ONLY")
        self.resolve(task, self.decision())
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 0)

    def test_event_replay_and_resolver_replay_idempotent(self):
        message, task = self.ingest("stable")
        self.resolve(task, self.decision())
        with self.collector.connect() as db:
            db.execute("UPDATE events SET processed_at=NULL")
        self.dispatch.drain()
        self.resolve(task, self.decision())
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM collector_answer_tasks")[0], 1)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM messages")[0], 1)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM turns")[0], 1)

    def test_followup_does_not_change_first_question_time(self):
        first, task = self.ingest("first", "2026-09-30T22:50:00+08:00")
        outcome = self.resolve(task, self.decision())
        ledger = PerformanceLedger(self.business)
        unit = ledger.create_unit(outcome.message_id, "语法填空", scope_key="passage", grouping_reason="verified same material", question_id=outcome.question_id)
        for i in range(3):
            _, followup = self.ingest("follow" + str(i), "2026-09-30T23:10:00+08:00", text="为什么不选B？")
            answer = self.resolve(followup, self.decision(Intent.FOLLOWUP, question_id=outcome.question_id))
            ledger.link_activity(unit, answer.message_id, question_id=answer.question_id, kind="FOLLOWUP", reason="same question")
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM performance_units")[0], 1)
        row = self.business.one("SELECT * FROM performance_units WHERE id=?", (unit,))
        self.assertEqual(row["question_time"], first.sent_at_local)
        self.assertEqual(row["category"], "REGULAR")

    def test_new_grammar_after_23_is_material_count_even_next_day_resolution(self):
        message, task = self.ingest("night", "2026-09-30T23:01:00+08:00")
        with patch("helpdesk.service.now", return_value="2026-10-01T04:00:00+00:00"):
            outcome = self.resolve(task, self.decision())
        # Completion metadata is a test fixture, not a model run or actual reply.
        with self.collector.connect() as db:
            db.execute("UPDATE messages SET answered_at=? WHERE message_id=?", ("2026-10-01T04:05:00+00:00", message.message_id))
        unit = PerformanceLedger(self.business).create_unit(outcome.message_id, "语法填空", scope_key="night", grouping_reason="independent material", question_id=outcome.question_id)
        row = self.business.one("SELECT * FROM performance_units WHERE id=?", (unit,))
        self.assertEqual((row["category"], row["measure_unit"], row["question_time"]), ("NIGHT", "篇", message.sent_at_local))

    def test_2310_grammar_and_three_blank_followups_remain_one_material(self):
        first, task = self.ingest("grammar-2310", "2026-09-30T23:10:00+08:00")
        outcome = self.resolve(task, self.decision())
        ledger = PerformanceLedger(self.business)
        unit = ledger.create_unit(outcome.message_id, "语法填空", scope_key="grammar-article", grouping_reason="one grammar passage", question_id=outcome.question_id)
        for blank in (1, 2, 3):
            _, task = self.ingest("grammar-blank-" + str(blank), "2026-09-30T23:15:00+08:00", text="第" + str(blank) + "空为什么不对？")
            followup = self.resolve(task, self.decision(Intent.FOLLOWUP, question_id=outcome.question_id))
            ledger.link_activity(unit, followup.message_id, question_id=followup.question_id, kind="FOLLOWUP", reason="same grammar passage blank clarification")
        row = self.business.one("SELECT * FROM performance_units WHERE id=?", (unit,))
        self.assertEqual((row["category"], row["measure_unit"], row["question_time"]), ("NIGHT", "篇", first.sent_at_local))
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM performance_units")[0], 1)

    def test_option_order_change_retains_all_messages_and_one_counting_unit(self):
        _, first = self.ingest("original", "2026-09-30T23:10:00+08:00")
        initial_decision = self.decision()
        outcome = self.resolve(first, initial_decision)
        ledger = PerformanceLedger(self.business)
        unit = ledger.create_unit(outcome.message_id, "阅读", scope_key="article", grouping_reason="single article", question_id=outcome.question_id)
        _, correction = self.ingest("reordered", "2026-09-30T23:15:00+08:00", text="刚才选项顺序发错了")
        original = initial_decision.verified_question
        revised = replace(original, options=tuple(Option.confirmed(label, original.options[3-i].verified_text, i, "verified")
            for i, label in enumerate("ABCD")))
        decision = Incoming(self.binding, "", intent=Intent.CORRECTION, question_id=outcome.question_id, verified_question=revised)
        corrected = self.resolve(correction, decision)
        ledger.link_activity(unit, corrected.message_id, question_id=corrected.question_id, kind="CORRECTION", reason="same problem revised options")
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM performance_units")[0], 1)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM question_versions")[0], 2)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM messages")[0], 2)
        with self.collector.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)

    def test_unknown_identity_review_no_business_message(self):
        utc, local = normalize_sent_time("2026-09-30T23:01:00+08:00")
        message = NormalizedMessage(source_type="wecom_archive", source_message_id="unknown", room_id="other", sender_id="student", sent_at_utc=utc, sent_at_local=local)
        task = self.dispatch.enqueue(message, SyncMode.LIVE)
        self.assertEqual(self.business.one("SELECT state FROM collector_answer_tasks WHERE id=?", (task,))[0], "NEEDS_REVIEW")
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM messages")[0], 0)

    def test_event_auto_reply_policy_double_gate(self):
        _, task = self.ingest("policy")
        with self.collector.connect() as db:
            db.execute("UPDATE events SET auto_reply_allowed=0")
        with self.assertRaises(ValueError):
            self.resolve(task, self.decision())
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 0)

    def test_gui_relative_time_unverified_sender_never_guesses(self):
        observation = {"provenance": "official_windows_mcp", "room_id": "room", "foreground": True,
            "desktop_unlocked": True, "position_continuity": True, "last_position": "p1",
            "messages": [{"text": "question", "display_time": "23:03", "position": "p1"}]}
        source = GUIMessageSource(lambda **kwargs: observation, expected_room_id="room")
        message = source.fetch_page(None, SyncMode.LIVE, 1000).messages[0]
        self.assertIsNone(message.sent_at_utc)
        self.assertEqual((message.source_confidence, message.time_confidence), ("low", "low"))
        observation["foreground"] = False
        with self.assertRaises(RuntimeError):
            source.fetch_page(None, SyncMode.LIVE, 1000)

    def gui_observation(self, *, position="row-1", text="老师，这题为什么选B？", **changes):
        item = dict(sender_id="student", sender_identity_verified=True,
            consecutive_header_verified=True, position=position, text=text,
            sent_at="2026-10-02T10:00:00+08:00", full_time_verified=True, **changes)
        return dict(provenance="official_windows_mcp", room_id="room", room_name="Anonymous English",
            foreground=True, desktop_unlocked=True, position_continuity=True,
            last_position="page-1", evidence={"capture": "anonymous-frame-1"}, messages=[item])

    def test_gui_overlapping_observation_after_restart_queues_one_ack_per_message(self):
        observation = self.gui_observation()
        source = GUIMessageSource(lambda **kwargs: observation, expected_room_id="room")
        first = source.fetch_page(None, SyncMode.LIVE, 10)
        self.collector.persist_batch(source.source_name, SyncMode.LIVE, first, None)
        dispatcher = CollectorDispatcher(self.collector, self.business, processing_mode="ACK_ONLY")
        dispatcher.drain()
        observation.update(last_position="page-2", evidence={"capture": "anonymous-frame-2"},
            messages=[dict(observation["messages"][0]),
                      self.gui_observation(position="row-2", text="老师，第32题为什么选C？")["messages"][0]])
        restarted = GUIMessageSource(lambda **kwargs: observation, expected_room_id="room")
        second = restarted.fetch_page(first.next_cursor, SyncMode.LIVE, 10)
        result = self.collector.persist_batch(source.source_name, SyncMode.LIVE, second, first.next_cursor)
        dispatcher.drain()
        self.assertEqual((len(result.inserted_ids), result.duplicate_count, result.conflict_count), (1, 1, 0))
        with self.collector.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM collector_answer_tasks")[0], 2)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox WHERE purpose='ACK'")[0], 2)
        stored = self.collector.get_message(first.messages[0].message_id)
        self.assertEqual(json.loads(stored["raw_payload"])["observation"], {"capture": "anonymous-frame-1"})
        self.assertEqual(stored["sent_at_local"], "2026-10-02T10:00:00+08:00")
        self.assertIsNone(stored["source_message_id"])
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM performance_units")[0], 0)

    def test_gui_same_logical_position_with_changed_message_retains_original_and_blocks_ack(self):
        observation = self.gui_observation()
        source = GUIMessageSource(lambda **kwargs: observation, expected_room_id="room")
        first = source.fetch_page(None, SyncMode.LIVE, 10)
        self.collector.persist_batch(source.source_name, SyncMode.LIVE, first, None)
        original = dict(observation["messages"][0])
        cursor = first.next_cursor
        for index, changed in enumerate(({"text": "老师，题干更正了，为什么选D？"},
                {"sender_id": "another-student"}, {"sent_at": "2026-10-02T23:10:00+08:00"},
                {"sender_role": "teacher"}, {"context": "different referenced question"},
                {"full_time_verified": 1})):
            with self.subTest(changed=changed):
                observation.update(last_position=f"changed-{index}",
                    evidence={"capture": f"anonymous-changed-{index}"}, messages=[original | changed])
                batch = source.fetch_page(cursor, SyncMode.LIVE, 10)
                result = self.collector.persist_batch(source.source_name, SyncMode.LIVE, batch, cursor)
                cursor = batch.next_cursor
                self.assertEqual((len(result.inserted_ids), result.duplicate_count, result.conflict_count), (0, 1, 1))
        stored = self.collector.get_message(first.messages[0].message_id)
        self.assertEqual((stored["raw_content"], stored["sender_id"], stored["sent_at_local"]),
            (original["text"], "student", original["sent_at"]))
        CollectorDispatcher(self.collector, self.business, processing_mode="ACK_ONLY").drain()
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 0)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM performance_units")[0], 0)

    def test_gui_observation_exception_does_not_apply_to_arbitrary_message_id(self):
        observation = self.gui_observation()
        source = GUIMessageSource(lambda **kw: observation, expected_room_id="room")
        first = source.fetch_page(None, SyncMode.LIVE, 10)
        first = MessageBatch((replace(first.messages[0], message_id="legacy-unverified-id"),), first.next_cursor)
        self.collector.persist_batch(source.source_name, SyncMode.LIVE, first, None)
        observation.update(last_position="page-2", evidence={"capture": "anonymous-frame-2"})
        second = source.fetch_page("page-1", SyncMode.LIVE, 10)
        second = MessageBatch((replace(second.messages[0], message_id="legacy-unverified-id"),), second.next_cursor)
        result = self.collector.persist_batch(source.source_name, SyncMode.LIVE, second, "page-1")
        self.assertEqual((len(result.inserted_ids), result.duplicate_count, result.conflict_count), (0, 1, 1))

    def test_gui_unlocated_messages_remain_separate_unverified_evidence(self):
        observation = self.gui_observation(position=None)
        observation["messages"].append(dict(observation["messages"][0]))
        source = GUIMessageSource(lambda **kw: observation, expected_room_id="room")
        batch = source.fetch_page(None, SyncMode.LIVE, 10)
        result = self.collector.persist_batch(source.source_name, SyncMode.LIVE, batch, None)
        self.assertEqual((len(result.inserted_ids), result.duplicate_count), (2, 0))
        self.assertTrue(all(message.source_confidence == "low" for message in batch.messages))
        CollectorDispatcher(self.collector, self.business, processing_mode="ACK_ONLY").drain()
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 0)

    def test_gui_identical_text_in_distinct_positions_or_groups_is_not_collapsed(self):
        observation = self.gui_observation()
        observation["messages"].append(dict(observation["messages"][0], position="row-2", sender_id="second"))
        first = GUIMessageSource(lambda **kw: observation, expected_room_id="room").fetch_page(None, SyncMode.LIVE, 10)
        self.collector.persist_batch("windows_gui", SyncMode.LIVE, first, None)
        observation.update(room_id="other-room", last_position="page-2")
        second = GUIMessageSource(lambda **kw: observation, expected_room_id="other-room").fetch_page("page-1", SyncMode.LIVE, 10)
        result = self.collector.persist_batch("windows_gui", SyncMode.LIVE, second, "page-1")
        self.assertEqual((len(result.inserted_ids), result.duplicate_count, result.conflict_count), (2, 0, 0))
        with self.collector.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 4)

    def test_gui_duplicate_logical_positions_in_one_batch_stop_before_commit(self):
        observation = self.gui_observation()
        observation["messages"].append(dict(observation["messages"][0], sender_id="second"))
        source = GUIMessageSource(lambda **kw: observation, expected_room_id="room")
        with self.assertRaisesRegex(ValueError, "position"):
            SyncEngine(self.collector, source).sync()
        self.assertIsNone(self.collector.get_sync_state(source.source_name)["cursor"])
        with self.collector.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    def test_gui_missing_original_time_stays_unverified_after_reobservation(self):
        observation = self.gui_observation()
        observation["messages"][0].update(sent_at=None, display_time="23:03", full_time_verified=False)
        source = GUIMessageSource(lambda **kw: observation, expected_room_id="room")
        first = source.fetch_page(None, SyncMode.LIVE, 10)
        self.collector.persist_batch(source.source_name, SyncMode.LIVE, first, None)
        observation.update(last_position="page-2", evidence={"capture": "anonymous-frame-2"})
        second = source.fetch_page("page-1", SyncMode.LIVE, 10)
        result = self.collector.persist_batch(source.source_name, SyncMode.LIVE, second, "page-1")
        self.assertEqual((len(result.inserted_ids), result.duplicate_count, result.conflict_count), (0, 1, 0))
        stored = self.collector.get_message(first.messages[0].message_id)
        self.assertIsNone(stored["sent_at_utc"])
        self.assertEqual(stored["time_confidence"], "low")
        CollectorDispatcher(self.collector, self.business, processing_mode="ACK_ONLY").drain()
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 0)

    def test_gui_teacher_and_self_messages_never_queue_received(self):
        dispatcher = CollectorDispatcher(self.collector, self.business, processing_mode="ACK_ONLY")
        for index, (metadata, reason) in enumerate((
                ({"sender_role": "teacher"}, "TEACHER_MESSAGE_ACK_FORBIDDEN"),
                ({"role": "staff"}, "TEACHER_MESSAGE_ACK_FORBIDDEN"),
                ({"sender_role": None, "role": "teacher"}, "TEACHER_MESSAGE_ACK_FORBIDDEN"),
                ({"sender_role": "", "role": "staff"}, "TEACHER_MESSAGE_ACK_FORBIDDEN"),
                ({"is_self": True}, "SELF_MESSAGE_ACK_FORBIDDEN"))):
            with self.subTest(metadata=metadata):
                position = f"p{index}"
                observation = {"provenance": "official_windows_mcp", "room_id": "room",
                    "foreground": True, "desktop_unlocked": True, "position_continuity": True,
                    "last_position": position, "messages": [{"sender_id": "student",
                    "sender_identity_verified": True, "consecutive_header_verified": True,
                    "position": position, "text": "老师，帮我看一下这题",
                    "sent_at": "2026-10-01T18:00:00+08:00", "full_time_verified": True,
                    **metadata}]}
                message = GUIMessageSource(lambda **kwargs: observation,
                    expected_room_id="room").fetch_page(None, SyncMode.LIVE, 1).messages[0]
                self.collector.persist_batch("windows_gui", SyncMode.LIVE,
                    MessageBatch((message,), position), None if index == 0 else f"p{index-1}")
                dispatcher.drain()
                task = self.business.one("SELECT * FROM collector_answer_tasks WHERE collector_message_id=?",
                    (message.message_id,))
                self.assertEqual((task["state"], task["reason"]), ("ACK_HELD", reason))
                self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 0)
                self.assertEqual(self.business.one("SELECT COUNT(*) FROM messages")[0], 0)

    def test_gui_original_image_survives_raw_store_receipt_and_resolution(self):
        root = Path(self.tmp.name) / "original-media"
        root.mkdir()
        image = root / "question.png"
        image.write_bytes(b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="))
        digest = sha256(image.read_bytes()).hexdigest()
        observation = {"provenance": "official_windows_mcp", "room_id": "room",
            "foreground": True, "desktop_unlocked": True, "position_continuity": True,
            "last_position": "image-p1", "messages": [{"sender_id": "student",
            "sender_identity_verified": True, "consecutive_header_verified": True,
            "position": "image-p1", "text": "老师，帮我看一下这题", "message_type": "image",
            "sent_at": "2026-10-01T18:00:00+08:00", "full_time_verified": True,
            "local_media_path": str(image), "media_hash": digest}]}
        source = GUIMessageSource(lambda **kwargs: observation, expected_room_id="room", attachment_root=root)
        message = source.fetch_page(None, SyncMode.LIVE, 1).messages[0]
        self.collector.persist_batch("windows_gui", SyncMode.LIVE, MessageBatch((message,), "image-p1"), None)
        dispatcher = CollectorDispatcher(self.collector, self.business, processing_mode="ACK_ONLY")
        dispatcher.drain()
        task = self.business.one("SELECT * FROM collector_answer_tasks WHERE collector_message_id=?", (message.message_id,))
        stored = self.collector.get_message(message.message_id)
        self.assertEqual((stored["local_media_path"], stored["media_hash"]), (str(image), digest))
        with self.assertRaises(ValueError):
            self.resolve(task, self.decision())
        decision = self.dispatch.received_incoming(task["id"], intent=Intent.NEW,
            verified_question=self.decision().verified_question, raw_material="passage", verified_material="passage")
        outcome = self.resolve(task, decision)
        receipt = self.business.one("SELECT * FROM messages WHERE id=?", (outcome.message_id,))
        original, = json.loads(receipt["attachments"])
        self.assertEqual((original["path"], original["sha256"]), (str(image.resolve()), digest))
        self.assertEqual(receipt["source_sent_at"], message.sent_at_local)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 1)
        image.write_bytes(b"changed fixture image")
        self.assertNotIn("path", dispatcher.source_attachments(stored)[0])

    def test_gui_media_outside_approved_directory_rejected_before_file_read(self):
        root = Path(self.tmp.name) / "approved-media"
        root.mkdir()
        observation = {"provenance": "official_windows_mcp", "room_id": "room",
            "foreground": True, "desktop_unlocked": True, "position_continuity": True,
            "last_position": "p1", "messages": [{"position": "p1", "message_type": "image",
                "local_media_path": str(Path(self.tmp.name) / "outside.png")}]}
        for approved in (None, root):
            with self.subTest(approved=approved):
                source = GUIMessageSource(lambda **kwargs: observation, expected_room_id="room", attachment_root=approved)
                with patch.object(Path, "read_bytes", side_effect=AssertionError("out-of-scope media must not be read")):
                    with self.assertRaises(ValueError): source.fetch_page(None, SyncMode.LIVE, 1)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM messages")[0], 0)

    def test_successful_empty_sync_differs_from_failure(self):
        class Empty:
            source_name = "empty"
            def fetch_page(self, cursor, mode, page_size):
                return MessageBatch((), cursor)
        SyncEngine(self.collector, Empty()).sync()
        self.assertEqual(status(self.collector, source_name="empty")["health"], "HEALTHY")
        self.collector.record_error("empty", SyncMode.LIVE, "network error")
        self.assertEqual(status(self.collector, source_name="empty")["health"], "FAILED")

    def test_ack_only_live_question_queues_received_without_teaching(self):
        self.dispatch = CollectorDispatcher(self.collector, self.business)
        message, task = self.ingest("ack-live", text="老师请问这道题怎么做？")
        self.assertEqual(task["state"], "ACK_QUEUED")
        ack = self.business.one("SELECT * FROM outbox")
        self.assertEqual((ack["purpose"], ack["body"], ack["state"]), ("ACK", "收到", "PENDING"))
        for table in ("cases", "questions", "turns", "runs", "answers", "performance_units"):
            self.assertEqual(self.business.one("SELECT COUNT(*) FROM " + table)[0], 0)
        with self.assertRaises(ValueError):
            self.resolve(task, self.decision())

    def test_ack_only_event_replay_exactly_one_ack(self):
        self.dispatch = CollectorDispatcher(self.collector, self.business)
        self.ingest("ack-replay", text="请问为什么不选B？")
        with self.collector.connect() as db:
            db.execute("UPDATE events SET processed_at=NULL")
        self.dispatch.drain()
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 1)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM messages")[0], 1)

    def test_ack_only_option_inquiry_without_why_queues_receipt_but_not_thanks_or_history(self):
        self.dispatch = CollectorDispatcher(self.collector, self.business)
        inquiries = (
            "老师，这道题的第34问，如果b选项human relationships seem more attractive to children中，把more改为less，这个选项是否可以视为正确？",
            "老师，这题能选B吗？",
            "第34题的B选项对吗？",
        )
        for index, text in enumerate(inquiries):
            with self.subTest(text=text):
                _, task = self.ingest("option-inquiry-" + str(index), text=text)
                self.assertEqual(task["state"], "ACK_QUEUED")
        _, thanks = self.ingest("option-thanks", text="第34题的B选项懂了，谢谢老师")
        _, history = self.ingest("option-history", mode=SyncMode.BACKFILL, text=inquiries[0])
        self.assertEqual(thanks["reason"], "NO_VERIFIED_QUESTION_REQUEST")
        self.assertEqual(history["reason"], "BACKFILL_ACK_FORBIDDEN")
        self.dispatch.drain()
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox WHERE purpose='ACK'")[0], 3)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM cases")[0], 0)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM performance_units")[0], 0)

    def test_two_students_same_image_have_independent_messages_and_cases_in_explicit_resolution_mode(self):
        second_binding = Helpdesk(self.business).bind("room", "student2", "学生2", verified=True)
        utc, local = normalize_sent_time("2026-09-30T23:00:00+08:00")
        first = NormalizedMessage(source_type="wecom_archive", source_message_id="same-image-one", room_id="room", sender_id="student",
            message_type="image", media_id="identical-image", raw_content="same image", sent_at_utc=utc, sent_at_local=local)
        second = replace(first, message_id="second-image", source_message_id="same-image-two", sender_id="student2")
        self.collector.persist_batch("archive", SyncMode.LIVE, MessageBatch((first, second), "2"), None)
        self.dispatch.drain()
        outcomes = []
        for msg, binding in ((first, self.binding), (second, second_binding)):
            task = self.business.one("SELECT * FROM collector_answer_tasks WHERE collector_message_id=?", (msg.message_id,))
            outcomes.append(self.resolve(task, replace(self.decision(), binding_id=binding)))
        self.assertNotEqual(outcomes[0].case_id, outcomes[1].case_id)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM messages")[0], 2)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM cases")[0], 2)
        with self.collector.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)

    def test_ack_only_history_and_nonquestion_do_not_ack(self):
        self.dispatch = CollectorDispatcher(self.collector, self.business)
        _, history = self.ingest("ack-history", mode=SyncMode.BACKFILL, text="请问怎么做？")
        _, chat = self.ingest("ack-chat", text="谢谢老师，晚安")
        self.assertEqual(history["reason"], "BACKFILL_ACK_FORBIDDEN")
        self.assertEqual(chat["reason"], "NO_VERIFIED_QUESTION_REQUEST")
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 0)

    def test_ack_only_self_teacher_conflict_and_unknown_time_are_held(self):
        utc, local = normalize_sent_time("2026-09-30T23:00:00+08:00")
        template = NormalizedMessage(source_type="wecom_archive", source_message_id="self", room_id="room", sender_id="student",
            raw_content="请问怎么做？", normalized_text="请问怎么做？", sent_at_utc=utc, sent_at_local=local)
        dispatcher = CollectorDispatcher(self.collector, self.business, self_sender_ids=("student",))
        task_id = dispatcher.enqueue(template, SyncMode.LIVE)
        self.assertEqual(self.business.one("SELECT reason FROM collector_answer_tasks WHERE id=?", (task_id,))[0], "SELF_MESSAGE_ACK_FORBIDDEN")
        dispatcher = CollectorDispatcher(self.collector, self.business)
        teacher = replace(template, source_message_id="teacher", message_id="teacher", raw_payload={"sender_role": "teacher"})
        conflict = replace(template, source_message_id="conflict", message_id="conflict")
        unknown = replace(template, source_message_id="time", message_id="time", sent_at_utc=None, sent_at_local=None)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 0)
        for message, allowed, reason in ((teacher, True, "TEACHER_MESSAGE_ACK_FORBIDDEN"),
            (conflict, False, "EVENT_POLICY_OR_CONFLICT_FORBIDS_ACK"),
            (unknown, True, "ORIGINAL_TIME_REQUIRES_REVIEW_ACK_HELD")):
            tid = dispatcher.enqueue(message, SyncMode.LIVE, auto_reply_allowed=allowed)
            self.assertEqual(self.business.one("SELECT reason FROM collector_answer_tasks WHERE id=?", (tid,))[0], reason)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 0)

    def test_ack_only_verified_student_image_and_injected_detector(self):
        utc, local = normalize_sent_time("2026-09-30T23:00:00+08:00")
        image = NormalizedMessage(source_type="wecom_archive", source_message_id="image-request", room_id="room", sender_id="student",
            message_type="image", media_id="image", sent_at_utc=utc, sent_at_local=local)
        dispatcher = CollectorDispatcher(self.collector, self.business)
        tid = dispatcher.enqueue(image, SyncMode.LIVE)
        self.assertEqual(self.business.one("SELECT state FROM collector_answer_tasks WHERE id=?", (tid,))[0], "ACK_QUEUED")
        denied = replace(image, source_message_id="not-request", message_id="not-request")
        dispatcher = CollectorDispatcher(self.collector, self.business, question_detector=lambda message: False)
        tid = dispatcher.enqueue(denied, SyncMode.LIVE)
        self.assertEqual(self.business.one("SELECT state FROM collector_answer_tasks WHERE id=?", (tid,))[0], "ACK_HELD")
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 1)

    def test_ack_only_business_commit_before_event_ack_crash_restart(self):
        self.dispatch = CollectorDispatcher(self.collector, self.business)
        original_enqueue = self.dispatch.enqueue
        def committed_then_crashed(*args, **kwargs):
            original_enqueue(*args, **kwargs)
            raise RuntimeError("simulated crash after business commit before source acknowledgement")
        with patch.object(self.dispatch, "enqueue", side_effect=committed_then_crashed):
            with self.assertRaises(RuntimeError):
                self.ingest("commit-crash", text="请问这题怎么做？")
        with self.collector.connect() as db:
            self.assertIsNone(db.execute("SELECT processed_at FROM events").fetchone()[0])
        business_path = self.business.path
        self.business.close()
        self.business = Store(business_path)
        restarted = CollectorDispatcher(CollectorStore(self.collector.path), self.business)
        self.assertEqual(restarted.drain(), 1)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 1)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM collector_answer_tasks")[0], 1)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM audit WHERE event='COLLECTOR_ACK_ONLY_QUEUED'")[0], 1)
        with self.collector.connect() as db:
            self.assertIsNotNone(db.execute("SELECT processed_at FROM events").fetchone()[0])

    def test_concurrent_ack_drainers_do_not_duplicate_ack_or_audit(self):
        self.dispatch = CollectorDispatcher(self.collector, self.business)
        with patch.object(self.dispatch, "enqueue", side_effect=RuntimeError("defer business processing")):
            with self.assertRaises(RuntimeError):
                self.ingest("parallel-ack", text="请问这题怎么做？")
        def worker(_):
            business = Store(self.business.path)
            try:
                return CollectorDispatcher(self.collector, business).drain()
            finally:
                business.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            completed = list(pool.map(worker, range(2)))
        self.assertEqual(sum(completed), 1)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM outbox")[0], 1)
        self.assertEqual(self.business.one("SELECT COUNT(*) FROM audit WHERE event='COLLECTOR_ACK_ONLY_QUEUED'")[0], 1)

    def test_gui_requires_boolean_verification_and_valid_incremental_page(self):
        item = {"sender_id": "student", "sender_identity_verified": "false", "consecutive_header_verified": "false",
            "position": "p1", "text": "请问怎么做？", "sent_at": "2026-09-30T23:00:00+08:00", "full_time_verified": True}
        observation = {"provenance": "official_windows_mcp", "room_id": "room", "foreground": True,
            "desktop_unlocked": True, "position_continuity": True, "last_position": "p1", "messages": [item]}
        source = GUIMessageSource(lambda **kwargs: observation, expected_room_id="room")
        self.assertEqual(source.fetch_page(None, SyncMode.LIVE, 1).messages[0].source_confidence, "low")
        for flag in (False, 1, "true", "false", None):
            item["sender_identity_verified"] = flag
            item["consecutive_header_verified"] = True
            self.assertEqual(source.fetch_page(None, SyncMode.LIVE, 1).messages[0].source_confidence, "low")
        item["sender_identity_verified"] = True
        self.assertEqual(source.fetch_page(None, SyncMode.LIVE, 1).messages[0].source_confidence, "high")
        for value in (None, "", "  ", 123):
            item["sender_id"] = value
            self.assertEqual(source.fetch_page(None, SyncMode.LIVE, 1).messages[0].source_confidence, "low")
        for size in (True, 0, -1, 1.5):
            with self.assertRaises(ValueError):
                source.fetch_page(None, SyncMode.LIVE, size)
        observation["messages"] = [item, item]
        with self.assertRaises(ValueError):
            source.fetch_page(None, SyncMode.LIVE, 1)
        observation["messages"] = [item]
        with self.assertRaises(RuntimeError):
            source.fetch_page("p1", SyncMode.LIVE, 1)


if __name__ == "__main__":
    unittest.main()
