"""HTTP acceptance checks for the loopback simulation UI."""
from contextlib import closing
from http.client import HTTPConnection
import json
from io import BytesIO
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from zipfile import ZipFile

from helpdesk.demo_server import DemoHTTPServer
from helpdesk.storage import Store


class DemoHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = DemoHTTPServer(("127.0.0.1", 0), Path(self.temp.name) / "demo.db")
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.token = self.request("GET", "/api/state")[1]["csrf_token"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        data = None if body is None else json.dumps(body).encode("utf-8")
        connection.request(method, path, body=data, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        content_type = response.getheader("Content-Type", "")
        connection.close()
        return status, json.loads(raw) if "application/json" in content_type else raw.decode("utf-8")

    def action(self, name):
        return self.request("POST", "/api/action", {"action": name}, {
            "Content-Type": "application/json", "Origin": self.origin, "X-CSRF-Token": self.token,
        })

    def native_export(self, *, path='/api/native-records/export', headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        connection.request('GET', path, headers=headers or {'X-CSRF-Token': self.token})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def make_native_archive(self):
        from helpdesk.chat_text_archive import archive_clipboard
        base = Path(self.temp.name)
        root = base / 'archives'
        root.mkdir()
        acquisition = 'a' * 32
        raw = '为什么不选B？\r\n[图片]\r\n'
        result = base / (acquisition + '.json')
        result.write_text(json.dumps({'attempt_id': acquisition, 'tool': 'Clipboard',
            'is_error': False, 'content': [{'type': 'text', 'text': 'Clipboard content:\n' + raw}]}), encoding='utf-8')
        (base / ('attempt-' + acquisition + '.json')).write_text(json.dumps({
            'attempt_id': acquisition, 'tool': 'Clipboard', 'arguments': {'mode': 'get'},
            'status': 'TOOL_RETURNED', 'result_path': str(result),
            'started_at': '2026-09-30T03:00:00+00:00'}), encoding='utf-8')
        folder = archive_clipboard(result, root, observed_group='English答疑群（已退出）')
        self.server.native_archive_root = root.resolve()
        return folder, raw

    def test_native_export_download_preserves_original_bytes_partial_scope_and_database(self):
        folder, raw = self.make_native_archive()
        before = self.server.db_path.read_bytes()
        originals = {p: p.read_bytes() for p in folder.iterdir() if p.is_file()}
        status, headers, payload = self.native_export()
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'application/zip')
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        with ZipFile(BytesIO(payload)) as package:
            manifest = json.loads(package.read('导出清单.json'))
            self.assertEqual(manifest['coverage'], 'partial')
            self.assertFalse(manifest['formal_statistics_eligible'])
            self.assertEqual(manifest['collection_count'], 1)
            self.assertIsNone(manifest['question_count'])
            name = next(n for n in package.namelist() if n.endswith('/原始文字记录.txt'))
            self.assertEqual(package.read(name), raw.encode('utf-8'))
        self.assertEqual(self.native_export()[2], payload)
        self.assertEqual(self.server.db_path.read_bytes(), before)
        self.assertEqual(originals, {p: p.read_bytes() for p in originals})
        self.assertIn('下载原始记录导出包', self.request('GET', '/')[1])

    def test_native_export_rejects_unauthorized_paths_tampering_and_concurrent_work(self):
        folder, _ = self.make_native_archive()
        self.assertEqual(self.native_export(headers={'X-CSRF-Token': 'wrong'})[0], 403)
        self.assertEqual(self.native_export(headers={'X-CSRF-Token': self.token, 'Origin': 'https://other.invalid'})[0], 403)
        self.assertEqual(self.native_export(headers={'X-CSRF-Token': self.token, 'Host': 'other.invalid'})[0], 403)
        self.assertEqual(self.native_export(path='/api/native-records/export?root=C:/')[0], 400)
        self.server.native_export_lock.acquire()
        try:
            self.assertEqual(self.native_export()[0], 409)
        finally:
            self.server.native_export_lock.release()
        (folder / '原始文字记录.txt').write_bytes(b'tampered')
        self.assertEqual(self.native_export()[0], 409)
        self.assertEqual((folder / '原始文字记录.txt').read_bytes(), b'tampered')

    def test_native_export_empty_or_oversized_source_is_rejected(self):
        root = Path(self.temp.name) / 'empty'
        root.mkdir()
        self.server.native_archive_root = root.resolve()
        self.assertEqual(self.native_export()[0], 404)
        folder, _ = self.make_native_archive()
        (folder / 'clipboard-attempt.json').write_bytes(b'x' * 65537)
        self.assertEqual(self.native_export()[0], 409)

    def test_page_and_local_security(self):
        code, page = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("SIMULATION", page)
        self.assertIn("不是真实 DeepSeek", page)
        self.assertIn("textContent", self.request("GET", "/app.js")[1])
        with self.assertRaises(ValueError):
            DemoHTTPServer(("0.0.0.0", 0), Path(self.temp.name) / "bad.db")
        self.assertEqual(self.request("POST", "/api/action", {"action": "new_alice"},
                                      {"Content-Type": "application/json", "X-CSRF-Token": self.token})[0], 403)
        self.assertEqual(self.request("POST", "/api/action", {"action": "new_alice"},
                                      {"Content-Type": "application/json", "Origin": self.origin})[0], 403)
        self.assertEqual(self.action("delete_database")[0], 400)
        self.assertEqual(self.request("POST", "/api/action", {"action": "new_alice", "group": "outside"},
                                      {"Content-Type": "application/json", "Origin": self.origin,
                                       "X-CSRF-Token": self.token})[0], 400)
        self.assertEqual(self.request("GET", "/api/state", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/action", {"action": "new_alice"},
                                      {"Host": "attacker.example", "Content-Type": "application/json",
                                       "Origin": self.origin, "X-CSRF-Token": self.token})[0], 403)

    def test_demo_flow_and_unknown_send(self):
        self.assertEqual(self.action("followup_alice")[0], 400)
        for action in ("new_alice", "new_bob", "dispatch_ack", "followup_alice"):
            status, payload = self.action(action)
            self.assertEqual(status, 200, (action, payload))
        state = self.request("GET", "/api/state")[1]["dashboard"]
        self.assertEqual(len(state["questions"]), 2)
        self.assertEqual(len(state["messages"]), 3)
        for action in ("generate", "approve", "dispatch", "subquestion_alice", "correction_alice"):
            status, payload = self.action(action)
            self.assertEqual(status, 200, (action, payload))
        status, rejected = self.action("generate")
        self.assertEqual(status, 200, rejected)
        self.assertEqual(rejected["result"]["reason"], "RECHECK_REQUIRED")
        state = self.request("GET", "/api/state")[1]["dashboard"]
        self.assertTrue(any(row.get("state") == "STALE" for row in state["answers"]))
        self.assertFalse(any(row.get("state") == "PENDING" and row.get("purpose") == "CORRECTION"
                             for row in state["outbox"]))
        for action in ("reorder_alice", "generate", "approve", "dispatch"):
            status, payload = self.action(action)
            self.assertEqual(status, 200, (action, payload))
        state = self.request("GET", "/api/state")[1]["dashboard"]
        self.assertTrue(any(row.get("state") == "SENT_UI_CONFIRMED" and row.get("purpose") == "CORRECTION"
                            for row in state["outbox"]))
        for action in ("dispute_alice",
                       "unknown_alice", "dispatch_unknown", "recover", "stop", "resume"):
            status, payload = self.action(action)
            self.assertEqual(status, 200, (action, payload))
        state = self.request("GET", "/api/state")[1]["dashboard"]
        self.assertTrue(any(row.get("state") == "SEND_UNKNOWN" for row in state["outbox"]))
        self.assertTrue(state["reviews"])
        store = Store(self.server.db_path)
        try:
            self.assertEqual({row["group_key"] for row in store.all("SELECT DISTINCT group_key FROM bindings")},
                             {"SIMULATION_GROUP"})
        finally:
            store.close()

    def test_incomplete_history_keeps_formal_totals_unknown_without_replacing_local_summary(self):
        from helpdesk.history_coverage import record_observed_group, record_coverage
        with closing(Store(self.server.db_path)) as store:
            gid=record_observed_group(store,'English fixture exited group',membership='EXITED',
                evidence={'platform_group_id':'fixture-platform-group-001','fixture':'native record export'},verified=True)
            record_coverage(store,gid,'2026-09-17','2026-09-30',status='PARTIAL',
                actor='fixture-reviewer',evidence='fixture export missing first day')
        status,data=self.request('GET','/api/performance?date=2026-09-30')
        self.assertEqual(status,200)
        self.assertFalse(data['report']['coverage']['complete'])
        self.assertEqual(data['formal_totals'],dict(day_composite_articles=None,
            grammar_listening_actual_questions=None,night_articles=None))
        self.assertEqual(data['report']['summary']['day_composite_articles'],0)
        self.assertEqual(data['summary_scope'],'CONFIRMED_LOCAL_SUBSET')

    def test_complete_verified_history_exposes_numeric_formal_totals(self):
        from helpdesk.history_coverage import record_observed_group, record_coverage
        from helpdesk.storage import now
        with closing(Store(self.server.db_path)) as store:
            gid=record_observed_group(store,'English fixture full group',membership='ACTIVE',
                evidence={'platform_group_id':'fixture-platform-group-002','fixture':'native record export'},verified=True)
            record_coverage(store,gid,'2026-09-17','2026-09-30',status='COMPLETE',
                actor='fixture-reviewer',evidence='fixture native export, verified empty student activity')
            store.execute('INSERT INTO history_inventory_reviews VALUES(?,?,?,?,?)',
                ('fixture-inventory-review',1,'fixture-reviewer','fixture all-group inventory verified',now()))
        status,data=self.request('GET','/api/performance?date=2026-09-30')
        self.assertEqual(status,200);self.assertTrue(data['report']['coverage']['complete'])
        self.assertEqual(data['formal_totals'],dict(day_composite_articles=0,
            grammar_listening_actual_questions=0,night_articles=0))
        for key,value in data['formal_totals'].items():
            self.assertIsInstance(value,(int,float));self.assertEqual(value,data['report']['summary'][key])

    def test_native_records_preserve_raw_text_and_unknown_metadata_without_business_changes(self):
        from helpdesk.chat_text_archive import archive_clipboard
        from helpdesk.native_intake import index_native_records
        base = Path(self.temp.name)
        acquisition = 'a' * 32
        result = base / 'clipboard.json'
        original = '<script>alert(1)</script>\n为什么不选B？'
        result.write_text(json.dumps({'attempt_id': acquisition, 'tool': 'Clipboard',
            'is_error': False, 'content': [{'type': 'text', 'text': 'Clipboard content:\n' + original}]}), encoding='utf-8')
        (base / f'attempt-{acquisition}.json').write_text(json.dumps({
            'attempt_id': acquisition, 'tool': 'Clipboard', 'arguments': {'mode': 'get'},
            'status': 'TOOL_RETURNED', 'result_path': str(result),
            'started_at': '2026-09-30T03:00:00+00:00'}), encoding='utf-8')
        archive_clipboard(result, base / 'archives', observed_group='English 已退出测试群')
        with closing(Store(self.server.db_path)) as store:
            index_native_records(store, base / 'archives')
            before = {table: store.one(f'SELECT COUNT(*) FROM {table}')[0]
                      for table in ('messages', 'outbox', 'performance_units')}
        for _ in range(2):
            status, data = self.request('GET', '/api/native-records')
            self.assertEqual(status, 200)
            self.assertEqual(data['count'], 1)
            self.assertFalse(data['coverage_complete'])
            self.assertFalse(data['formal_statistics_eligible'])
            row, = data['records']
            self.assertEqual(row['original_text'], original)
            self.assertIsNone(row['original_message_time'])
            self.assertIsNone(row['sender'])
        with closing(Store(self.server.db_path)) as store:
            self.assertEqual(before, {table: store.one(f'SELECT COUNT(*) FROM {table}')[0]
                                     for table in before})

    def test_operator_http_draft_review_idempotency_and_no_implicit_generation(self):
        headers = {'Content-Type': 'application/json', 'Origin': self.origin, 'X-CSRF-Token': self.token}
        payload = {'passage': 'Local fixture passage.', 'stem': 'Why did he return home?',
                   'number': '12', 'question_type': '阅读理解',
                   'options': {'A': 'To visit a friend.', 'B': 'To find a job.',
                               'C': 'To study.', 'D': 'To look after his mother.'}}
        def post(body):
            return self.request('POST', '/api/operator-tasks', body, headers)
        status, data = post({'action': 'create', 'request_id': 'one', 'payload': payload})
        self.assertEqual(status, 200)
        draft = data['result']
        self.assertEqual(post({'action': 'create', 'request_id': 'one', 'payload': payload})[1]['result']['id'], draft['id'])
        with closing(Store(self.server.db_path)) as store:
            self.assertEqual(store.one('SELECT COUNT(*) FROM messages')[0], 0)
        body = {'action': 'review', 'draft_id': draft['id'], 'revision': draft['revision'],
                'reviewer': 'fixture reviewer', 'source_evidence': 'explicit local fixture'}
        status, data = post(body)
        self.assertEqual(status, 200)
        task = data['result']
        self.assertEqual(post(body)[1]['result']['id'], task['id'])
        self.assertEqual(post({'action': 'freeze', 'task_id': task['id']})[0], 400)
        self.assertEqual(post({'action': 'create', 'request_id': 'bad',
            'payload': {**payload, 'attachments': [{'path': 'C:/private.txt'}]}})[0], 400)
        self.assertEqual(self.request('POST', '/api/operator-tasks', body)[0], 403)
        status, listing = self.request('GET', '/api/operator-tasks')
        self.assertEqual(status, 200)
        self.assertEqual(len(listing['drafts']), 1)
        self.assertFalse(listing['formal_statistics_eligible'])
        with closing(Store(self.server.db_path)) as store:
            self.assertEqual(store.one('SELECT COUNT(*) FROM messages')[0], 1)
            self.assertEqual(store.one('SELECT source FROM messages')[0], 'OPERATOR_TEST')
            self.assertEqual(store.one('SELECT COUNT(*) FROM runs')[0], 0)
            self.assertEqual(store.one('SELECT COUNT(*) FROM performance_units')[0], 0)
            self.assertEqual(store.one('SELECT COUNT(*) FROM delivery_checks')[0], 0)

    def test_performance_preview_never_counts_simulated_answer_as_official(self):
        from datetime import datetime
        from helpdesk.performance_rules import business_zone
        for action in ("new_alice", "generate", "approve", "dispatch"):
            self.assertEqual(self.action(action)[0], 200)
        day = datetime.now(business_zone()).date().isoformat()
        status, data = self.request("GET", "/api/performance?date=" + day)
        self.assertEqual(status, 200)
        self.assertEqual(data["report"]["summary"]["night_articles"], 0)
        self.assertGreater(data["report"]["summary"]["pending_records"], 0)
        self.assertEqual(self.request("GET", "/api/performance?date=not-a-date")[0], 400)


if __name__ == "__main__":
    unittest.main()
