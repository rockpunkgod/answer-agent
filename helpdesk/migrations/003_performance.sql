ALTER TABLE messages ADD COLUMN source_sent_at TEXT;
ALTER TABLE messages ADD COLUMN source_time_evidence TEXT;

CREATE TABLE performance_units(
 id TEXT PRIMARY KEY,
 case_id TEXT NOT NULL REFERENCES cases(id),
 binding_id TEXT NOT NULL REFERENCES bindings(id),
 material_id TEXT REFERENCES materials(id),
 scope_key TEXT NOT NULL,
 question_type TEXT NOT NULL,
 measure_unit TEXT NOT NULL CHECK(measure_unit IN ('篇','题','独立知识点','待核验')),
 first_message_id TEXT NOT NULL REFERENCES messages(id),
 question_time TEXT,
 question_time_source TEXT,
 first_response_at TEXT,
 completed_at TEXT,
 completion_outbox_id TEXT REFERENCES outbox(id),
 category TEXT NOT NULL DEFAULT 'PENDING' CHECK(category IN ('REGULAR','NIGHT','PENDING')),
 category_basis TEXT NOT NULL,
 night_window_date TEXT,
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','CONFIRMED','EXCLUDED','REVOKED')),
 confirmed_quantity INTEGER NOT NULL DEFAULT 0 CHECK(confirmed_quantity >= 0),
 actual_question_count INTEGER NOT NULL DEFAULT 0 CHECK(actual_question_count >= 0),
 requested_conversion INTEGER,
 approved_conversion INTEGER,
 conversion_approver TEXT,
 conversion_approved_at TEXT,
 conversion_evidence TEXT,
 rule_version TEXT NOT NULL,
 grouping_reason TEXT NOT NULL,
 review_actor TEXT,
 review_at TEXT,
 review_evidence TEXT,
 timeliness_status TEXT NOT NULL DEFAULT 'PENDING',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(binding_id,scope_key)
);
CREATE TABLE performance_links(
 id TEXT PRIMARY KEY,
 unit_id TEXT NOT NULL REFERENCES performance_units(id),
 message_id TEXT NOT NULL REFERENCES messages(id),
 question_id TEXT REFERENCES questions(id),
 turn_id TEXT REFERENCES turns(id),
 question_version TEXT REFERENCES question_versions(id),
 link_kind TEXT NOT NULL CHECK(link_kind IN ('FIRST','SUBQUESTION','FOLLOWUP','SUPPLEMENT','CORRECTION','DISPUTE','DELIVERY','OTHER')),
 reason TEXT NOT NULL,
 linked_at TEXT NOT NULL,
 UNIQUE(unit_id,message_id,link_kind)
);
CREATE TABLE performance_events(
 id INTEGER PRIMARY KEY,
 unit_id TEXT NOT NULL REFERENCES performance_units(id),
 event TEXT NOT NULL,
 actor TEXT NOT NULL,
 reason TEXT NOT NULL,
 evidence TEXT,
 before_json TEXT,
 after_json TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE performance_rules(
 key TEXT PRIMARY KEY,
 value TEXT,
 status TEXT NOT NULL CHECK(status IN ('CONFIRMED','UNCONFIRMED')),
 source TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE performance_rule_changes(
 id TEXT PRIMARY KEY,
 key TEXT NOT NULL,
 old_value TEXT,
 new_value TEXT,
 old_version TEXT NOT NULL,
 new_version TEXT NOT NULL,
 actor TEXT NOT NULL,
 reason TEXT NOT NULL,
 evidence TEXT NOT NULL,
 changed_at TEXT NOT NULL
);
CREATE TRIGGER immutable_performance_rule_change_update BEFORE UPDATE ON performance_rule_changes
 BEGIN SELECT RAISE(ABORT, 'immutable performance rule change'); END;
CREATE TRIGGER immutable_performance_rule_change_delete BEFORE DELETE ON performance_rule_changes
 BEGIN SELECT RAISE(ABORT, 'immutable performance rule change'); END;
INSERT INTO performance_rules VALUES('night_start','23:00','CONFIRMED','用户第十五节',CURRENT_TIMESTAMP);
INSERT INTO performance_rules VALUES('night_end',NULL,'UNCONFIRMED','用户第十五节要求独立配置',CURRENT_TIMESTAMP);
INSERT INTO performance_rules VALUES('night_end_inclusive',NULL,'UNCONFIRMED','用户第十五节要求独立配置',CURRENT_TIMESTAMP);
INSERT INTO performance_rules VALUES('night_date_attribution',NULL,'UNCONFIRMED','用户第十五节要求独立配置',CURRENT_TIMESTAMP);
INSERT INTO performance_rules VALUES('night_unit','篇','CONFIRMED','用户第十五节',CURRENT_TIMESTAMP);
INSERT INTO performance_rules VALUES('night_price_per_piece',NULL,'UNCONFIRMED','用户第十五节：单位与单价分开',CURRENT_TIMESTAMP);
INSERT INTO performance_rules VALUES('retroactive_recalculation','false','UNCONFIRMED','用户第十五节：未授权仅预览',CURRENT_TIMESTAMP);
CREATE INDEX performance_units_status ON performance_units(status,category,completed_at);
CREATE INDEX performance_links_message ON performance_links(message_id);
INSERT INTO schema_migrations VALUES(3);
