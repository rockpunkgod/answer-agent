-- Old test copies retain their exact body and delivery receipt hashes.
CREATE TABLE IF NOT EXISTS test_answer_copies(
    source_outbox_id TEXT PRIMARY KEY REFERENCES outbox(id),
    test_outbox_id TEXT NOT NULL UNIQUE REFERENCES outbox(id),
    target_platform TEXT NOT NULL,target_key TEXT NOT NULL,target_display_name TEXT NOT NULL,
    verification_evidence TEXT NOT NULL,verified_at TEXT NOT NULL,
    source_body_hash TEXT NOT NULL,question_version TEXT NOT NULL,
    context_revision INTEGER NOT NULL,answer_revision INTEGER NOT NULL,created_at TEXT NOT NULL);
ALTER TABLE test_answer_copies ADD COLUMN body_format TEXT NOT NULL DEFAULT 'legacy-prefixed-v1'
    CHECK(body_format IN ('legacy-prefixed-v1','plain-source-v2'));
INSERT INTO schema_migrations VALUES(5);
