# 当前能力与缺口

截至2026-10-04，源码提交仅包含程序、测试、配置示例与通用说明。本机聊天记录、学生图片、数据库、登录状态、运行凭据及历史验收材料保留在原工作目录，不上传。

## 当前最终验收基线（2026-10-03）

以本人最新 Level 3 验收标准为准，起点业务提交为 `b697faa3171018fa83a36cee8b127d5d840522bf`。当前结论为 **NOT_READY**；以下逐项映射实际代码与测试，UNIT/MOCK 不代替 REAL。后文日期更早的记录保留为历史，不代表当前版本通过。后续获准的 DeepSeek 网页专项验证与生产闭环分别记录；未操作生产数据库、未开启群发送，本轮没有数据库迁移。

| 最终条件 | 已有实现和相关测试 | 当前缺口/验收状态 |
|---|---|---|
| 1. ANSWER 冻结 | `answer_teaching.py`、`teaching_routes.py`、`lesson_checks.py`；`test_answer_teaching`、`test_teaching_bundle`、`test_lesson_checks` | b04 原文和依赖已实际核验；阅读/完形四选项受检包已接原脚本 SOURCE/DRAFT 检查。真实阅读专项已上传固定原文，生成稿有一项 REVIEW_REQUIRED；其他题型仍只有预览/人工流程，教学正确性未验收 |
| 2. 消息身份/时间边界 | `native_message_source.py`、`collector_storage.py`、`collector_dispatch.py`；`test_native_message_source`、`test_collector_integration` | 文件由本机受控复制/人工准备，现有增量导入不是持续监听；生产者运行、停止检测和端到端最大延迟尚无真实证明 |
| 3. Ack 时效 | `delivery_tasks.py`、`automatic_delivery_runtime.py`、`automatic_answer_runtime.py`、`message_sla.py`；相关发送/等待/时延测试 | 发送层及后台执行循环已分离并持久化，可选详细队列已接线。状态接口已按有证据的source_sent_at及完整真实回执计时，包含采集等待，迟到成功仍标超时；匿名边界通过，真实端到端15分钟未验收 |
| 4. 检索备用 | `reference_lookup.py`、`reference_fetch.py`；`test_reference_lookup`、`test_reference_fetch` | 已补有界备用调度；默认检索和网络访问关闭，示例选择 Brave，实际 Provider、凭据和来源准入仍未验证；网页专项不把未配置记为无搜索结果 |
| 5. Top2 | `reference_lookup.top_candidates`；`test_reference_lookup` | 本批确定性最多两项、同内容去重和真实候选 ID 通过 Mock；尚未交到真实 DeepSeek |
| 6. 两次 DeepSeek | `question_matching.py`、`mcp_preparation.py`、`mcp_generation.py`、`automatic_answer_runtime.py`；两阶段/队列测试 | 授权真实专项已完成同一会话两次上传、提交和结构解析，草稿待复核；没有检索候选，未走生产队列。已补首次请求后认领真实链接、提交消息证据及截断保护的Unit/Mock；修补后的自动队列、完整正文采集及真实Top2路径仍未验收 |
| 7. 会话及追问 | `workflow.py`、`session_isolation.py`、`followup_reuse.py`、`mcp_generation.delivery_context`；`test_question_session_isolation`、`test_mcp_followup_context`、`test_followup_reuse` | 普通追问沿用原核验与课程上传证据、同一会话仅上传本轮上下文已有 Mock；真实网页复用未验收 |
| 8. Answer 安全重试 | `reviewed_question_queue.py`、`delivery_tasks.py`、`mcp_generation.py`、`question_matching.begin_attempt`、`followup_reuse.begin_upload`；相关队列与两阶段测试 | 第二阶段完整回复的格式失败可同会话有限重试，保留原调用和失败证据；未知网页提交或追问上传不能改文件名重提，UNKNOWN不重发。原调用已确认的完整生成捕获可核验后回写；教学疑点仍转人工，其他网页阶段恢复和真实验证仍缺 |
| 9. 自动发送 | `workflow._validate`、`mcp_group_delivery.py`；`test_mcp_group_delivery`、`test_automatic_delivery_workbench` | 目标/版本/未知结果/接管防护已有 Mock，当前测试群真实自动发送未授权执行或验收 |
| 10. 实际交付回流 | `delivery_batches.py`、`manual_delivery.py`、`workflow._record_check`；`test_delivery_batches`及原交付测试 | 一个完整稿对应一个Outbox分段计划；顺序核验、部分不计量、全部成功回流原绩效已接通Mock；真实多段发送未验证 |
| 11. 中途更正 | `service.py`、`workflow.py`；`test_delivery_batches`及原版本测试 | 保留旧版本拦截；多段中途更正停止余段并保留已发事实通过Mock，真实更正链路未验收 |
| 12. 绩效一致性 | `semantic_decisions.py`、`performance.py`；`test_shared_semantic_consumers`、`test_performance_rules`、`test_performance_delivery_eligibility` | 原归属/计量/夜间规则保留，模拟边界已覆盖；真实新闭环的投影未验收 |
| 13. 实际成本 | `call_costs.py`、搜索/两阶段/本机Luna调用入口、`tools.report_task_costs`；费用与执行测试 | 已按原run/audit记录观察到的调用及人工账单证据，沿用原确认篇数计算均值/P50/P95/上限；本机网页循环的Luna计量已接，其他调度/付费调用、内部网络重试及真实账单尚未完整采集。未知费用或未分配任务不允许宣布达标 |
| 14. 重启恢复 | 现有 SQLite、Outbox、`reviewed_question_queue`；相关队列/发送/会话恢复测试 | 匹配结果同库重读、确认生成捕获后尚未完成原run的回写，以及发送前、第一段后、第二段副作用后UNKNOWN、最后一段回流已有Mock；完整六断点与真实恢复试运行仍缺 |
| 15. 无阻断级问题 | 现有版本/路径/目标/未知发送回归 | 真实 A–H 类别及至少 20 个任务试运行尚未执行；测试数量不证明生产稳定 |
| 16. 限制公开 | 本文件、README、现有验证日志 | 保留 UNIT/MOCK/REAL/SKIPPED/FAILED 区分；CLI、SDK、上云、集群和符号链接权限不当作个人 Demo 上线前置条件 |

### 授权网页重测与 Edge 插件接入（2026-10-04）

业务基线为 `bd046933a5226a234439f45294be064f8e025e8f`，本次授权仅为已回复阅读题的真实 MATCH、TEACH 各一次，只生成草稿。主 Agent 检查文件、原始记录和数据库，仍只有一个 Luna medium 操作端。新专项与此前已保存的草稿分开；没有修改生产库、8767 服务、发送策略或正式日报。

ANSWER 仍为干净的 `b04ebc26d7fa096404111a0bb12f6c77cc8525b9`，未更新或修改教学内容。复用原位置已核验的原文教学包，SOURCE_READY；重定位的 manifest 因路径绑定检查被拒绝，未拿它上传或冒充有效包。检索仍为 UNAVAILABLE_NOT_CONFIGURED，候选零，不伪称搜索完成。原始发送时间仍为空，不用本轮当前时间补值。

REAL：Windows-MCP 已在空白 DeepSeek 页面上传原 JPEG 和题面 JSON；图片在树和视觉中可见，JSON 文件名仅由视觉确认，分别保留证据。609 字符原 prompt 通过新 DOM 逐字核验后提交一次，取得真实会话链接。Luna 在最终截图看到完整回复，但多次未截断的 DOM 树漏掉正文，尚无可供程序接受的完整 MATCH 输出。因此本轮没有捕获成功、结构核验通过或第二阶段提交的结论，也没有新草稿。

正常关闭两个专用 MCP 连接后，主 Agent 独立核对已知桥进程不存在、原生 windows-mcp.exe 为零，用户独立打开的同一个 Edge 进程及创建时间保持不变。未重新提交 MATCH。TTY 长输入失败使用私有助手的固定引用解决，不把它当成生产管道故障；第三版只读复制助手未发原生请求即关闭，随后按用户明确选择的 @Edge 转入浏览器插件。

Browser 插件初次初始化因请求 `26.930.21537` 的 service 文件，而缓存仅有 `26.915.31945` 失败。当前已安装应用 `26.930.2377.0` 自带插件 manifest 的实际版本恰为 `26.930.21537`；从安装包补齐该完整缓存，384 文件、13,776,842 字节逐文件 SHA256 一致，旧缓存保留，未改写插件源码或用户登录数据。初始化恢复一次后 setup 成功，`get('edge')` 仍返回 Browser is not available，诊断 family 列表为空；没有取得 tab 或进行插件页面操作。

按该插件官方只读脚本检查：Edge 已安装且运行，扩展在选定环境已安装、启用；native-host 检查退出 1，连接注册及 manifest 缺失。按 `control-in-app-browser` 指定的连接排障文档，不自行安装或修复 native host，不改用其他控制器绕过显式 Edge 选择。已请用户通过应用插件界面重装 Browser，或明确选择暂用原 Windows-MCP 继续同一会话；等待答复期间不进行后续页面操作。各次失败、缓存恢复原文件哈希和诊断原始输出保留于忽略的私有专项目录。

只读核对专项库 integrity_check=ok，run 保持 RUNNING、完成时间为空；答案、交付核验和绩效单位/关联/事件均为零。原生输入、提交及 URL 等 5 份原始结果与 journal 独立核验通过；恢复检查点要求只读原 MATCH，不重放。成本记录只有一次 MATCH 尝试，完整费用仍 UNVERIFIED。程序源码未变，未重跑 UNIT/MOCK 或全量；此前 SDK 与两项符号链接 SKIPPED 未复验，不算通过。当前结论仍为 **NOT_READY**。

以下至“后续实施”之前为已提交 `7b6b128` 的上一批结果，保留其当时限制和测试证据；本批增量以“后续实施”为准。

上一批实际处理：ANSWER 默认分支 main 最新 `b04ebc26d7fa096404111a0bb12f6c77cc8525b9`，先检查旧工作树干净，再 fetch 并快进到该精确提交，工作树仍干净；没有改写教学内容。新版 README 明确客观题共用 `gaokao-grammar-fill/scripts/check_lesson.py`，路由改为该仓库相对路径。主客观题只取自己的教学模块和共享脚本快照，不混入语法教学方法。新增测试保留原文、脏文件、缺项拦截、人工入口、缓存防串提交等断言；旧位置即使出现同名文件也不作为缺项替代。

六类教学包本地实际生成并再次核验，必需缺项均为零，重复构建字节和修改时间均不变；提交、源文件哈希和只读原文清单留在忽略的 `data/private/teaching-bundles/`。只对明确标注的自建阅读题运行上游原脚本：定位返回 0；故意缺少讲解的稿件返回 1、报告 2 个疑点，这是预期拦截，不是教学通过。脚本 SHA256 为 `58322af6d1b794bb9bb862a2da0b800d777e225bac4b70cbd1e0130dded6323f`；结果保存在 `artifacts/verification/20261003-answer-freeze/checker-smoke/`。自动教学仍未激活，真实 DeepSeek 未上传，真实交付未验证。

检索只补缺口，不改变 `domain.compare` 或候选确认：新增 `initial_question` 触发原因和 `top_candidates` 输出，最多两项、稳定排序、同内容去重、不带外部答案，登记后绑定现有候选表 ID；候选少于两项不补造。最多三个适配器共享六次查询/六页及现有时间预算，失败或空结果为备用保留额度，按来源交替选页以免首来源挤占全部页数；页面仍逐个执行准入和存储检查。生产配置仍只有既有 Brave，多适配器通过测试注入验证，不伪称真实备用网站已接通。

相关验证：`python -X utf8 -B -m unittest tests.test_teaching_routes tests.test_answer_teaching tests.test_teaching_bundle` 为 44 项、43 通过、1 符号链接跳过，49.297 秒。`python -X utf8 -B -m unittest tests.test_reference_lookup tests.test_reference_providers tests.test_reference_resolution tests.test_reference_workbench` 最终 84 项全部通过，9.043 秒；首轮 1 项失败发现首次候选输出的 tuple 与 SQLite/缓存 JSON 的 list 不一致，已在真实输出边界统一 JSON 表示并保留重复运行一致性断言。该轮没有外网题库、付费调用或业务消息输入。

全量命令 `python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261003-answer-freeze-top2-final`：1147 项、1144 通过、0 失败、3 跳过，227.040 秒，退出码 0，运行期间源码未变化。源码快照 `sha256:37a12291e90902a81f54dc21c28546bfd924cee00f9ea5e899f72885d1f6f599`；原始日志和 evidence.json 已复核，证据有效，等级仍为 MOCK_INTEGRATION_VERIFIED。3 项 SKIPPED 为官方 SDK 文件未提供、教学依赖符号链接无法创建、浏览器目录符号链接无法创建，均不计通过。真实核验仅为上述 ANSWER Git/文件/脚本的本地运行，未获得真实新题、网页生成、外发、绩效或成本样本。

### 后续实施：每题原脚本检查与两阶段入口

本批从 `7b6b1288c6d6ac37c58b13d70e1bd28bec590fb3` 继续，未更改冻结的 ANSWER 提交。原有 format2 预览保持禁生成；显式 `--for-generation` 只为现有四选项阅读/完形构建独立 format3 缓存。六类包齐全不等于六类自动任务都已支持：现有 `Question.complete`、选项结果和网页契约仍限四选项，七选五、语法、写作自动任务未接通，原文与人工流程保留。

`lesson_checks.py` 使用已核对哈希的 ANSWER 原脚本，固定参数、10秒超时、无 shell，按当前题号检查源文及实际成稿；临时输入用完清理。定位卡进入第二阶段文本，检查疑点或失败阻止生成稿进入发送。证据写入原 run/audit，不增数据库表；发送前重查教学来源、题目/上下文绑定、正文哈希及程序保存的 SOURCE/DRAFT 记录。模型自报成功或置信度不能代替这些记录。SOURCE 失败不取消独立 ACK。零自动疑点不证明完整教学正确。

`question_matching.py` 将有界检索的真实 Top2 固定在同一 run，候选/外部答案不覆盖学生题面。新 `VERIFY_THEN_TEACH` 复用当前 Windows-MCP 上传/页面校验：先上传学生图与不含教学文件的核验文本，仅提交一次六字段结构化核验；程序沿用 `domain.compare` 校验候选、四选项双射和差异。清晰无适用候选可按学生原题继续；模糊、冲突、配置不可用和中断明确停止。通过后上传同提交 ANSWER 与含核验结果的上下文，再由原生成入口发起第二次教学。已有题面人工核对只复用一次，没有伪造新的人工审核。

第一次上传前已在原 audit 持久化尝试，换文件名不能绕过未知提交；核验结果、页面证据和原会话绑定可同库重读。普通追问的原核验/附件复用尚未接通，新的受检入口明确转待处理，不暗中重做全套搜索或重新上传。默认工作台尚无生产网页执行回调，未部署到运行配置，也未打开发送。这是可供受控联调的两阶段入口，不是 Level 3 验收通过。

本机真实原脚本核验：b04 的阅读、完形受检包已分别生成到忽略的 `data/private/teaching-bundles/answer-b7bc64968b256a1a2d237ade/` 与 `answer-db966bf4c81cf6ebee2039b6/`；只对明确标注的自建文字题调用当前 `Workflow.start` 与 `check_lesson`，两类定位均 SOURCE_READY，故意不完整稿均 REVIEW_REQUIRED、2 个疑点。原始结果在 `artifacts/verification/20261003-teaching-matching-source/results.json`。这是本地脚本真实运行，未包含真实 DeepSeek、学生消息或交付。

相关模拟验证已覆盖 24 种选项排列、题号、NOT/EXCEPT、数字角色及范围端点变化、缺选项、候选冲突、无候选清晰题、同库重读、旧版阻止登记、原图先于课程、两次网页提交、草稿不计绩效、第一次提交未知不重发。首轮原脚本进程错误被误报为 JSON 错误，已修正分类并保留断言；另一次测试命令误写两个不存在的模块名，属测试命令失败，改为仓库实际模块后重跑，不作为通过。

首次全量回归 `artifacts/verification/20261003-teaching-matching-final/` 为1172项、1169通过、0失败、3跳过，299.117秒，运行期间源码未变。之后复核修正了“已拒绝候选会使整个核验入口停止”的边界：保留其拒绝状态，只作为第一阶段差异证据，不能被模型提升为可用同题；清晰学生题面仍可选择 STUDENT_ONLY。专项用例通过；最新全量结果以后续验证记录为准，旧快照不冒充修改后版本。

最新全量命令：`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261003-teaching-matching-final-v2`。结果1173项、1170通过、0失败、3跳过，299.449秒，退出码0，源码未在运行中变化。快照为 `sha256:17414bb3979f951bff11a95fc93b61f936ef45fdd8bc7142635fb719538a51cf`；evidence.json 与日志复核有效、未过期，等级仍为 MOCK_INTEGRATION_VERIFIED。SKIPPED分别为官方SDK文件未提供、教学符号链接无法创建、浏览器Profile符号链接无法创建，均不计通过。不要求为这三项提升管理员权限；不是实际路径防护失败。REAL仅上述本机ANSWER原脚本与原文检查，真实网页、群发送、真实成本与正式日报提交均未执行。

下一步补普通追问复用、生产网页调度连接、完整交付分段和实际成本归集，再完成六断点恢复与授权真实试运行。官方 [ChatGPT 浏览器扩展](https://learn.chatgpt.com/docs/chrome-extension) 支持 Edge 的文档已核对，仅作为网页联调候选；未确认本机扩展连接或 Python 后台调用能力，不将其列为启动依赖。

### 后续实施：完整答案的顺序交付（2026-10-03）

从 `9533cfdf1ea0fd889ee0db39b6f65948384ccbd9` 继续。重新核对ANSWER检出目录仍干净、HEAD仍为 `b04ebc26d7fa096404111a0bb12f6c77cc8525b9`；没有更新教学包或改变教学规则。没有操作真实桌面、发送消息、调用DeepSeek、部署8767、修改正式数据库或迁移结构。

`delivery_batches.py`复用原Outbox的完整答案、audit的唯一分段计划和delivery_checks的逐段证据。固定formatter按2000个UTF-16单位、优先换行切分，逐字还原完整稿；不添加提示前缀、不压缩、不让模型决定拆分。每段有固定传输ID，UI核验绑定当前群、学生、正文和该段。每次只发当前一段，已核验后才推进；不确定时原任务SEND_UNKNOWN，停止余段，显式只读核验后继续。重试预算按段保存，重启不重放此前段落，新ACK可在两段间优先发送。

完整回执明确为 `ORDERED_TEXT_BATCH`，依据全部实际分段回执聚合，不伪造单条平台消息ID或已读状态；只有完整核验才更新原任务、原语义计量单元。计量资格会重新检查计划、所有段顺序与证据，删除一段证据后不再计入确认数量。部分旧答案已经发出的正文进入后续上下文，未发余段仍不进入。更正/人工接管保护保留；原整稿人工粘贴及Worker发送不能接管已开始的分段，避免整份重发。工作台已有任务卡显示已核验段数和未知段。

新增匿名测试覆盖原文/Unicode/换行不丢失、唯一计划、同库重启、ACK插队、逐段有限重试、第二段未知、只读恢复、错误群、回执串段、计划篡改、部分交付与追问、中途更正、最后一段恢复、夜间1篇和重复日报。真实MCP适配器使用合成UIA/剪贴板协议，仍属于MOCK。旧人工/Worker入口保留；本轮没有为它们另建分段通道。

相关结果：原发送/人工交付/绩效74项通过；新增首批11项通过。相邻组合命令曾因误写不存在的`tests.test_worker_coordinator`报1项ImportError，其余104项通过；已改用仓库实际存在的`test_worker_business_api`、`test_worker_runtime`、`test_worker_reliability_acceptance`。新增补充与Worker/追问组合77项全部通过，10.184秒。上述范围有重叠，不相加宣称测试总数。

补充检查实际复现了同一回执时间下第10段排到第2段前的问题；现按实际时间和持久回执顺序合并完整/部分交付，不按传输ID排序。丢失分段证据也会阻止该完整稿进入后续教学上下文。首轮完整回归 `20261003-ordered-delivery-final` 为1187项、1183通过、1失败、3跳过，302.787秒；失败来自`test_single_input_review`的历史交付fixture只置状态、未提供时间，已补明确合成时间并保留原文断言。该轮检查期间有上述修正，code_changed_during_run=true，不作为最终证据。新混合历史测试曾误把追问ACK成功当成答案成功，断言现明确校验原答案task_id。修正后相关62项全部通过；当前分段专项16项全部通过，2.950秒。最终固定源码回归结果随后记录。

最终回归命令：`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261003-ordered-delivery-final-v2`。1189项、1186通过、0失败、3跳过，302.975秒，退出码0，源码运行期间未变化。快照 `sha256:b21859e6a0275369f8fe1c965e1b29827641d35f2bbd56c51819ca93aef0401e`；原始日志与evidence.json复核有效、未过期，等级 **MOCK_INTEGRATION_VERIFIED**。3项SKIPPED仍为未提供官方SDK文件、教学符号链接无法创建、浏览器Profile符号链接无法创建，均不算通过。没有新真实账号、网页、群发送、付费调用或日报提交样本。

当前仍为 **NOT_READY**：持续收题生产者、原始发送时间起算的SLA展示与真实15分钟Ack、真实两次DeepSeek、普通追问复用、实际成本及A–H试运行仍缺。Edge官方浏览器扩展可用于Codex真实网页联调，当前会话未接通，Python独立调用未验证；不把扩展作为新的启动依赖。

### 后续实施：按学生原始发送时间显示应答时延（2026-10-03）

从`b151f81a39703810441327d6d283a87aa96c13ff`继续，`message_sla.py`只读取原messages/Outbox/delivery_checks，不增加表或改写消息。`workflow.dashboard`不再以created_at计时；原时间必须有来源、定位与证据，原时间缺失、冲突、时序矛盾保持待核验。按明确时区计算采集等待和实际交付时长，收到15分钟边界包含等号，已迟到成功仍保留超时事实。模拟回执、错误绑定、未核验或缺时间回执都不建立真实SLA结果；手工多部分和自动分段沿用既有完整性校验，少一段不算完整答疑。

原状态接口把详细答疑的固定3600秒当成超时线，但没有相应已确认政策配置。本批保留实际答疑时长，`answer_overdue`与门槛保持未判定，正式绩效时效继续使用原确认规则，不另造考核。首次专项21项中20通过、1失败：复用的匿名夜间fixture将提问改成23:10，却留下23:05采集时间。仅在新SLA测试的临时库中提供明确合成的23:11采集时间，保留矛盾时序拒绝测试；未修改真实消息来制造边界结果。随后57项相邻回归全部通过，12.668秒；补充自动分段证据缺失检查后的最终结果见后续记录。

本批仍无真实桌面、DeepSeek请求、群发送或正式日报提交。ANSWER版本未改，生产入口未部署。Edge官方浏览器扩展支持文档已核对，可以作为网页联调入口候选；未验证本会话连接与Python独立调用，不增启动依赖。原始时间起算已补；连续生产者、普通追问复用、真实网页/发送、实际成本与稳定试运行仍缺，结论保持 **NOT_READY**。

最终专项命令：`python -X utf8 -B -m unittest tests.test_message_sla tests.test_manual_delivery_workbench tests.test_workflow tests.test_delivery_batches`，58项全部通过，13.330秒，0失败、0跳过。其中13项为新的时延测试，覆盖原发送与采集差值、15分钟含边界、跨时区、迟到成功、时间缺失/冲突/倒置、模拟与未知回执、人工部分交付及自动完整分段缺证据；其余复用原工作台HTTP/无界面浏览器和发送回归。均为UNIT/MOCK，不是REAL。此次没有重跑全量；上一批全量快照不能替代本批源码的验证。

### 后续实施：普通追问复用原核验和会话附件（2026-10-03）

从`0674a586aec26615e11c82a9553dbbfa8af9cdf5`继续。本机ANSWER仍为干净的`main / b04ebc26d7fa096404111a0bb12f6c77cc8525b9`，未更新教学内容。`followup_reuse.py`只在原run/audit记录上传来源与复用关系，没有新增表或第二份答案/交付台账。原核验仍绑定原任务，不改写原证据来冒充本轮网页调用。

只有原任务具有PreparedDeepSeekGenerator完整生成记录、原上传/核验/最终页面证据可重新核对、学生/题目版本/会话/教学文件一致时，普通追问才复用。准备记录区分本轮上传与旧会话材料；追问只上传本轮上下文，跳过完整搜索、原题第一阶段和整包ANSWER上传。未实际交付的旧稿不进入实际回复历史。新图、课程或版本变化、旧证据缺失均停止；实质更正仍由原版本路径重新核验。上传前持久化一次尝试，未知结果不能改文件名重试。生成中更正继续由原Workflow阻止旧版发送。

原八项匿名SQLite/模拟网页专项全部通过，228.528秒，覆盖连续追问、实际交付上下文、同库重开、原证据缺失、新图、实质更正、错误会话/来源和未知上传。新增四项全部通过，120.254秒：原生成结果未知/被改写、复用后旧准备文件变化、追问生成中更正保留旧稿且禁止发送、原消息入口无图追问复用原图及原始时间。新增专项共12项，最终随下述全量固定源码验证。测试中的界面回执与原脚本均为明确合成夹具，不是新的真实学生或DeepSeek证据。

本批没有操作真实桌面、上传DeepSeek、向群发送、改动正式数据库或部署8767。历史段落中的“普通追问尚未接通”是当时状态，以本节为准；生产网页执行回调、持续收题、实际成本和授权A–H稳定试运行仍未验收，结论 **NOT_READY**。Edge官方扩展文档支持已登录浏览器操作，本机Default配置中已检测到ChatGPT扩展文件、版本1.26.901.11451；当前聊天连接、题图上传和Python独立调用尚未验证，不把它加入启动依赖。

最终回归命令：`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261003-followup-reuse-final`。1214项、1211通过、0失败、3跳过，676.667秒，退出码0，源码运行期间未变化。快照`sha256:29ef8ed091b04cc14ff6e668a5cbadc6208e0d9d634df08a5a68acbaa949baab`；原始日志及evidence.json复核有效、未过期，等级 **MOCK_INTEGRATION_VERIFIED**。SKIPPED分别为未提供官方SDK文件、教学依赖符号链接无法创建、浏览器Profile符号链接无法创建，均不计通过。本批REAL仅本机ANSWER Git状态和Edge扩展文件的只读检查；真实网页、群交付和正式统计未执行。

### 后续实施：观察调用与费用证据（2026-10-03）

从`0002be76b3ecd2d7e4326d52c323556be92b5d0a`继续。本批新增`helpdesk/call_costs.py`、`tools/report_task_costs.py`与匿名费用测试，修改现有`reference_lookup.py`、`question_matching.py`、`mcp_preparation.py`、`mcp_generation.py`、`followup_reuse.py`及对应测试。调用只记在原audit表，不新增数据库、迁移或独立进程，不改变交付和绩效完成判定。每条记录使用原run及其学生/题目/版本/会话绑定；启动前记录尝试，返回后记录观察结果，中断仍保留未知，不能重放。费用文件限定批准目录，账单项按内容哈希和已核验条目标识去重；人工登记明确是人工账单核验，不伪造平台计费回执。

搜索缓存及普通追问复用会记录本次未再调用的证据。复用事实先保存、费用范围记录中断时，重启重新核验原材料再补记录，不再搜题或提交。实际费用不从消息数、调用数、模型置信度、网上单价或“网页版免费”推算；未采集范围和费用证据缺失、变化均保持UNVERIFIED。只有原绩效篇数已确认、完整实际交付仍有效、所有关联任务成本可核验且没有遗漏未分配任务时，才计算平均/P50/P95/最大值和上限结果。同题追问归入原篇，数量大于一篇或成本归属歧义不擅自平摊。

只读命令`python -X utf8 -B -m tools.report_task_costs --db <现有业务库>`使用同一读取快照，不创建/迁移数据库或启动工具。加`--run <已有run_id>`查看任务，`--max-average-cny`明确覆盖默认0.50元门槛。本批没有自动账单接入或人工费用页面；费用登记暂为本地受信函数。SCHEDULER/OTHER目前未自动计量，重试计数只覆盖观察到的适配器尝试，页面抓取内部网络重试仍未计量。因此本批尚未满足完整“实际成本”验收条件。

本机ANSWER版本保持冻结，本批未更新或修改教学内容。Edge官方扩展支持范围已按OpenAI Docs重新核对，Default目录有ChatGPT扩展文件、版本1.26.901.11451；已安装文件不代表本会话已连接，不证明Python独立调用和题图上传。仍不加启动依赖。本批没有操作真实桌面、上传/提交DeepSeek、向群发送、改正式数据库、部署8767或提交日报；结论保持 **NOT_READY**。

首次费用专项16项中15通过、1测试错误：新测试误用不存在的`performance_deliveries`表。改为使用原交付方法返回的Outbox ID，保留UNKNOWN断言，随后16项全部通过。相邻回归命令`python -X utf8 -B -m unittest tests.test_call_costs tests.test_reference_lookup tests.test_question_matching tests.test_followup_reuse tests.test_mcp_generation tests.test_mcp_preparation tests.test_performance_delivery_eligibility tests.test_delivery_batches`，119项全部通过，389.906秒，0失败、0跳过。

回归后补充未分配任务不能被费用汇总遗漏、未知付费次数不显示零，以及数据库文件丢失时不能新建空库；最终`python -X utf8 -B -m unittest tests.test_call_costs`，18项全部通过，0.751秒。另执行`python -X utf8 -B -m unittest tests.test_question_matching.TwoStageIntegrationTests.test_teaching_unknown_keeps_one_cost_attempt_and_never_resubmits`，1项通过，12.383秒：第一阶段已捕获、第二阶段提交未知时仍仅保留一次尝试，无答案或额外调用。三组命令计数存在重叠，不相加宣传覆盖量。均为UNIT/MOCK；真实费用平均/P50/P95/最大值均UNVERIFIED，没有真实账单或本批真实付费调用。REAL仅ANSWER干净工作树/提交及Edge扩展文件只读检查。本批专项无SKIPPED或剩余失败；此前官方SDK文件与两项符号链接SKIPPED未复验，仍不算通过。未重跑全量，上一批全量快照不替代本批验证。

### 后续实施：接通已审核题目的网页执行队列（2026-10-03）

从`83f44740772730dbfd6d26559dc7c4c7886a2d6b`继续。新增`helpdesk/automatic_answer_runtime.py`、禁用的`config/automatic-answer.example.json`及匿名测试，修改`demo_server.py`、`luna_navigation.py`、`run_current_demo.ps1`、README和本文件。没有数据库迁移、第二份任务/完成/绩效台账或新的服务；ANSWER仍为干净的`b04ebc26d7fa096404111a0bb12f6c77cc8525b9`，未更新或改写教学内容。

可选本机循环已连接原reviewed_question_queue.advance的三个回调，复用prepare_input、DeepSeekSessionPreparer和run_existing。Luna固定medium，仅在受控截图中定位新建或编辑器控件；坐标通过新快照、唯一节点、网址、显示器范围及原生输入保护复核，模型不能提供目标群、URL、路径或教学结论。SCHEDULER调用在原audit计量，未知费用仍不补零，OTHER及真实账单/内部网络重试尚未完整采集。

新会话只点击一次，仅观察到独立的真实唯一链接后绑定；空白页或无法确认时停止，不编造UUID/链接或发占位请求。拥有明确会话的题目可继续准备，强制原图+Top2核验再ANSWER教学；跟进仍沿用原会话和已确认材料。生成写回原run/Outbox，草稿不计量，原实际交付回流和发送策略保持。

执行循环与收到/入队循环分开，共享已有桌面锁，每次原生调用结束释放，生成等待不占锁。执行前先恢复并检查持久事实；前台页面不可用时最多3次检查，次数在audit保留，未创建网页执行尝试；验证码/登录提示直接暂停。真正开始的网页步骤保留原STARTED/UNKNOWN不重放；未返回的原生操作保留真实进程并持有桌面锁，进程退出前拒绝恢复。当前需要对应Edge页面前台，不自动切页或切群；真实焦点协调及新聊天即时URL均UNVERIFIED。

初轮11项出现2个失败、1个测试错误，原因是临时fixture改工作目录后缺默认检索配置，以及误写模拟发送模块名。定向3项的2项通过、1项错误为fixture把字符串DB路径传给要求Path的服务器。修正fixture与实际模块后40项出现1失败、1测试错误：更正后原run成为失效状态，队列正确投影NEEDS_ATTENTION，测试原先误期望EXECUTION_UNCERTAIN；未知费用断言误用了不存在的汇总字段。现断言原调用记录为UNKNOWN、无点击/绑定/答案，不弱化防护。加入持久页面等待及安全恢复后，`python -X utf8 -B -m unittest tests.test_automatic_answer_runtime tests.test_luna_navigation tests.test_automatic_delivery_workbench tests.test_reviewed_question_workbench tests.test_call_costs tests.test_reviewed_question_queue`79项全部通过，130.394秒，0失败、0跳过。补充验证码暂停后的最终验证另记；这些范围有重叠，不累计宣传数量。

本批没有真实桌面、Luna调用、上传或DeepSeek提交、群发送、生产数据库修改、8767部署或正式日报提交。UNIT/MOCK验证不能代替REAL，真实新题持续采集、页面URL/控件与上传提交、15分钟ACK、成本和A–H稳定试运行仍未验收，结论 **NOT_READY**。已有官方SDK文件与两项符号链接SKIPPED未复验，不算通过。

验证码暂停补充后的命令：`python -X utf8 -B -m unittest tests.test_automatic_answer_runtime tests.test_mcp_preparation tests.test_mcp_generation tests.test_question_matching`，48项全部通过，155.457秒，0失败、0跳过；15项为当前执行入口测试。涵盖新链接无法取得不伪造、不二次点击、两阶段仅执行一次、旧稿不计量、已绑定会话复用、窗口/控件歧义、布局变化、已更正原题不操作、模型及原生未知结果不重放、三次只读页面等待跨重启保留、验证码全局暂停，以及生成等待时收到仍推进。启动帮助、改动Python语法、PowerShell入口解析及diff空白检查通过。上述测试均UNIT/MOCK；REAL仅本机ANSWER提交/干净工作树与Edge扩展文件版本再次核对，插件目录查询未证明本会话连接。未重跑全量，先前快照不冒充本批验证。

### 授权 DeepSeek 网页专项（2026-10-03）

从业务提交 `3dd1aa9d41d8c24d0a50d59866ed75d110d6dedf` 验证。授权范围仅为一道已回复阅读题的两阶段真实网页练习，生成草稿；不包括企业微信发送、正式交付或绩效入账。桌面由一个 Luna medium 操作端串行执行，主 Agent 只检查代码、原始工具记录和本地文件。实际显示器清单仅有编号 0，按已获授权的当前显示器操作，没有沿用旧屏幕 2 的固定编号。

`ANSWER_COMMIT=b04ebc26d7fa096404111a0bb12f6c77cc8525b9`，本机 main 工作树干净。重新生成阅读受检包，原文教学文件 46072 字节，必需依赖缺项为零，实际原脚本 SOURCE 检查为 SOURCE_READY；教材内容和脚本没有改写。原始照片按字节复制，学生原话保留；原消息发送时间无可靠证据，练习不能计入正式日报。

第一阶段已在真实页面上传原图和题面文件、提交一次，并从最终回复的复制按钮取回完整正文。原生记录和 journal 校验、六字段结构检查及固定教学来源复核通过，结果为 VERIFIED_STUDENT_ONLY。本次参考检索未配置，明确保留 UNAVAILABLE_NOT_CONFIGURED；没有执行搜索，不把它写成 NO_RESULTS，也不将专项结果伪装成正式检索回执。

真实界面确认两项自动入口缺口：新聊天在首次提交前仍是主页网址，现有队列要求点击后立即取得独立链接，尚未适配；use_dom=True 的原生树达到默认 500 元素上限时缺少回复正文。专项采用最终回复复制与原生 Clipboard.get 校验，未弱化生产页面解析断言。旧格式 console 输入曾损坏中文，在提交前发现并改用明确 UTF-8 读取与 ASCII JSON 转义；这不证明原生产 MCPProcess 的 UTF-8 管道有同样问题。

第二阶段准备期间，一次剪贴板调用的 journal 已 TOOL_RETURNED，但操作端未确认回传，按等待上限关闭原工具会话并确认单一新进程链。恢复时 Edge 进程已不在，原因未确认；重新打开已绑定会话、重新核验附件。原生输入成功记录的 content=[] 是有意隐藏回显，Clipboard.get 的固定说明前缀不属于正文；去前缀后的第二阶段原文逐字一致。没有重提第一阶段或将输入确认当成网页提交、实际交付。

第二阶段已在同一真实会话上传固定教学原文与核验上下文，逐字核对 640 字符的 prompt 后提交一次，取回完整最终正文。现有结果结构检查通过，模型返回当前学生选项 D；原稿原样保存为 `data/private/deepseek-pilot-20261003/draft.txt`，没有由主 Agent 改写。ANSWER 原脚本 DRAFT 检查返回 1、REVIEW_REQUIRED，唯一自动疑点为 method_visibility：首句没有点明实际课程方法。记录为 DRAFT_SAVED_REVIEW_REQUIRED，不伪称教学通过；本轮没有追加第三次请求或修改教学 Skill。

只读核对专项 SQLite 完整性为 ok，原 run 仍 RUNNING、完成时间为空；自动创建的模拟 ACK 保持 PENDING、sent_at 为空，实际交付核验与绩效计量单元均为零。原调用记录显示 DEEPSEEK_MATCH、DEEPSEEK_TEACH 各一次且已确认；这仅覆盖观察到的网页尝试，费用和完整调度成本仍 UNVERIFIED，不能据此声称免费或成本达标。收尾工具会话正常退出、已知 MCP 子进程全部结束且没有 pending 调用；此时 Edge 也未运行，退出原因未确认，浏览器与工具生命周期隔离仍需验证。

REAL 为上述网页上传、两次提交、完整结果取回、固定来源及原脚本 SOURCE/DRAFT 检查；DRAFT 的一项疑点保留待复核。执行命令为专项 `capture_pilot.py --stage MATCH`、`capture_pilot.py --stage TEACH`（均需已绑定网址及原生 URL/回复记录），以及只读 `python -X utf8 -B -m tools.report_task_costs --db <专项库> --run <本轮run>`；结构捕获命令均返回 0，DRAFT 子检查返回 1。没有重跑 UNIT/MOCK 全套；此前官方 SDK 文件与两项符号链接 SKIPPED 未复验，仍不算通过。准备时的中文损坏和回传未确认均记录并处理，不计为通过。

所有原图、数据库、网页会话链接、工具快照和专项脚本在 Git 忽略的 data/private 内。生产库、启动配置与发送白名单未改；当前仍为 NOT_READY。这是一条受控的网页生成练习，不代替生产收题、ACK、审核交付、追问、绩效与稳定试运行验收。

### 后续实施：首次请求后的会话绑定与页面截断保护（2026-10-03）

业务修改基线为 `7fe5a45e1e6987a3ffaa9101dfa3ecd2c3f3f2a9`。本批只修复上述真实网页练习暴露的自动入口缺口，未新增生产渠道、数据库结构或独立服务。ANSWER仍为 `b04ebc26d7fa096404111a0bb12f6c77cc8525b9`；本地工作树干净，未fetch、更新或修改教学内容。

`automatic_answer_runtime.py`、`mcp_preparation.py`保留原持久化队列：在新建网页前核对业务归属并完成有界检索。空白主页仅限当前Luna创建尝试，上传学生原图和核验输入后提交一次正式MATCH，再最多观察三次真实会话链接；不提交占位请求，也不自动重提未知请求。普通追问仍走原会话复用。不同导航动作分别记录调用身份，修复同一创建步骤中定位新会话与定位输入框误用同一成本尝试ID的问题，模拟调用不声明实际费用。

`question_matching.py`、`session_isolation.py`、`mcp_page_contract.py`在认领链接前检查原始学生/题目归属、当前版本、唯一真实网址、已发消息中的完整原prompt及唯一空输入框。仅有网址、prompt仍在编辑器或观察不完整时停止。会话URL归属与绑定审计使用同一个原Store事务，写入失败一起回滚；恢复时复用相同证据检查。`reviewed_question_queue.py`遇到有网址而缺少首次提交绑定证据的记录，直接转NEEDS_ATTENTION，不定位输入框或重新上传。生成稿仍由原run/Outbox处理，未实际交付不完成任务、不计绩效。

`mcp_transport.py`、`tools/windows_mcp_session.py`通过已安装Windows-MCP支持的环境参数将默认树预算从500设为4000，允许当前进程配置500—10000，保留工具超时且不修改上游安装文件。准备、输入、生成结果与会话绑定发现截断标记均停止；增加上限本身不证明真实完整正文采集成功。

相关模拟专项：`python -X utf8 -B -m unittest tests.test_mcp_page_contract`为9项通过；五项新会话定向回归为5项通过（105.974秒），覆盖prompt未发出、绑定证据写入失败完整回滚、孤立URL停止、恢复证据被改，以及一题两次调用并在重启后不重复入账。Luna medium只读静态复核确认所发现的两处缺口已修复；未把该复核当作运行测试。

只读比对既有真实快照还发现原生消息节点与属性之间使用两个空格，已兼容这种格式并补匿名节点测试，没有改变完整消息和空输入框要求。旧真实快照仍含截断标记，不删除标记或冒充完整记录。第一轮126项运行返回OK，但期间代码变化，只保留为初步结果；第二轮因这个格式修补主动中断，没有完整测试结果，不能计为通过。最终验证使用修补后保持固定的源码。

最终相关回归实际执行如下命令：

```powershell
python -X utf8 -B -m unittest -v tests.test_automatic_answer_runtime tests.test_question_matching tests.test_question_session_isolation tests.test_reviewed_question_queue tests.test_mcp_preparation tests.test_mcp_preparation_review tests.test_mcp_generation tests.test_mcp_page_contract tests.test_mcp_transport tests.test_windows_mcp_audit tests.test_followup_reuse
```

结果为132项全部通过、0失败、0跳过，718.519秒，退出码0。运行时间为2026-10-03 04:57:41—05:09:40 UTC，开始与结束源码均为 `sha256:a3f70753fa61d329b92ba6f29db8726abe3df70a1408b63f54e2da780bcd717c`，期间未变化。日志保存在忽略的 `artifacts/verification/20261003-new-chat-page-final-v3/unittest.log`。这是上述11个模块的UNIT/MOCK相关回归，不是全项目回归或真实环境通过；既有符号链接和SDK文件SKIPPED本批未复验，仍不算通过。

REAL：本批没有追加DeepSeek请求、进行桌面操作、修改生产库、部署8767或启用外发。前述授权网页专项保留真实证据与DRAFT_REVIEW_REQUIRED限制；这次自动入口修补仍为UNIT/MOCK，不替代真实队列、首次应答SLA、实际Top2及A–H/20任务试运行。当前仍为NOT_READY。

### 后续实施：已确认生成的重启回流（2026-10-03）

基线为`1237a092910bb3ca51e59b428ff15d746fe0359a`。ANSWER仍冻结在`b04ebc26d7fa096404111a0bb12f6c77cc8525b9`，没有更新或修改教学原文。本批只修补现有网页入口与生成回流，不迁移数据库、不改生产配置、不新增服务。

首先复现“新建后出现变化后的会话URL，但页面仍有另一题的已完成回答”会被错误认领的缺口；修复前新用例失败，修复后13项相关UNIT/MOCK通过（97.468秒）。主页和直接获得会话URL的两条新建路径共用旧回答/生成标记检查；已有绑定会话和正常追问不受这个新建检查影响。

其次复现生成文件已为FINAL_OUTPUT_CAPTURED、原调用已确认，但尚未Workflow.finish时中断，重启仍变EXECUTION_UNCERTAIN的缺口。新用例首次失败（34.640秒），修复后同库重启用例通过（41.010秒）。`mcp_generation.py`复用原最终输出契约，核对原调用、绑定、捕获内容哈希及完整最终页面；`tools/run_prepared_deepseek.py`提供不操作桌面的恢复模式，队列在判定步骤不可重放前消费可核验结果。回写仍使用原Workflow.finish及其版本、课程、选项和原脚本检查，不增加第二份答案或完成台账。

捕获没有CONFIRMED原调用、文件变更、页面截断、存储结果与页面不一致或暂停时停止；未知提交保留原人工核验入口，不靠恢复伪造确认。异常边界与原恢复入口11项通过（54.351秒）。同库恢复后只创建一份待交付草稿，MATCH和TEACH仍各一次，没有新增调用、实际交付或绩效。后续固定源码相关回归结果在下段记录。

最终相关回归命令：`python -X utf8 -B -m unittest -v tests.test_automatic_answer_runtime tests.test_prepared_run_resume tests.test_prepared_generation_reconcile tests.test_reviewed_question_queue tests.test_mcp_generation tests.test_call_costs tests.test_mcp_page_contract tests.test_followup_reuse tests.test_ack_before_generation tests.test_question_matching`。136项全部通过、0失败、0跳过，819.459秒，退出码0；2026-10-03 05:28:37—05:42:17 UTC运行期间源码未变化，前后快照均为`sha256:404204a70a6e2d46dee9cda59ed6baaf4a47c7592f87ef1295941ce4ef19338d`。原始日志保存在忽略的`artifacts/verification/20261003-captured-generation-final/unittest.log`。范围是上述十个模块的UNIT/MOCK，不是全项目或真实闭环验收。既有两项符号链接和官方SDK文件SKIPPED未在本批复验，仍保持未验证，不计通过。

REAL仅由Luna medium执行一次只读DisplayInventory和固定前台探针：当前显示器0为2560×1600、缩放1.5，前台是ChatGPT，因此没有截取或读取群消息、激活其他应用、输入或提交请求。官方MCP桥正常退出；原始证据留在忽略的data/private/windows-mcp。不能据此前台状态宣布群里没有消息，也不能证明持续消息入口或15分钟ACK时效。本批未追加DeepSeek测试、外发、登记真实交付、修改正式数据库或提交日报；此前网页专项的教学疑点仍保留，当前结论NOT_READY。

### 后续实施：完整回复格式失败的有限重试（2026-10-03）

基线为`ef03990969ffa96488c0ca5137b48a45f044222b`。本批修改`mcp_generation.py`、`automatic_answer_runtime.py`、`workflow.py`、`followup_reuse.py`、`tools/run_prepared_deepseek.py`、网页配置示例、四个相关测试文件、README及本进度文件。没有新增数据库表、迁移、消息通道或服务。再次只读核对ANSWER main、干净工作树和`b04ebc26d7fa096404111a0bb12f6c77cc8525b9`；未fetch或修改教学文件。

仅同一真实会话、独立本次BEGIN/END标记、非截断完整页面并已完成的回复，出现JSON解析失败、空讲解或学生选项字母不成立时，才允许第二阶段再提交一次。运行器配置`max_generation_attempts`只接受1或2，默认2；手动生成命令默认1，可显式设置`--max-generation-attempts 2`。本轮已结束的一次核验加一次教学网页专项不能因该默认值而增加请求；此类专项显式保持1。提交未知、超时、截断、验证码、暂停、题目或课程变化均不进入此重试，教学检查疑点仍转人工。

重试保留原run、Question和Session，不重复MATCH、检索、课程上传或ACK；第二次使用不同响应标记。第一次完整失败快照以排他创建的`<run>.failed-1.json`保留，原audit分别记录两次实际尝试，调用确认仅说明回复已经观察到，不等于教学通过或已交付。重启及普通追问复用第二次成稿时，核对最终捕获、第一次失败文件的原始字节哈希、两次调用确认和当前有效题面；错误历史证据不能被第二次成功覆盖。达到上限仍无效为`GENERATION_FAILED_CONFIRMED`，没有确认结果仍为`GENERATION_UNCERTAIN`，均不新增答疑完成或绩效。

已用合成输入验证：一次格式失败后两次TEACH仍对应同一个run/会话，MATCH仅一次，重试调用计数为1、费用仍未知；同库重启只读回写原答案/Outbox，追问沿用原会话而不再检索，未发送草稿不进入实际交付历史。故意改写第一次失败证据时拒绝恢复，恢复原字节后方可继续。第一回复完成后通过现有`correct_material`登记合成更正，第二次提交被阻止，旧run保留STALE与失败快照。2项定向用例通过（83.771秒），固定源码未变化；这些不是实际学生消息、真实DeepSeek或正式绩效样本。

初轮新增测试的错误预期将合法NEEDS_ATTENTION误写成EXECUTION_UNCERTAIN，已改为保留教学拒绝、单次调用和零交付断言；扩展重启测试误用已关闭的Store绑定，已重建原OperatorTasks句柄。同一次执行输出丢失且已无运行进程时，结果记为无法确认，使用独立日志再执行定向测试，不推断成功。另一次手动测试命令类名错误未运行目标测试；改为实际类名后1项通过（0.061秒）。没有削弱实现或删除保护断言来消除这些测试错误。

首轮相关长回归观察到3个证据篡改子用例失败后停止，无完整测试总数，日志及中断记录保留在`artifacts/verification/20261003-completed-output-retry-final/`，不能记为通过。单独复现1项、3个失败子用例（18.037秒），确认新捕获校验正确拒绝了改写，但没有返回原追问接口的业务错误码。`followup_reuse._origin`恢复既有`REUSE_GENERATED_ANSWER_EVIDENCE_CHANGED`并保留异常原因，未修改原测试；定向复验1项通过（18.047秒）。

最终相关回归命令：`python -X utf8 -B -m unittest -v -f tests.test_mcp_generation tests.test_prepared_run_resume tests.test_prepared_generation_reconcile tests.test_live_generation tests.test_workflow tests.test_call_costs tests.test_lesson_checks tests.test_followup_reuse tests.test_reviewed_question_queue tests.test_automatic_answer_runtime tests.test_ack_before_generation tests.test_delivery_tasks`。177项全部通过、0失败、0跳过，934.843秒，退出码0；2026-10-03 06:27:42—06:43:18 UTC运行期间源码未变化，前后快照均为`sha256:22f9d12f6d57b3c7ebb4e73648c8412dfd180ec53e2407f0dc69dadf0eb44f69`，配置示例哈希均为`778d62bbb66cba120c786803e50ff7dddb736b622d286dde96a529110b5f9094`。原始日志在忽略的`artifacts/verification/20261003-completed-output-retry-final-v2/unittest.log`，SHA256为`878e67407f2ef64b3e754ad62c826f3bdb9f11784c01b79b9befd2bcaa07ede5`。这是12个相关模块的UNIT/MOCK回归，不是全项目或真实环境通过；命令帮助及diff空白检查也通过。

本批仅后台代码与UNIT/MOCK验证，没有新增真实网页请求、桌面操作、群发送、实际交付登记、生产配置修改、8767部署或正式日报提交。前述真实阅读稿的method_visibility疑点仍未解决；真实入口时效、Top2、自动队列和A–H/至少20任务试运行仍未验收，结论保持**NOT_READY**。既有SDK及两项符号链接SKIPPED仍未复验，不算通过。

### 后续实施：应用启动与工具连接分开（2026-10-03）

从`201ff08ec6bf3d405df15c7f4e05ee4e71a336d3`继续；起始工作树干净。本批只修改`tools/windows_mcp_session.py`、`tests/test_windows_mcp_audit.py`、README及本文件，不改变Reference、Version、Delivery、Performance核心、数据库或生产配置，ANSWER仍冻结在`b04ebc26d7fa096404111a0bb12f6c77cc8525b9`。

对既有原生记录只读核对，2026-10-03的两项App结果含`msedge.exe`启动PID，确认网页恢复时曾在工具连接中使用`launch_executable`。本机Windows-MCP为0.8.5、MCP SDK为2.2.0；已安装SDK用Job Object管理服务进程，关闭Job会终止其受管成员。但是历史记录缺实际Job归属及完整退出因果链，因此Edge退出原因保持UNVERIFIED，不能说本次已复现或解决了那次浏览器退出。

最小修补是在本业务stdio边界拒绝`launch`、`launch_executable`、隐式默认启动及可执行路径/参数/工作目录；显式`switch`/`resize`和原窗口字段继续可用。工具`list`的App Schema同步只显示这两个模式、要求显式mode，不影响其他工具。既有受控企业微信/Edge激活入口仍先核验当前窗口和显示器，不把激活当成群身份核验。SDK进程清理保持原样，不修改上游依赖、不引入启动服务或任意命令入口。日常先在Windows中独立打开并登录Edge/企业微信，再连接工具。

新增3项匿名测试。修补前实际运行3项，10个失败子断言说明启动请求仍会送到Fake原生端、Schema仍暴露启动字段；切换/调整用例当时已通过。修补后`tests.test_windows_mcp_audit`11项通过（0.716秒）。最终相关命令`python -X utf8 -B -m unittest -v -f tests.test_windows_mcp_audit tests.test_windows_mcp_foreground_guard tests.test_screen2_activation tests.test_mcp_transport tests.test_mcp_bound_input_process tests.test_mcp_window_probe tests.test_mcp_display_scope tests.test_mcp_preparation`：93项全部通过、0失败、0跳过，5.070秒，退出码0；源码前后均为`sha256:1091f084e9f76af04489ee67f8d652b587cab472191083a475b2b37e6ba1033c`，期间未变化。日志在忽略的`artifacts/verification/20261003-mcp-launch-boundary/unittest.log`，SHA256为`607adfb71556656410851ff2a40c6a05d1881389fc70fb4775d2543fbddbff57`。这是相关UNIT/MOCK，不替代上一批其他模块或全项目验证。

REAL仅为无界面本机OS实验：本轮自己创建的安静Python测试进程，没有账号、Profile、聊天文件或网络请求。第一次使用venv启动器，子进程未进入SDK Job，初始断言失败，原结果保留为FAILED；与SDK源码描述的“启动器先生成实际进程可能逃逸”一致，不把它改为通过。第二次单独记录为v2，使用基础Python并核对正在执行的PID与Popen相同，确认测试子进程属于该Job、独立测试进程不属于；关闭Job后前者退出、后者仍活着，再正常结束独立进程，结果VERIFIED。证据和探针在上述忽略目录，已只读确认已知测试PID及首轮启动器子进程均不再运行。这只证明OS进程生命周期边界，不证明真实MCP服务/Edge在当时的归属，也不证明修补后的真实网页流程通过。

本批没有真实桌面操作、MCP浏览器启动、DeepSeek新请求、群发送、实际交付、正式库/配置修改或日报提交。真实生成疑点、入口/SLA、Top2、追问和A–H/20任务试运行仍需原授权边界内的实际证据，结论**NOT_READY**。SDK文件和两项符号链接历史SKIPPED未复验，仍不算通过。

### 后续实施：工具清单响应与主程序一致（2026-10-03）

从干净的`788110b0c7aa62eb64ac503bfab589d97f6a0119`继续。实际检查发现`list`回复仅有`tools`，而现有`MCPProcess`要求回复中的`tool`与请求相同；因此主程序调用真实封装的清单时会报`MCP_RESPONSE_TOOL_MISMATCH`。新增用例直接将实际封装产生的清单回复交给主程序传输层，修补前1项错误，修补后通过。最小修改只补`tool: list`，保留响应绑定检查、App启动禁用及所有未知结果停止规则，没有替换连接或进程管理。

相关UNIT/MOCK命令与上一批相同，新增上述用例后94项全部通过、0失败、0跳过，5.929秒，退出码0。源码前后为`sha256:677520e5073fcbd771e70c62b9dd8ab82c400264b096b79d344a82bf02abf57f`，未变化；日志为忽略目录`artifacts/verification/20261003-mcp-list-envelope/unittest.log`，SHA256为`039e8132b2f65cade6905bb126ad7ce7853aa6250411535319f289fe9cc9d742`。原业务数据库、ANSWER、生产配置及外发权限未修改。

修补前Luna的一次真实元数据尝试在取得清单之后失去exec句柄，未能调用DisplayInventory或正常quit，不计通过。Luna报告可见Schema与当前源码不一致，但该次没有保存原始清单，具体原因仍UNVERIFIED；不能仅检查源码就声称真实过滤已生效，也不能把当前无匹配进程等同于正常退出。

随后同一Luna medium用现有`MCPProcess`进行一次真实连接，保持输入管道，顺序调用`list`与`DisplayInventory`；未调用App、截图、页面读取或输入。助手首次因本地模块路径缺失在创建连接前失败，原记录保留；仅修正助手路径后才开始这一连接。原始清单已核对App仅含四个窗口字段、显式mode及switch/resize；原生显示器结果为索引0、DISPLAY1、2560×1600、缩放1.5。退出前bridge仍存活，上下文请求quit后退出码0，无pending；主Agent独立核对当时记录的7个进程均已不存在。没有独立原生quit回执，不把上下文返回称为此类回执。

该REAL元数据验证于UTC 07:13:57—07:14:02完成，当前封装文件前后SHA256为`fb01e70c4ec5a46df918fde999079d6230b5d65236f97c38eef7e2d7029ce650`。原始清单、显示器返回、进程身份与失败记录在忽略的`data/private/verification/windows-mcp-metadata/run-20261003T070856Z-4a7ebe00b24e4b81bc9f9427f689f1fb/`，不上传Git。仅连接及清单契约升级为REAL VERIFIED；没有追加DeepSeek请求、群发送、实际交付或正式计量。此前真实草稿疑点与网页自动队列、入口SLA、Top2及完整试运行仍未验收，结论**NOT_READY**。

随后尝试只读回访已有DeepSeek测试会话：UTC 07:20:21—07:20:30的单一连接仅调用DisplayInventory、固定DesktopStatus及Foreground探针。当时桌面已解锁且为本地会话，前台为ChatGPT、Edge窗口数为0，因此没有Snapshot、App切换或页面读取。主Agent离线复核原记录及7个进程已退出，无pending，封装与原生程序哈希未变化。证据在忽略的`data/private/verification/deepseek-readonly-20261003/run-20261003T073000Z/`；目录标签不作为观测时间。该记录只能证明当时桌面状态，已过新鲜度期限，不能用于后续操作准入；已有会话与4000元素完整页面验证保持UNVERIFIED。

改用工具连接之外独立打开Edge时，`exec_command`在创建PowerShell进程阶段被自动审批拒绝，原始原因仅为`blocked by policy`。拟执行命令包含写入启动助手，助手内容包含固定Start-Process；没有证据表明这些步骤执行。主Agent随后确认目标助手不存在、Edge进程仍为0。没有改写或隐藏命令绕过拦截；已请求本人从Windows打开Edge并恢复测试会话。再次两阶段生成的确认问题仍未回复，不能将只读回访当成新请求授权。本次没有业务代码、生产库、配置、群发送或正式绩效变更，结论仍为**NOT_READY**。

## 本轮审计与最小改动

业务检出目录为 `E:\作业帮\tmp\answer-agent-publish-20261001`，审计起点为 `main / 126747e1fed4a70c78c6fca46f802dfa9f8984c1`，远端为 `rockpunkgod/answer-agent`。后续提交可从该基线追溯。审计当时原工作目录运行8767工作台；最新只读检查未发现该端口监听，HTTP不可连接，不能沿用早期运行状态。本批未部署或迁移正式数据库。

| 环节 | 代码或配置证据 | 实际问题或待验证风险 | 本轮最小处理 | 验证方法 |
|---|---|---|---|---|
| 官方CLI | 上游 `package.json`、`docs/cli-reference.md`、`skills/wecomcli-message/SKILL.md` | 当前文档描述近期机器人会话发送；指定的四份 `wecomcli-msg` 读取文档不存在，实际Schema在线下发 | 记录真实版本；不据缺文档推断工具不存在，不编造读取适配器 | 授权后核对Schema和原群覆盖 |
| 官方SDK | 上游 `aibot/message_handler.py:55`、`client.py:301`、`ws.py:527` | 通用与具体事件均触发；body可覆盖chatid；队列断线清空 | 未启用、未复制上游代码；不能替代业务Outbox | 机器人场景覆盖成立后才做薄适配 |
| 原消息 | `native_message_source.py`、`collector_storage.py:147`、`collector_dispatch.py:71` | 当前读取已保存原文，不是GUI监听；缺可靠原时间会暂挂ACK任务 | 保留去重、原时间证据和事务；不把未采集显示成群无消息 | 保留相关回归；下一批再分离内部处理与绩效时间门槛 |
| 教学来源 | 旧 `teaching_bundle.py` 默认本机Skill；`teaching_routes.py` 已有正确分工 | 旧包未绑定ANSWER提交；主客观题检查脚本缺失；语法还依赖内部修稿说明 | 新增固定提交、按题型完整原文的预览与缓存，补齐语法说明，缺项不借用 | 本地Git原文比对、缓存、篡改、脏文件、目录跳转测试 |
| 桌面 | `mcp_window_probe.py`、`tools/windows_mcp_session.py` | 标题栏被遮挡限制点击；App使用打开窗口缓存做名称匹配 | 可选受控App切换，仅允许缓存中的唯一企业微信与屏幕2原生窗口一致；切换后再核验 | 模拟窗口、PowerShell假窗口、错误目标/移动/超时拦截；新切换未做真实输入验证 |
| 交付/绩效 | `workflow.py`、`delivery.py`、`semantic_decisions.py`、`performance.py` | 真实交付回流仍缺接线，现有版本/计量保护已存在 | 保留共享归属、过期拦截、未知发送核验、夜间与日报规则；不重做数据库 | 完整离线及Mock回归，不把草稿计完成 |

审计时原工作目录配置为 `ACK_ONLY`、`helpdesk.native_message_source:create_source`；七个发送目的均为 `DISABLED`。当时8767服务可访问、网页持久队列线程运行；消息与Outbox为空只说明本地未形成业务记录，不代表群里没有消息，也不代表会话创建/网页准备/生成回调已连接。API调度模型未配置；已有Codex的Luna medium有限导航连接不等于业务语义已接通，也不新增模型投票。实时状态须重新核验。

## 官方渠道：源码核对与账号覆盖分开

- [wecom-cli](https://github.com/WecomTeam/wecom-cli/tree/c4b9b6610c7ca2854441bfa336a5b458daeeb707)：源码 `1.3.4 / c4b9b6610c7ca2854441bfa336a5b458daeeb707`，MIT。Rust可执行程序，npm发布入口为Node包装器，未给业务程序新增Node依赖。当前消息文档是 `skills/wecomcli-message/SKILL.md`，提供近期可发送会话与机器人主动通知；没有据此证明完整聊天读取。帮助和Schema需凭据及网络；认证错误853004可能刷新后重放一次请求，未来发送适配不能把它当通用安全重试。会话列表的 `last_msg_time` 不能充当每条学生消息的发送时间。
- [wecom-aibot-python-sdk](https://github.com/WecomTeam/wecom-aibot-python-sdk/tree/6bcb59a9a636c566f4c6ea5268b228e3def1611a)：`master / 6bcb59a9a636c566f4c6ea5268b228e3def1611a`，MIT；包元数据1.0.1，`__version__`仍1.0.0，应以提交和实际安装元数据核对。依赖websockets、aiohttp、pyee、cryptography、certifi，未安装到业务环境。源码提供机器人回调与回复；缺aes_key直接返回原字节，不能据此标READY；连接与认证分开，重连没有历史补读机制。默认日志可含完整回调，未来使用须关闭或脱敏。早到/迟到ACK与req_id关联的风险仅为源码推断，本轮未复现。

本机只检查了工具和凭据是否存在，未读取或输出密钥：CLI不在PATH，当前配置目录无 `credentials.enc`，未设置CLI token，Python未安装aibot。没有调用官方聊天、附件或发送接口，**结论为本账号尚未验证**，不能判为权限不足，更不能判为原群没有消息。

| 本账号验收项 | 当前证据与结论 |
|---|---|
| 企业身份、授权范围、原学生群类型与可查询性 | 尚未通过官方接口核验；客户端登录不证明API授权 |
| 原群未@普通消息、追问、引用、补图与更正 | 尚未验证，不声明可作为主要读取源 |
| 老师手动回复及完整绩效交付覆盖 | 尚未验证，不能生成完整实际交付总量 |
| 每条原始发送时间的字段语义、精度、时区与附件可下载性 | 尚未验证，不用回调/采集时间补齐 |
| 断线补读、历史范围与9月17日起缺口 | 尚未验证，SDK重连不代表补齐；客户端保存原文仍只构成局部证据 |
| 发送身份、目标、内容与平台回执 | 尚未验证，保留禁发与现有Outbox，不跨通道重发 |

不根据个人开发身份推断企业规模；当前版本与账号限制需用官方文档和原企业账号验证，不新建企业或切换身份规避。其余四个社区项目本轮未接入、未核验，不作为接口实现依据。覆盖成立后才考虑一条只读对照路径：无ACK、无DeepSeek提交、无正式计量，再按群与时间范围显式切换；不同时上线CLI/SDK/Webhook，不删除现有可用入口。

## ANSWER版本与预览结果（历史：2026-10-02）

本机 `E:\作业帮\.tools\ANSWER-reference` 为干净的main，提交 `57159d7a8b03a0743225ed27f3f1e6128bbcd45d`，与核对时远端默认分支一致。用户提供的 `ANSWER-main.zip` 中211个文件逐一与该提交的Git blob一致；不能将检出目录CRLF与ZIP中LF的差异误报成教学修改。未修改、拉取或执行ANSWER教学脚本。

读取固定提交，新增 [answer_teaching.py](../helpdesk/answer_teaching.py) 与 [teaching-source.toml](../config/teaching-source.toml)，复用既有题型路由与manifest验证入口。新的 `prepare_teaching_bundle` 默认仅生成一个原文教学输入与必要源文件快照，拒绝来源/提交变化、未提交改动、缓存篡改和目录跳转；同版本同题型复用缓存。没有改动现有Workflow生成行为或运行中的教学配置。

实际本地预览六类均成功且重复生成不改写缓存：阅读、七选五、完形各报告主目录缺少 `scripts/check_lesson.py`；语法使用旧版目录，带语法模块、课程依据、定位/修稿说明与脚本快照；两类写作各只选 `gaokao-writing` 对应模块。所有新包保持自动生成、真实上传及真实交付为false。文件存在和来源匹配不等于已经执行课程检查、消除教学冲突或模型完整遵守了教学条件；课程查证语料仍按需检索，未混入旧上传文件。

本轮不迁移消息入口、创建新业务库/MCP服务、执行真实发送或写绩效。启动/暂停/恢复沿用README；新教学预览未配置进生产，停止调用新增预览命令即可保留原运行配置。新App切换是可选操作，原有带窗口核验的标题栏入口保留；发生不确定结果仍停止核验。

## 原题检索与身份确认增量（2026-10-02）

本批仅在发布副本实施。原工作目录8767服务未重启，未打开生产数据库、切换真实账号或执行发送。没有修改ANSWER检出目录、教学规则、绩效规则或消息通道。

| 文件 | 原有职责与复用能力 | 本批实际修改 | 数据库与风险处理 |
|---|---|---|---|
| `domain.py`、`service.py` | 学生Question版本、精确选项映射、现有reference表及答疑上下文 | `compare()`增加完整性、关系、逐字段证据和关键差异；只有明确确认且允许消费的参考进入上下文 | 缺选项、空材料、图表未核验或OCR不确定均不能确认；相似分数不授权 |
| `reference_resolution.py` | 复用现有`reference_candidates.comparison`与`audit` | 薄服务持久保存候选生命周期、来源/哈希、规则版本、核对人/时间/依据；按内容及来源去重 | **无新表、无迁移，schema仍为6**；历史缺确认记录不静默升级，重复确认保留原核对人 |
| `reference_lookup.py`、`reference_fetch.py`、`reference_providers.py` | 按需查询、已有题目ID及版本、缓存和受控HTTP | Local/HTTP统一来源、URL、内容、取得时间、哈希；抓取结果仅形成候选，检索与匹配状态独立 | 默认关闭；网络需单独开启，terms、robots和路径分别准入；临时正文只在内存，来源许可后才保存必要候选 |
| `tools/crawl_reference.py`、`tools/lookup_reference.py` | 只读参考获取与本地演示入口 | 原浏览器路径改为复用受控HTTP；可指定已存在的题目建立候选 | 不安装新依赖；不绕过付费/登录/验证码；导出仅元数据，Crawl4AI浏览器适配暂缓 |
| `demo_server.py`、`static/reference-lookup.js`、`static/index.html` | 本机工作台、Origin/CSRF检查、现有题面入口 | Shadow展示学生版/参考版、来源、时间、哈希、差异，确认/拒绝/重新检索 | 人工依据必填；Shadow确认不消费，无置信度或调试日志展示，不增加发送权限 |
| `mcp_preparation.py`、`mcp_preparation_review.py`、`mcp_generation.py`、`session_isolation.py`、`workflow.py`、`test_answer_queue.py` | 冻结输入、准备附件、会话所有权、批准和发送前校验 | 已确认参考随题面文本进入网页准备；有原图也保留文本；核对变更在上传、生成回调、批准和发送时阻断，共用答案校验也覆盖测试副本 | 参考答案/解析不进入教学输入，学生版本优先；草稿不算交付或绩效 |
| `run_current_demo.ps1`、`config/reference-lookup.example.toml` | 一个本机启动入口与可选配置 | 增加可选核对配置；旧服务不静默忽略参数或自动重启 | 关闭开关停止新增检索；撤销已允许使用的候选需先拒绝，不删除记录 |

已经实施：最多6次查询/6页、串行网络、同域至少10秒、单请求15秒/总预算120秒且服从原任务剩余时间；有限恢复重试与Retry-After；重定向和DNS固定连接地址；慢速响应总时限；来源准入；大小/编码限制；缓存版本、题目/上下文更新失效；失败不冒充负搜索结果。网页图片/PDF或无法结构化的内容明确返回缺口，不补造题面。通用HTML提取只经自建样例验证，不声称任何题库生产选择器或命中率已经验证。

| 来源或能力 | 当前实际状态 | 本轮真实覆盖 |
|---|---|---|
| Brave Web Search | 已实现；离线适配验证；**真实查询待密钥** | 核对官方[搜索文档](https://api-dashboard.search.brave.com/app/documentation/web-search/codes)的URL结果与认证契约，没有付费调用 |
| `jyeoo.com`/www | 默认禁用；条款已读但未批准自动访问及保存 | [条款](https://www.jyeoo.com/home/note)和首页审查，未验证题面详情；robots在审查工具中不可读，不冒充站点HTTP结论 |
| `shuajuanzi.com`/www | 默认禁用；待授权与真实详情验证 | 未取得可验证自动抓取许可 |
| `zy.21cnjy.com` | 默认禁用；待授权与真实详情验证 | 首页可读不代表题面或保存许可 |
| `zujuan.com`/www | 默认禁用；待授权与真实详情验证 | 审查工具根页面读取失败；不据名称推断其他站点许可 |
| `chujuan.cn`/www | 默认禁用；待授权与真实详情验证 | 未取得可验证自动抓取许可 |
| `zybang.com`/www | 默认禁用；不允许当前自动抓取 | [robots.txt](https://www.zybang.com/robots.txt)审查时对通配User-agent设置`Disallow: /`；其他产品条款不作为全站授权 |
| `easylearn.baidu.com` | 默认禁用；待授权与真实详情验证 | 未取得可验证自动抓取许可 |
| LocalProvider/HttpProvider | 已实现；本地文件和Mock/本地HTTP验证 | 真实学生问题的原题联网命中、账号页面和正式使用仍未验证 |
| Crawl4AI浏览器、网页图片/PDF识别 | 当前新入口暂不支持 | 不将此前一次官网抓取当作现有安全边界和题目提取验收 |

匿名身份验收的预期与实际：完整一致→`MATCH_CANDIDATE`；缺选项→`INCOMPLETE`且禁止确认；换序→明确映射（参考B→学生D）；题号变动→同内容且不建新题；NOT/EXCEPT→题干及关键条件冲突；数字/范围→条件变化；错误候选→`REJECTED`；重复及并发检索→同一候选；具名审核→`CONFIRMED`而Shadow不消费；显式允许后→冻结网页输入包含确认证据。上述十项通过。另验证OCR不能冒充条件变化、普通追问/重启、目录越界、篡改、待核对更正、候选撤销及未交付不计绩效。

带图/纯文本 × 严格准备/快速准备四种组合均以FakeDesktop及模拟网页生成完成验证，生成稿仍是待交付。测试明确将假传输结果按模拟适配器记录，不伪造真实回执。首次浏览器回归的报告fixture缺少题目ID、新增JSON字段与旧准备校验不兼容、测试结果适配器不一致等问题已在回归中修正；没有删除关键断言。

增量命令：`python -X utf8 -B -m unittest tests.test_reference_resolution tests.test_reference_providers tests.test_reference_lookup tests.test_reference_workbench tests.test_mcp_preparation_text tests.test_mcp_fast_preparation`，85项通过。相邻网页、会话及准备回归73项通过。这些范围有重叠，**不相加成独立测试总数**。

首轮全量 `20261002-reference-identity` 实际为995项、990通过、2失败、3跳过，201.499秒，期间源码未变化。两个失败来自`test_operator_tasks`使用本机可变教学目录，触发了教学内容变化及语法文件缺失防护；没有改动该目录或放宽生产防护。现改为明确的临时匿名文件和测试专用审核契约，保留题型不匹配、冻结幂等、没有网页调用及没有绩效等原断言。补齐共用交付校验后，相关80项为79通过、0失败、1符号链接跳过；新增测试副本撤销场景没有产生任何传输回执。

最终全量命令：`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261002-reference-identity-final`。实际996项，993通过、0失败、3跳过，unittest运行184.253秒，退出码0，期间源码未变化；源码快照为`sha256:14b40e84481904786fe3e559866b13103423de400938fb6df3f4cf385eaa928a`。原始日志和校验结果保留在本机忽略的`artifacts/verification/20261002-reference-identity-final/`，不上传业务材料。

验证范围分别记录：匿名SQLite、模拟网页/桌面适配器、本地HTTP和无界面浏览器通过；真实ANSWER的本地原文预览通过；真实题库查询、参考进入真实DeepSeek网页、企业微信交付及正式日报提交未验证。3项SKIPPED为官方存档SDK文件未提供、教学依赖符号链接无法创建、浏览器目录符号链接无法创建，均不算通过，不要求管理员运行或关闭系统安全设置。最终回归无失败，首轮两个失败及其修复保留上述记录。

## 人工交付回流增量（2026-10-02）

起点为已推送的`064a88003f6ca01df1c822f900b0de514f47b722`。本批继续原计划阶段2，补齐人工入口；不新增消息通道、教学规则、计量口径、依赖或数据库迁移，未部署到原工作目录8767。

新增`manual_delivery.py`复用Outbox、delivery_checks和audit保存每部分实际内容、附件哈希、时间、核验人、依据及核验方式。支持已有正式生成稿及老师直接回复已有学生轮次；后者不要求先产生AI草稿。原消息、原版本、生成稿及已有语义关系保留，人工实际回复回到原轮次，追问读取实际交付而非未发送稿。人工证据明确为`MANUAL_ATTESTATION`，平台消息ID和已读为null、自动回执为false。

`workflow.py`、`service.py`、`performance.py`仅补共用回流和资格校验：所有声明部分未全部核验时不完成，不计入已确认数量；按最后实际交付时间完成，与登记顺序分开。重复登记不重复建记录或增加数量，冲突登记拒绝覆盖。完整交付停止尚在生成的旧任务、取消同轮次待发旧稿，旧回调不能产生新发送任务。旧学生版本的实际交付可以留证，但不能恢复当前完成或直接计量。复用已有确认的语义绑定与原计量台账，没有绑定不新造归并规则。

`demo_server.py`、`static/index.html`、新增`static/manual-delivery.js`提供本机表单和严格Host/Origin/CSRF接口；目标来自现有任务，拒绝外部收件人、任意命令及本机练习题。附件限定数据库目录下`delivery-attachments/`，读取实际文件与哈希；登记不初始化桌面或模拟传输数据库。暂停和禁发不抹去已经发生的人工交付。

`operator_tasks.py`、`static/app.js`复用该交付包显示原题的实际完成时间；后续实质更正显示历史已交付、题目已更正，不继续提示等待发送。已知“收到”或致谢不可登记为完成答疑，英文答案词不凭字面猜成首次应答；表单要求人工明确核验为解答或附件。模拟来源和本机练习来源均拒绝正式交付登记。

人工登记提交使用点击核验时的固定内容、时间和附件；等待令牌期间的后续编辑不会进入原任务。请求未完成时禁止重复提交或切换任务，主看板刷新不解除已完成登记的禁用状态。这些均在本机匿名浏览器验证，不涉及企业微信输入。

联调实际发现旧ACK有已确认状态却无发送时间时`/api/state`断连，已修复为未知时延，不填当前时间或0。保留原空时间，绩效仍使用独立学生提问的原始发送时间。

相关命令：`python -X utf8 -B -m unittest tests.test_manual_delivery_registration tests.test_manual_delivery_workbench tests.test_shared_source_delivery_integration tests.test_manual_delivery_reconciliation tests.test_performance_delivery_eligibility tests.test_reference_resolution`。81项通过，19.095秒；覆盖直接人工回复、编辑稿、附件、完整/部分交付、重复登记、原消息时间、过期版本、原始计量、后续追问、旧回调、Origin/CSRF及纯文本UI。首轮两处测试预期不匹配已修正；HTTP联调的5处错误来自上述真实空时间缺陷，修复生产代码后通过，未删除关键断言。所有老师消息、桌面、网页和时间均为匿名合成数据，不作真实交付验收。

最终代码回归：`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261002-manual-delivery-release`，1026项、1023通过、0失败、3跳过，197.031秒；开始与结束代码快照一致（`sha256:e607dba433282830fadddd0e2379f76c2c8daa188a40bb4eb49cc5f8e14fa136`）。最终人工登记相关30项通过（14.232秒），界面及现有Worker/绩效阶段相关27项通过（16.546秒）。新增原题卡片测试最初误选夹具中的两个任务，现按具体任务ID核验，保留实际完成及后续更正断言。

验证级别：匿名SQLite、模拟网页/桌面、本机HTTP与无界面浏览器通过；本批没有真实账号、桌面交付、DeepSeek执行、付费搜索或正式日报提交。SKIPPED分别为官方SDK文件未提供、教学依赖符号链接无法创建、浏览器目录符号链接无法创建，不算通过，不要求管理员运行。最终无失败。

真实限制：当前仍需先形成可信学生消息与题目归属；任意未归属聊天不直接计完成。真实老师回复观察、实时采集、ANSWER缺项与真实DeepSeek接线仍需后续验证。本轮可以证明匿名“原消息→既有语义关系→生成稿或直接人工回复→交付回流→追问→日报”路径；不等同于完整生产闭环。

## MCP安装路径与真实只读核验（2026-10-02）

本批从`5e4b99a01da679e40186c6b251f4a106597247d8`继续原计划阶段3的现有MCP接线。真实只读探针发现发布副本默认查找自己目录内的工具环境，首次启动返回WinError 2；本机已有的官方Windows-MCP安装可用，无需增加安装或复制整套环境。

`mcp_transport.py`与`tools/windows_mcp_session.py`统一采用可信本机环境变量`HELPDESK_WINDOWS_MCP_HOME`，未设置保持原默认。客户端Python和官方服务来自同一安装目录，当前检出的session封装、工具白名单、屏幕2检查、前台校验、有限超时和日志目录保留。已有网页准备、生成、草稿、测试发送及Worker入口取消重复硬编码工具路径；HTTP、模型输出及学生消息不能设置该目录。相对路径、网址、命令字符串、控制字符及网络共享在启动进程前拒绝。

使用一个Luna medium子任务做只读桌面能力核验，主Agent只改后台代码。真实DisplayInventory确认DISPLAY2为index 1、2560×1440、y=-1440..0，桌面已解锁且非远程。唯一企业微信主窗探针返回`VISIBLE_SCREEN2_WECOM_MAIN_WINDOW_UNAVAILABLE`，屏幕2仅有图片预览窗口；因此没有读取群消息、切换或发送，也无法核验UIA正文、当前发送人、原时间与图片字段。未把不可读取标成“没有新消息”，已请求本人恢复屏幕2目标群。

修复后以默认`MCPProcess()`、仅当前Python进程设置上述环境变量，真实官方MCP读取DisplayInventory成功：session PID 92132、退出码0，主Agent随后确认该PID已不存在。私有证据为`data/private/windows-mcp/screen2-readonly-capability/20261001T190111Z-transport-launch.json`，不上传。此结果只证明当前代码可启动现有官方MCP并做元数据读取，不证明自动收题或DeepSeek已运行。

相关回归`python -X utf8 -B -m unittest tests.test_mcp_transport tests.test_mcp_bound_input_process tests.test_windows_mcp_audit tests.test_windows_mcp_foreground_guard tests.test_screen2_activation tests.test_mcp_window_probe`：59项通过，4.820秒。验证路径选择、当前封装保留、拒绝无效配置、超时不重放及原有输入保护；这些测试为匿名/模拟，和上述真实元数据核验分别报告。

阶段3仍缺真实`GUIMessageSource.observation_provider`。`CollectorSupervisor`仅轮询已保存原文，`chat_text_archive.py`是手工Clipboard归档器，不是自动生产者；需要在指定群可读后按实际字段接线。固定ANSWER缺项和当前DeepSeek回调等原计划范围继续保留，未缩减为文件导入完成。

本批同时补齐阶段1中旧包仍可进入真实操作的缺口。`teaching_bundle.verify_bundle(for_generation=True)`要求重新核验固定ANSWER源、依赖和课程权限；旧格式不用于新的真实生成。`workflow.py`在冻结的每份教学文件中绑定manifest路径、政策版本、题型、哈希及原文，`mcp_preparation.py`与`mcp_generation.py`在上传或再次提交之前共同复核；旧冻结输入没有来源绑定时停止并要求重新冻结，不改写原记录。历史准备证据读取、已生成结果核验和交付记录仍保留。

`mcp_generation.py`移除业务程序内的段落、目的/结果、宽窄含义及技巧触发等本地英语教学判据与强制开头。教学方法和讲解方式仅取固定ANSWER对应章节，保留学生版本/选项优先、输入不能改权限、完整正文及本轮边界解析。未修改ANSWER教学文件或计量规则，实际ANSWER仍为干净提交`57159d7a8b03a0743225ed27f3f1e6128bbcd45d`。

有效ANSWER预览包可打开现有原消息题面入口，`demo_server.py`和`static/app.js`明确显示缺必需文件或课程尚未验证，暂停自动队列；题面人工核对、已有任务、人工实际交付和日报继续使用。未经审核的旧自定义包仍不能取得权限。未迁移数据库或更改8767运行配置；没有通过修改JSON提升预览包权限。客观题仍缺`gaokao-english/scripts/check_lesson.py`；其他预览也不因源文件存在而自动获得生成授权。

教学/准备/身份/恢复相关回归140项，139通过、0失败、1符号链接跳过，52.761秒。首轮1失败来自旧提示词断言，8错误来自将新操作来源检查同时放入历史准备证据读取；已更新预期并把检查保留在产生新操作的入口，未删原业务断言。工作台相关43项首轮有1个测试错误（新夹具mock位置不存在），已修正注入位置，新增有效ANSWER预览工作台单项通过（2.492秒）。匿名网页和任务测试显式模拟教学权限；来源、缺项和禁止操作的集成测试独立执行真实本地Git/文件校验，均不代表真实DeepSeek执行。

最终集成暴露了共享夹具尚未模拟新教学权限与嵌套夹具清理遗漏：第一轮全量`20261002-mcp-and-answer-source`为1037项、1000通过、34失败、3跳过（196.692秒）；第二轮`...-final`为1037项、1033通过、1错误、3跳过（205.175秒）。已在现有HTTP/原消息夹具声明模拟边界、提前登记嵌套清理，并补上`test_mcp_bound_input_process`对内嵌夹具的`doCleanups`。后台排队测试等待实际入队状态而非仅看到run ID，保留模拟ACK拦截、身份、版本、交付、计量与旧包禁止断言。相关83项为82通过、1跳过（33.116秒），传输及真实来源相邻55项为54通过、1跳过（44.951秒）。生产来源检查未放宽。

本批最终命令：`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261002-mcp-and-answer-source-release`。实际1037项、1034通过、0失败、3跳过，204.728秒，退出码0；开始与结束源码快照一致（`sha256:fc05353607701a9ee83ece15821910131cfd4f9f3efc45b9afb37ccdf9a26f7d`）。原始失败和最终日志均保留在忽略的验证目录中。本批没有新增依赖、表或迁移，没有修改源消息、教学文件或绩效规则。

验证分层：匿名SQLite、模拟网页/桌面、本机HTTP及无界面浏览器通过；真实本地Git/文件来源检查通过；真实MCP启动与DisplayInventory通过。群原消息读取、固定ANSWER下真实DeepSeek上传/生成、真实交付和正式日报提交仍未验证。3项跳过为官方存档SDK未提供、教学符号链接权限不足、浏览器目录符号链接权限不足，不算通过、不要求管理员权限。当前全计划仍未完成。

## 题目身份增量审计与条件顺序修正（2026-10-02）

本批基线为已提交的`106335adcda633e0dd51ce439e5aab0d8a03a34f`，对照最新题目身份确认要求重新检查现有代码。`ReferenceResolution`已复用原候选表和审计表，保存五个候选状态、来源与内容哈希、逐字段证据和具名人工确认；`domain.compare()`仍是唯一比较入口。工作台Shadow、候选拒绝、确认后显式消费以及上传/生成/批准/发送前版本复核已接通，无需重复模型或新增迁移。

匿名复现发现，原`_conditions()`对数字和条件词排序，导致“12名学生使用21条毯子”改为“21名学生使用12条毯子”、范围端点10与20互换时，只标记普通题干差异。原实现已经拒绝匹配，不存在这两项被自动确认为同题的结果。本批保留条件出现顺序，将其明确显示为`CONDITION_CHANGED`及`KEY_CONDITION_CONFLICT`；不改变精确同题与选项映射的准入条件，不自动建立新题或绩效单元。

检索模块由`reference-lookup-v2`更新为`reference-lookup-v3`，新查询不复用旧报告，过期v2缓存仍按原受控规则清理。兼容性测试验证重新计算生成两份检索记录，仍仅有一个候选、零个绩效单元。已有确认与原始题面记录不覆盖，候选确认规则版本继续为`question-compare-v1`，本次只增强此前已拒绝内容的差异标记。

第二个匿名复现发现，重复检索的去重分支没有将新比较结论回流到已存候选，Shadow仍会显示旧的普通差异。`ReferenceResolution.add()`现在先核验原候选内容，以原候选ID和版本重新比较；未确认且结论变化时，旧/新比较写入现有审计表，再更新差异标记，保留候选原状态、来源、取得时间和人工决定。已确认结论变化或内容篡改时停止，不自动改写授权。再次重算不重复增加审计事件、候选或绩效。

修改文件为`domain.py`、`reference_lookup.py`、`reference_resolution.py`、两个现有参考测试、README与本进度文件。没有新增依赖、表、迁移、消息通道或Agent，没有修改ANSWER、真实消息时间、原数据库与8767运行配置。ANSWER本地仍为干净提交`57159d7a8b03a0743225ed27f3f1e6128bbcd45d`，客观题必需的`gaokao-english/scripts/check_lesson.py`仍缺失；受影响自动教学分支继续停止。

测试记录：修改前现有参考/准备回归85项通过，选项映射7项通过。补充数字角色/范围端点、历史候选回流用例后，两项目标测试分别实际失败；修正生产代码后通过，没有删除关键断言。最终相关命令`python -X utf8 -B -m unittest tests.test_reference_resolution tests.test_reference_providers tests.test_reference_lookup tests.test_reference_workbench tests.test_mcp_preparation_text tests.test_mcp_fast_preparation tests.test_core.MappingTests`，95项通过、0失败、0跳过，8.863秒。这些为匿名SQLite、获准本地样例及Mock网页/桌面，不属于真实账号或外发验证。

补充历史候选回流前的全量`20261002-reference-condition-order`为1038项、1035通过、0失败、3跳过，205.201秒，运行期间源码未变化；该结果不代替本批最终版本验证。最终命令`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261002-reference-condition-order-final`：1040项、1037通过、0失败、3跳过，206.490秒，退出码0，运行期间源码未变化。源码快照`sha256:a72098ce7e18b0a97fdcb2cedde0570d02a4308424b5d7fed5c4481cb4e4d49f`与最终源码一致，日志哈希及证据验证通过；原始结果保存在忽略的验证目录，不上传业务材料。

3项跳过为官方存档SDK未提供、教学依赖符号链接与浏览器目录符号链接在当前环境无法创建，均不算通过。真实本地检查仅核对ANSWER提交、干净工作树和缺项；没有用本轮模拟结果冒充真实站点抓取、网页上传、学生消息或交付验收。

仍有的范围缺口：精确匹配仅支持完整材料与四选项客观题；本地抓取支持UTF-8文本/HTML，网页结构提取尚未做真实站点验证；Crawl4AI浏览器抓取没有接入。联网题库命中、固定ANSWER下真实DeepSeek上传/生成、真实群交付仍未验收。默认检索关闭与Shadow保留，不将模拟通过显示为完整闭环上线。

## 题目身份确认依据与原始出处补齐（2026-10-02）

基线为`e38c5846036ce4f8e848bc68c8f7f0fd4f608544`。上一轮只读审计后，新匿名HTTP用例实际复现：旧`apply`只接受核对人、自动填写固定确认理由，未提供具体依据仍返回成功。本批要求该兼容入口显式提供`reason`，先校验再复用现有候选审核；无依据或格式无效不进入确认。重复登记保留首次核对人、时间与理由，未创建答案或绩效。未改动候选状态、精确比较和来源使用许可。

现有候选卡增加只读原始出处：版本对应的原始材料、题干、各选项出处、来源消息、原始发送时间与采集时间。原始识别/录入文本与已核验字段分开显示，缺项仍为INCOMPLETE，不能凭原始OCR或候选补齐。当前版本原图复用已有`source-question-image`入口，先核验来源与附件哈希，只提供草稿ID和索引；浏览器不取得本机路径，不下载外站图片。无绑定或原图变化时显示不能可靠预览。预览仍遵守原有题面入口开关，不新增真实采集能力或上传权限。

相关命令：`python -X utf8 -B -m unittest tests.test_reference_resolution tests.test_reference_providers tests.test_reference_lookup tests.test_reference_fetch tests.test_reference_workbench tests.test_core tests.test_source_question_http tests.test_source_question_tasks tests.test_manual_delivery_registration`。实际176项通过、0失败、0跳过，30.524秒。新增用例覆盖无依据确认被拒绝、原始OCR不提升为确认题面、22:58原始时间与23:05采集时间分开显示，以及源图片哈希变化后不可预览；既有串题、旧版本拦截和交付回流回归继续通过。UI用例中折叠证据需展开后读取可见文本，修正操作后保留原有安全与审核断言。

验证分层：匿名SQLite、Mock抓取/桌面、本机HTTP及无界面浏览器通过；本批无跳过。真实ANSWER工作树仍为干净提交`57159d7a8b03a0743225ed27f3f1e6128bbcd45d`，客观题必需检查脚本缺项未变。本轮Luna medium独立只读执行三项Windows-MCP元数据调用，全部返回；屏幕2企业微信主窗口仍无法唯一确认，`VISIBLE_SCREEN2_WECOM_MAIN_WINDOW_UNAVAILABLE`。MCP子进程关闭后实际退出；没有读取聊天内容、输入、上传或发送。真实题库检索、固定ANSWER下DeepSeek生成与真实交付仍未验收。

本批未新增依赖、数据库表或迁移，未修改ANSWER规则、绩效规则、真实时间及8767运行配置。原有未提交的`manual_delivery.py`与`source_question_tasks.py`改动保留，未作为本批提交；上述相关结果属于当前本地工作树，不将其声称为远端完整闭环验收。后续继续完成已采集老师回复的实际交付关联，再补真实自动采集与网页链路。

## 保存的老师回复回流到原任务（2026-10-02）

在`90dbbbd9b8eb851dd6bbc29514f68c8a7b1b1502`之后，完成此前保留的`manual_delivery.py`与`source_question_tasks.py`未提交工作。人工交付入口可从原任务绑定的原消息库选择单条老师文字回复；服务器确定原消息库、来源、群和声明的老师身份，客户端不能替换目标、时间、内容、路径或命令。原始时间必须可解析并与保存的规范时间一致，错误引用、媒体、低可靠性和采集冲突不能当作解答。没有明确引用时仍要求本人核验归属；方法为`MANUAL_ATTESTATION`，不制造平台回执或已读。该入口不启动消息采集、不外发。

复用现有Outbox、delivery_checks、共享语义单元与日报，不新增表或迁移。原回复ID、原任务来源摘要和内容证据摘要保存在原交付记录中；内容/时间从保存的记录读取。多部分均核验后才计完成，同一原回复不能占用不同部分；重复登记、重复导入和重复生成日报保持一致。发送不确定不会被生成稿补成完成，人工更改已发生的回复内容不会被原生成稿覆盖。

原来源传输校验从当前题目校验中分出一个只读函数，用于核验历史已发生的交付；生成、上传和当前题面预览仍保留原有版本、上下文与附件校验。实质更正后可以保存真实历史交付事实，但不关闭当前待答或直接计绩效。后续追问读取实际交付历史，夜间分类仍使用首次独立学生提问原始时间。

新匿名用例实际发现同线程SQLite锁冲突：登记持有原消息库锁时，绩效投影再次请求同一写锁。现在先完成源证据与交付事务，再释放源锁并调用已有计量投影；失败或重复登记仍可从原记录核对，不重发消息。测试对临时原消息库缩短等待，以真实SQLite锁错误检出回归，没有改变生产超时或伪造计量结果。

工作台复用原人工登记表，增加已保存回复选项；切换任务后丢弃旧请求的迟到结果，源内容/时间只读，每次重新选择都取消未重新核验的勾选。无来源、读取失败与成功但没有可用选项分别提示；全段原文导入仍不具备单条老师身份/原时间保证，不能据此称为持续监听。候选仅限最近100条中至多20条可核验纯文字；图片、语音或无可靠时间继续手工登记，不在本批虚构自动覆盖。

相关回归`python -X utf8 -B -m unittest tests.test_saved_teacher_delivery tests.test_manual_delivery_workbench tests.test_manual_delivery_registration tests.test_manual_delivery_reconciliation tests.test_shared_source_delivery_integration tests.test_source_question_tasks tests.test_source_question_http tests.test_reference_workbench tests.test_reference_resolution tests.test_performance_delivery_eligibility`在追加生成稿关联与采集冲突两项前为140项通过、0失败、0跳过，46.807秒；追加后保存回复模块14项通过、0失败、0跳过，4.549秒。整批最终全量验证随后执行，未将阶段结果替代真实账号验收。

最终命令`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261002-saved-teacher-delivery-final`：1058项，1055通过、0失败、3跳过，213.132秒，退出码0。运行期间源码未变化，最终快照`sha256:f952e1f7ef00f20c5565ed998a94fd3a85bc7c544d79cce0f4dc892486e326b6`；证据校验和日志哈希校验有效。日志保存在忽略的验证目录，不上传业务材料。3项跳过仍为官方存档SDK未提供、教学依赖符号链接不可创建、浏览器目录符号链接不可创建，均不计通过、不要求管理员权限。

本批验证均为匿名记录、Mock网页/桌面、本机HTTP及无界面浏览器，没有真实外发或正式日报提交。8767原运行配置与数据未修改；真实自动采集生产者、固定ANSWER下DeepSeek网页执行和平台交付观察仍未接通，屏幕2主窗口仍待就绪。全计划保持未完成。

## 追问输入绑定实际交付（2026-10-02）

基线为`7f41593b283b8d7458ab9eae06b1ca3a97738b72`。检查真实网页适配器发现：本地上下文虽已有实际交付，生成提示仍沿用首轮，冻结摘要也没有包含实际回复。两项匿名回归实际复现正文、时间、题目版本和部分数变化不改变摘要，改变后的回复仍可使用旧准备材料。本批在`mcp_generation.py`、`mcp_preparation.py`和`mcp_preparation_review.py`复用原上传与生成流程，加入本轮原话、intent及实际交付上下文，保存文字原貌，不由LLM重述。历史附件只投影名称、哈希与大小，不读取或上传其路径；模拟交付不能作为真实答疑的实际回复。

有原图的追问也提供本轮冻结上下文文件；原文、回复时间、版本和交付部分纳入输入摘要。普通追问只处理本轮疑点；更正和答案异议重新核验当前学生版本。没有实际回复时明确不预设已交付，部分交付不意味着整包完成。上下文缓存使用`question-context-v1`文件名，旧文件保留，不因格式更新复用或覆盖。旧准备包缺本轮上下文时停止，不能自动重放上传或生成。

`PreparedDeepSeekGenerator`在输入及提交前重新读取原数据库，检查暂停、原运行状态及当前上下文。生成期间实际回复变化时，`Workflow.finish()`保存拒绝结果，不建立待交付稿；生成后、交付前变化时，`validate_source_answer()`阻止审核及粘贴旧稿。均复用原运行、Outbox与版本记录，没有新增数据库表、迁移、依赖、框架、消息通道或发送权限。

相邻回归发现两项计量失败：本轮刚核验送达的回答被新摘要当作此前输入变化。`source_question_tasks.py`仅在原来源审核校验中排除当前run自己的已核验输出，其他回复仍参与输入校验，原始消息、身份、版本与附件校验保留。修正后既有成功交付、计量恢复、来源变化停止三类断言继续通过，没有降低计量要求。

相关命令`python -X utf8 -B -m unittest tests.test_mcp_followup_context tests.test_mcp_generation tests.test_shared_source_delivery_integration tests.test_mcp_preparation tests.test_mcp_preparation_review tests.test_mcp_preparation_text tests.test_mcp_fast_preparation tests.test_prepared_run_resume tests.test_prepared_generation_reconcile tests.test_automatic_preparation tests.test_reviewed_question_queue tests.test_source_question_tasks tests.test_manual_delivery_registration tests.test_saved_teacher_delivery tests.test_live_generation tests.test_reference_resolution`：增加缓存分隔用例前194项通过、0失败、0跳过，22.010秒；随后上下文、缓存与交付五模块52项通过、0失败、0跳过，4.767秒。新增执行用例通过真实SQLite和现有人工交付API登记匿名记录，网页及准备证据使用Mock；保留原练习来源禁记正式交付规则，不通过重新标记消息来源绕过该保护。

本轮Luna medium再次有界核对三项Windows-MCP元数据，均成功返回。屏幕2仍不可唯一确认企业微信主窗口，固定码`VISIBLE_SCREEN2_WECOM_MAIN_WINDOW_UNAVAILABLE`；MCP进程PID23532正常退出、返回码0，主Agent核对进程已不存在。原始探针结果进一步说明：屏幕2仅列出两个`ImagePreview`预览窗口，没有符合条件的聊天主窗口，不能把预览窗口当成群聊。没有读取聊天正文、截图、输入、上传或发送。ANSWER仍为干净提交`57159d7a8b03a0743225ed27f3f1e6128bbcd45d`，必需的客观题脚本`gaokao-english/scripts/check_lesson.py`缺项未变。

最终命令`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261002-followup-actual-context-final`：1071项，1068通过、0失败、3跳过，213.777秒，进程与有效退出码均为0。运行期间源码未变化，前后快照均为`sha256:8e2c85e1e5792e712a15c9a6883b4bbd03774b65cb15eff681c25be8c6e43899`，证据与日志哈希校验有效。3项跳过为官方存档SDK未提供、教学依赖与浏览器目录的符号链接不可创建，均不计通过。本批真实验证仅包括只读桌面元数据与本地ANSWER提交/缺项；答疑、交付、上下文及日报回归均为匿名SQLite、Mock适配器、本机HTTP及无界面浏览器，没有真实消息上传、发送、付费调用或正式日报提交。

默认工作台仍只恢复队列准入，没有连接真实网页执行回调；Windows-MCP真实自动收题生产者、固定ANSWER下DeepSeek真实生成和平台交付观察尚未贯通。此次将实际交付接到现有网页输入，不能把Mock页面通过写成真实接通。8767旧服务、原数据与正式日报未修改，全计划保持未完成。

## 网页执行的显示器范围与负坐标（2026-10-02）

基线为`59c1376dca53dc3f50dbda1fc5c1049aa4289609`。Luna medium按本轮只读范围执行DisplayInventory、DesktopStatus及一次屏幕2无图DOM快照，三项均返回成功，PID89272正常退出且主Agent确认已不存在。真实快照报告`Selected Displays: 1`和`Screenshot Region: (0,-1440,2560,0)`，桌面已解锁；活动树无窗口节点，没有可确认的DeepSeek会话、输入框或新会话控件。后台窗口列表包含Edge不构成当前页面身份。私有原始证据为`data/private/windows-mcp/screen2-readonly-capability/20261001T223943Z-screen2-edge-dom-snapshot-raw-private.json`，不上传。

该证据定位到现有网页控件解析只接受正坐标的缺陷，两项新增匿名测试先实际失败。本批在`mcp_page_contract.py`和`mcp_preparation.py`支持原始带符号坐标，并以本地`controls.display_index`控制每次Snapshot/Screenshot范围。原文核验、审批证据和生成沿用该字段；配置缺失、范围不一致、点位越界或提交前控件移动均停止。图片裁剪先扣除当次截图原点，点击仍使用物理坐标，不把负数取绝对值或硬编码当前显示器位置。兼容MCP缩放与未缩放截图尺寸字段及JSON文字输出。

`mcp_preparation_review.py`和`mcp_generation.py`保留并核对上传候选的显示器配置，不能删掉字段后将审批降为不限定屏幕。真实MCP传输必须显式配置；旧证据只保留离线复查兼容。`run_prepared_deepseek.py`移除依据旧标题的通用App切换，要求本人或授权Luna先把已认领会话置前台，再按新快照检查。恢复运行时继续绑定msedge输入保护。没有新增数据库字段、迁移、依赖、通道或自动重试。

相关网页准备、生成、追问、队列及恢复回归99项通过、0失败、0跳过，5.435秒；追加审批降级和旧标题恢复用例后，新增显示器模块与恢复模块19项通过、0失败、0跳过，0.613秒。上传、生成和坐标动作均为匿名Mock，未创建真实输入、上传或提交，不能作为真实网页验收。新增用例首次三处错误来自复用测试fixture时误用不存在的`files`属性，改为其已有课程材料后通过，生产断言保留。

测试文件为`tests/test_mcp_display_scope.py`、`tests/test_mcp_bound_input_process.py`及`tests/test_prepared_run_resume.py`，使用说明更新README和本文件。首次全量1082项中1项错误：原传输绑定用例未提供新的必需显示器配置；补齐匿名fixture配置后原断言继续保留，相邻27项通过、0失败、0跳过，0.617秒。失败日志保留在`artifacts/verification/20261002-display-scope-final/`，不冒充通过。

最终命令`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261002-display-scope-fixed-final`：1082项，1079通过、0失败、3跳过，213.314秒，退出码0，测试期间源码未变化。源码快照`sha256:cffa591a222223e401595b2c09dc598c8f30ff775d63d5b7c4cdacf6685e1e4f`，本机证据校验有效；全量等级仍为离线/Mock集成。3项跳过分别为未提供官方SDK文件和两项符号链接创建不可用，不计通过。真实验证仅包含上述只读MCP元数据及新解析器对其显示器范围的读取；解析器在缺少当前Edge页面身份时返回`WRONG_FOREGROUND_APPLICATION`。没有真实题目上传、DeepSeek生成、企业微信交付或正式日报提交。

ANSWER仍为干净提交`57159d7a8b03a0743225ed27f3f1e6128bbcd45d`，主客观题分支缺少`gaokao-english/scripts/check_lesson.py`，未借用备份目录同名脚本。原8767配置、数据库和绩效台账未修改。真实持续采集生产者、工作台网页回调及当前固定ANSWER的真实教学仍未贯通；页面缺失已在多次有界读取中重复出现，不能用历史快照或模拟动作代替接通。

## 收到与答案任务分离、自动发送及重试（2026-10-02）

基线为`89b51f315e6098e5d51e203e3b6cb2a6c33d18ec`。遵循既有STATUS，不重做采集、归属、网页、数据库或绩效架构。新增`delivery_tasks.py`中的AckTask/AnswerTask视图，分别消费既有ACK及ANSWER/CORRECTION Outbox；不建第二份完成台账。ACK只核验原消息、绑定、身份和原始时间，不等待题面确认或教学依赖。次数、到期时间和结果沿用现有审计表，跨重启保留，收到优先于到期答案。旧`collector_answer_tasks`保留兼容准入记录，发送权威仍为Outbox。

`automatic_delivery_runtime.py`接入工作台的已有后台循环；答疑生成不在该发送循环内。默认启动保留Worker边界；显式提供本机自动配置才选用现有Windows-MCP发送入口，不能同时开启两种桌面发送边界。`demo_server.py`沿用本机Origin/CSRF保护，新增独立任务查看与pause/resume/approve/inspect；本机界面展示群、学生、完整原文稿、尝试和到期时间。启动时保留现有AUTO/MANUAL/DISABLED及审核开关，不因题面队列启用而改写自动发送策略。旧运行服务不被静默升级。

`mcp_group_delivery.py`是当前Windows-MCP连接内的受控文字交付，必须使用本人已核验的本地群/学生白名单及控件证据。每轮重新核验原绑定、当前前台WXWork窗口、显示器区域、真正的标题栏容器、唯一编辑器和本人消息容器；不自动切群/激活其他窗口。完整原文输入后用新剪贴板标记、精确复制和发送前快照检查，一次按发送键。仅新出现的本人消息可确认，旧ACK、学生同文、聊天正文冒充标题、旧剪贴板均不能代替回执。私有尝试证据绑定Outbox、正文哈希和审核配置，不伪造平台消息ID或已读。

`workflow.py`仅把UI操作移出业务数据库写事务，保留SENDING提交、阶段/版本复核和实际核验。确认文字交付后复用已有计量归并；未核验、模拟、练习和ACK不计完成。原群、学生和SOURCE_MESSAGE任务从已有记录取得，不能将OPERATOR_TEST等练习提升为正式外发。确认或显式只读核验均回到原Outbox；未找到共享计量关系仍待核对，不数消息。

重试仅接受可证明未输入/未提交的临时预检失败或桌面锁忙，默认最多3次，间隔5/10秒，可在简单JSON中有界配置。永久身份错误、旧题、验证码、草稿残留和未知发送不盲目重发。重启遗留的自动SENDING转SEND_UNKNOWN，阻止后续自动操作并等待原记录核验；未结束的MCP调用暂停并禁止另开连接。数据库空闲时消息可继续入库，匿名等待生成用例确认后续ACK独立发送。没有数据库迁移、源消息时间改写、附件删除或正式报表修改。

新增测试为`test_delivery_tasks.py`、`test_mcp_group_delivery.py`、`test_automatic_delivery_workbench.py`，并补充已有启动测试。首轮原生协议用例中8项失败来自匿名窗口高度把编辑点放在下边界；修正fixture几何后保留越界断言。随后移动测试的模拟动作发生在已生成快照之后且未移动窗口元数据，补齐真实窗口移动模拟；共享来源fixture另有旧ACK待发送，接入真实来源校验并先核验该ACK，保留收到优先断言。界面测试关闭HTTP监听时调用顺序不正确导致线程异常，改为先shutdown再server_close；浏览器取消只读刷新连接按正常断连处理。均未删掉关键断言或把失败计通过。

新增发送/原生/HTTP/启动模块相关40项通过（23.803秒），交付登记、题面及界面相邻98项通过（38.568秒）；追加锁忙耗尽状态用例后相关60项通过（25.715秒）。这些均为匿名SQLite、Mock原生协议、本地HTTP/PowerShell与无界面浏览器，没有真实企业微信输入/发送、DeepSeek上传/提交、付费Provider调用或正式日报提交。

最终命令`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261002-ack-answer-automatic-final`：1117项、1114通过、0失败、3跳过，225.857秒，过程与有效退出码均0；测试期间源码未变化，快照`sha256:2c9e7c18d07f3178cda464b33cf853c618ae8859ca1e1ba09adf2efa18a09f20`。3项跳过为未提供官方SDK文件及两项符号链接创建不可用，不计通过。等级为离线/Mock集成，不用全量通过替代本批真实外发验收。

ANSWER仍为干净固定提交`57159d7a8b03a0743225ed27f3f1e6128bbcd45d`，客观题必需`gaokao-english/scripts/check_lesson.py`仍缺失。本机原8767服务和配置未操作；发送配置示例明确禁用、控件未核验。新发送入口为已核验的前台单群文字交付能力，不证明持续收题、跨群导航、媒体发送或网页执行回调已贯通，当前没有真实自动新题全流程验收。

用户另要求subagent提炼本人先前发给DeepSeek的prompt，并明确范围为所有置顶会话。初始系统交互标准已撤回；代码模板、生成稿和DeepSeek回答不作为个人用法样本，不修改ANSWER教学Skills、不新增重复进度文件。

仅由`gpt-6-luna / medium`操作读取桌面，主Agent只检查代码、进程元数据及已保存证据。初始屏幕2快照明确失败：本次只有索引0（DISPLAY9，2560×1600）；用户随后明确授权只读当前唯一屏幕。同一Luna通过新快照、真实Edge前台身份及DeepSeek网址核验页面；侧栏置顶组之后为“今天”，实际来源共10个，主Agent已离线复核该分组边界。原文及私人链接保存在本机`pinned-user-prompts-20261002.json`，不提交Git。

10个来源已逐项核对：4个有真实USER文字样本（其中2条旁有图片），4个仅见文档附件，1个扫描未取得明确USER，1个附件归属待核验。文档卡片以右侧上传区和独立DeepSeek响应区的界面分界为证据，只记可见文件名，截断名保持截断，不读取课程正文、不算题图。主Agent核验了10个唯一来源、4条原文一致性、2条图片标记及4份文档截图存在。样本支持原句提问、题号加图片及简短追问；未核验到纯图片无文字USER消息，该用法保留为本人明确偏好。未采样完整历史，不以AI回答或角色不明的图片补齐；这些样本不作为实际交付或绩效证据。

私有读取助手的启动失败来自发布副本默认工具目录不存在；已仅在助手进程指定现有`HELPDESK_WINDOWS_MCP_HOME`，没有改系统设置、原8767服务或生产配置。只读导航使用既有Windows-MCP的新快照、点击、有限滚动及固定Ctrl+Home；连接正常关闭后才重开。回访时前台守卫检测到Weixin，点击被阻止，没有切回、操作或读取该窗口正文，2项进一步回访未完成。MCP PID77888、91872均正常关闭，主Agent独立确认不存在；后续仅复核已保存文件。没有发送、上传、编辑聊天或启动DeepSeek生成。本次唯一屏幕的只读授权不扩大正式群发送范围。业务改动已提交为`fc728440a12ecc8de376e098791283ec03962d8c`，本次只补充README和本进度中的实际采样结果。

## GUI重叠观察去重修正（2026-10-02）

在`b1e50bc`之后复现：相邻观察包含同一条消息及一条新消息时，旧适配器重新生成UUID，最终保存3条并触发一次指纹冲突。`gui_message_source.py`现按群及提供者的持久逻辑位置生成内部身份，不伪造平台消息ID；同一批出现重复位置时在提交前停止。位置不能是屏幕坐标或可见行号，无法证明连续性时提供者必须停止。没有可靠位置或原始时间的消息保留未核验状态，不创建ACK。

`collector_storage.py`只对本适配器身份、完整结构及全部消息字段一致的重读允许采集证据变化，原记录和首次证据仍保留。内容、发送人、原始时间、附件、归属或核验标志变化继续记录冲突并阻止尚未处理消息的自动应答；任意及历史UUID不适用此例外。不自动迁移旧身份、不改表、不操作正式数据。`tests/test_collector_integration.py`补7项匿名场景，覆盖重启、重叠、跨群、冲突、缺时间/位置及重复位置。真实持续观察提供者仍未接通。

相关回归107项通过。全量命令：`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261002-gui-observation-identity-final`；1124项、1121通过、0失败、3跳过，退出码0。源码快照`sha256:5064625aa83f704eeaf7efacb0e4b889d26d2b2f9ec78feafa54c66ae2600cbe`开始与结束一致，证据核验有效。测试为匿名SQLite、Mock、本地HTTP及无界面浏览器；3项跳过仍为官方SDK文件未提供及两项符号链接创建不可用，不计通过。

另由Luna medium完成一次真实的只读显示器/窗口元数据检查，未截图、切窗、输入或发送。当前仅索引0（DISPLAY1，2560×1600），没有屏幕2；桌面解锁，前台Edge，可见窗口计数为Edge 2、WXWork 4，不能据此认定唯一业务窗口。屏幕2企业微信身份未能确认，停止进一步业务桌面操作。MCP进程31452正常关闭且已退出。仅屏幕0的DeepSeek置顶会话只读授权没有扩大为企业微信操作授权。本批没有真实收题、DeepSeek生成、群发送或正式日报提交。

## Luna显示器定位修正（2026-10-02）

`luna_navigation.py`原先写死索引1、DISPLAY2及`(0,-1440,2560,0)`，即使获准读取当前屏幕也不能定位。现增加显式`display_index`（默认1），从同一份新截图核对唯一选择、显示器条目、区域及实际图像比例，按当前原点换算建议坐标。设备名数字不再被当成显示器索引。重复/矛盾区域、多屏混合截图、错误配置、失真或过期图像在模型调用前停止；图像放大后的末端像素也不会换算到屏幕外。模型仍为Luna medium，仅提供限定动作建议，既有窗口身份复核与禁任意工具能力保留；本模块不执行输入。

新增7项匿名测试。相关命令`python -X utf8 -B -m unittest tests.test_luna_navigation tests.test_mcp_display_scope tests.test_mcp_window_probe tests.test_mcp_bound_input_process tests.test_native_intake`：42项通过。全量`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261002-navigation-display-final`：1131项、1128通过、0失败、3跳过，242.892秒，退出码0。源码快照`sha256:bfda20bf00347f2cd1e579f2280d1a9cbfed99cc8a58e5dfc1fcbc1b28cbfb64`未变化，证据核验有效。范围仍为匿名/Mock、本机HTTP与无界面浏览器；未提供SDK及两项符号链接创建不可用分别记SKIPPED。

本人随后授权当前所有屏幕用于只读联调，并明确“现在真实联调，我不动电脑”；不据此启用群发送或DeepSeek新请求。第一次尝试的前台已变为Weixin，Luna脚本在检查后误多取一次只读Snapshot，未点击、滚动、输入或发送；该私有记录不解析为业务消息、不上传Git，PID16296已退出。恢复联调时要求前台检查与截图分开执行，先判定应用身份再读取。真实消息字段、空白DeepSeek会话网址及完整业务闭环仍需实际证据，不能用上述Mock结果替代。

## 当前显示器上的受控企业微信激活（2026-10-02）

`mcp_window_probe.py`将既有只读企业微信窗口查询与快照核验参数化；设备名称只允许严格的Windows显示器格式，不能形成任意命令。`tools/windows_mcp_session.py`新增同一连接内的`ActivateWeComOnDisplayByApp`入口，只接受显示器索引和设备名，复用原有唯一主窗、缓存名称/句柄/几何、桌面可用性和切换后核验。原屏幕2入口保持兼容，原审计字段名保留但记录实际显示器参数。不新增MCP服务、部署单元、消息通道、数据库或发送权限。

原因是本机Windows-MCP的原生App在窗口缓存缺失时会隐式调用完整桌面状态并进行模糊名称匹配；本入口先明确检查选定显示器缓存及原生身份，不允许空缓存退回这一隐式读取。缓存构建仍使用现有Snapshot/UI树能力，并非独立的无UIA激活实现；只消费窗口元数据，预检快照正文不写入业务消息，审计仅保存其哈希。当前页面读取是否可用需另外验证。

真实Luna验证已成功：index0/DISPLAY1，激活前后独立原生检查确认WXWork、同一窗口句柄及一致几何，受控入口返回`TOOL_RETURNED`。之后单独的群内容Snapshot返回`REQUEST_OR_NATIVE_RESULT_UNCONFIRMED`，对应尝试保持`OUTCOME_UNCONFIRMED`且没有结果路径；这次失败不能作为群名、学生、原始时间或消息证据。此前未经预热的App尝试也保留未知记录，不改成成功。MCP进程50056及已知子树49536/49836/10388/8420均已退出，未重复激活、未输入或发送。

改用不读取UI树的快速Screenshot后成功取得一张原始图像；Luna从画面确认当前为已授权范围内的English答疑群，仍为同一WXWork窗口。可见聊天气泡但缺每条消息的日期和原始发送时间，侧栏会话更新时间不代替这些字段；未看到可据此保存的原题图片。原截图与MCP结果保存在忽略的私有目录，不上传Git。截图仅用于定位，未作为消息导出、原始时间补值或绩效输入；持续采集及原文导出须另行验证。

随后一次人工Agent定位遗漏截图缩放：PNG为1728×1080，实际区域为2560×1600，直接以图像像素点击打开了群列表菜单。Luna在新截图中发现没有消息复制项后停止，未选择菜单项、操作剪贴板或生成原文记录；对应连接及可枚举子树已退出。这不是MCP映射错误，也不代表已有`luna_navigation`的坐标换算失败；此次手工调用没有使用换算结果。后续操作必须使用本次截图实际尺寸与区域换算后的坐标，不能重复使用旧像素位置。

`test_screen2_activation.py`补4项，覆盖当前屏幕、身份/范围矛盾、非法参数、移动及超时不重试。相关51项通过。全量命令`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261002-configured-wecom-activation-final`：1135项、1132通过、0失败、3跳过，221.582秒，退出码0，源码快照`sha256:dcc9229af996d51ef94af43c633704019370838f2bc314a7a987d28cbb261a5c`未变化，证据有效。全量仍是匿名/Mock集成；本次真实验证覆盖激活、前台身份和截图定位，群内容Snapshot读取失败。3项跳过仍为SDK文件未提供及两项符号链接创建不可用。

## 当前显示器上的 Edge 入口（2026-10-03）

当前显示器的Edge入口另于2026-10-03完成最小兼容修改：`ActivateEdgeOnDisplay`复用原有标题栏原生检查，只接受物理坐标和严格设备名，核验命中的真实进程、标题栏及切换后同一窗口/几何；旧屏幕2行为保持兼容，没有新增桌面服务或任意命令入口。补3项匿名测试，相关47项通过。全量`python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261003-edge-display-final`为1138项、1135通过、0失败、3跳过，226.213秒；源码快照`sha256:6057ff05eb7e61a20da6140d8bf31f739b83d7324398a008bb1b19232230bfbc`未变化，证据有效，仍属离线/Mock。

真实尝试在首次前台检查发现Chrome后停止，尚未执行这个Edge入口，也未读取DeepSeek页面。最后MCP连接及可枚举子树已退出，无待返回调用；未上传、提交、群发送或写正式计量。此前重新定位企业微信的尝试仅取得新截图，在下一次右键前停止，仍未导出原文。真实切窗、完整收题和网页队列均未因此升级为通过。3项SKIPPED仍为官方SDK未提供及两项本机符号链接创建不可用。

## 已可使用

- 一个 PowerShell 入口启动本地工作台，缺可选采集配置或讲解目录仍能查看台账；复用服务不会自动恢复暂停。
- 原生文字增量导入、原文导出及来源校验；原始时间与采集时间分开，重复采集去重。保存原文不等于实时监听。
- 原消息的一次题面确认、持久队列、题目与上下文版本、选项映射和旧答案拦截。
- 内部原题核对的候选、逐字段证据、人工确认/拒绝与确认结果的冻结输入；默认关闭及Shadow不消费参考，不新增发送权限。匿名与模拟网页路径验证，真实网站尚未验收。
- Outbox、未知发送停止核验、人工接管、目录限制及本机访问。
- 本机登记人工实际交付及多部分结果，回到原任务与原计量台账；不依赖重新生成AI草稿，缺归属时明确待核对。
- 完整讲解展示和复制，三栏日报及明细导出；绩效检查实际交付资格，已提交报表不静默覆盖。
- 本机已验证屏幕2的企业微信标题栏激活、原题文字复制与原生图片保存；一题历史练习已通过真实 DeepSeek 网页上传原教学文件和生成完整讲解。练习没有向群发送，也没有增加绩效。
- 已登录的本机 Codex 可进行 Luna medium 有限导航定位；输出只是控件位置建议，程序仍核验新鲜的窗口身份、物理坐标和目录。可选网页执行已接入原业务队列并通过Mock，持续观察及修补后的真实队列仍待验证。

## 尚未贯通

- 持续读取真实群中新消息，取得可靠学生身份、原始发送时间和附件，并由自动入口完成“收到”。历史现场确认的 ACK 不作为当前实时服务的证明。
- 共享归属语义与真实题面恢复接线，以及已接持久网页队列回调的真实联调。
- 原群草稿与人工发送后的真实交付观察；本机人工登记和计量回流已通过匿名验证，真实操作仍未验收。
- 所有 English 群（含已退出群）9月17日起的完整历史覆盖；部分导出不能视为正式总量，缺原年份不得猜测。

因此，当前工作台可运行，完整新题自动答疑仍未通过真实环境验收。历史题练习保持禁发，不能把模拟回执或生成成功改成正式完成。

## 本轮验证

早期教学来源回归17项通过；屏幕2与相邻保护回归52项通过。真实ANSWER的六类本地原文预览与重复缓存核对通过，范围仅本地来源与文件生成；当时没有上传DeepSeek、运行教学检查或向企业微信输入消息。当时完整回归为上述996项；“MCP安装路径与真实只读核验”阶段最终版本为1037项、1034通过、0失败、3跳过。当前最新结果见本文件开头“当前最终验收基线”，原有有效测试保留。

本轮针对导航、物理窗口、输入契约、传输和审计的47项回归，以及相邻准备/草稿路径的27项回归通过。原消息/附件衔接及页面断连恢复分别完成相关回归，本地服务检查未执行发送或增加计量。

发布副本完整回归实际执行 `python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261001-initial-git-publication`：889项，886通过、0失败、3跳过，127.943秒，退出码0；测试期间源码未变化。219份业务源码、测试、提示词及入口/依赖文件与原工作目录逐字节一致。两个跳过是本机无法创建符号链接，另一个是独立源码副本未提供官方存档SDK的原生DLL，均不计通过。不上传私有SDK文件来消除这项跳过。

测试范围为匿名数据、模拟适配器、本地HTTP、PowerShell入口和无界面浏览器；没有实际企业微信发送、实时新题完整答疑、付费Provider调用或正式日报提交。原始测试日志和源码校验结果保留在本机发布副本的忽略目录中。

早期真实原图已从客户端保存并校验为原生 JPEG；题面识别清楚，但完整原始提问时间仍待补证据。该早期DeepSeek历史练习使用已核对文本，不能声称它使用了后来取得的图片；2026-10-03获准原图上传的专项另见上文，不补造原始时间或交付。

## 暂缓扩建

保留现有轻量接口和 Worker 连接，暂停扩建多节点、主备、微服务、监控平台及自动模型升级。开源更新定时维护暂缓，先解决上述真实闭环。仅在实际多机、多人或远程访问需求出现时恢复相应工作。

教学 Skill 的真实路径与允许目录防护继续保留。符号链接测试受本机权限限制时记为未验证风险；只加载本人审核、目录受控的材料，不开放外部任意 Skill 路径入口。
