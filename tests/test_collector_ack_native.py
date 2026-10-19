"""Backend selection and per-input guards; no live desktop or source claims."""
import unittest
from unittest.mock import MagicMock,Mock,patch

from helpdesk.collector_test_ack_cli import ack_connection
from helpdesk.wecom_tools import PinnedDesktop


class NativeAckConnectionTests(unittest.TestCase):
    def setUp(self):
        self.pin={'observation_mode':'fixed_foreground_probe','display_index':1,
                  'display_bounds':[0,0,2560,1440]}
        self.store=Mock();self.store.one.return_value={'id':'ack','state':'PENDING'}
        self.flow=Mock();self.flow._stopped.return_value=False
        self.native=MagicMock();self.native.__enter__.return_value=self.native

    def test_native_never_starts_windows_mcp_and_revalidates_before_input(self):
        with patch('helpdesk.wecom_native.NativeWeComTransport',return_value=self.native) as native, \
                patch('helpdesk.collector_test_ack_cli.CollectorTestAckWorkflow',return_value=self.flow), \
                patch('helpdesk.collector_test_ack_cli.MCPProcess',side_effect=AssertionError('Windows-MCP started')):
            with ack_connection('native',self.pin,self.store,Mock(),'ack') as (desktop,flow):
                self.assertIsInstance(desktop,PinnedDesktop)
                self.assertIs(flow,self.flow)
                guard=native.call_args.kwargs['guard']
                guard()
                self.flow._validate.assert_called_once_with({'id':'ack','state':'PENDING'})
                self.flow._stopped.return_value=True
                with self.assertRaisesRegex(ValueError,'STOPPED'):guard()
                self.flow._stopped.return_value=False
                self.store.one.return_value={'id':'ack','state':'SENT_UI_CONFIRMED'}
                with self.assertRaisesRegex(ValueError,'STATE_CHANGED'):guard()

    def test_source_validation_failure_is_not_replaced_by_native_permission(self):
        self.flow._validate.side_effect=ValueError('VERIFIED_NATIVE_SOURCE_CAPTURE_REQUIRED')
        with patch('helpdesk.wecom_native.NativeWeComTransport',return_value=self.native) as native, \
                patch('helpdesk.collector_test_ack_cli.CollectorTestAckWorkflow',return_value=self.flow):
            with ack_connection('native',self.pin,self.store,Mock(),'ack'):
                with self.assertRaisesRegex(ValueError,'SOURCE_CAPTURE_REQUIRED'):
                    native.call_args.kwargs['guard']()

    def test_missing_display_review_never_constructs_transport(self):
        with patch('helpdesk.wecom_native.NativeWeComTransport') as native:
            with self.assertRaisesRegex(ValueError,'DISPLAY_PIN_REQUIRED'):
                with ack_connection('native',{},self.store,Mock(),'ack'):pass
        native.assert_not_called()
