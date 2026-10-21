"""Anonymous workbench integration, isolated subprocess mocked; no real account."""
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
from threading import Thread
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from helpdesk import personal_wechat_polling as bridge
from helpdesk.collector_supervisor import CollectorSupervisor
from helpdesk.collector_storage import CollectorStore
from helpdesk.demo_server import DemoHTTPServer
from helpdesk.message_sources import SyncMode
from helpdesk.message_sync import SyncEngine
from helpdesk.personal_wechat_source import PersonalWeChatSource
from tests.test_personal_wechat_source import Reader,ROOMS


class PersonalWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);private=self.root/'data/private/replica'
        private.mkdir(parents=True);self.private=private
        self.database=private/'messages.sqlite'
        self.json=private/'source.local.json'
        self.json.write_text(json.dumps({'private_root':str(private),'collector_database':str(self.database),
                                        'prototype_only':True,'groups_reviewed':True}))
        python=private/'venv/Scripts/python.exe';python.parent.mkdir(parents=True);python.touch()
        self.config={'collector':{'processing_mode':'ACK_ONLY','database':str(self.database),'poll_seconds':1},
                     'source':{'kind':'personal_wechat_local','replica_config':str(self.json)}}
        self.patch=patch.object(bridge,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)

    def result(self,**kwargs):
        return SimpleNamespace(returncode=0,stdout=json.dumps(dict(status='RAW_COLLECTOR_SYNCED',
            mode='LIVE',desktop_access=False,sending_enabled=False,inserted=1,duplicates=0,**kwargs)))

    def test_bridge_uses_fixed_collection_command_and_no_drain(self):
        poller=bridge.PersonalWeChatPoller(self.config)
        with patch.object(bridge.subprocess,'run',return_value=self.result()) as run:
            result=poller.poll()
        self.assertTrue(result['collection_only']);self.assertFalse(result['drain_enabled'])
        self.assertEqual(result['events_processed'],0)
        command=run.call_args.args[0]
        self.assertEqual(command[0],str(self.private/'venv/Scripts/python.exe'))
        self.assertEqual(Path(command[4]).name,'personal_wechat_collect.py')
        self.assertIn('poll',command);self.assertNotIn('history',command)

    def test_bridge_rejects_database_mismatch_and_desktop_wakeup(self):
        self.config['collector']['database']=str(self.root/'different.sqlite')
        with self.assertRaisesRegex(ValueError,'DATABASE_MUST_MATCH'):bridge.PersonalWeChatPoller(self.config)
        self.config['collector']['database']=str(self.database)
        self.config['collector']['unread_wakeup']={'enabled':True}
        with self.assertRaisesRegex(ValueError,'NO_DESKTOP_WAKEUP'):bridge.PersonalWeChatPoller(self.config)

    def test_config_changes_or_untrusted_child_result_stop_without_dispatch(self):
        poller=bridge.PersonalWeChatPoller(self.config)
        self.json.write_text('{}')
        with patch.object(bridge.subprocess,'run') as run:
            with self.assertRaisesRegex(bridge.PersonalWeChatPollFailed,'CONFIG_CHANGED'):poller.poll()
            run.assert_not_called()
        self.json.write_bytes(poller.config_bytes)
        forged=self.result();value=json.loads(forged.stdout);value['sending_enabled']=True
        forged.stdout=json.dumps(value)
        with patch.object(bridge.subprocess,'run',return_value=forged):
            with self.assertRaisesRegex(bridge.PersonalWeChatPollFailed,'RESULT_INVALID'):poller.poll()

    def test_child_failure_blocks_supervisor_without_automatic_retry(self):
        supervisor=CollectorSupervisor(self.config)
        self.addCleanup(lambda:supervisor.stop(wait_seconds=2))
        with patch.object(bridge.subprocess,'run',return_value=SimpleNamespace(returncode=2,stdout='secret')) as run:
            supervisor.start()
            deadline=time.monotonic()+3
            while supervisor.snapshot()['state'] not in ('BLOCKED','STOPPED') and time.monotonic()<deadline:
                time.sleep(.01)
            state=supervisor.stop(wait_seconds=2)
        self.assertEqual(state['state'],'BLOCKED');self.assertTrue(state['collection_only'])
        run.assert_called_once();self.assertNotIn('secret',json.dumps(state))
        self.assertFalse((self.root/'business.sqlite').exists())

    def test_supervisor_success_preserves_collection_only_and_stops(self):
        self.config['collector']['trigger_mode']='SIGNAL'
        supervisor=CollectorSupervisor(self.config)
        self.addCleanup(lambda:supervisor.stop(wait_seconds=2))
        with patch.object(bridge.subprocess,'run',return_value=self.result()) as run:
            supervisor.start()
            self.assertTrue(supervisor.request_sync()['wake_accepted'])
            deadline=time.monotonic()+3
            while not supervisor.snapshot()['last_result'] and time.monotonic()<deadline:time.sleep(.01)
            state=supervisor.stop(wait_seconds=2)
        run.assert_called_once();self.assertEqual(state['state'],'STOPPED')
        self.assertEqual(state['last_result']['events_processed'],0)
        self.assertEqual(state['logs'][-2]['event'],'LIVE_COLLECTION_COMMITTED')

    def test_workbench_reports_personal_source_and_displays_existing_raw_records(self):
        raw=CollectorStore(self.database)
        source=PersonalWeChatSource(Reader(),account_id='wxid_self',rooms=ROOMS,room_id='100@chatroom')
        SyncEngine(raw,source).sync(SyncMode.BACKFILL)
        toml=self.root/'collector.toml'
        quote=lambda value:json.dumps(str(value))
        toml.write_text('[collector]\nprocessing_mode="ACK_ONLY"\ndatabase='+quote(self.database)+
            '\n[source]\nkind="personal_wechat_local"\nreplica_config='+quote(self.json))
        server=DemoHTTPServer(('127.0.0.1',0),self.root/'business.sqlite',
                              processing_mode='ACK_ONLY',collector_config=toml)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            state=server.collector_snapshot()
            self.assertEqual(state['collection_kind'],'PERSONAL_WECHAT_LOCAL')
            self.assertFalse(state['listener_started_by_workbench'])
            self.assertTrue(state['collection_only']);self.assertEqual(state['pending_ack_count'],0)
            connection=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            try:
                connection.request('GET','/api/native-records')
                response=connection.getresponse();body=json.loads(response.read())
                self.assertEqual(response.status,200);self.assertEqual(body['count'],1)
                record=body['records'][0]
                self.assertEqual(record['source_kind'],'PERSONAL_WECHAT_LOCAL')
                self.assertTrue(record['reliable_timestamp']);self.assertTrue(record['original_message_time'])
                self.assertFalse(record['formal_statistics_eligible'])
                self.assertNotIn('raw_payload',record)
            finally:connection.close()
        finally:server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
