# 集成契约

## Worker与共享消费者当前补充

可执行统一Schema为helpdesk/worker_contracts.py：严格高级动作白名单、原task/outbox与UUID版本/上下文、内容hash、账号/范围、worker/epoch、授权时效、预算和明确结果/健康/接管/权限。worker_coordinator位于服务器现有业务DB内，仅执行元数据版本1，核心Schema仍6；worker_client本地仅执行journal/待回传缓存，不维护正式绩效。固定API不提供自由命令/工具/路径/账号切换；批准资源按command/lease/ref/sha读取，禁止共享SQLite。生产无verifier不能置SENT，tool成功不能算核验。

SEMANTIC_DECISION_CONFIRMED由同一固定源、resolver指纹、Turn/版本、题型/范围证据供answer_input与counting_unit消费；SOURCE_SEMANTIC_DECISION_BOUND沿用Source一次题面确认，不独立重判类型。原群草稿冻结同一semantic_decision/unit/outbox/bodyhash；实际教师回执经原身份/正文/时间/版本核验才写delivery_checks及现有确定性计量投影，重复恢复不入账。单价、历史覆盖和未确认年份不补猜。具体接口/边界见WORKER_DEPLOYMENT.md；旧缺口表保留历史，当前等级为Mock通过、真实入口待接。

本文件复用现有实体，记录已实现的边界和仍未接通的消费者；不另建平行消息、计量或交付系统。代码与测试证据以 `.agent/project-state.json` 和项目自查结果为准。

| 概念 | 当前权威记录/接口 | 校验与消费边界 |
|---|---|---|
| 标准消息 | `NormalizedMessage`；原文库 messages | 原始发送时间与 ingested_at 分开；平台ID与GUI指纹分别标明来源 |
| 同步/持久任务 | collector events、sync_state、message_media、collector_answer_tasks | 同批消息/事件/游标同事务；LIVE与BACKFILL独立；媒体失败不丢原消息 |
| 身份 | business bindings | 群/学生验证后才进入业务；GUI观察名称不是平台永久身份 |
| 事项/题面 | cases、materials/material_versions、questions/question_versions、turns | 学生版本是依据；引用关系校验学生归属；更正与上下文变化使旧待发答案失效 |
| 语义判断 | collector_answer_tasks.decision_json、RECEIVED_MESSAGE_RESOLVED、MESSAGE_LINKED | 已保存意图、决策指纹及Outcome；必须检查LINKED和当前版本。尚无答疑与绩效共同消费同一计量范围决策的完整接口 |
| 一次题面确认 | SOURCE_MESSAGE draft/task、SOURCE_QUESTION_INPUT_REVIEWED | 沿用原message/ACK/Case/版本；原图/原始时间/来源证明不变；不增第二次内容审核 |
| 生成 | runs/input_json、sessions、answers/answer_evidence | 固定学生版本、完整实际Skill及哈希；独立会话；未知提交结果不重放 |
| 交付 | outbox、delivery_checks、audit | 源消息/绑定/版本/正文核验；AI成稿、粘贴草稿和SENT工具返回都不是实际交付 |
| 计量单元 | performance_units、performance_links、performance_events | 首次独立提问时间不被追问重置；候选/收到/模拟发送不计完成；仍需接共享语义判断 |
| 当前交付资格 | PerformanceLedger.delivery_eligibility | 只读核对原绑定、版本/上下文/材料、完成时间、实际Outbox与readback。普通追问只在连续版本及逐条审计可证时保留历史完成资格，不放宽旧答案发送 |
| 日报 | PerformanceReports.build/generate | build只读；当前资格有效才计三栏；submitted历史保留，重算修订另存 |
| 项目证据 | workspace_snapshot、verify_evidence、project_doctor | 实现状态与验证等级独立；代码增删改或日志变化使旧证据失效；离线证据不提升真实等级 |

## 共享事件的现状

| 统一含义 | 已有记录 | 尚缺什么 |
|---|---|---|
| message_ingested | collector events.message_received | 真实新消息事实入口尚未验收 |
| media_ready | message_media状态及媒体字段 | 可执行的共享事件投影 |
| semantic_decision_confirmed | decision_json、RECEIVED_MESSAGE_RESOLVED | 共同消费接口；NEEDS_REVIEW不能当确认完成 |
| question_version_changed | question/material版本、MESSAGE_LINKED及失效记录 | 统一变更事件投影 |
| answer_generated | runs/answers/answer_evidence | 保留simulated、版本和完整输出证据 |
| delivery_verified | outbox/delivery_checks/audit | 人工最终发送后回流至原Outbox的公共核验入口 |
| counting_unit_changed | performance_events | 共享语义决策到候选/关联的接线 |
| human_review_resolved | HUMAN_REVIEW_RESOLVED及单次题面确认记录 | 按审核类型区分，不泛化为全部审核 |

## 并行修改约定

当前目录不是Git仓库，无可复用分支或worktree；不初始化、reset、stash或推送用户文件。主Agent管理Schema、迁移、入口、全局配置、状态和最终证据。子Agent使用明确的不同文件范围、匿名临时数据库及独立端口；公共契约变更先提交给主Agent。

实际桌面仅由Luna medium独占；答疑语义和教学判断交真实DeepSeek网页与实际gaokao Skill。Codex子Agent的执行模型不是已经验证的业务API model_id。新平台上传、收费、报表提交和广泛真实外发不在本轮授权内。

现有原群固定“收到”的明确授权保留；所有新外发仍需原Outbox与目标核验。低层Windows-MCP人工工具桥不构成业务Outbox闭环，不能用它的工具成功记录补造SENT或正式绩效。
