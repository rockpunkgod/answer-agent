CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
CREATE TABLE bindings(
 id TEXT PRIMARY KEY, group_key TEXT NOT NULL, student_key TEXT NOT NULL,
 display_name TEXT NOT NULL, verified INTEGER NOT NULL CHECK(verified IN (0,1)),
 UNIQUE(group_key, student_key)
);
CREATE TABLE messages(
 id TEXT PRIMARY KEY, binding_id TEXT NOT NULL REFERENCES bindings(id),
 source TEXT NOT NULL, platform_id TEXT, observation_id TEXT,
 observed_at TEXT NOT NULL, raw_text TEXT NOT NULL, attachments TEXT NOT NULL,
 fingerprint TEXT NOT NULL, possible_duplicate INTEGER NOT NULL DEFAULT 0,
 intent TEXT NOT NULL, status TEXT NOT NULL, case_id TEXT, question_id TEXT,
 quote_message_id TEXT REFERENCES messages(id), created_at TEXT NOT NULL,
 UNIQUE(binding_id, source, platform_id), UNIQUE(binding_id, source, observation_id)
);
CREATE TABLE cases(
 id TEXT PRIMARY KEY, binding_id TEXT NOT NULL REFERENCES bindings(id),
 status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE source_conflicts(
 id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id),
 conflicting_text TEXT NOT NULL, conflicting_attachments TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE materials(id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), current_version TEXT);
CREATE TABLE material_versions(
 id TEXT PRIMARY KEY, material_id TEXT NOT NULL REFERENCES materials(id),
 parent_id TEXT REFERENCES material_versions(id), raw_text TEXT NOT NULL,
 verified_text TEXT, source_message TEXT NOT NULL REFERENCES messages(id), created_at TEXT NOT NULL
);
CREATE TABLE questions(
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
 material_id TEXT REFERENCES materials(id), current_version TEXT, context_revision INTEGER NOT NULL DEFAULT 1,
 status TEXT NOT NULL
);
CREATE TABLE question_versions(
 id TEXT PRIMARY KEY, question_id TEXT NOT NULL REFERENCES questions(id),
 parent_id TEXT REFERENCES question_versions(id), material_version TEXT REFERENCES material_versions(id),
 payload TEXT NOT NULL, source_message TEXT NOT NULL REFERENCES messages(id),
 diff TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE turns(
 id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), question_id TEXT REFERENCES questions(id),
 message_id TEXT NOT NULL UNIQUE REFERENCES messages(id), question_version TEXT REFERENCES question_versions(id),
 context_revision INTEGER, intent TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE reference_candidates(
 id TEXT PRIMARY KEY, student_version TEXT NOT NULL REFERENCES question_versions(id),
 reference_version TEXT NOT NULL, payload TEXT NOT NULL, material_text TEXT,
 comparison TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE answers(
 id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id), question_id TEXT NOT NULL REFERENCES questions(id),
 question_version TEXT NOT NULL REFERENCES question_versions(id), context_revision INTEGER NOT NULL,
 answer_revision INTEGER NOT NULL, text TEXT NOT NULL, state TEXT NOT NULL,
 adapter TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(question_id, answer_revision)
);
CREATE TABLE outbox(
 id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id), case_id TEXT REFERENCES cases(id),
 turn_id TEXT REFERENCES turns(id), binding_id TEXT NOT NULL REFERENCES bindings(id), purpose TEXT NOT NULL,
 body TEXT NOT NULL, question_version TEXT REFERENCES question_versions(id), context_revision INTEGER,
 review_status TEXT NOT NULL DEFAULT 'UNREVIEWED', idempotency_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL CHECK(state IN ('PENDING','SENDING','SENT_UI_CONFIRMED','SEND_UNKNOWN','FAILED','STALE','CANCELLED')),
 created_at TEXT NOT NULL
);
CREATE TABLE human_tasks(
 id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id), reason TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'OPEN', created_at TEXT NOT NULL
);
CREATE TABLE audit(
 id INTEGER PRIMARY KEY, case_id TEXT, question_id TEXT, turn_id TEXT, run_id TEXT, outbox_id TEXT,
 event TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER immutable_qv_update BEFORE UPDATE ON question_versions BEGIN SELECT RAISE(ABORT, 'immutable question version'); END;
CREATE TRIGGER immutable_qv_delete BEFORE DELETE ON question_versions BEGIN SELECT RAISE(ABORT, 'immutable question version'); END;
CREATE TRIGGER immutable_mv_update BEFORE UPDATE ON material_versions BEGIN SELECT RAISE(ABORT, 'immutable material version'); END;
CREATE TRIGGER immutable_mv_delete BEFORE DELETE ON material_versions BEGIN SELECT RAISE(ABORT, 'immutable material version'); END;
CREATE INDEX message_pending ON messages(binding_id, status);
CREATE INDEX question_case ON questions(case_id);
CREATE INDEX outbox_state ON outbox(state, created_at);
INSERT INTO schema_migrations VALUES(1);
