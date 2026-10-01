# 原消息的一次题面确认

`helpdesk.source_question_tasks.SourceQuestionTasks` 沿用已排队“收到”的学生原消息、原绑定和已建立的 Case / Question / Version。入口需要可信解析器先完成新题、追问或更正的归属；本模块不判断教学内容、不调用模型，也不把本机录入副本转成真实学生消息。

## 后台接线

```python
from helpdesk.source_question_tasks import SourceQuestionTasks

# collector_task_id 已由 CASE_RESOLUTION 解析为 LINKED。
# 原学生身份、原发送时间和 LIVE 来源事件必须已有证据。
tasks = SourceQuestionTasks(business_store)
draft = tasks.create_from_received(
    collector_store, collector_task_id,
    question_type=trusted_question_type,
    resolver_evidence=trusted_resolver_evidence,
)
```

草稿标记为 `SOURCE_MESSAGE`，引用原 message、原 collector task 及不可变来源哈希。它不创建第二条消息、ACK、绑定、Case、问题或版本。与本机 `OPERATOR_TEST` 共用现有草稿、任务和持久队列表，保留两类来源的区别。浏览器不能提供任意来源路径、学生身份、原发送时间或附件路径来调用这个后台入口。

## 工作台的一次确认

可信真实工作台配置 `auto_prepare_after_question_review: true` 后，原消息卡片显示原群、学生、原提问时间、已转录完整题面和原图。用户只需点击一次“确认题面清楚并排队”。原图通过同站只读接口按 draft ID 和图片序号提供，每次核对现存原文件及哈希；来源改变后不能继续使用旧图或旧审核。来源不可靠时确认按钮禁用。

审核只保存 `SOURCE_QUESTION_INPUT_REVIEWED` 及原题面的指纹，不新增人工答案审批。未取得原绑定的实际 ACK 确认时显示 `WAITING_ACK`；后台持续核对该前置条件，条件成立后复用同一次审核冻结并入队，页面自动显示进度。重复确认不重复审核、冻结或回复“收到”。查看页面和原图不执行冻结、网页操作或发送。

阶段设置 `require_source_clarity_review` 可配置并保留变更审计。在启用该阶段的真实工作台中，直接启动生成也必须具有原消息的这一次确认，不能绕过工作台入口。来源、事件策略、题面版本、原图或审核证据改变时，自动准备和交付前的核验都会暂停旧任务；更正使用既有问题版本流程。

## 网页与交付

后续沿用持久队列及 `complete_automatic_preparation`：Luna 创建每学生、每题的独立真实 DeepSeek 会话，先上传实际 Skill 文件，再上传题目，一次生成并按 Skill 自检。程序投影首次题面确认，记录 `INITIAL_SOURCE_CLARITY_REVIEW`、原发送人和原发送时间，不要求第二次人工核验。没有可信 Luna 执行回调时保持等待，不选择其他模型替代桌面操作。

生成的完整原文进入既有答案 / outbox，由原群草稿适配器核对来源后粘贴；正式讲解仍由人工最后按发送。草稿、排队和“收到”均不构成正式绩效；后续实际交付及绩效归并各自核验。

## 现场边界

这项接线已提供原消息入口、原图预览、一次确认、持久排队及准备 / 交付来源核验。当前8767服务仍是 `ACK_ONLY` 原生文字导入，导入器并非实时桌面监听。来源缺可靠原始身份和时间的现存片段不能使用此入口；不能把隔离测试的 collector、ACK、模型答案或桌面替身当作现场成功证据。真实新消息的来源采集、可信归属和 Luna 网页执行仍需现场验证。
