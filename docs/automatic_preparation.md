# 首次题面审核自动承接网页准备

`helpdesk.automatic_preparation.complete_automatic_preparation` 仅处理已审核、已冻结的
`OPERATOR_TEST` 或 `SOURCE_MESSAGE` 任务和 `FAST_UPLOAD_THEN_GENERATE` 上传候选材料。它不打开桌面、
不创建聊天、不调用模型、不发送消息，也不创建真实学生身份或绩效记录。

```python
from helpdesk.automatic_preparation import complete_automatic_preparation

# 在首次题面审核及 freeze 已提交、独立聊天已建立、上传候选材料已保存后调用。
# 不要在 Store.transaction() 内调用；现有准备核验会独立核验/保留聊天归属。
preparation = complete_automatic_preparation(
    store,
    task_id,
    candidate_path,
)
assert preparation["status"] == "ATTACHMENTS_READY_SOURCE_REVIEWED"

# 调用方随后可按已有唯一 attempt 规则调用 run_existing。
# 本函数本身不会生成或发送任何消息。
```

输出使用任务 `freeze()` 已保存的 `preparation_path`。默认在同目录保存
`source-question-review.json` 和 `readiness.json`；调用方可通过
`source_review_path=`、`readiness_path=` 指定互不相同的证据路径。

`source-question-review.json` 是首次审核记录的程序投影。它明确记录
`review_origin=INITIAL_OPERATOR_INPUT_REVIEW`（本机测试）或 `INITIAL_SOURCE_CLARITY_REVIEW`
（已解析的原消息）、`new_human_review=false`，引用首次审核
audit ID，保留原审核人、原审核时刻、原来源说明、草稿版本及内容hash。首次题面确认
覆盖冻结原文、题干、选项和所附题图；上传后无需再次要求人类确认同一题面。现有
`review_preparation` 契约中的 reviewer 和题面确认字段取自这一次审核，不代表新审批。
课程摘录由程序从已核验且hash未变的课程快照提取，标明
`course_excerpt_origin=PROGRAM_VALIDATED_FROZEN_COURSE`，不声称操作员新读了课程。

核验交叉检查任务、草稿当前revision、revision内容hash、首次审核audit、冻结run中的
operator_test或source_clarity_review来源标记、当前题面fingerprint、manifest、课程内容和题图hash。
原消息任务还复核collector事件策略、原学生绑定、原发送时间和不变的来源证明；原消息的
完整媒体元数据仍保留，只投影上传需要的path/hash/provenance。随后调用
现有 `review_preparation` 核验上传journal、附件readiness及聊天归属，再调用
`PreparedDeepSeekGenerator._preparation` 确认契约可用。成功过程中不新增 `reviews`
或人工审核audit。聊天归属若尚未登记，仍由现有核验逻辑保留；其他题占用的URL被拒绝。

相同任务与相同证据重复调用返回原契约，不改文件内容和时间戳、不重复新审批。源材料、
任务、URL或证据改变时拒绝继续；不替换成功发布的不可变证据。调用方须把该函数的拒绝
作为自动流程停止条件，不能绕过它使用先前契约。首次执行失败会清理本次创建的准备
契约及证据，防止留下可用授权；既有残缺工件被拒绝，不会自动覆盖或重做桌面上传。

此模块只接通“首次审核→上传候选→准备契约”这一段。自动新建聊天、上传调度、原群
草稿及Luna桌面执行仍由外层编排提供，不能将固定题号或固定网页URL套用于新题。
