"""Offline collection runner tests; never initializes a client or desktop."""
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import personal_wechat_collect as cli
from helpdesk.personal_wechat_replica import load_read_modules


class CollectionRunnerTests(unittest.TestCase):
    def test_retired_gui_operations_are_rejected_before_loading_account(self):
        from tools.personal_wechat_replica_cli import run
        for operation in ('probe-accessibility','locate-image','send'):
            with self.assertRaisesRegex(ValueError,'OPERATION_NOT_SUPPORTED'):
                run({},operation)

    def test_once_uses_shared_storage_without_desktop_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            config=Path(folder)/'source.json';config.write_text('{}')
            result={'status':'RAW_COLLECTOR_SYNCED','passes':[[
                {'inserted':2,'duplicates':1,'conflicts':0,'has_more':False}]]}
            with patch('tools.personal_wechat_replica_cli.run',return_value=result) as run:
                summary=cli.collect_once(config,'LIVE')
            run.assert_called_once_with({},'sync',mode='LIVE')
            self.assertEqual(summary['inserted'],2)
            self.assertFalse(summary['desktop_access']);self.assertFalse(summary['sending_enabled'])
            result['passes'][0][0]['has_more']=True
            with patch('tools.personal_wechat_replica_cli.run',return_value=result):
                with self.assertRaisesRegex(RuntimeError,'INCOMPLETE'):cli.collect_once(config,'LIVE')

    def test_watch_fresh_process_per_poll_and_bounded_cycles(self):
        child=SimpleNamespace(returncode=0,stdout=json.dumps({'status':'RAW_COLLECTOR_SYNCED'}))
        with patch.object(cli.subprocess,'run',return_value=child) as run,patch.object(cli.time,'sleep') as sleep,redirect_stdout(io.StringIO()):
            self.assertEqual(cli.watch('source.json',cycles=2,interval=3),0)
        self.assertEqual(run.call_count,2);sleep.assert_called_once_with(3)
        args=run.call_args.args[0]
        self.assertEqual(args[0],sys.executable);self.assertIn('poll',args)
        self.assertNotIn('history',args)

    def test_failed_timeout_or_invalid_poll_stops_without_retry(self):
        for child in (SimpleNamespace(returncode=2,stdout='private exception'),
                      SimpleNamespace(returncode=0,stdout='invalid'),
                      subprocess.TimeoutExpired('fixture',1)):
            with self.subTest(child=type(child).__name__):
                kwargs={'side_effect':child} if isinstance(child,Exception) else {'return_value':child}
                with patch.object(cli.subprocess,'run',**kwargs) as run,patch.object(cli.time,'sleep') as sleep,redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(cli.watch('missing.json',cycles=3),2)
                run.assert_called_once();sleep.assert_not_called()
                self.assertNotIn('private exception',output.getvalue())

    def test_main_routes_history_and_live_and_sanitizes_errors(self):
        for operation,mode in (('history','BACKFILL'),('poll','LIVE')):
            with patch.object(cli,'collect_once',return_value={}) as once,redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main([operation,'--config','source.json']),0)
            once.assert_called_once_with(Path('source.json'),mode)
        with patch.object(cli,'collect_once',side_effect=ValueError('private secret')),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['poll','--config','missing.json']),2)
        self.assertNotIn('private secret',output.getvalue())

    def test_invalid_watch_bounds_never_start_child(self):
        with patch.object(cli.subprocess,'run') as run:
            for kwargs in ({'cycles':0},{'interval':0},{'timeout':601}):
                with self.assertRaises(ValueError):cli.watch('unused',**kwargs)
            run.assert_not_called()

    def test_failure_replaces_old_success_without_exposing_exception(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);private=root/'data/private/collector';private.mkdir(parents=True)
            config=root/'source.json'
            config.write_text(json.dumps({'private_root':str(private),'prototype_only':True}))
            status=private/'last-result.local.json';status.write_text('{"status":"OLD_SUCCESS"}')
            with patch.object(cli,'ROOT',root),patch.object(cli,'collect_once',side_effect=OSError('secret')),redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['poll','--config',str(config)]),2)
            self.assertEqual(json.loads(status.read_text())['status'],'COLLECTION_FAILED')
            self.assertNotIn('secret',status.read_text())

    def test_database_only_loader_does_not_require_or_import_media_or_desktop(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'wechatauto').mkdir()
            content=b'DATABASE_ONLY=True\n';(root/'wechatauto/db.py').write_bytes(content)
            original_path=list(sys.meta_path)
            # Restore all module registrations/import hooks after the isolated fixture.
            try:
                with patch.dict(sys.modules),patch('helpdesk.personal_wechat_replica.HASHES',{'db.py':sha256(content).hexdigest()}):
                    db,media=load_read_modules(root,include_media=False)
                    self.assertTrue(db.DATABASE_ONLY);self.assertIsNone(media)
                    self.assertNotIn('wechatauto.media',sys.modules)
                    for name in ('wechatauto.wx','wechatauto.guia','wechatauto.media'):
                        with self.assertRaisesRegex(ImportError,'FORBIDDEN'):__import__(name)
            finally:sys.meta_path[:]=original_path


if __name__=='__main__':unittest.main()
