-- User-confirmed amendment: 23:00 inclusive through next-day 07:00 exclusive.
-- Apply only to unresolved settings. Existing confirmed alternatives retain their
-- values and require a separate reviewed change; old counting units are untouched.
INSERT INTO performance_rule_changes(id,key,old_value,new_value,old_version,new_version,actor,reason,evidence,changed_at)
SELECT 'user-2026-night-end-0700','night_end',
       json_object('end_hour',e.value,'end_inclusive',i.value),
       json_object('end_hour',7,'end_inclusive',0),
       COALESCE((SELECT new_version FROM performance_rule_changes WHERE key='night_end' ORDER BY changed_at DESC,rowid DESC LIMIT 1),
                '2026-09-30-user-section15-v1'),
       '2026-09-30-user-night-0700-v2','user','用户确认夜间结束时刻',
       '本会话：到次日早上七点前问的都算先天的；先天按上下文理解为前一天',CURRENT_TIMESTAMP
FROM performance_rules e, performance_rules i
WHERE e.key='night_end' AND i.key='night_end_inclusive'
  AND e.status='UNCONFIRMED' AND i.status='UNCONFIRMED';
UPDATE performance_rules SET value='7',status='CONFIRMED',
 source='本会话：到次日早上七点前问的都算先天的',updated_at=CURRENT_TIMESTAMP
WHERE key='night_end' AND status='UNCONFIRMED'
  AND EXISTS(SELECT 1 FROM performance_rule_changes WHERE id='user-2026-night-end-0700');
UPDATE performance_rules SET value='false',status='CONFIRMED',
 source='本会话：到次日早上七点前问的都算先天的',updated_at=CURRENT_TIMESTAMP
WHERE key='night_end_inclusive' AND status='UNCONFIRMED'
  AND EXISTS(SELECT 1 FROM performance_rule_changes WHERE id='user-2026-night-end-0700');

INSERT INTO performance_rule_changes(id,key,old_value,new_value,old_version,new_version,actor,reason,evidence,changed_at)
SELECT 'user-2026-date-attribution-0700','night_date_attribution',value,
       'original_question_business_day_07:00','2026-09-30-user-section15-v1',
       '2026-09-30-user-night-0700-v2','user','统计日期于07:00切换',
       '本会话：到次日早上七点前问的都算先天的；先天按上下文理解为前一天',CURRENT_TIMESTAMP
FROM performance_rules WHERE key='night_date_attribution' AND status='UNCONFIRMED';
UPDATE performance_rules SET value='original_question_business_day_07:00',status='CONFIRMED',
 source='本会话：到次日早上七点前问的都算先天的',updated_at=CURRENT_TIMESTAMP
WHERE key='night_date_attribution' AND status='UNCONFIRMED';

INSERT OR IGNORE INTO performance_rules(key,value,status,source,updated_at)
VALUES('reporting_cutoff','07:00','CONFIRMED',
       '本会话：到次日早上七点前问的都算先天的',CURRENT_TIMESTAMP);
INSERT INTO performance_rule_changes(id,key,old_value,new_value,old_version,new_version,actor,reason,evidence,changed_at)
SELECT 'user-2026-report-cutoff-0700','reporting_cutoff',NULL,'07:00',
       '2026-09-30-user-section15-v1','2026-09-30-user-night-0700-v2',
       'user','统计日期于07:00切换',
       '本会话：到次日早上七点前问的都算先天的；先天按上下文理解为前一天',CURRENT_TIMESTAMP
FROM performance_rules WHERE key='reporting_cutoff' AND source='本会话：到次日早上七点前问的都算先天的';
INSERT INTO schema_migrations VALUES(4);
