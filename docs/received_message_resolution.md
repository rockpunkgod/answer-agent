# 已回复“收到”的原消息接入答疑事项

`ACK_ONLY` 已将可信实时消息和固定“收到”写入业务库。旧实现随后调用普通 `ingest`，会因为同一个平台消息 ID 返回重复记录，导致无法建立对应的答疑事项。

现在 `CollectorDispatcher.resolve` 在显式 `CASE_RESOLUTION` 模式下识别已有的收件记录，使用 `Helpdesk.resolve_received_message` 原地关联 Case、Question、Version 和 Turn。新消息导入和收件记录提升共用 `_link_received`，只有一个归属及版本算法。

## 可信后台调用

```python
dispatcher = CollectorDispatcher(collector_store, business_store,
                                 processing_mode="CASE_RESOLUTION")
decision = dispatcher.received_incoming(
    task_id,
    intent=verified_intent,
    verified_question=verified_question,
    raw_material=original_material,
    verified_material=verified_material,
    # 普通追问需要由可信归属判断提供原 question_id / quote_message_id 等证据。
)
outcome = dispatcher.resolve(task_id, decision, reviewer=reviewer_id,
                             rationale=source_and_association_evidence,
                             confidence=verified_confidence)
```

此接口不推断意图、不解题，也不进行桌面操作。题目及归属决策仍由可信的 DeepSeek 网页与答疑 Skill 工作流提供；自动化执行模型不能编造这些判断。`received_incoming` 从原收件记录提取传输字段，只接受业务字段。`resolve` 再次核对原采集来源，而不是将这个构造方法当作来源验证。

## 保留和恢复

- 原消息 ID、学生及群绑定、文字、附件、平台定位、采集时间、原发送时间及证据保持不变。不会删后重建或使用当前时间补齐。
- 原“收到”的 ID、内容、发送状态和证据保持不变。不论原 ACK 是待发、已确认或发送结果未知，都不会再次排队或伪造确认。
- 建立事项不等于允许生成答案。原 ACK 未实际确认时，既有生成门禁继续阻止真实生成。
- 首次解析结果和业务决策摘要写入持久审计。相同业务内容的重试返回原来的 Turn；新的内部选项 UUID 不构成新的决策。修改已解析条件必须走明确的新消息、更正或版本流程。
- 业务关联已提交但采集任务状态尚未提交时，恢复使用持久解析结果，保留原 Turn，不重复建立 Case、Version、ACK 或计量单元。
- 解析期间短暂锁定原采集记录及事件策略，与采集处理保持相同锁顺序。锁内只执行本地验证和业务写入，不等待模型或桌面。

## 来源限制

原消息必须是已验证学生、可靠原发送时间、允许自动处理的 LIVE 事件，且无来源冲突。原群/学生变化、教师/本人消息、缺失或被撤销的事件策略、跨学生引用、时间及附件证据冲突都会阻止关联。历史回放不会提升为实时收件记录；原有显式历史解析仍禁止所有回复。

新采集记录若已带有可读取的本地原图及正确哈希，会保留原媒体元数据，同时记录 `path`、`sha256`、`provenance` 供后续冻结使用。GUI 原图没有平台媒体 ID 也保留其本地证据。文件缺失、不可读取、过大或哈希不符时不补造证明。旧记录不被改写；旧记录缺图片证明时仍需处理媒体前置条件。

## 当前验证范围

新增独立数据库测试覆盖原地关联、未确认 ACK 保留、原文及时间不可替换、跨学生引用、历史隔离、事件撤销、来源冲突、图片变更、并发解析、进程中断恢复、旧任务重放和普通追问计量归并。

这些是后台隔离测试，未读取实际桌面、发送群消息或给实际记录补身份时间。本机工作台继续使用 `ACK_ONLY`，现存原文片段缺可靠身份/原发送时间，不能因此接口自动升级为正式题目或绩效。已解析的可靠原消息现可经 `SourceQuestionTasks` 接入一次题面确认及同一持久队列，见[原消息题面确认](source_question_tasks.md)。这项后台接线仍需真实新消息、来源采集和 Luna 网页流程的现场验证，不能单独称为完整自动答疑验收。
