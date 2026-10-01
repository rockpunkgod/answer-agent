# 采集验收测试对应关系

所有证据均来自临时数据库、模拟消息源或模拟 SDK 桥，未证明当前账号已有官方存档权限，也未证明真实 GUI 持续监听已运行。当前用户选择仅存档与“收到”，默认 ACK_ONLY；答疑与绩效关联测试仅显式 CASE_RESOLUTION 兼容模式，当前不会自动启用。

测试文件简称：C = `tests/test_message_collector.py`；I = `tests/test_collector_integration.py`；A = `tests/test_wecom_archive.py`。

| 原要求 | 具体测试 | 实际断言 |
| --- | --- | --- |
| 1 重复批次只入库一次 | C `test_same_batch_twice_one_message_event` | 重拉后 messages=1、events=1、duplicate_count=1 |
| 2 第1001条不遗漏 | C `test_1001_messages_continue_paging`；A `test_thousand_and_1001_pagination_and_persistent_dedup` | 两页1001条，游标1001；官方适配协议页尾为m1001 |
| 3 写入失败游标不提前推进 | C `test_db_failure_does_not_advance_cursor` | 插入触发器失败后cursor为空，messages/events均0 |
| 4 写消息后游标前崩溃 | C `test_crash_between_write_and_cursor_rolls_back_then_restart`、`test_actual_process_exit_before_commit_recovers`；I `test_ack_only_business_commit_before_event_ack_crash_restart` | 真子进程退出后未提交消息与游标均回滚；跨库业务提交而事件未ack时，重启仅一条ACK、一条任务、一条audit |
| 5 22:58发送23:05拉取 | I `test_live_is_real_pending_task_no_collector_reply_and_send_time_preserved` | source_sent_at=22:58，observed_at=23:05，计量category=REGULAR |
| 6 23:01题次日答仍夜间 | I `test_new_grammar_after_23_is_material_count_even_next_day_resolution` | 次日处理及模拟answered_at元数据后question_time仍23:01，category=NIGHT，单位篇；未实际运行模型或发送 |
| 7 22:50首问23:10追问 | I `test_followup_does_not_change_first_question_time` | 原question_time仍22:50，仅1个绩效单元，category=REGULAR |
| 8 23:10新语法填空按篇 | I `test_2310_grammar_and_three_blank_followups_remain_one_material` | 首问23:10，category=NIGHT，measure_unit=篇 |
| 9 同材料追问3个空不3篇 | I `test_2310_grammar_and_three_blank_followups_remain_one_material` | 夜间同篇分别追问第1/2/3空后performance_units仍1，首问时间仍23:10 |
| 10 调换选项不新增篇数 | I `test_option_order_change_retains_all_messages_and_one_counting_unit` | 原文2条、question_versions=2、performance_units=1 |
| 11 两学生相同图片独立 | C `test_two_students_identical_image_official_ids_independent`；I `test_two_students_same_image_have_independent_messages_and_cases_in_explicit_resolution_mode` | 原消息与媒体记录各2，显式归属的case_id不同 |
| 12 媒体失败仍保留消息并重试 | A `test_failed_download_keeps_message_and_resumes_committed_chunk` | 首次FAILED但消息存在；新worker沿next分块游标下载成功且内容/hash可校验 |
| 13 BACKFILL不回复 | C `test_backfill_cursor_and_reply_permission_isolated`；I `test_ack_only_history_and_nonquestion_do_not_ack`、`test_backfill_resolution_never_creates_ack_or_other_outbox` | LIVE游标不变，历史auto_reply_allowed=0；ACK_ONLY历史不建outbox，历史归属也不建ACK |
| 14 LIVE触发正常处理 | I `test_ack_only_live_question_queues_received_without_teaching`；兼容 I `test_live_is_real_pending_task_no_collector_reply_and_send_time_preserved` | 当前仅ACK/PENDING/收到，cases/questions/turns/runs/answers/performance均0；显式归属兼容模式才创建待归属任务和业务turn |
| 15 GUI不确定时间不伪造 | C `test_gui_ambiguous_time_never_fabricates_sent_time`；I `test_gui_relative_time_unverified_sender_never_guesses` | sent_at UTC/local为空、time_confidence=low、不可自动ACK；前台丢失暂停 |
| 16 重启继续已确认位置 | C `test_restart_resumes_last_committed_cursor`；I `test_ack_only_business_commit_before_event_ack_crash_restart` | 新采集器从已提交1拉取2，跨库重启ACK不重复 |

额外可靠性证据：I `test_concurrent_ack_drainers_do_not_duplicate_ack_or_audit` 的两个并行dispatcher只有一次事件消费、一条ACK和audit；I `test_gui_requires_boolean_verification_and_valid_incremental_page` 拒绝字符串/数字充当验证标志、非法页大小、超页及无位置推进；I `test_ack_only_self_teacher_conflict_and_unknown_time_are_held` 保留本人、教师、冲突及原时间未知的禁ACK原因；C `test_cli_monitoring_tracks_daily_duplicates_per_source` 检查监控按source统计。

运行：

```powershell
python -m unittest tests.test_message_collector tests.test_wecom_archive tests.test_collector_integration -v
```
