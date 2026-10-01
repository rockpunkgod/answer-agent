-- Keep legacy sessions and runs intact. Only newly owned sessions can resume.
CREATE TABLE session_owners(
 session_id TEXT PRIMARY KEY REFERENCES sessions(id),
 binding_id TEXT NOT NULL REFERENCES bindings(id),
 question_id TEXT NOT NULL REFERENCES questions(id),
 created_at TEXT NOT NULL
);
CREATE INDEX session_owner_question ON session_owners(binding_id,question_id);
-- A single-question historical session has an unambiguous owner. Mixed
-- historical sessions stay unowned; never alter their runs or snapshots.
INSERT INTO session_owners(session_id,binding_id,question_id,created_at)
SELECT s.id,c.binding_id,MIN(r.question_id),s.created_at
FROM sessions s JOIN cases c ON c.id=s.case_id JOIN runs r ON r.session_id=s.id
JOIN questions q ON q.id=r.question_id
GROUP BY s.id
HAVING COUNT(DISTINCT r.question_id)=1 AND MIN(q.case_id)=s.case_id AND MAX(q.case_id)=s.case_id;
CREATE TABLE deepseek_chats(
 session_url TEXT PRIMARY KEY,
 session_id TEXT NOT NULL UNIQUE REFERENCES session_owners(session_id),
 first_run_id TEXT NOT NULL REFERENCES runs(id),
 created_at TEXT NOT NULL
);
INSERT INTO schema_migrations VALUES(6);
