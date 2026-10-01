ALTER TABLE outbox ADD COLUMN answer_id TEXT REFERENCES answers(id);
ALTER TABLE outbox ADD COLUMN run_id TEXT;
ALTER TABLE outbox ADD COLUMN simulated INTEGER NOT NULL DEFAULT 1;
ALTER TABLE outbox ADD COLUMN sent_at TEXT;
ALTER TABLE outbox ADD COLUMN last_error TEXT;
CREATE TABLE sessions(
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), adapter TEXT NOT NULL,
 state TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE runs(
 id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id), question_id TEXT NOT NULL REFERENCES questions(id),
 question_version TEXT NOT NULL REFERENCES question_versions(id), context_revision INTEGER NOT NULL,
 session_id TEXT NOT NULL REFERENCES sessions(id), input_json TEXT NOT NULL,
 state TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, completed_at TEXT
);
CREATE UNIQUE INDEX one_active_run ON runs(question_id) WHERE state='RUNNING';
CREATE TABLE answer_evidence(
 answer_id TEXT PRIMARY KEY REFERENCES answers(id), run_id TEXT NOT NULL REFERENCES runs(id),
 correct_option_id TEXT NOT NULL, complete INTEGER NOT NULL,
 uploads_confirmed INTEGER NOT NULL, session_id TEXT NOT NULL, simulated INTEGER NOT NULL
);
CREATE TABLE reviews(
 id TEXT PRIMARY KEY, outbox_id TEXT NOT NULL REFERENCES outbox(id),
 question_version TEXT NOT NULL REFERENCES question_versions(id), context_revision INTEGER NOT NULL,
 answer_revision INTEGER NOT NULL, body_hash TEXT NOT NULL, reviewer TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT INTO settings VALUES('stop_requested','false');
CREATE TABLE delivery_checks(
 id TEXT PRIMARY KEY,outbox_id TEXT NOT NULL REFERENCES outbox(id),status TEXT NOT NULL,
 evidence TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE INDEX runs_state ON runs(state,created_at);
INSERT INTO schema_migrations VALUES(2);
