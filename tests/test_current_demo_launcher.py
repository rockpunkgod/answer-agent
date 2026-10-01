"""Run the Windows entry point against anonymous HTTP state; never launch desktop tools."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from threading import Thread
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe') if os.name == 'nt' else None


@unittest.skipUnless(POWERSHELL, 'Windows PowerShell is required to exercise the Windows launcher')
class CurrentDemoLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.launcher = self.root / 'run_current_demo.ps1'
        shutil.copyfile(ROOT / 'run_current_demo.ps1', self.launcher)
        self.arguments = self.root / 'python-arguments.json'
        self.harness = self.root / 'harness.ps1'
        self.harness.write_text('''param([string]$Launcher, [int]$Port, [string]$RecordFile, [string]$SourceReviewManifest, [switch]$NoAutoCollect)
$ErrorActionPreference = 'Stop'
function global:python {
    ConvertTo-Json -InputObject @($args) -Compress | Set-Content -LiteralPath $RecordFile -Encoding UTF8
    $global:LASTEXITCODE = 0
}
& $Launcher -Port $Port -SourceReviewManifest $SourceReviewManifest -NoAutoCollect:$NoAutoCollect
''', encoding='ascii')
        self.posts = []

    def serve(self, state=None, *, status=200):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = json.dumps(state or {'error': 'unverified service'}).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                owner.posts.append(self.path)
                self.send_error(500)

            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown(); server.server_close(); thread.join(timeout=3)
        self.addCleanup(cleanup)
        return server.server_port

    def invoke(self, port, *, no_auto_collect=False, manifest=None):
        command = [POWERSHELL, '-NoProfile', '-NonInteractive', '-File', str(self.harness),
                   '-Launcher', str(self.launcher), '-Port', str(port), '-RecordFile', str(self.arguments)]
        if no_auto_collect: command.append('-NoAutoCollect')
        if manifest: command += ['-SourceReviewManifest', str(manifest)]
        return subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)

    def free_port(self):
        with socket.socket() as candidate:
            candidate.bind(('127.0.0.1', 0))
            return candidate.getsockname()[1]

    def test_reuse_with_missing_optional_files_does_not_restart_a_paused_collector(self):
        port = self.serve({'processing_mode': 'ACK_ONLY',
            'collector': {'collection_kind': 'NATIVE_CLIPBOARD_IMPORT', 'control': {'worker_alive': False}}})
        result = self.invoke(port)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Current demo is available', result.stdout)
        self.assertIn('Collection is not running', result.stdout)
        self.assertEqual(self.posts, [])
        self.assertFalse(self.arguments.exists())
        self.assertFalse((self.root / 'data').exists())

    def test_reuse_recognizes_the_unconfigured_local_workbench(self):
        port = self.serve({'application': 'wecom-english-helpdesk', 'processing_mode': 'ACK_ONLY',
            'collector': {'configured': False, 'collection_kind': 'OFFICIAL_ARCHIVE', 'control': {'worker_alive': False}}})
        result = self.invoke(port)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.posts, [])
        self.assertFalse(self.arguments.exists())

    def test_missing_optional_files_prepare_local_business_mode_without_simulation_or_collection(self):
        result = self.invoke(self.free_port())
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(self.arguments.read_text(encoding='utf-8-sig'))
        self.assertEqual(arguments[arguments.index('--processing-mode') + 1], 'ACK_ONLY')
        self.assertEqual(arguments[arguments.index('--db') + 1], 'data/native-demo.db')
        self.assertIn('--enable-performance', arguments)
        self.assertIn('--worker-boundary', arguments)
        for disabled in ('--auto-start-collector', '--collector-config', '--answer-review-root', '--real-config'):
            self.assertNotIn(disabled, arguments)
        self.assertIn('Opening the local workbench only', result.stdout)

    def test_configured_collection_can_be_left_stopped_on_cold_start(self):
        config = self.root / 'data/private/windows-native-demo/collector.local.toml'
        config.parent.mkdir(parents=True)
        config.write_text('# anonymous launcher fixture', encoding='utf-8')
        result = self.invoke(self.free_port(), no_auto_collect=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(self.arguments.read_text(encoding='utf-8-sig'))
        self.assertIn('--collector-config', arguments)
        self.assertNotIn('--auto-start-collector', arguments)

    def test_an_unverified_listener_does_not_launch_a_duplicate_or_create_a_database(self):
        for status, state in ((503, None), (200, {'processing_mode': 'COMPATIBILITY'})):
            with self.subTest(status=status):
                result = self.invoke(self.serve(state, status=status))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.arguments.exists())
                self.assertFalse((self.root / 'data').exists())
                self.assertEqual(self.posts, [])

    def test_explicit_source_review_manifest_is_forwarded_but_cannot_upgrade_a_live_service(self):
        manifest = self.root / 'approved course fixture' / 'manifest.json'
        result = self.invoke(self.free_port(), manifest=manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = json.loads(self.arguments.read_text(encoding='utf-8-sig'))
        self.assertEqual(arguments[arguments.index('--source-review-manifest') + 1], str(manifest))
        self.arguments.unlink()
        port = self.serve({'application': 'wecom-english-helpdesk', 'processing_mode': 'ACK_ONLY',
            'source_review_enabled': False, 'collector': {'control': {'worker_alive': False}}})
        result = self.invoke(port, manifest=manifest)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('source review disabled', result.stderr)
        self.assertFalse(self.arguments.exists())
        self.assertEqual(self.posts, [])


if __name__ == '__main__': unittest.main()
