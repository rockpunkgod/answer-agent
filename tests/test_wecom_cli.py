from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from tools.wecom_cli import main


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
