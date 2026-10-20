from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from tools.wecom_cli import main


class GroupAckCliTests(unittest.TestCase):
    """Anonymous temp DBs and fake execution; never uses a real desktop."""
    def setUp(self):
        from helpdesk.storage import Store
        from tests.test_native_group_ack import config
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.database = self.root / 'business.db'
        Store(self.database).close()
        self.delivery = self.root / 'delivery.json'
        self.delivery.write_text(json.dumps({'enabled':True, 'desktop':config()}), encoding='utf-8')
        self.collector = self.root / 'collector.toml'
        self.collector.write_text('[collector]\nprocessing_mode="ACK_ONLY"\nbusiness_database='+json.dumps(self.database.as_posix())+
                                  '\ndatabase='+json.dumps((self.root/'messages.db').as_posix())+'\n', encoding='utf-8')

    def args(self):
        return ['--database',str(self.database),'--delivery-config',str(self.delivery),
                '--collector-config',str(self.collector)]

    def run_cli(self, args):
        with redirect_stdout(StringIO()) as output:
            code = main(args)
        return code, json.loads(output.getvalue())

    def test_status_is_read_only_without_contact_tool_migration_or_desktop(self):
        before = self.database.read_bytes()
        with patch('tools.wecom_cli.WeComTools', side_effect=AssertionError('test-contact entry must not be used')), \
                patch('helpdesk.storage.Store._migrate', side_effect=AssertionError('must not migrate')), \
                patch('helpdesk.wecom_native.NativeWeComTransport', side_effect=AssertionError('must not touch desktop')):
            code, result = self.run_cli(self.args()+['ack-status'])
        self.assertEqual(code, 0)
        self.assertEqual(result['scope'], 'EXISTING_OUTBOX_ONLY')
        self.assertFalse(result['continuous_collection_verified'])
        self.assertEqual(result['runtime']['allowed_purposes'], ['ACK'])
        self.assertEqual(result['runtime']['live_validation'], 'NOT_VERIFIED')
        self.assertEqual(self.database.read_bytes(), before)

    def test_tick_requires_existing_input_and_send_flags(self):
        with patch('helpdesk.automatic_delivery_runtime.AutomaticDeliveryRuntime.tick') as tick:
            code, result = self.run_cli(self.args()+['ack-tick'])
        self.assertEqual((code,result['reason']), (2,'SEND_DISABLED'))
        tick.assert_not_called()

    def test_unknown_is_not_success_or_retried(self):
        with patch('helpdesk.automatic_delivery_runtime.AutomaticDeliveryRuntime.tick',
                   return_value={'state':'SEND_UNKNOWN','task_id':'anonymous'}) as tick, \
                patch('tools.wecom_cli.WeComTools', side_effect=AssertionError('must not use contact tool')):
            code, result = self.run_cli(self.args()+['--enable-input','--enable-send','ack-tick'])
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'SEND_UNKNOWN')
        self.assertFalse(result['automatic_retry'])
        tick.assert_called_once_with()

    def test_missing_database_is_not_created(self):
        missing = self.root/'missing.db'
        code, result = self.run_cli(['--database',str(missing),'--delivery-config',str(self.delivery),'ack-status'])
        self.assertEqual(code, 2)
        self.assertFalse(missing.exists())

    def test_old_schema_status_refuses_migration_and_remains_unchanged(self):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('DELETE FROM schema_migrations WHERE version=6')
            db.commit()
        before = self.database.read_bytes()
        code, result = self.run_cli(self.args()+['ack-status'])
        self.assertEqual((code,result['reason']), (2,'BACKUP_AND_MIGRATION_REQUIRED'))
        self.assertEqual(self.database.read_bytes(), before)

    def test_source_database_mismatch_rejects_before_tick(self):
        self.collector.write_text('[collector]\nprocessing_mode="ACK_ONLY"\nbusiness_database="other.db"\n', encoding='utf-8')
        with patch('helpdesk.automatic_delivery_runtime.AutomaticDeliveryRuntime.tick') as tick:
            code, _ = self.run_cli(self.args()+['--enable-input','--enable-send','ack-tick'])
        self.assertEqual(code, 2)
        tick.assert_not_called()

    def test_test_contact_session_cannot_be_reused_for_group_ack(self):
        with patch('helpdesk.automatic_delivery_runtime.AutomaticDeliveryRuntime.tick') as tick:
            code, _ = self.run_cli(self.args()+['--session','test-contact.json','--enable-input','--enable-send','ack-tick'])
        self.assertEqual(code, 2)
        tick.assert_not_called()

    def test_status_caps_queue_and_omits_answer_and_original_body(self):
        from contextlib import closing
        from helpdesk.storage import Store
        from helpdesk.service import Helpdesk, Incoming
        with closing(Store(self.database)) as db:
            app = Helpdesk(db)
            binding = app.bind('room', 'student', '匿名学生', verified=True)
            for i in range(26):
                incoming = app.ingest(Incoming(binding, 'PRIVATE ORIGINAL '+str(i), platform_id='original-'+str(i)))
            app._outbox(incoming.message_id, binding, 'ANSWER', 'PRIVATE ANSWER '+('x'*10000))
        code, result = self.run_cli(self.args()+['ack-status'])
        self.assertEqual(code, 0)
        self.assertEqual(result['ack_task_count'], 26)
        self.assertEqual(len(result['ack_tasks']), 20)
        self.assertTrue(result['truncated'])
        self.assertTrue(all(task['within_configured_targets'] for task in result['ack_tasks']))
        self.assertNotIn('PRIVATE ANSWER', json.dumps(result))
        self.assertNotIn('PRIVATE ORIGINAL', json.dumps(result))


class CliTests(unittest.TestCase):
    def test_unverified_sends_and_unexpected_status_fail_without_replay(self):
        for status in ('SEND_UNKNOWN','SENDING','FAILED','CANCELLED','PENDING','UNEXPECTED'):
            service=Mock();service.call.return_value={'status':status,'resubmitted':False}
            with patch('tools.wecom_cli.WeComTools',return_value=service),redirect_stdout(StringIO()):
                self.assertEqual(main(['--database','db','--enable-input','--enable-send','send','id']),2)
            service.call.assert_called_once_with('send_reply',outbox_id='id')

    def test_verified_send_exit_success(self):
        service=Mock();service.call.return_value={'status':'SENT_UI_CONFIRMED'}
        with patch('tools.wecom_cli.WeComTools',return_value=service),redirect_stdout(StringIO()):
            self.assertEqual(main(['--database','db','--enable-input','--enable-send','send','id']),0)

    def test_watch_unknown_status_does_not_continue_or_sleep(self):
        service=Mock();service.call.return_value={'status':'UNEXPECTED'}
        with patch('tools.wecom_cli.WeComTools',return_value=service),redirect_stdout(StringIO()), \
                patch('tools.wecom_cli.time.sleep') as sleep:
            self.assertEqual(main(['--database','db','watch','chat']),2)
        service.call.assert_called_once();sleep.assert_not_called()

    def test_interrupt_exit_is_not_success(self):
        service=Mock();service.call.side_effect=KeyboardInterrupt
        with patch('tools.wecom_cli.WeComTools',return_value=service),redirect_stdout(StringIO()) as output:
            self.assertEqual(main(['--database','db','watch','chat']),130)
        self.assertEqual(json.loads(output.getvalue())['status'],'STOPPED')

    def test_watch_stops_on_failure_without_retry(self):
        service=Mock();service.call.side_effect=[{'status':'NO_NEW_OBSERVATIONS'},
            {'status':'UNAVAILABLE','reason':'WINDOW_CHANGED'}]
        with patch('tools.wecom_cli.WeComTools',return_value=service),redirect_stdout(StringIO()) as output, \
                patch('tools.wecom_cli.time.sleep') as sleep:
            code=main(['--database','db','--enable-input','watch','bound-chat','--iterations','3'])
        self.assertEqual(code,2)
        self.assertEqual(service.call.call_count,2)
        sleep.assert_called_once_with(5)
        self.assertEqual(len(output.getvalue().splitlines()),2)

    def test_watch_limits_checked_before_service_creation(self):
        with patch('tools.wecom_cli.WeComTools') as factory,patch('sys.stderr',StringIO()):
            with self.assertRaises(SystemExit):
                main(['--database','db','watch','chat','--iterations','0'])
        factory.assert_not_called()

    def test_uses_same_service_and_exact_bound_id(self):
        service=Mock();service.call.return_value={'status':'PREPARED_VERIFIED','sent':False}
        with patch('tools.wecom_cli.WeComTools',return_value=service) as factory,redirect_stdout(StringIO()) as output:
            code=main(['--database','existing.sqlite','--enable-input','prepare','original-outbox'])
        self.assertEqual(code,0)
        self.assertFalse(factory.call_args.kwargs['allow_send'])
        service.call.assert_called_once_with('prepare_reply',outbox_id='original-outbox')
        self.assertFalse(json.loads(output.getvalue())['sent'])

    def test_unavailable_is_failure_and_never_retried(self):
        service=Mock();service.call.return_value={'status':'UNAVAILABLE','reason':'SEND_DISABLED'}
        with patch('tools.wecom_cli.WeComTools',return_value=service),redirect_stdout(StringIO()):
            self.assertEqual(main(['--database','existing.sqlite','send','id']),2)
        service.call.assert_called_once_with('send_reply',outbox_id='id')

    def test_invalid_configuration_does_not_echo_private_error(self):
        with patch('tools.wecom_cli.WeComTools',side_effect=ValueError('private secret')),redirect_stdout(StringIO()) as output:
            self.assertEqual(main(['--database','existing.sqlite','state']),2)
        self.assertNotIn('private secret',output.getvalue())

    def test_arbitrary_command_and_recipient_are_not_arguments(self):
        with patch('tools.wecom_cli.WeComTools') as factory,redirect_stdout(StringIO()),patch('sys.stderr',StringIO()):
            with self.assertRaises(SystemExit):main(['--database','db','send','id','--recipient','other'])
        factory.assert_not_called()
