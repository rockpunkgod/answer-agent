from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import os
from .locking import resource_lock


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def ensure_local_database(path):
    """SQLite is server/local cache storage, never a shared network ledger."""
    value = str(path)
    if value.startswith(('\\\\', '//', 'file:')):
        raise ValueError('SQLite requires a local file, not a network share or URI')
    if os.name == 'nt' and value != ':memory:':
        import ctypes
        anchor = Path(value).resolve().anchor
        if anchor and ctypes.windll.kernel32.GetDriveTypeW(anchor) == 4:
            raise ValueError('SQLite cannot be opened on a mapped network drive')
    return path


class Store:
    def __init__(self, path: str | Path):
        ensure_local_database(path)
        self.path = str(path)
        self.connection = sqlite3.connect(str(path), isolation_level=None, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=10000")
        with resource_lock(str(Path(self.path).resolve()) + ".schema.lock"):
            self._migrate()

    def _migrate(self):
        existing = self.connection.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone()
        if not existing:
            migration = Path(__file__).parent / "migrations" / "001_initial.sql"
            self.connection.executescript("BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") + "\nCOMMIT;")
        version = self.connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        if version == 1:
            migration = Path(__file__).parent / "migrations" / "002_workflow.sql"
            self.connection.executescript("BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") + "\nCOMMIT;")
            version = 2
        if version == 2:
            migration = Path(__file__).parent / "migrations" / "003_performance.sql"
            self.connection.executescript("BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") + "\nCOMMIT;")
            version = 3
        if version == 3:
            migration = Path(__file__).parent / "migrations" / "004_night_0700.sql"
            self.connection.executescript("BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") + "\nCOMMIT;")
            version = 4
        if version == 4:
            migration = Path(__file__).parent / "migrations" / "005_test_answer_body_format.sql"
            self.connection.executescript("BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") + "\nCOMMIT;")
            version = 5
        if version == 5:
            migration = Path(__file__).parent / "migrations" / "006_question_sessions.sql"
            self.connection.executescript("BEGIN IMMEDIATE;\n" + migration.read_text(encoding="utf-8") + "\nCOMMIT;")
            version = 6
        if version != 6:
            raise RuntimeError("Unsupported database schema")

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def execute(self, sql, params=()):
        return self.connection.execute(sql, params)

    def one(self, sql, params=()):
        return self.execute(sql, params).fetchone()

    def all(self, sql, params=()):
        return self.execute(sql, params).fetchall()

    def close(self):
        self.connection.close()
