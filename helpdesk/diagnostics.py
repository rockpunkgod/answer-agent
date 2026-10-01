"""Read-only integration readiness report. Never prints environment variable values."""
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform
import json
import sqlite3
import time
import tomllib


def prepared_workbench_status(config_path):
    """Inspect configured persistent state read-only; never probe the desktop."""
    path = Path(config_path).resolve(strict=True)
    config = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(config, dict) or not {'database', 'run_id', 'preparation', 'manifest'} <= config.keys():
        raise ValueError('Invalid prepared workbench configuration')
    def local(key):
        value = Path(config[key])
        return value.resolve() if value.is_absolute() else (path.parent / value).resolve()
    database = local('database').resolve(strict=True)
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        run = connection.execute('SELECT state,input_json FROM runs WHERE id=?', (config['run_id'],)).fetchone()
        if not run:
            raise ValueError('Configured run does not exist')
        snapshot = json.loads(run['input_json'])
        preparation = json.loads(local('preparation').read_text(encoding='utf-8'))
        reviewed = preparation.get('operator_verified') is True and preparation.get('run_id') == config['run_id']
        prepared = snapshot.get('simulated') is False and snapshot.get('generation_adapter') == 'WINDOWS_MCP_PREPARED_DEEPSEEK'
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        staged = connection.execute('SELECT COUNT(*) FROM native_chat_staging').fetchone()[0] if 'native_chat_staging' in tables else 0
        delivery = None
        if config.get('test_outbox_id'):
            row = connection.execute('SELECT purpose,state,simulated FROM outbox WHERE id=?', (config['test_outbox_id'],)).fetchone()
            if row:
                delivery = dict(row)
        pin_valid = False
        if config.get('pin'):
            pin = json.loads(local('pin').read_text(encoding='utf-8'))
            pin_valid = (pin.get('outbox_id') == config.get('test_outbox_id')
                         and isinstance(pin.get('expires_at'), (int, float))
                         and pin['expires_at'] > time.time())
        return {'configured': True, 'frozen_prepared_adapter': prepared,
                'operator_preparation_reviewed': reviewed, 'run_state': run['state'],
                'manifest_file_exists': local('manifest').is_file(), 'test_delivery': delivery,
                'test_pin_time_valid': pin_valid, 'native_acquisitions': staged,
                'desktop_checked_now': False, 'history_coverage_verified': False,
                'note': 'Persistent state only. A fresh foreground/contact check remains required for each new send; UI confirmation is not a server receipt.'}
    finally:
        connection.close()


def doctor(config_path=None, *, real_config_path=None):
    config = {}
    if config_path:
        with open(config_path, "rb") as stream:
            config = tomllib.load(stream)
    model = config.get("scheduler", {})
    missing = [key for key in ("provider", "base_url", "model_id") if not model.get(key) or model.get(key) == "UNCONFIGURED"]
    try:
        playwright = version("playwright")
    except PackageNotFoundError:
        playwright = None
    skills = config.get("teaching", {}).get("skill_paths", [])
    result = {"python": platform.python_version(), "demo": "SIMULATION_ONLY", "real_send": "DISABLED_IN_DEFAULT_MODE",
            "scheduler": {"connection_verified": False, "missing_configuration": missing,
                          "note": "Channel label is not an API model ID; no provider connection test has run."},
            "windows_mcp": {"configured": bool(config.get("desktop", {}).get("windows_mcp_endpoint")), "verified": False},
            "deepseek": {"playwright_installed": playwright, "live_page_verified": False,
                         "note": "Browser fixture tests do not establish live DeepSeek compatibility."},
            "teaching": {"explicit_paths": len(skills), "existing_paths": sum(Path(p).is_file() for p in skills),
                         "live_upload_verified": False},
            "capabilities": {"message_ingress": "demo presets", "ocr": "not integrated", "search": "not integrated",
                             "desktop": "persistent mock receipts", "browser": "local Playwright contract tests"}}
    if real_config_path:
        workbench = prepared_workbench_status(real_config_path)
        result['prepared_workbench'] = workbench
        result['demo'] = 'REAL_PREPARED_CONFIGURED'
        result['real_send'] = 'LIMITED_REVIEWED_TEST_COPY_WITH_FRESH_DESKTOP_CHECK'
        result['capabilities']['message_ingress'] = 'native body staging; sender/time/identity remain unverified'
        result['capabilities']['desktop'] = 'Windows-MCP prepared-task test-copy adapter; not probed now'
        result['capabilities']['browser'] = 'Edge prepared DeepSeek workflow; live page not probed now'
    return result
