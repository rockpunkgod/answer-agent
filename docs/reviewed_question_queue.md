# 首次题面确认后的持久队列

模块 `helpdesk.reviewed_question_queue` 保存自动流程调度进度。原题面仍在现有
draft/task/turn 中，生成结果仍以 run/answer/outbox 为准；队列不保存另一份题面或答案。

## 工作台入口

可信本机真实工作台配置可设置 `"auto_prepare_after_question_review": true`。
省略或false保留原入口；非布尔值在打开业务库前拒绝。启用后，题面review事务
提交即调用request_enqueue，不需要再点击冻结或生成；工作台只读显示持久队列进度。
缺少原绑定的已确认ACK时保留首次审核并显示WAITING_ACK，不伪造收到记录。
工作台后台每秒调用resume_pending核对实际ACK和材料条件，齐备后自动复用enqueue，
无需第二次人工review。重复等待不修改时间戳或重复审计；网页GET不推动执行。

开启此阶段时正式ANSWER/CORRECTION为MANUAL，无第二次人工答案审批。浏览器
不能调用旧固定生成、审核、测试发送或手动freeze/generate绕过Luna队列；运行
控制仍可停止/恢复。该开关不会启动桌面执行器，没有可信Luna回调时明确等待。
本机OPERATOR_TEST仍不代表原始学生消息，不计绩效。当前8767原生导入服务
仍为ACK_ONLY，未因这项接口改造自动变成完整的真实学生新题入口。

## 题面确认后自动冻结并排队

```python
from helpdesk.reviewed_question_queue import enqueue, get, list_queue

# OperatorTasks.review() 的事务已提交。manifest来自可信后台配置。
state = enqueue(store, task_id, manifest)
# 初始phase是WAITING_DESKTOP_EXECUTOR，不代表网页已创建或材料已上传。

state = get(store, task_id)     # 只读，无autorun或桌面调用。
states = list_queue(store)     # 只读；尚未建队列表时返回[]。
```

`enqueue` 自动调用既有 `OperatorTasks.freeze()`。已有冻结任务复用原manifest、
preparation和generation路径；新任务默认使用数据库旁
`private/reviewed-question-queue/<task_id>/` 目录。可以显式指定
`preparation_path=`、`evidence_dir=`、`candidate_path=`；默认候选材料文件为
`preparation-candidate.json`。同任务同配置重复调用不会创建新run或新队列任务。
改变已冻结配置会被拒绝，不会覆盖已有准备材料。

首次审核audit、draft revision/hash和当前题面均在冻结前核验。现有
ACK-before-generation要求仍由freeze检查；未满足时抛出 `ACK_REQUIRED`，不会把本机
测试ACK或用户口头确认伪造成学生收到回执。该模块接受OPERATOR_TEST首次审核，及
`SourceQuestionTasks` 的可靠原消息首次题面确认。后者沿用原message/ACK/Case/版本并
复核原始来源，见[原消息题面确认](source_question_tasks.md)。队列不创建或猜测真实群、
真实学生身份，也不自行计正式绩效。

get/list只返回task/run/candidate路径、phase及执行尝试元信息，不返回原题、答案、
审核人或来源正文；回调异常内容不会放进状态接口。

## 后续仅由显式Luna执行器推进

```python
from helpdesk.reviewed_question_queue import advance

# Luna不可用时：只做持久状态及既有证据核验，不执行任何回调。
state = advance(store, task_id)

# 桌面执行器在后续明确可用时，由可信后台注入；此库不会选择其他模型替代。
state = advance(
    store, task_id,
    executor="LUNA",
    session_creator=verified_luna_session_creator,
    preparer=verified_luna_preparer,
    generator=verified_luna_generator,
)
```

一次advance最多执行一个阶段。所有回调接收keyword参数
`store, task, snapshot, attempt_id`：

- `session_creator`：创建独立网页聊天，返回现场核验过的完整DeepSeek聊天URL。
  本模块不会猜新聊天按钮或选择器。成功后登记现有session/chat归属。
- `preparer`：另接收 `session_url, candidate_path`，按Skill后题目顺序上传，并在
  candidate_path保存FAST候选证据；回调返回值不代替证据文件。队列随后自动调用
  `complete_automatic_preparation()`复用首次题面审核，无第二次人工确认。
- `generator`：通过既有 `run_existing` / Workflow 保存真实生成结果。返回
  `{"state": "GENERATED"}` 不足以标记完成，队列必须查到当前run及合法真实outbox。

`executor="LUNA"`是可信后台注入契约，不能让浏览器自行传标签或任意回调作为桌面
执行授权。没有相应回调就保持等待，并给出 `LUNA_EXECUTOR_REQUIRED:<stage>`。
停止开关开启时不执行回调。本模块不填原群草稿、不调用发送或交付确认；生成完成后
应由外层独立的manual draft适配器处理，正式发送仍由人按键。

## 进度、重启和未知结果

持久phase包含 `WAITING_DESKTOP_EXECUTOR`、`READY_FOR_PREPARATION`、
`ATTACHMENTS_READY`、`GENERATED`。这些进度分别要求实际已保存的会话归属、有效准备
契约、真实run/outbox，不因排队或回调口头返回而成立。执行期间保存
`SESSION_CREATION_STARTED`、`PREPARATION_STARTED` 或 `GENERATION_STARTED`；
源材料变更等失败显示 `NEEDS_ATTENTION`。

每阶段唯一attempt会在回调前提交为STARTED，因此中断或异常会留下持久记录。
不能确定结果时进入 `EXECUTION_UNCERTAIN`，不自动重试同阶段。重启只可从已登记的
chat归属、完整FAST候选/准备契约、已保存真实run/outbox恢复进度，绝不重新执行网页
创建、上传或模型提交。如果这些证据不存在，需要既有恢复核验流程提供真实证据，
不能删掉attempt以便重复执行。

运行advance采用同数据库的有界执行锁，适合低并发。enqueue/advance须在既有事务
提交后调用。此库没有后台线程、定时器、桌面依赖启动或隐藏autorun。

## 持久待ACK入队意图与只读终态

工作台首次题面确认建议调用 `request_enqueue`，使未满足ACK条件的任务也能持久
恢复。manifest和可选路径只来自可信后台配置，不经浏览器请求传入。

```python
from helpdesk.reviewed_question_queue import request_enqueue, resume_pending, list_admissions

# 首次source review已提交；此调用不操作桌面或生成消息。
state = request_enqueue(store, task_id, trusted_manifest)

# 可信后台线程定期调用；不得在GET请求或已有事务中调用。
states = resume_pending(store)

# GET展示：纯读，无重试、freeze、advance或桌面调用。
admissions = list_admissions(store)
queues = list_queue(store)
```

`request_enqueue` 先保存调度意图，再尝试后台入队；条件满足时立即冻结一次并返回队列
状态。未满足时 `phase=WAITING_ACK`、`enqueued=false`、`run_id=null`，不冻结、不修改
ACK，不新增人工审批。ACK必须来自原任务绑定的实际确认记录且simulated=0；旧模拟
回执不能释放真实准备任务。停止开关开启时显示 `STOPPED`。新意图中断于首次尝试前
保留 `READY_FOR_ENQUEUE`；重启后resume继续处理。冻结后尚未插入队列、或插入队列后
尚未更新意图的中断，均复用原task/run，不创建第二个run。

已提交初次审核的revision/hash、audit、材料、manifest/hash和冻结配置会被复核。源或
材料改变等异常转为 `NEEDS_ATTENTION`，定期resume不再重复尝试该意图。条件未变的
`WAITING_ACK`/`STOPPED` 重复检查不改变 `updated_at`，不重复审计。审计事件仅为调度
请求/状态变化，绝不是新的人工题面审核。ACK满足后无需第二次POST review；后台
自动入队，但不会执行Luna步骤。

返回契约（均为配置引用或进度，不含原题、答案和凭据）：

| 字段 | 含义 |
|---|---|
| `task_id` | 已提交首次审核的任务ID |
| `run_id` | 实际冻结run ID；尚未冻结为null |
| `phase` | 展示阶段：等待ACK/停止/待入队，或已入队的有效队列阶段 |
| `admission_phase` | 持久意图阶段，完成入队后为ENQUEUED |
| `enqueued` | 是否实际存在队列记录 |
| `manifest_path`, `manifest_sha256` | 可信课程清单位置及固定hash |
| `preparation_path`, `evidence_dir`, `candidate_path` | 固定证据位置 |
| `last_error` | 状态错误码，不包含回调异常正文 |
| `created_at`, `updated_at` | 状态时间；阶段未变时不改写 |
| `source_review_required_again` | false；不要求同一题面第二次审批 |
| `formal_statistics_eligible`, `student_delivered` | false；调度不构成绩效或学生交付 |

入队后的get/list_queue额外提供 `stored_phase` 和 `authoritative_run_state`。
`phase` 按run终态纯读校正：REJECTED/STALE/缺失run显示NEEDS_ATTENTION；GENERATED
必须存在当前run归属、版本/正文/证据合法的真实outbox才显示GENERATED，并返回
`outbox_id`, `outbox_state`。非法或缺失outbox显示GENERATED_OUTBOX_INVALID。外部恢复
工具未同步队列时，界面也不会继续显示“等待Luna”。展示校正不写回队列或audit，不
提交生成，不修改未知发送状态。list_admissions对已入队任务复用同样投影；root可先
合并admissions，再用list_queue覆盖相同task ID。
