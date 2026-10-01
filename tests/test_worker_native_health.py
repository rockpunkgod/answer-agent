"""Anonymous metadata seam tests; never invokes a Windows API or MCP child."""
from datetime import datetime, timedelta, timezone
import json
import unittest

from helpdesk.worker_contracts import WorkerHealth
from helpdesk.worker_native_health import WindowsMCPHealthProvider
from helpdesk.windows_worker_probe import DESKTOP_STATUS_COMMAND


class FixedNativeHealthTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)
        self.tick = 1
        outer = self
        class API:
            allowed = True
            error = False
            def worker_state(self, worker):
                if self.error: raise TimeoutError('PRIVATE_TRANSPORT_DETAIL')
                return {'worker_id': worker, 'desktop_reads_allowed': self.allowed}
        class MCP:
            uncertain = False
            calls = 0
            fail = False
            def call(self, tool, args):
                self.calls += 1
                outer.assertEqual(tool, 'PowerShell')
                outer.assertEqual(args, {'command': DESKTOP_STATUS_COMMAND, 'timeout': 10})
                if self.fail:
                    self.uncertain = True
                    raise TimeoutError('PRIVATE_NATIVE_DETAIL')
                value = dict(schema_version=1, observed_at=outer.now.isoformat(),
                    session_id=1, wts_state=0, session_flags=1, remote=False,
                    input_desktop='DEFAULT', input_accessible=True, windows={'wxwork': 1, 'edge': 1})
                return {'tool': tool, 'attempt_id': 'anonymous-probe', 'is_error': False,
                    'content': [{'type': 'text', 'text': 'Response: '+json.dumps(value)+'\nStatus Code: 0'}]}
        self.api, self.mcp = API(), MCP()
        self.provider = WindowsMCPHealthProvider(self.mcp, self.api, worker_id='worker-1',
            account_id='account-1', clock=lambda: self.now, monotonic=lambda: self.tick)

    def app(self):
        return WorkerHealth('worker-1','account-1',True,True,True,'HEALTHY',
                            None,'bound-group',None,self.now.isoformat(),False)

    def test_desktop_metadata_without_application_identity_never_enables_gui(self):
        result = self.provider()
        self.assertTrue(result.interactive_desktop)
        self.assertTrue(result.desktop_unlocked)
        self.assertFalse(result.gui_ready)
        self.assertEqual(result.state, 'UNKNOWN')
        self.assertTrue(self.provider.native_metadata_connected)

    def test_only_trusted_separate_application_provider_can_supply_identity(self):
        self.provider.app_provider = self.app
        self.assertTrue(self.provider().gui_ready)
        self.assertEqual(self.provider().observed_scope, 'bound-group')
        self.assertEqual(self.mcp.calls, 1)

    def test_human_or_stop_blocks_even_cached_observation_and_application_read(self):
        self.provider()
        self.api.allowed = False
        self.provider.app_provider = lambda: self.fail('No application read during human ownership')
        result = self.provider()
        self.assertFalse(result.gui_ready)
        self.assertEqual(self.mcp.calls, 1)
        self.assertEqual(self.provider.last_reason, 'DESKTOP_READS_NOT_AUTHORIZED')

    def test_disconnected_server_does_not_probe_desktop(self):
        self.api.error = True
        self.assertFalse(self.provider().connected)
        self.assertEqual(self.mcp.calls, 0)
        self.assertEqual(self.provider.last_reason, 'SERVER_UNAVAILABLE')

    def test_native_timeout_is_not_replayed_and_retains_original_pending(self):
        self.mcp.fail = True
        self.assertTrue(self.provider().native_call_pending)
        self.assertTrue(self.provider().native_call_pending)
        self.assertEqual(self.mcp.calls, 1)
        self.assertEqual(self.provider.last_reason, 'ORIGINAL_NATIVE_RESPONSE_PENDING')

    def test_expired_cache_is_reobserved_using_new_bounded_permission(self):
        self.provider()
        self.now += timedelta(seconds=3)
        self.tick += 3
        self.assertFalse(self.provider().gui_ready)
        self.assertEqual(self.mcp.calls, 2)

    def test_unbound_application_observation_is_rejected(self):
        self.provider.app_provider = lambda: {'state':'HEALTHY','verified':True}
        self.assertFalse(self.provider().gui_ready)
        self.assertEqual(self.provider.last_reason, 'APPLICATION_IDENTITY_UNCONFIRMED')
