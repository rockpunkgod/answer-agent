### 2026-10-21：完整交付的终态与重复推进

沿用原队列和完整交付验证，只有原任务完整回执已核验且题目/上下文仍有效时才投影phase=DONE；持久化生成phase和原回执保持原样，不新增完成表或状态迁移。交付后更正仍是NEEDS_ATTENTION，保留DELIVERED_STALE历史事实。advance及AutomaticAnswerRuntime遇DONE直接返回，不再次进入生成恢复、网页或执行回调。工作台补充DONE显示名。

DONE不覆盖原来源/生成核验错误：自动交付继续要求原生成与来源验证通过；人工交付在原稿取消后复用原来源回执验证。来源证据改变仍保留历史交付，但任务进入NEEDS_ATTENTION，不因为有一条完成回执而掩盖错误。

本轮主终态改动的UNIT/MOCK回归：`python -X utf8 -B -m unittest -q tests.test_automatic_answer_runtime tests.test_delivery_batches tests.test_reviewed_question_queue tests.test_manual_delivery_registration tests.test_source_question_tasks`，114项通过、0失败/跳过，603.732秒；测试句柄和进程均核验结束，未因较慢另启同批测试。

随后来源边界加强后的最终定向：`python -X utf8 -B -m unittest -q tests.test_reviewed_question_queue tests.test_manual_delivery_registration tests.test_delivery_batches.NativeBatchIntegrationTests`，53项通过、0失败/跳过，15.818秒。包括自动/人工交付后的来源篡改拒绝DONE、当前版本更正与UNKNOWN边界。独立启动新Python进程打开原数据库，get和advance均返回DONE，禁止执行的四类回调均未调用；outbox/delivery_checks/audit/performance_events/reviewed_question_attempts数量不变。JS语法/diff检查通过。这是匿名SQLite和模拟网页/原生协议恢复，不是真实账号崩溃/发送验收，不将两批重叠测试相加成唯一测试数量。

完整Level 3目标仍NOT_READY；未操作桌面、读取真实新消息、外发、提交DeepSeek或修改正式绩效。本批只补实质终态保护，不扩建工作流平台。

### 2026-10-21：原任务展示自动交付事实

已修复上轮发现的回流展示缺口。复用 completion_for_turn、原 Outbox/delivery_checks/分段计划，在任务卡和原队列只读投影自动交付；不创建新台账、服务、状态迁移或发送动作。必须有完整、匹配任务/版本/目标/正文/完成时间的原回执；分段复用原 validate_complete，单段回读复用原回执校验。模拟、草稿、部分成功、UNKNOWN及缺少/损坏回执不显示已完成。指定原Outbox时不会借用同轮其他任务的回执。

reviewed_question_queue原执行phase保持原义，student_delivered只反映已验证的历史交付事实；新增只读delivery_status区分VERIFIED_COMPLETE与DELIVERED_STALE。交付后版本或上下文变化仍保留历史事实并标旧，不当成当前题目已解决。人工登记原路径继续使用原验证方法；界面区分人工核验与实际发送回读，不伪造平台ID或已读。重复查看不写发送/绩效记录。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_delivery_batches tests.test_shared_source_delivery_integration tests.test_manual_delivery_registration tests.test_manual_delivery_reconciliation tests.test_source_question_tasks`，83项通过、0失败/跳过，26.233秒。新增四项覆盖完整后才回显、UNKNOWN拒绝、交付后更正保留旧事实、回执丢失不报完成；重复读取未增加outbox/check/audit/performance_events。JS语法检查和diff检查通过。模拟原生协议不等于真实发送，本轮未操作客户端、读取真实新消息、提交网页、外发或修改正式绩效。

完整目标仍NOT_READY。前一目标轮属于进展（轻量化已提交5faceda）；本轮修补实际业务回流展示，不以此替代真实ACK、自动题面归属、原图补下载、Reference准入、两阶段全链路、20任务/恢复/成本验收。当前用户仍要求后台开发，真实桌面待可用时验证。

### 2026-10-21：轻量化首批清理

当前默认入口为既有工作台控制采集；history/poll/watch 独立命令保留用于首次基线、单独运行或维护，不要求同时开两套循环。采集 CLI 移除未取得原图的 probe-accessibility / locate-image 实验接线，并在加载账号前拒绝旧命令；不再加载原生桌面传输、读取前台窗口元数据或依赖 observe_foreground 开关。移除无活跃调用者的个人微信激活/idle方法及该停用方法的专用测试；通用焦点释放与用户接管保护及测试保留。UIA诊断代码、其保护测试、原失败/UNKNOWN证据仍保留，不是当前启动步骤。

README收敛为73行当前用法，旧内容归档 LEGACY_OPERATIONS.md；不删除数据库、附件、运行证据或旧配置，不更改生产配置、发送/版本/Reference/Performance核心保护。未成功取图没有因清理被改写成成功，仍缺真实自动原图获取。

回归命令：`python -X utf8 -B -m unittest -q tests.test_personal_wechat_collect tests.test_personal_wechat_polling tests.test_personal_wechat_source tests.test_wecom_native tests.test_personal_wechat_accessibility tests.test_collector_supervisor tests.test_current_demo_launcher tests.test_demo_http`。93项通过、0失败/跳过，24.187秒；CLI帮助确认只剩inventory/sync/media/upgrade-cursors，JS语法检查通过。新进程离线导入两个采集入口后，原生桌面、UIA、上游wx/media均未加载。上述为UNIT/MOCK/匿名HTTP/启动脚本验证，本轮没有真实采集、桌面操作、外发或正式绩效变更。

完整Level 3目标仍NOT_READY。本次复核另外发现：completion_for_turn当前只投影人工登记完成包，reviewed_question_queue的student_delivered固定false；自动回执到任务卡的只读完成投影尚待补齐。本轮遵照先精简的最新要求没有顺带扩建该功能。其余既有缺口仍以原完整验收映射为准，不以轻量化删掉消息事实、ACK独立、实际交付或绩效需求。

### 2026-10-21：个人微信采集接入现有工作台（未运行真实账号）

复用 CollectorSupervisor 的原线程和控制入口，个人来源每轮调用隔离 poll 子进程，继续写原 CollectorStore，不跑 ACK drain、不新建业务完成台账。工作台显示 PERSONAL_WECHAT_LOCAL，只读原文区展示本库消息、发送人和原始时间；不标为官方存档，不用假定0条替代未知统计。失败或超时停止，配置改变要求重启；JSON/TOML同库校验保留。启动脚本显式指定不同采集配置时拒绝复用旧服务，避免误用旧来源。

私有 collector.local.toml 已建立且仅离线核对路径，未覆盖已有配置，未开启轮询。启动方式：`./run_current_demo.ps1 -Port 8768 -CollectorConfig data/private/replica-runtime/collector.local.toml -NoAutoCollect`，在工作台点击启动；去掉 NoAutoCollect 可自动启动。先完成历史基线，不要同时另开独立 watch。范围仍为只读采集/入库/展示，个人来源至 AnswerTask、ACK、教学、发送和正式绩效的原限制不解除。

UNIT/MOCK及匿名HTTP/Windows启动器回归：`python -X utf8 -B -m unittest -q tests.test_personal_wechat_polling tests.test_collector_supervisor tests.test_personal_wechat_collect tests.test_personal_wechat_source tests.test_current_demo_launcher tests.test_demo_http`，64项通过、0失败/跳过，24.541秒；JS语法检查通过。初次新增回归1失败1错误：测试命令下标误期望，以及GET中原后置sqlite3局部导入造成原文分支错误；均修正后完整回归通过。不是Mock替代真实账号；本轮无真实采集/外发，无正式绩效修改。真实持续新到达、原图补下载和下游全链路保持待验收。

### 2026-10-21：独立采集入口（未启用生产）

新增 tools/personal_wechat_collect.py：history / poll / watch，脱离答疑主程序运行，复用原 CollectorStore 与持久化游标。watch 为持续增量轮询，每轮隔离子进程，失败/超时即停；不复用上游失败后仍推进水位的 Listener。采集模式只加载 db.py，不加载媒体、UIA、桌面传输、答疑或发送模块；历史不 ACK，个人来源生产守卫不解除。原媒体和诊断命令保留，不为精简重写已稳定内核。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_personal_wechat_collect tests.test_personal_wechat_source tests.test_personal_wechat_accessibility tests.test_wecom_native`，57项通过、0失败/跳过（1.284秒）；CLI --help 实际通过。当前仅后台开发，没有真实运行该入口或常驻服务。真实持续新消息、轮询成本和中断恢复尚待验证，原图缺缓存仍不自动补下载。启动方式与依赖边界见 PERSONAL_WECHAT_LOCAL_SOURCE.md。

### 2026-10-21：取图定位诊断及后台修补（未上线）

当前用户正在使用电脑，已停止桌面输入、激活和无障碍探测，仅后台开发/离线测试。未发送 ACK/正文，未调用付费 API 或 DeepSeek，未修改正式绩效。

REAL：普通 UIA 只有 Qt 外层；显式隔离诊断临时开启 Qt 无障碍后，在最小化测试群中读到标题及图片控件，并以精确文字/图片序列唯一绑定原消息，前台不变。临时进程标志已回读恢复；这包含进程内存写入，默认关闭，不能宣称纯只读进程操作。后台一次 Invoke 和前台一次 Invoke 均未核验到预览窗口，保留 PREVIEW_OUTCOME_UNKNOWN，不算自动取图成功。前台实验检测到新输入后不强制恢复窗口。异常进程终止后的标志恢复尚未验证。

最小改动：独立 opt-in UIA 诊断和图片定位 CLI；复用现有原生传输添加有限激活、输入状态与条件释放；媒体保存后实际完整解码，损坏文件保留证据但不标可用。不引入生产依赖或第二套控制器。实现和能力边界见 PERSONAL_WECHAT_LOCAL_SOURCE.md。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_personal_wechat_accessibility tests.test_wecom_native tests.test_personal_wechat_source`，50项通过、0失败/跳过，1.157秒。真实已保存原图离线解码通过（1080×1920）；这不是自动获取验证。整体仍 NOT_READY；下一步先消解未知预览结果，再验证受控原图获取，当前用户使用电脑期间不执行。

### 2026-10-21：Level 3 目标复核及自动接线增量（未上线）

用户目标原文已读取：`2384694d-9325-4171-9fe4-2cb55a7084f4/pasted-text-1.txt`。保持完整 Level 3 目标，不把本轮只读练习或新增接线缩小成最终成功条件。业务远端 main=7bd61a5868619a6033f6b45af91db34e49b6138a；原主工作区13项未提交变更未动。

ANSWER_COMMIT=1c388a0596b13935ae7c6a1af00834915a8efa33。重新 fetch 并用 ls-remote --symref 核对远端默认 main/HEAD，最新确实为该提交；验收工作树干净。主参考目录 HEAD=b04ebc2，不能把它冒称最新内容；实际教学包来自独立的 ANSWER-acceptance-1c388a0 工作树。当前原文、完整依赖及缓存仍由原 build/verify 检查，不修改教学文件或脚本。

本批实际改动：SourceQuestionTasks 支持共享语义决定确认后的自动来源核验/任务创建，原 AutomaticAnswerRuntime 在显式 admit_confirmed_sources 下承接，复用原 WAITING_ACK 和恢复队列。默认关闭，仅已有可信来源、既有 CollectorStore、ego MATCH_THEN_TEACH 允许配置；不解除个人微信只读原型限制。失败按决定记录原 audit，不无限重试，也不挡住后续合法决定。此前人工审核结果原样复用。此接线不提供尚未接通的消息语义模型。

修复真实网页发现的 JSON 代码块按钮文字：只解包已观察的 json/复制/下载前缀或完整 Markdown fence，原捕获不改写；半截、额外说明、重复键、NaN 和非对象拒绝。仍执行原候选ID、差异、选项映射和版本校验。

| 冻结验收项 | 现有实现/证据入口 | 当前结论与真实缺口 |
| --- | --- | --- |
| ANSWER 来源/依赖/缓存 | answer_teaching、teaching_bundle、teaching_routes；远端实查与原文 manifest | 版本已重新核实；该次真实草稿检查失败仍保留 |
| 消息/身份/原始时间/去重/15分钟入口 | message_sources、message_sync、collector_storage、native_message_source；personal_wechat_source | 文件来源保留，个人微信四群本地读取已实测；持续真实新到达及生产者/SLA未验收 |
| AckTask 独立与幂等 | collector_dispatch、delivery_tasks、AutomaticDeliveryRuntime、test_ack_before_generation | 原收到路径保留；个人微信原型不发送；新消息真实15分钟SLA未证实 |
| Reference 预算/独立失败/Top2 | reference_lookup、reference_fetch、reference_resolution；现有相关测试 | 站点配置和保护保留；准入/真实可用候选来源未完成，不伪造已搜索 |
| 两阶段核验与24种映射 | question_matching、ego_generation、test_question_matching、test_ego_matching | 结构/ID/映射已有匿名回归；独立网页练习不是完整业务匹配回执 |
| 详细教学与发送前检查 | lesson_checks、Workflow、ANSWER原脚本 | 新测试群两稿真实失败；不放宽检查、不继续绕过该题重试预算 |
| Session及追问复用 | session_isolation、followup_reuse、question_session_isolation | 匿名隔离/追问测试保留；新个人微信群真实追问交付未验收 |
| AnswerTask分阶段重试 | reviewed_question_queue、automatic_answer_runtime、ego_revision | 新增自动准入，无第二次人工点击；语义模型输出至确认仍未自动接线 |
| AutoSendGate | Workflow、delivery_tasks、wecom_tools | 原目标/版本/暂停/UNKNOWN守卫保留；个人微信生产发送未启用 |
| DeliveryBatch/回读/DeliveryRecord | delivery_batches、delivery、Workflow | 原有分段/部分未知停止保护保留；本轮无真实发送或交付回流 |
| 中途更正/旧答案阻止 | service、Workflow、test_question_matching、相关版本测试 | 保护保留；新消息源真实中途更正试运行未完成 |
| 绩效/23:00/重复日报 | semantic_decisions、performance_rules、performance_reports | 复用同一归属，候选数量不等于确认完成；新源无正式交付、不记正式绩效 |
| 实际成本统计 | call_costs、RunMeter、task_cost | 正式队列已有记录接口；当前真实全链路实际账单仍未核实，平均/P50/P95/max不能报0 |
| A–H真实场景与20任务试运行 | 原STATUS真实证据及私有Pilot记录 | 未达到；不把历史图或匿名fixture凑成真实20任务 |
| 六阶段重启恢复 | test_process_restart、test_ego_process_restart、test_ego_revision | 原匿名进程测试保留；新群完整真实恢复未验收 |

REAL修补：原图加载仅改变download_status，旧整行锚点误停。先备份，再对照原始行逐个升级当前四群8个v1游标为v2；未移除seen锚点、不吞未见消息，旧停用测试群游标不动。随后LIVE四群0新增/0重复/0冲突，前台未变；原始17条消息及events/message_conflicts/message_media与备份逐表哈希一致。不是新消息SLA或自动补下载验收。

UNIT/MOCK最新定向：9组89项通过，0失败/跳过，22.529秒，含来源自动准入、原图下载状态、v1证据升级、网页解析、两阶段/JS守卫、HTTP来源、交付集成和会话隔离。新增测试初次对阶段名误期望ENQUEUED，原API实际投影WAITING_DESKTOP_EXECUTOR；只修正测试期望，业务阶段未改，已在该89项中回归。先前较完整回归82项中81通过、该误期望1失败（591.038秒）；不将失败整批说成全绿，也不为此反复运行整套长测试。

补充复核：空间名称与旧客户端共用原物理页面锁，各名称的未完成提交互相拦截；不能靠换名称或不传名称绕过UNKNOWN。新增跨名称/旧入口互斥后，5组52项定向通过，0失败/跳过，3.312秒。一次补跑命令误用了配置测试所属类名，报2个测试定位错误；改用AutomaticAnswerConfigurationTests后4项通过（0.314秒），没有据此修改生产逻辑。当前进程BRAVE_SEARCH_API_KEY未配置，未发现主工作区私有目录顶层的reference TOML；不能据此断言其他账号不存在配置，也不能声称本轮已启用真实搜索。

原有核心回归：`python -X utf8 -B -m unittest -q tests.test_reference_lookup tests.test_reference_fetch tests.test_reference_resolution tests.test_delivery_batches tests.test_performance_rules tests.test_call_costs tests.test_followup_reuse`，135项通过，0失败/跳过，433.477秒；这些是UNIT/MOCK或本地HTTP，不是对真实网站、真实发送或实际账单的验证。原文包实际verify_bundle(for_generation=True)再次通过，8份原文、1项必需依赖完整。新的正文稳定窗口已在两次真实生成使用，但真实流式长停顿/中断样本未验收，不以稳定窗口单测宣称完整捕获已全面解决。

教学来源/依赖/缓存回归：`python -X utf8 -B -m unittest -q tests.test_answer_teaching tests.test_teaching_routes tests.test_teaching_bundle`，48项运行、47通过、1跳过、0失败，79.079秒。单独verbose复核跳过项为test_symbolic_link_dependency_is_rejected，原因Filesystem cannot create symbolic links；保留SKIPPED，不算通过，不要求管理员权限或关闭系统设置。已有真实路径与Windows junction防护及测试保留。

结论仍 NOT_READY。下一业务缺口保持：自动语义理解/现有确认接口接线、原图无缓存时的自动获取、授权测试目标的真实交付与追问，以及可核实成本和稳定性试运行；不以新增模块或测试数量替代验收。

### 2026-10-21：隔离个人微信数据库采集原型

`prototype/personal-wechat-db-20261021` 基于 main 7bd61a5，原 main 工作区未提交修改保持不动。新增明确来源 personal_wechat_local，复用 CollectorStore/SyncEngine，不允许 ACK、业务投影或正式绩效。只加载固定上游 db/media，替代有丢回调风险的原 Listener。配置、缓存、密钥及证据只在私有目录。完整范围与命令见 PERSONAL_WECHAT_LOCAL_SOURCE.md。

REAL：三个 English 群各三条本地系统记录；后续唯一“测试”群读到真实文字与图片、完整原始时间及发送者。旧测试群停用并保留记录。用户加载原图后，本地后台原图读取成功且哈希已保存。不能把这次人工加载说成自动获取。未自动发 ACK/正文，未改正式台账；来源至现有 Workflow/Outbox 尚未接通，整体全自动仍 NOT_READY。

UNIT/MOCK：最终采集与网页13组162项通过、0失败/跳过，17.278秒；JS worker 内15项通过。合并首次运行新增 JS 测试的清理代码误用不存在的 fixture.directory 导致1项失败，改用已有 cleanup 后重跑全部通过，未删断言。真实恢复去重有验证，真实新 LIVE 到达、缺原图自动获取和进程崩溃后的真实恢复尚未验证。

REAL WEB：新测试群B篇原图只上传一次；识别24—27题、同会话上传固定ANSWER原文、生成稿、唯一一次修订均真实执行。最初捕获半截JSON，原捕获不覆盖；网页只读补取完整结果后校验结构。修正以后捕获增加正文稳定检查。原ANSWER定位脚本和成稿检查脚本实际运行：初稿5个method_visibility疑点，唯一修订后仍2个，状态BLOCKED_DRAFT_CHECK，未再重试。保留原始输出、补取证据及两稿；未伪造教学通过或Workflow/Outbox接通，未外发/计绩效。网页结果页保留供查看，控制已释放。

### 2026-10-20：单条原始时间的人工确认回读

- 用户仅确认本条测试消息日期为 2026-10-19；确认追加至现有 audit，关联原始文字、时间观察和既有 OPERATOR_TEST 消息，写入前已备份 SQLite。未修改原始证据或批量补年份。
- poll 现在通过 confirmed_observation_times 返回与当前观察严格匹配的人工确认；不改变原始消息时间字段、不放开自动 Ack 或正式绩效。日期/时钟不匹配的确认不采用，冲突年份停止处理。后续检查补充：当前扫描未取得原始时间标签，或标签不同，即使原文相同也不回填旧确认，避免把另一条同文消息误标旧日期；对应88项回归再次通过，无跳过。
- UNIT/MOCK VERIFIED：相关七组回归共 88 项通过，无跳过。覆盖重启回读、重复采集不新增、错误日期拒绝及冲突年份停止。
- REAL DATA VERIFIED：在现有测试数据库只读回读确认，得到 2026-10-19T23:12:32+08:00；未操作桌面或外发。新消息时效、完整真实闭环和 20 个真实任务仍未验收，结论仍为 NOT_READY。

# 当前能力与缺口

## 绩效三栏入口与正式数量边界（2026-10-20）

最小增量：tools.performance_report新增totals起止日期命令，复用原generate/catch_up及核验台账，直接显示日期/白天综合/语法听力/夜间答题数字；可导出UTF-8-BOM CSV，不依赖Office、额外模型或Node。完整原日报/待核对明细仍保留，输出明确为已核验本地小计，缺历史覆盖不冒充全群总数。现有每日调度默认同时输出三栏CSV、缺文件时补齐同一版本；export_csv=false可关闭。不改XLSX、调度安装或现有生产配置。

修复：history coverage完整但持久化07:00归属规则冲突时，原formal_totals可能输出0；现在正式数量为null并列明REPORT_DATE_POLICY_CONFLICT。正常规则/完整覆盖仍输出确认数量，小计与待核对明细保留；正式可用性变化进入原修订差异，不静默覆盖已提交报表。未改语义归并、单价、交付门槛或计量规则；未改数据库结构。

UNIT/MOCK：python -X utf8 -B -m unittest -q tests.test_performance_reports tests.test_performance_rules tests.test_performance_delivery_eligibility tests.test_operator_performance_exclusion tests.test_performance_review tests.test_performance tests.test_performance_report_schedule tests.test_shared_source_delivery_integration tests.test_shared_semantic_consumers：93项通过，无失败/跳过。新增三栏跨日/23:00/06:59/07:00、白天语法5题与夜间听力1篇、重复导出不增版本、已提交重算仅预览、丢失CSV补齐、规则冲突不伪造正式零及修订差异检查。

REAL DATA/LOCAL CLI：只读备份现有受控测试库到data/private/performance-export-20261020/check.sqlite，再运行真实totals/CSV导出。三栏0/0/0，符合测试隔离；原数据库未改、无外发或提交。该结果不是9.17以来正式群历史绩效，正式群历史覆盖/语义核对仍需现有真实记录与交付证据。整体生产结论保持NOT_READY。

## 最新消息受控真实交付成功（2026-10-20）

REAL：仅企业微信苇中鹤。原生poll取得新原话“老师讲讲第二题C错在哪，binge-watching是主旨我就选C了”（复制证据01082a6dd400437b9250affa085fa314），新增观察1；三张新原图经原生预览/另存为保存、可解码及哈希核验。固定收到Outbox 2a991e9ca27a445da17f636405551463于北京时间13:59:24核验送达。界面分钟标签13:50没有完整原日期/秒证据，不填造source_sent_at，不据此宣称正式SLA通过。

新阅读任务5e6fea3f17b64709979121857cdf335e，run782ae73dd30b43ea976f1ad99bff0ff6。冻结ANSWER 1c388a0596b13935ae7c6a1af00834915a8efa33、对应阅读manifest；原图+教学原文+原话在新题独立DeepSeek会话提交一次并实际捕获；原检查器NO_AUTOMATIC_FLAGS、版本/选项证据通过。无教学重试。

原生CLI发现TEST_ANSWER错用原任务身份核验目标的缺口，发送前已停。最小修复：目标仅经现有validate_test_copy取得，仍检查冻结原答案、审核、版本、映射和白名单，不让模型填写目标。相关75项UNIT/MOCK回归通过；此前两次命令误写不存在的测试模块，各报1项导入错误，修正命令后全部通过。命令：python -X utf8 -B -m unittest -q tests.test_wecom_tools tests.test_wecom_cli tests.test_wecom_native tests.test_mcp_test_delivery tests.test_routing。

REAL：答案Test Outbox 40d632e5272e4d52be3415a1a2c46961，原文无测试前缀，14:05:45实际发送并精确复制回读SENT_UI_CONFIRMED。测试库原政策只允许PROGRESS自动，首次返回MANUAL_SEND_REQUIRED且未点击发送；在本轮完整测试授权下，仅现有唯一待发TEST_ANSWER临时允许AUTO，完成后恢复原政策。重复同一send返回resubmitted=false，SEND_STARTED仍1。收到与答案各成功1条，无UNKNOWN/重发。

绩效：OPERATOR_TEST与测试副本仍明确排除正式统计；同一原库两次generate日报版本一致、第二次changed=false，白天综合/语法听力/夜间答题均0。未把测试副本标成学生原群已交付，未静默完成原来源Outbox。详细私有证据：data/private/ego-acceptance-20261020/latest-1350-acceptance.json及latest-1350-daily-report.json。

本条验证范围：人工监督下新消息→原生收到→三图保存→真实DeepSeek→原生实际答案发送→原台账回执与测试排除/日报幂等。未验证正式群持续收题、完整原始时间SLA、该题真实追问、付费成本或20个真实任务；不得据单条成功宣称CORE_FLOW_STABLE，整体NOT_READY。

## 本轮完整联调实际停止点（2026-10-20）

后续用户确认“以印刷题号为准”（call_a99d1be1415140d2ae6347fb4e6cd8e3）。先用既有confirm_input解决原定位人工任务，再通过既有CORRECTION草稿/审核建立同一Question的明确澄清版本，未伪造成学生追问或增加独立题。任务b72a05b75cd94a8e98296215b5e0e517、run11c387b4351147b2907cf3b39fc01287及唯一修订f970d09f03344eb7aee7b4aabc4f5e79均复用原DeepSeek会话。自然请求增加印刷第7空的已核验前后原文，未增加答案、教学规则或JSON。两次真实捕获已回到Coach your child instead of taking over段；仍被原ANSWER检查器的source_position_cue拦截。独立调用原quoted函数确认修订稿识别引文数为0：裸英文未带要求的双引号。

未改变ANSWER、原始捕获、检查输入或旧拒绝状态；未为旧版本重置重试预算。answer-agent的既有revision_prompt只补充明确的中文双引号格式说明，供后续合法修订使用；本题不再自动调用。现有human_tasks登记ANSWER_QUOTE_FORMAT_REQUIRES_REVIEW，答案Outbox=0、正式绩效=0。UNIT/MOCK：python -X utf8 -B -m unittest -q tests.test_ego_revision tests.test_ego_generation tests.test_lesson_checks，28项通过、无跳过/失败；没有以模拟代替本轮交付验收。NOT_READY。

用户要求本轮尝试跑完全部，外发范围仍仅苇中鹤。当前未见新学生消息；继续原“讲七选五和阅读”请求中尚未运行的第7题，不补发历史收到。空间列表实际为空，依据长期授权重建ego空间9；DeepSeek登录正常。ANSWER仍冻结1c388a0596b13935ae7c6a1af00834915a8efa33，本地工作树干净，manifest核验通过。

REAL：原始JPEG2/3与既有第7题草稿经题面定位审核，创建任务423e0fe90c584fa78efbc72c5283d53b。新会话2ecb74c3-bedb-429e-9488-4bb6280d3953成功上传原图+教学原文、语义发送按钮提交“第7题”、捕获完整答案。原run 0c079e933ea34f5d97d647ca937a3761被ANSWER原检查器拒绝；唯一修订run 49455436116f447e94bdbe50a8944892复用原会话、无附件重传，仍错误定位Give specific praise段，拒绝且预算耗尽。两个答案/捕获原文保留，未产生答案Outbox、未外发、performance_units=0。既有human_tasks登记QUESTION_NUMBER_LOCATION_REQUIRES_REVIEW；已询问印刷/手写题号口径，不能继续默认题号无歧义。

结论：FAILED_AT_TEACHING_POSITION_CHECK，完整链路未通过；本轮没有验证新消息SLA、实际答案发送、追问或绩效完成投影。未升级ego（有更新提示，维护延期），未放宽检查或伪造完成。NOT_READY。

## 当前只读联调与核心回归（2026-10-20）

- REAL：用户重新交接前，原生窗口元数据发现MissionPlanner前台、企业微信最小化，未输入。用户恢复苇中鹤后，新帧的原联系人标题哈希完全匹配；应用左栏自身头像外观变化，初次严格校验停止，经查看新帧仅在新限时只读配方重新审核该区域，不修改原配方或发送守卫。
- REAL：原生CLI watch有限2轮、间隔2秒，均NO_NEW_OBSERVATIONS，confirmed_observation_times准确回读用户确认的2026-10-19T23:12:32+08:00；无发送、无新增任务或正式绩效。仅可见历史文字去重，不是新消息、完整历史或15分钟SLA验收。
- UNIT/MOCK：python -X utf8 -B -m unittest -q tests.test_process_restart tests.test_ego_process_restart tests.test_ack_before_generation tests.test_delivery_batches tests.test_delivery_tasks tests.test_question_session_isolation tests.test_performance_rules tests.test_performance_delivery_eligibility tests.test_operator_performance_exclusion tests.test_question_costs tests.test_teaching_bundle tests.test_teaching_routes：93项，92通过、1跳过、0失败，113.649秒。真实Python硬退出/同库恢复，外部服务为模拟。另定向确认symlink测试跳过原因为Filesystem cannot create symbolic links；Windows junction越界检查通过。跳过不算通过，不要求管理员运行。
- REAL DATA：现有测试库成本报告显示3次有记录的DeepSeek教学生成，费用及调度调用账单仍UNVERIFIED；average/P50/P95/max均null，不能宣称满足0.50元/篇。当前完整任务仍1/20，NOT_READY。

## scan自动关联原时间证据（2026-10-20）

后续去重修补：保留原内容基键和历史观察，不破坏性迁移；用已取得的月日时分秒辅助区分相同原文的不同发生时间，重复扫描/重启仍复用原观察。时间证据日期补零归一化，缺年份不补；同钟点但年份一方缺失或候选不唯一时停止，不能擅自合并跨年。观察仍不是平台消息，未改任务/绩效。匿名UNIT/MOCK相关87项通过、0失败/跳过，新增同文两时刻顺序1/1/0/0、重启去重、补零差异、非法日期/年份及不猜跨年；此轮无新真实消息、无桌面动作。该条年份已获用户确认并追加证据，见本文顶部；其他消息年份仍不推断。

将已真实验证的悬停步骤接入现有scan，不增加工具或服务。需本机审核time_hover空白区域/点击点；清除选择后重新定位同一位置唯一气泡，悬停并局部OCR时间，再将后续复制绑定到当前气泡图像。首次实测因时间标签阴影改变上缘3个像素而停止；确认几何未变后，在尚未接受文字前重新核验唯一气泡并固定悬停后的当前哈希，后续复制仍做原内容区域/目标核验，未弱化发送守卫。

REAL：一次scan自动返回原文及10/19 23:12:32、年份null、REQUIRES_YEAR_EVIDENCE及帧/裁剪证据；无需Agent另行悬停。原source_sent_at仍null，不填当前时间、不创建Ack/绩效。用户随后明确确认仅此条为2026-10-19，确认已追加到既有audit；原始时间观察不覆盖。原始观察不可覆盖，poll对后来取得的时间追加独立事件并按观察键+标签去重。

UNIT/MOCK：相关84项通过、0失败/跳过。覆盖缺年份不补、标签缺失/歧义/无效/不一致、悬停后找不到同一气泡即停止、时间OCR不可用仍保留缺失及既有观察补时间不重计。控制台命令为python -X utf8 -B -m unittest -q tests.test_wecom_visible tests.test_wecom_tools tests.test_wecom_cli tests.test_wecom_native tests.test_wecom_control_ocr tests.test_collector_ack_native tests.test_collector_test_ack。未发送消息/调用DeepSeek，完整任务仍1/20，NOT_READY。


## 恢复桌面后的真实自动定位/复制/去重（2026-10-20）

用户明确回复已停止使用，可以继续。原过期会话保留；重新激活主窗口并取得新快照，窗口/完整头部/头像与原审核值精确匹配后，建立本次新限时只读会话scan-resumed-readonly-session.json，未启用发送。

REAL：scan自动定位来信气泡，通过局部Windows OCR定位复制菜单及剪贴板取得原文“老师能讲一下七选五和这篇阅读吗”，未预先指定消息位置或原文哈希。随后真实poll第一次OBSERVATIONS_SAVED/new=1，第二次NO_NEW_OBSERVATIONS/new=0；Outbox ID及状态前后完全一致，performance_units=0。此为单条既有历史文字，不是新消息SLA/持续群入口验收，不增加完整任务数。

新增受限原生Move（不点击/键盘），只用于已审核消息悬停。选中状态下第一次悬停无标签；核验消息区空白后单次点击取消选择，再悬停来信灰色边缘，实际显示10/19 23:12:32。扩展现有本地OCR脚本TimeLabelOnly，仅输出严格匹配的时间标签，不输出学生正文；首次小裁剪匹配为空，扩大标签裁剪并规范空白/全角冒号后读到10 / 19 23 ： 12 ： 32。保留原始OCR及规范形式、帧/裁剪哈希并以WECOM_VISIBLE_TIME_OBSERVATION关联原观察，年份null，source_sent_at仍null，REQUIRES_YEAR_EVIDENCE，不补系统时间或计绩效。悬停时间仍为受控步骤，尚未接入每轮扫描。

UNIT/MOCK：可见扫描/原生/业务CLI/复制OCR/测试Ack回归79项通过、0失败/跳过；新Move验证无点击或键盘及拒绝额外动作参数。未发消息、未调用DeepSeek、未计新完整答疑；NOT_READY。


## 可见来信气泡发现与扫描（2026-10-20）

当前提交b9452e8已实际通过本机MCP 2.2.0的独立stdio进程initialize/list_tools，15个工具加载成功；scan/search对白名单外匿名目标均经真实协议调用返回UNAVAILABLE/CHAT_OUTSIDE_ALLOWLIST。使用临时数据库，无桌面输入。此为工具传输层真实验证，不替代企业微信原文复制验收；桌面使用状态问题仍待回复。

新增helpdesk/wecom_visible.py、scan/wecom_scan_messages，并让明确配置的poll/watch复用扫描。使用本机审核消息视口/左侧位置/颜色的连通区域定位，选中文本覆盖大面积背景时要求四边完整框；最多20候选，超限停止。没有LLM、学生文字OCR、任意点击参数或新消息通道。候选仅在原窗口/区域守卫后复制原文；复制菜单可显式仅用限定区域本地OCR。临时reader复制会话，不修改原消息配方。观察按联系人+原文去重，不冒充平台唯一消息、不补原时间、不创建Ack/绩效。无候选不等于空聊天。

REAL存留像素：实际WeCom图像772476...识别出[306,751,507,782]；蓝色选中文本图fa151...初次被密度条件漏掉，改为四边框核验后同一区域识别。真实CLI第一次无候选（未声称没有消息）；后续复制在首个Clipboard之前发现前台切回ChatGPT，返回NATIVE_FOREGROUND_OR_LAYOUT_CHANGED，未计完整读取通过。已询问用户当前是否使用鼠标键盘，待回复；不自动反复激活重试。搜索在激活后补充一次有限渲染等待，未知控件状态仍停止。

UNIT/MOCK：python -X utf8 -B -m unittest -q tests.test_wecom_visible tests.test_wecom_tools tests.test_wecom_cli tests.test_wecom_native tests.test_collector_ack_native tests.test_collector_test_ack，75项通过、0失败/跳过。新增未预定义气泡/选中/外发和零散像素/限额/不修改原配方及不创建Outbox。首次测试缺显示器fixture导致1失败，补齐测试前提后回归通过，未删关键断言。当前未取得可靠原始时间/平台身份，未通过新消息持续采集、15分钟Ack或完整真实新题闭环；NOT_READY。


## 固定联系人搜索CLI真实验证（2026-10-20）

新增search/wecom_search_chat。复用原生串行桌面、固定会话联系人名及本机审核搜索框/结果项，不接受任意搜索文字；核验原生主窗口和显示器范围、输入点和结果点范围、查询复制回读、精确结果项及最终目标会话。焦点在其他应用时只激活已审核主窗口，再重新核验；不输入Enter、不发送。

REAL：当前苇中鹤的联系人搜索结果被实际检查。首次查询源的整框哈希受闪烁光标影响；标题区在搜索弹层阴影下像素变化，相关尝试均停止。保持正式最终标题校验未弱化；搜索框仅用审核控件区域和正常/聚焦/关闭三种固定外观，不用未知图形兜底。原限时会话过期后保留原件，在当前已授权桌面交接期间重新人工查看标题及结果，建立新的私有只读搜索会话，移除发送/回执控件，不用于无人值守续期。结果关闭状态第一次CLI停止，补充实际审核外观后CLI真实返回OPENED_VERIFIED/exit0；增加自动激活后独立CLI再次成功。

UNIT/MOCK：相关67项通过、0失败/跳过，覆盖固定查询、越界目标、输入点范围、未知布局、错误结果不点击、最终会话核验与激活。此前完整discover进程37112因代码在运行期间继续变化，不作为当前版本验收，已定向中止，不能记PASS。没有据超时重启测试。新增消息自动发现/时间证据/群采集仍未完成，NOT_READY。


## 企业微信真实翻页与原图保存（2026-10-20）

用户明确交接桌面，授权只读取/操作、不发送。重新激活企业微信，当前苇中鹤头部/头像与原审核哈希均一致，建立限时私有history会话。首次history因ChatGPT占据焦点返回WECOM_NOT_FOREGROUND，无输入；随后open_chat核验目标，原生history返回HISTORY_VIEW_CHANGED。动作是真实通过，messages_imported=0，不当作完整历史读取。

原生open_image打开同一消息的第2张照片，保存按钮打开实际另存为。发现文件名ID1001、保存按钮ID1；UIA在后续读取出现Pane类型不稳定，初次脚本编码/选择/回读分别失败，均未点击保存。改为ASCII脚本及固定原生控件ID、有限SendMessageTimeout写入/回读/BM_CLICK一次；实际保存383788字节JPEG，sha256=e25848726f6e89ea541e9cdfcb0526ce7330206eecb2ac54c1798df7e76c4ee3，与原original-2.jpg字节完全一致。随后save-image CLI同一路径完整再验证一次，新增native-d1109d28642943dab432154377d06db7.jpg，哈希一致，不覆盖旧件。

实现tools/wecom_save_image.ps1、WeComTools.save_open_image、CLI save-image、MCP wecom_save_open_image。保存前审核预览/图片/按钮，写入仅原库旁项目私有attachments、新UUID文件；保存后可解码、哈希核验；意图/结果使用原audit，不发送、不计绩效。仅已审核图片配方，未实现任意新气泡自动发现。REAL脚本边界：私有目录外/已有文件分别明确拒绝、未输入。UNIT/MOCK相关62项通过、0失败/跳过；完整discover仍运行，未算通过。真实链路未向任何联系人发送，NOT_READY。


## 新入口实际MCP传输验证（2026-10-20）

使用本机已有MCP 2.2.0解释器，通过ClientSession + stdio_client真实启动tools/wecom_mcp_server.py，initialize/list_tools列出12个工具，含wecom_history_page和wecom_poll_messages。两者使用白名单外匿名目标实际call_tool均返回UNAVAILABLE/CHAT_OUTSIDE_ALLOWLIST，未调用桌面。使用临时SQLite，退出后清理；没有访问正式库或发送。属于真实本机MCP进程/协议验证，不是企业微信业务动作REAL通过。历史翻页/图片保存仍需桌面交接后适配；不据此宣称完成持续收题或上线。NOT_READY。


## 受限历史翻页CLI/MCP（2026-10-20）

新增history/wecom_history_page，使用NativeWeComTransport的一次固定向上滚动；没有Windows-MCP、模型或任意键鼠参数。要求本机会话history区域/哈希/点已审核，点经显示器缩放换算后须落入该区域，操作前后核验现有会话窗口。画面变化不等于消息入库，未变化不等于历史耗尽，不生成Ack或绩效。滚动后不匹配旧配方则停止，没有自动重标定/盲目循环。

UNIT/MOCK：原生/CLI/业务工具/测试Ack相关60项通过、0失败/跳过。新增一次滚动与焦点重验、拒绝方向/次数扩展、缺审核和跨目标停止、区域外滚动点停止、未变化不声称读完历史。REAL未翻页：桌面交接问题尚待用户回复；当前历史配方未建立，图片另存未实现。上一轮为PROGRESS（代码与差异清理），当前也为PROGRESS；未将等待用户当作已运行进程。整体NOT_READY。


## CLI脚本返回码与当前控件实测（2026-10-20）

上一轮为PROGRESS：已修改CLI/MCP及持久化观察去重。本轮继续修复命令返回码：按操作成功状态白名单判断，发送UNKNOWN/SENDING/FAILED/CANCELLED/PENDING和未知状态退出2；watch遇任何非预期状态立即停，不睡眠重试；人工Ctrl+C退出130。image动作返回仍只表示INVOKED_UNVERIFIED，不是图片核验通过。未修改Outbox状态机或新增自动重试。

UNIT/MOCK：原生/业务CLI/测试Ack回归56项通过、0失败/跳过，git diff --check通过。REAL READONLY：2026-10-20原生Win32列出4个子窗口，MSAA child_count=1；UIA仍只有3个顶层节点，无内部消息/发送可用控件。没有激活/输入/发送，没有读取DeepSeek或重放原请求。新消息发现、历史翻页和另存为仍需实际布局适配，尚未完成；不把可见观察轮询算作完整持续入口。NOT_READY。

## 可见消息增量观察 CLI（2026-10-20）

新增poll/watch和MCP wecom_poll_messages，复用原生读取及现有audit事务保存观察原文/时间/证据，跨重启去重；有限轮询，失败或人工中断停止，不启用发送。不新增数据库或生产消息源。明确区分新增观察、无新增观察、读取失败；观察不等于已验证平台新消息，相同内容的不同平台消息无法区分，不自动创建Ack/题目/绩效。

UNIT/MOCK：python -X utf8 -B -m unittest -q tests.test_wecom_tools tests.test_wecom_cli tests.test_wecom_native tests.test_collector_ack_native tests.test_collector_test_ack，52项通过、0失败/跳过。覆盖跨实例去重、原文保存/变化、失败不消费、轮询失败停止及参数限制。REAL未操作桌面/发送，持续新气泡发现、历史翻页、另存附件、原生学生群自动Ack仍未验证/完成。NOT_READY。

## 第6题原图网页练习及发送按钮修正（2026-10-20）

后续REAL：用户明确确认第6题未提交、允许发送一次。接回同一空间8/p2，原附件/提示词/文件哈希及发送控件重新核验，写入独占确认意图后仅点击一次发送，无重传。取得独立会话15922f7e-9d15-4628-9706-0415ca2a10de，实际捕获自然答复；答复扩展多个题号及多个答案字母，原selected_option检查返回EGO_ANSWER_OPTION_REQUIRES_REVIEW，未放行。

修复已取得无效答复却留RUNNING的问题：仅对明确的选项歧义、错题号或追问选择变化，记录EGO_TEACH_CAPTURE_REJECTED而不是有效捕获事件，保留原始答复并沿用Workflow.finish保存REJECTED/人工任务。原校验仍失败、未伪造选项ID，不建立有效发送或材料复用证据；未知网页/来源异常仍抛出，不当完成结果。原capture-only回流后run为REJECTED、答案aaa7a0eba83e440ea7e5fb5c1a6ec236可查看，答案Outbox=0，performance_units=0。捕获后均读取缓存，没有新网页生成，未知调用经原捕获凭据核验，不将其假记免费。用户仅授权一次发送，本轮未自动修订；页面handOff保留可查看。

UNIT/MOCK：ego生成/修订/浏览器/JS守卫28项通过、0失败/跳过，覆盖多题/错题号原文持久化拒绝、原题明确错误原因、重复恢复不重提。此前CLI第一次回流抛异常为真实失败；后续修复后只读回流保存拒绝，不宣称此前已通过。完整真实交付仍未通过，累计1/20，NOT_READY。


用户明确交回ego空间8并要求继续原请求第6题。实际takeOver后在同空间新建p2，保留p1第5题历史；复用原照片、材料/选项及固定ANSWER，原任务858a10e03c97457a88dd80d866cc9b19冻结为独立run 8ab5bdfae4c54ec9b9464d2a226ab289，SOURCE_READY，未补发收到。已备份原库，仍OPERATOR_TEST不计正式绩效。

REAL：上传原图与teaching-input.md、填第6题后原脚本Enter未在15秒取得新URL，停在SUBMISSION_INTENT，调用记UNKNOWN。只读观察首页保留原提示词/附件，未见当前助手消息、发送按钮可用，但不据此断言原请求未提交。未再按Enter/点击/重传；已询问用户核验原提交状态，并handOff同空间供检查。无新答案或外发，未计完整任务。

修复未尝试请求的提交方式：先检查唯一可见、非禁用发送按钮，保存提交意图及SEMANTIC_SEND_BUTTON后只点击一次，保留原UNKNOWN只读恢复约束；不因版本更新重放已有意图。UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_ego_worker tests.test_ego_browser tests.test_ego_generation tests.test_ego_revision`，26项通过、0失败/跳过；其中JS实际13个匿名场景，包括禁用/歧义不提交、未知按钮结果只读恢复。首次Python包装断言误用了Node的另一种输出格式，已改为不依赖固定测试数/输出前缀的失败数断言。新按钮路径本轮仍未真实重提第6题，不能报告网页生成成功；整体NOT_READY，1/20。


## 真实来源测试Ack入口接原生后端（2026-10-20）

补充原生输入边界：激活/每次输入前检测用户按住Shift/Ctrl/Alt/Windows键即停止，不释放用户按键；脚本自己的快捷键释放失败时仍尝试释放其他已注入键，并报告KEY_RELEASE_UNCONFIRMED，避免失败残留Ctrl影响后续动作。UNIT/MOCK：原生、CLI、OCR、测试Ack、来源/发送回归合计93项通过、0失败/跳过，覆盖5类修饰键、释放失败继续清理及keydown失败只清理已注入键。REAL READONLY仅验证Win32修饰键查询可执行，观测当时无按住键；未真实注入按键故障或执行发送，不把Mock算真实接管验收。没有模型/Windows-MCP调用。完整答疑交付仍NOT_READY。


检查发现新WeComTools/CLI并未替换日常AutomaticDeliveryRuntime的MCPGroupDesktop，不能宣称demo已全量使用新封装。现有原生目标限定苇中鹤，不将该会话配置冒充正式群身份。最小接线在已有collector_test_ack_cli增加--backend native，prepare-pin/dispatch/inspect复用PinnedDesktop＋NativeWeComTransport及原CollectorTestAckWorkflow；老默认windows-mcp保留兼容，未新增服务、消息源或台账。

原生模式需固定前台/显示器配方，构建前验证必要显示器配置，输入前再次检查暂停、原Outbox仍待发/发送中/待核验，并调用原flow._validate核验实际collector来源、证据、目标及pin。既有FIXTURE禁止真实传输、UNKNOWN只显式核验、不重复收到和不计正式绩效保护保留。没有全局后台自动启用。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_collector_ack_native tests.test_collector_test_ack tests.test_wecom_tools tests.test_wecom_native tests.test_automatic_delivery_workbench`，46项通过、0失败/跳过。覆盖原生不构建Windows-MCP、每次输入前重验来源、暂停及已发送状态停止、缺少显示器审核不构建传输，以及原工作台回归。CLI --help实际启动通过。REAL未发送：没有新合格提问及完整审核发送配方，本轮未建立测试Ack或调用桌面。原生测试Ack实际发送及日常群接线仍UNVERIFIED；整体NOT_READY，1/20。


## 新截图本地OCR及菜单中断真实恢复（2026-10-20）

REAL：保持原苇中鹤和原消息，新原生截图经源日志/窗口/消息哈希核验，Windows OCR在实际菜单局部唯一识别复制位置，无Luna或Windows-MCP调用；识别结果及当前菜单哈希用于该本机私有配方，CLI随后复制原文一致。原始时间仍待核验，未猜时间或将历史记录重新作为新提问入库。此证明新截图定向OCR识别＋原生读取，不代表任意布局或持续采集通过。

实际暴露旧菜单残留遮挡消息：首次CLI明确MESSAGE_LAYOUT_CHANGED停止，未把失败当空消息。新增_message_frame仅在配置过OCR菜单区域且确认当前菜单后按一次Escape，再核验原消息；不确认菜单或内容仍变则停止。随后受控在原消息右键留菜单，单次新CLI真实恢复成功，审计确认关闭1次，原文回读一致。前后Outbox ID/状态和performance数量比较完全相同，无新发送、无额外网页请求，模型与Windows-MCP调用均0。证据私有保存live-ocr-menu-recovery.json等。

UNIT/MOCK：原生读取/发送/Luna来源/OCR/CLI相关73项通过、0失败/跳过，命令同上一节，新增确认菜单后只关闭一次、未确认菜单不输入、内容仍改变不循环。没有修改第5题拒绝、修订预算或教学规则；真实答疑交付仍未通过，完整任务仍1/20，NOT_READY。


## 本地OCR定向复制按钮兜底（2026-10-20）

复用现有ocr_window.ps1的Windows.Media.Ocr，增加CopyControlOnly，只输出精确复制标签的区域，不输出聊天原文。新增wecom_control_ocr复用已有原生截图日志/单显示器/有效期校验，不调用Luna；只处理本机指定最大512×256区域，局部放大4倍，15秒有界识别，唯一匹配才返回当前显示器坐标建议。位置本身不授权输入。

原get_messages复制菜单在固定哈希不匹配且本机显式配置ocr_search_box时才使用该兜底，仅接受WECOM_NATIVE记录；点击前再次核验当前窗口及菜单区域未变化。收件人、题面、发送按钮、原时间与绩效不使用OCR猜测。不自动修改既有会话配方，没有新服务/依赖/模型调用，也未新增MCP工具或CLI子命令。

REAL OFFLINE：在已保存的实际企业微信复制菜单图上，整屏识别未找到精确标签（明确非通过）；局部90×45放大4倍后，Windows OCR实际唯一识别复制标签，返回[28,67,133,120]。图片只在私有目录，本轮无截图采集、桌面输入或外发。完整运行时兜底尚未在真实新菜单上验证，不能报真实读取闭环成功。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_wecom_control_ocr tests.test_wecom_tools tests.test_luna_navigation tests.test_wecom_native tests.test_wecom_cli tests.test_mcp_test_delivery tests.test_routing tests.test_delivery_tasks`，71项通过、0失败/跳过。覆盖离散/负坐标显示器映射、唯一标签、歧义/缺失/越界、来源不可信时不执行OCR、显式启用及不产生输入；原消息/发送守卫回归保留。首次插入测试时将原选中态测试后半段放错函数，已恢复原断言位置，未删断言。第5题原拒绝与修订预算不变，完整任务仍1/20，NOT_READY。


## 教学原文核验及修订强退恢复（2026-10-20）

在原第5题上只读重新验证固定ANSWER 1c388a0596b13935ae7c6a1af00834915a8efa33，实际本地工作树干净、HEAD一致；冻结原文/依赖通过。实际上传teaching-input.md为77801字节，7份README/Skill/Agent/七选五模块/交付/路由/缺口原文逐字存在，未发现漏传教学模块，未改ANSWER或为消除拒绝放宽检查。私有核验证据teaching-input-audit-after-rejection.json。当前原稿和单次修订仍REJECTED，已询问用户该题转人工修订或保留待处理后继续其他题；没有额外网页提交。

新增修订路径进程强退回归：真实Python子进程在匿名SQLite的EGO_REVISION_PREPARED写入后、事务提交前os._exit，重新打开保持父任务/父run且无子run；事务提交后强退保留唯一子run，重复prepare_revision返回同一ID，无新增网页调用、答案/Outbox或绩效。既有数据库正常回滚/恢复，不清空或重建任务。`python -X utf8 -B -m unittest -q tests.test_ego_revision`，6项通过、0失败/跳过。此为真实进程/SQLite恢复＋Mock网页，不能算真实DeepSeek或企业微信故障恢复验收。补跑原有 `python -X utf8 -B -m unittest -q tests.test_ego_process_restart tests.test_process_restart`，2个场景测试通过、0失败/跳过，106.057秒，覆盖ego准备/提交意图/已提交/已捕获，以及原六阶段强退恢复；该套网页/桌面仍为Mock。生产代码未变化。


## 单题全运行成本视图，保留修订调用（2026-10-20）

检查确认原report已按已确认篇的关联Question汇总全部run，未发现只保留最新稿的成本漏算，故不修改其计量或分摊内核。新增只读question_cost及report_task_costs --question，与--run互斥，用于尚未交付时直接查看同题所有版本/修订/追问运行。保留每run费用证据与未核验原因；观察调用数与阶段覆盖是否完整分列，缺失账单不填0，修订关系不可靠时标记未核验且仍保留该run成本，不隐藏失败调用。没有新增数据库结构或绩效数量。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_question_costs tests.test_call_costs`，22项通过、0失败/跳过。合成账单测试覆盖父稿＋修订费用累加、未定价修订不当免费、伪失联修订费用不丢弃、重复只读CLI及不存在问题阻止；注入账单明确标识，不当真实价格。

REAL READONLY：在原苇中鹤测试库对Question 1f54432c1f8f42d785f091fd19798e1d运行新CLI，原run及单次修订run均出现，DEEPSEEK_TEACH已记录2次，其中修订1次。SEARCH/MATCH为direct-original已明确关闭；SCHEDULER/OTHER未完整记录，不能将观察0当实际0。无费用凭据，总费用null/UNVERIFIED；完整结果保存原私有任务目录question-cost-after-revision.json。未启动网页、LLM或桌面，无外发或正式绩效。整体仍NOT_READY，1/20。


## 原生CLI/MCP历史回复真实回读（2026-10-20）

新增receipt/wecom_verify_reply，复用原MCPTestAnswerDesktop的目标/头像/窗口核验、原生复制菜单及全文Clipboard回读。只接受原Outbox已SENT_UI_CONFIRMED的PROGRESS/TEST_ANSWER，不允许借历史同文气泡将PENDING、SENDING或SEND_UNKNOWN提升为成功。结果明确new_delivery=false、outbox_changed=false、无服务器回执/已读声明，完整证据存私有目录，输出仅摘要/路径/哈希以减少token。

新原生发送在preflight先检查完整发送/回读/复制控件已审核，缺失则在写草稿前停止；记录发送前回读区域基线，发送后未变或重启缺失基线时不确认新发送。原生鼠标/键盘调用后添加有界渲染等待，不增加重复点击，不模拟Windows-MCP Snapshot。当前独立MCP为10工具；默认仍禁输入与发送。

REAL：既有苇中鹤已核验收到8766637dff0146ffae6959930ddf708c，经新CLI receipt及独立MCP stdio wecom_verify_reply实际复制全文核验通过；新MCP列出10工具。原生后端，全程Windows-MCP调用0，无新发送、无Outbox或绩效变更。细节证据私有保存。中途证据摘要改动漏导入encode导致单测/一次只读命令失败，修复导入后回归及真实回读通过，没有伪报此前失败为成功。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_wecom_cli tests.test_wecom_tools tests.test_wecom_native tests.test_mcp_test_delivery tests.test_routing tests.test_delivery_tasks tests.test_luna_navigation`，67项通过、0失败/跳过。覆盖待发/未知发送不得提升、历史回读不写台账、未审核控件发送前停止、旧同文区域不变/丢失基线不确认。新原生发送和新答案实际交付仍未通过真实验收；七选五原题修订仍REJECTED，没有放行，不增加完整任务计数。整体NOT_READY，1/20。


## 单次教学修订及真实原会话回流（2026-10-20）

新增ego_revision，run_ego_answer支持--revise-rejected。仅明确的教学草稿检查拒绝可进入；验证原来源审核、当前Question/Version、固定ANSWER与捕获证据，事务内建立关联子run并更新原task的当前run引用。原REJECTED run/答案不修改；同Session不重传原图/课程，提示词只转交检查器提示和原稿片段，不增加教学来源。修订最多一次，重复准备返回同run，再次拒绝不得继续建修订；仍经原检查/Outbox，草稿不算交付或绩效。重建已丢失ego空间时只允许新修订冻结新浏览器位置，业务会话URL及来源仍必须匹配，审计记录新旧位置。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_ego_revision tests.test_ego_generation tests.test_ego_direct_queue tests.test_operator_tasks tests.test_automatic_preparation tests.test_seven_five_tasks`，73项通过、0失败/跳过。新增验证父稿保留、修订幂等、同版本/Session、不重传文件、不提前交付/计绩效、暂停/更正阻止、预算耗尽及父检查篡改阻止。Mock网页与合成检查提示仅作开发验证。

REAL：空间7确认已不存在，列表为空；按用户长期授权重建8/p1，重新打开原f1f3c746会话，无重发原题。SQLite可恢复备份后建立修订run 0769175b23624e22904b3fedd3aececc，实际网页单次提交修订、捕获自然答复并回流；原run 2e1b7ce666244d35a8fe012bf932a858保持REJECTED。新稿9d6a83d76f9444f2b7da66db985f3977仍被固定ANSWER 1c388a0596b13935ae7c6a1af00834915a8efa33检查拒绝，标记method_visibility/source_position_cue；子run为REJECTED，未创建答案Outbox，未外发或增加绩效。预算已用尽，未继续网页重试、未放宽检查；ego已handOff保留原会话供查看。真实验证的是单次受控修订及拒绝回流，不是答疑交付成功。原课程不改，价格无凭据，不将网页调用虚报0元。整体NOT_READY，累计完整任务仍1/20。


## CLI真实重复读取通过，未重发历史消息（2026-10-20）

沿用原苇中鹤会话和原请求，CLI state/open已真实核验；messages通过原生右键复制回读取得“老师能讲一下七选五和这篇阅读吗”，全程Windows-MCP调用0。原始消息发送时间没有足够证据，输出source_sent_at=null、时间待核验，未用采集时间替代；未重新入库为新提问或重建Ack。

真实发现复制后保留文字选中高亮，使重复读取误报布局变化。保留严格默认布局校验，仅允许本机显式审核的额外选中态哈希，且必须配置原文精确SHA256；实际复制内容仍核对该原文，其他布局继续停止。修复后连续两次独立CLI messages退出0并取得一致原文。open修复原生非前台守卫异常类型未被激活分支捕获的问题；只激活固定主窗口，标题不匹配仍停止。已有已发送收到的prepare命令真实返回ONLY_PENDING_REPLY_CAN_BE_PREPARED，未写草稿、未再发送。

UNIT/MOCK：63项通过、0失败/跳过，沿用上一节命令。REAL通过：CLI状态/已打开会话、当前已审核文本及重复读取、已发送记录不能重复填写。尚未通过：自动发现新消息、原始时间提取、新答案准备/发送、完整真实闭环；原第5题仍因ANSWER检查拒绝，没有可发送答案，不绕过教学检查创建Outbox。无正式绩效变化，累计完整任务仍1/20，整体NOT_READY。


## CLI复用业务封装及真实尝试（2026-10-20）

按用户要求新增 tools/wecom_cli.py，直接复用 WeComTools，提供state/controls/chats/open/messages/image/attachment/prepare/send；无MCP启动依赖，无任意坐标、命令或收件人参数。默认禁输入/发送，失败退出2且不重试。原生激活修复瞬时无法读取前台进程时未进入既有有界激活分支的问题；菜单复制添加固定短暂渲染等待，不重复点击。

UNIT/MOCK：上述57项加CLI4项，共61项通过，0失败/跳过；命令增加 tests.test_wecom_cli。REAL：新独立MCP确实通过当前苇中鹤状态、当前联系人列表、已打开会话核验；无消息操作记录时明确不可用，发送禁用。开始记录原消息与复制菜单的原生操作，尚未完成真实CLI读取；Luna定位后前台变化被原生守卫拦截，之后CLI状态也因画面/目标核验失败返回不可用。实际截图出现ego空间概览，不推定是用户操作或脚本已成功。无Windows-MCP调用、无输入草稿、无外发、无绩效增加。真实读取/填写/发送仍未验证通过，保留失败证据；不声明完整替代成功。CLI controls已真实执行成功，退出0，当前UIA为3节点，12类业务控件仍全部NOT_EXPOSED；节点数量随界面变化，不据此猜测内部按钮。


## 原生企业微信业务 MCP，后台回归完成（2026-10-20）

新增 wecom_tools、wecom_native 和独立 stdio MCP 入口，记录12类流程控件、暴露9个业务工具；默认原生 Win32 后端，不启动 Windows-MCP。复用原桌面互斥锁、暂停、Outbox、版本核验和发送结果；没有新生产消息通道或数据库迁移。Windows-MCP 仅保留显式可选后端，不自动失败重试。默认只读，仅限已审核苇中鹤；真实填写及发送尚未验收。

REAL能力探测：REA源码255d906e15a141b0f4acee60dc73976f03adc94c，包6.7.0，实际 observe-native-ui 返回 unsupported_provider/Native macOS utilities require macOS，不作为可用Windows驱动。企业微信5.0.12.6018实际UIA仅2节点，Win32子控件0、MSAA子节点0，没有编造AutomationId。独立MCP协议曾实际列出9工具并验证默认禁输入/发送。原生激活、经一次Luna定位进入联系人及标题/头像核验曾通过，未调用Windows-MCP；随后用户切换会话，目标核验阻止继续，没有填写或发送。原生MCP完整读取/填写/发送不算真实通过。用户确认仍使用电脑后，仅做后台，不再探测桌面。

UNIT/MOCK：原生输入限制、锁屏/窗口移动/目标变化、暂停、默认不启动Windows-MCP、控件来源、消息不可用、准备草稿不发送/计绩效和既有发送回归，57项通过，0失败/跳过。命令：`python -X utf8 -B -m unittest -q tests.test_wecom_native tests.test_wecom_tools tests.test_luna_navigation tests.test_mcp_test_delivery tests.test_routing tests.test_delivery_tasks`。新增测试曾缺STOP坐标字段、默认后端测试曾错误预期状态；已修正测试夹具，未放宽生产守卫。

能力边界：list仅当前核验联系人；open不自动导航其他聊天；get_messages需已审核可见消息操作记录，不是监听；附件仅已保存原件核验。实际按钮定位记录、跨布局更新、真实填写和发送待用户空闲后验证。没有将模拟通过标为真实成功；整体仍NOT_READY，累计完整任务仍1/20。

原第5题另已按用户“确认未提交”的证据恢复一次原请求，实际取得DeepSeek答复并回流原run；ANSWER草稿检查拒绝（source_position_cue），未创建答案Outbox、未发送或增加绩效。保留原始拒绝证据，未修改ANSWER规则或重发收到。


## 同篇多小题复用原事项，原网页提交仍待核验（2026-10-19）

复用已有Intent.SUBQUESTION和Helpdesk材料归属，OperatorTasks/CLI允许显式已审核parent-task的同篇小题；没有新增表、服务或自动分类。只允许隔离OPERATOR_TEST父任务，材料、题型、附件必须一致，七选五共享A—G不得变化。新小题与原题使用同学生/事项/材料但独立Question、Version与业务Session；相同题号重复审核停止，事务不留下重复消息/Ack/任务。普通追问仍引用对应小题。题面JSON仅为原本本地输入格式，不上传DeepSeek。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_operator_tasks tests.test_seven_five_tasks tests.test_ego_direct_queue`，48项通过、0失败/跳过，10.540秒。覆盖两个已预存相同小题草稿的重复审核、共享材料和独立Session、追问复用正确Session、七选五换共享选项拒绝、幂等请求及绩效0。首次新断言误从尚未生成任务的context读取session_id而失败；改为通过现有Mock生成流程建立真实本地Session后验证隔离/复用，不将Mock结果算真实网页成功。

REAL只读：原ego空间7/p2仍是首页、原题号保留输入、没有提问或答复；三个原图预览已加载，进度图标的祖先opacity=0，未发现可证明上传失败的可见错误。不能仅凭首页和草稿断言原Enter没有提交；未重提、重传、换页或发送。已请求用户核验原页面状态。另在原联系人测试库先备份，再为已保存七选五第6—9空建立SUBQUESTION草稿，两次运行返回相同四个ID；messages/outbox/runs/performance数量保持不变。仅准备草稿，未审核或生成，不能计为新增真实完成任务。累计完整完成仍1/20，整体NOT_READY。

## 七选五单空接通及原请求继续联调（2026-10-19）

在上轮实际失败点上增量适配，不改数据库或ANSWER。Question新增显式seven_five_blank种类，必须完整A—G；原single_choice仍必须A—D。沿用原选项ID、版本比较/一一映射、OperatorTasks审核冻结、ego捕获和Outbox；声明答案可为E/F/G，但不得出现不存在的字母、多个结论或错误小题。原SOURCE/DRAFT检查接ANSWER实际支持的seven-five参数，选定空号必须存在于学生材料；冻结原件与题型检查一致。七选五受检教学包单独缓存，缺依赖仍阻止；其他未接通题型不放行。多题整卷尚未自动分题，本次仅准备原请求的第5空，其余部分仍待处理。

UNIT/MOCK：核心、OperatorTasks、ego生成、ANSWER来源、教学检查、direct queue合计126项通过；新增七选五及文字准备/自动准备26项通过；补跑七选五缺依赖和不支持题型边界1项通过。新增测试覆盖七选项原文、缺G停止、显式题型、换序后的学生G、错版本、错误空号、实际草稿回原Outbox且绩效0。首次新测试错误地预期审核后没有任何Outbox；现有流程正常创建待处理Ack，改为断言没有答案或已发送记录，没有取消Ack或弱化绩效断言。无跳过项。

REAL：固定ANSWER仍为1c388a0596b13935ae7c6a1af00834915a8efa33；新七选五包和原始附件核验、实际ANSWER SOURCE检查通过。在原ego空间7为该题建立独立p2，上传原图及教学原文，按第5题一次取答。首次Enter后15秒未取得新会话URL；原请求停在SUBMISSION_INTENT，原页面只读观察仍为首页、输入框保留题号、无答复。不能据此断言未提交，未再Enter、点击发送、重新上传或建立替代请求；原网页保留待核验，尚无答案或交付，正式绩效0。“收到”沿用上一轮已核验记录，没有重发。当前累计完整完成仍1/20，整体NOT_READY。ego提示有更新，本轮未升级。

## 新真实请求：收到通过，阅读＋七选五闭环未通过（2026-10-19）

用户要求“真实验收”，沿用累计最多20任务、仅企业微信“苇中鹤”、不计正式绩效的授权。本次实际打开该联系人，复制新请求原话，并从原生图片查看器保存附件原文件，未把聊天截图当成题图或统计数据。实际首次应答“收到”仅提交一次，原Outbox为SENT_UI_CONFIRMED，最新气泡原文经Clipboard回读，performance_units=0。此为脚本辅助的真实联系人操作，不代表无人值守持续采集已通过。

新请求同时要求阅读与七选五。实际调用现有教学构建入口，七选五for_generation=True明确返回ANSWER_AUTOMATIC_TASK_SHAPE_NOT_SUPPORTED；现有OperatorTasks/Question生成契约仍限A—D四选一，不能将多题、多材料请求整体套入旧单题成功结论。固定ANSWER阅读包原文/来源核验通过，未删除或绕过七选五分支守卫，未提交新DeepSeek请求、未发送答案、未伪造完成或绩效。下一步需小步接通七选五及同次请求的多个题目归属，再继续本次已有原请求，不能重新发“收到”。

REAL通过：读取新请求、首次应答及真实回读；LOCAL边界验证：七选五任务形状明确阻止，阅读教学包核验通过。完整答疑交付未通过，未执行真实新网页生成；本轮没有把Mock记为真实结果，也没有运行无关回归。私有证据保存在本机本轮测试目录，不进Git。累计完整完成仍1/20，整体NOT_READY。

## 真实联系人Pilot台账的日报重复生成检查（2026-10-19）

遵照“暂未发送，先准备脚本”，未读取新桌面消息、调用模型或外发。在原Pilot SQLite以mode=ro打开后备份到隔离临时库，用现有PerformanceLedger/PerformanceReports连续生成2026-10-19日报两次。REAL仅指使用已保存的真实联系人交付记录：两次payload一致、version均1、第二次changed=false；一个已核验TEST_ANSWER未转成正式绩效，白天综合/语法听力/夜间三个已核验本地小计均0，performance_units仍0。coverage_complete=false，不把这些测试零值当作全群正式日报。私有预览和验证结果保留原Pilot目录，没有提交日报或写原库。

第一次检查的业务断言已过，但临时库清理因sqlite3连接上下文未实际close而报Windows文件占用；在验证脚本使用closing明确关闭源/副本连接后重新完整检查通过，隔离副本已清理，未修改生产代码或删除原数据。本轮无代码修改，未重复模拟回归。真实验收仍1/20；下一条消息尚未提供，整体NOT_READY。

## 下一条普通追问的纯脚本准备（2026-10-19）

用户暂未发送下一条消息，明确要求先准备脚本。本轮没有操作桌面/浏览器、补造消息或外发，累计真实完成仍1/20。原operator_task新增followup子命令：显式parent-task、UTF-8 message-file、稳定request-id，沿用原题面/选项/附件，仅替换本轮原话，复用已有create_draft幂等与过期检查。只支持隔离OPERATOR_TEST准备，不自动分类/核对/冻结/生成或正式入库，不代替生产采集；真实归属/更正判断保持原业务边界。此前私有prepare_followup中手拼payload的动作无需再由Agent重复完成。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_operator_tasks tests.test_ego_direct_queue`，41项通过、0失败/跳过，10.416秒。覆盖原话空白/换行、已确认原题所有字段沿用、相同请求重读、内容冲突、旧父任务停止、空输入拒绝；新增独立CLI子进程读取带BOM中文原话，草稿存在但messages/outbox/runs/performance数量不变。首次预期比较误用未标准化fixture（缺attachments字段）失败，改为与已保存父题原件比较后通过，没有修改业务规则。CLI帮助已验证，README和项目/本机Skill同步。没有运行真实新追问，不把这些测试算真实消息或完成任务。整体NOT_READY。

## 累计20任务授权与原网页会话只读恢复（2026-10-19）

用户已明确允许累计最多20个匿名测试任务，仅向企业微信“苇中鹤”外发，遇异常暂停，不计正式绩效，不向学生群发送。授权保存原Pilot私有配置；已完成的第2题任务计入累计1/20，不因其旧失败run、恢复或多次只读核验重复计任务。本轮已请求用户发送下一条真实追问/新题/更正；尚未取得下一条原始消息，不伪造学生输入或时间。

REAL只读恢复：实际ego空间列表再次为空。按用户长期重建授权建立空间7，直接打开原业务绑定的同一DeepSeek链接；DOM确认唯一输入框存在，原已捕获答案全文与当前页面历史一致。没有新建题目会话、上传、填入prompt或提交请求，原空间6冻结run及原捕获保持不变。后续追问可在新浏览器空间沿用该原网页会话；这不等于追问已完成。ego提示存在更新，本轮未升级，维护不作为当前验收前置。当前仍仅1个联系人Pilot任务完成，正式消息入口/绩效/SLA及20任务稳定性仍NOT_READY。本轮无代码改动，未重复跑模拟回归。

## 明确未提交草稿的现有CLI恢复（2026-10-19）

将真实Pilot中需要私有SQL处理的步骤收敛到原dispatch_test_answer的显式`--resume-draft`。复用原test_answer_queue、Outbox/audit及MCPTestAnswerDesktop，没有新服务、数据库或发送通道。只接受原FAILED TEST_ANSWER及最后FAILED回执明确submission_attempted=false、draft_may_remain=true；原答案/版本/目标重新通过原验证，历史存在UNKNOWN或成功回执一律拒绝，最多3次显式恢复。适配器必须看到已有草稿，跳过Type/粘贴，完整Clipboard回读一致才允许原单次发送；无自动恢复未知发送、无LLM判断或额外模型调用。暂停、窗口/针脚、正文和原有发送守卫不改变。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_mcp_test_delivery tests.test_live_generation tests.test_demo_real_http`，41项通过，0失败/跳过，11.275秒；覆盖已有稿不重贴、错误回读不发送、缺未提交证据、非FAILED状态、历史UNKNOWN、有限次数。首次新增测试错误沿用“空编辑器”模拟状态而被正确拒绝，调整fixture为真实预期的“发送前有稿、发送后为空”后通过，未修改该断言。REAL仅验证已完成Pilot测试Outbox增加新选项仍返回SENT_UI_CONFIRMED/resubmitted=false、不进入桌面；没有再次执行真实失败恢复或外发，不把Mock恢复写成新的真实通过。README及项目/本机Skill参考同步当前命令。整体NOT_READY。

## 已交付任务的独立进程重读与定位用量保留（2026-10-19）

REAL（本机已保存Pilot库，无Mock）：分别用全新Python进程调用现有dispatch_test_answer及run_ego_answer各两次，共4次。均返回原SENT_UI_CONFIRMED/GENERATED及resubmitted=false；前后messages/runs/answers/outbox/delivery_checks/performance_units/audit数量及Outbox状态完全一致，原生MCP attempt和ego round文件列表完全一致。未启动新桌面/浏览器调用、未生成新请求或外发。此项仅证明已完成任务在独立进程下读取结果，不代表实际生成中或发送中的所有重启恢复通过。私有验证记录留在原Pilot目录。

实际费用只读报告：本题一个已确认DEEPSEEK_TEACH调用、零该run重试；SCHEDULER/OTHER与金额仍UNVERIFIED，不把known_subtotal=0解释为总费用0。发现luna_navigation会丢弃CLI的turn.completed.usage，现把可取得的合法input/cached/output token作为call_usage附在原定位返回值，调用方沿用原证据保存，无额外模型调用、账本、价格估算或任意模型字段。缺失、布尔、负数或缓存量超过输入时不伪造用量；历史丢失用量不能追填。该元数据不自动成为已分配到AnswerTask的费用，任务归属与账单仍需真实证据。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_luna_navigation tests.test_call_costs`，33项通过、0失败、0跳过，1.377秒；测试使用匿名CLI事件，不调用真实Luna。覆盖从CLI实际字段保留用量、单次调用、缺失/异常不补零，原导航结构与工具隔离继续通过。整体NOT_READY，持续入口、正式绩效、完整费用及真实稳定性仍未验收。

## 新题真实联系人交付、脚本优先与长期空间恢复授权（2026-10-19）

用户批准重建ego空间后，实际建立空间6；原空间3请求确认PREPARING且在取得TaskSpace前即报不存在，没有上传或提交。备份隔离库后保留原失败run和原输入，在同一题目、同一任务、同一业务轮次冻结新的run；重建授权、新旧空间和旧失败证据进入原audit，没有重建题目或伪造一次成功。固定ANSWER原文与本轮1840×2329原JPEG实际上传，一次自然语言请求使用复制来的疑问。完整答案捕获，结论为第2题C。原“第2题落在C”表述被旧解析器遗漏，现只扩展明确带题号、单一字母及分隔符的结论识别，仍拒绝错题号、多个选择或“C和D之间”；修补后读取同一缓存完成原Workflow，不重提DeepSeek。SOURCE/DRAFT原检查通过，生成一个答案和一个原ANSWER Outbox。

REAL VERIFIED（仅授权联系人测试）：保留完整自然语言原文，无测试前缀、无压缩；通过现有TEST_ANSWER路由向“苇中鹤”实际发送一次。原测试适配器限制单行，本次支持多段正文并改用Clipboard+Ctrl+V；不把换行交给原生Type/SendKeys。编辑器回读发现LF被Windows转换为CRLF，最初在发送点击前正确停止；仅将CRLF/LF编码视为相同，其他文字、空格及段落改变仍拦截。备份后复用该草稿和原Outbox，不重复粘贴，点击发送一次。新本人气泡的完整Clipboard原文与答案一致。临时回执最初遗漏target字段导致UNKNOWN，随后只读检查原生Clipboard原件、当前窗口/会话/空编辑器并补关联目标，原Workflow更新同一测试Outbox为SENT_UI_CONFIRMED，未再次发送。已核验联系人收到应答1次、答案1次；本轮正式绩效0，原学生ANSWER仍PENDING，测试副本不冒充学生正式交付。没有向学生群发送。

更新项目及本机安装的answer-agent-deepseek-web Skill：用户长期允许本项目空间确认丢失时重建，不再重复询问；保留原请求核验、防重复提交和外发范围。默认可确定的采集、复制、上传、等待、捕获和台账操作由脚本执行，不默认开subagent；仅控件无法定位或布局变化时必要调用Luna，教学仍交给DeepSeek和ANSWER。本轮后段发送及回执修复全由脚本完成，没有新增定位模型调用。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_ego_process_restart tests.test_ego_browser tests.test_ego_worker tests.test_ego_generation tests.test_ego_direct_queue tests.test_call_costs tests.test_mcp_test_delivery tests.test_routing tests.test_live_generation tests.test_demo_real_http`，108项通过，0跳过、0失败，25.619秒。较早一次命令误列不存在的test_test_answer_queue模块而报ImportError；更正为实际覆盖queue_test_answer的测试模块后通过，不把该次失败算通过。新增覆盖自然结论、多行粘贴无Type/Enter、回读不符不点击及严格换行转换。Skill两副本逐字一致。

仍NOT_READY：本轮是隔离OPERATOR_TEST联系人Pilot，没有取得可靠完整平台身份/原始时间、持续采集生产者或正式学生交付，不作为15分钟SLA或正式绩效证明。参考搜索未配置，费用未完整核实，真实20任务及A–H稳定性未验收。此前账号验证、空间丢失和控件/回执失败记录保留，不用最终一次成功掩盖。

## 苇中鹤新题真实收到与原图保存（2026-10-19）

用户明确授权仅在“苇中鹤”完成新题收到、DeepSeek和答案交付测试，随后确认已发新题。实际Windows-MCP取得新题原话的Clipboard原件，并通过企业微信原图查看器的另存为保存1840×2329 JPEG；原件摘要、原始工具记录和授权保留私有目录，不上传。界面出现22:15及原图提示时间，但尚无可作为正式消息元数据使用的平台消息ID、完整原始时间或持续生产者，不伪造。

REAL VERIFIED（受控联系人应答，不是正式学生Ack SLA）：第一次准备在发送点击前再次触发账号安全验证，保留FAILED和可能残留草稿，未生成提交意图。用户完成验证后，备份隔离库、核验原FAILED回执确为submission_attempted=false，复用原Outbox和原有“收到”草稿，逐字回读后提交一次。只读复制最新22:22本人气泡，Clipboard全文确为“收到”，原Workflow.inspect_unknown将本轮同一Outbox更新为SENT_UI_CONFIRMED；没有重发旧22:06气泡或本轮气泡。复制菜单因“撤回”过期发生布局变化，首次核验保持UNKNOWN，之后使用当前菜单位置完成，不放宽生产前台/窗口守卫。另一次点击被守卫发现前台为ChatGPT而阻止，未落入其他应用。实际绩效仍0；新题后续答案尚未交付。

使用隔离OPERATOR_TEST题目入口冻结ANSWER 1c388a0596b13935ae7c6a1af00834915a8efa33，绑定本轮原话和JPEG，并通过既有audit关联已核验联系人应答，不把本地测试身份冒充真实平台身份。REAL FAILED（网页准备）：ego报告原TaskSpace 3不存在；实际listTaskSpaces返回空列表。原请求保持PREPARING，尚未上传、填写或提交DeepSeek，没有生成新答案。遵循ego Skill请求重建空间许可，不删除未决占用或换请求编号盲目重提。生产消息配置、原正式数据库和教学规则未改变。本轮无源码改动，未重复运行模拟回归。整体NOT_READY。

## ego 进程硬退出恢复与只读发送观察（2026-10-19）

新增 `tests/test_ego_process_restart.py`，实际启动独立Python子进程，分别在PREPARING、SUBMISSION_INTENT、SUBMITTED、CAPTURED后直接os._exit，未走finally释放锁。父进程用同一证据目录和新的适配器恢复：未决任务不能普通重提或被其他请求占页；准备未完成转人工；提交后只允许capture_only；完整捕获直接读取、不调用网页，核验后释放页面占用。没有改生产逻辑、数据库或ANSWER。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_ego_process_restart tests.test_ego_browser tests.test_ego_worker tests.test_ego_generation tests.test_ego_direct_queue tests.test_call_costs`，64项通过，0失败、0跳过，12.130秒。子进程退出和文件锁是真实操作，ego网页工作器为Mock；不算真实浏览器中断或稳定性试运行通过。

REAL只读观察：用户完成手机安全验证后，当前企业微信“苇中鹤”会话出现一条右侧“收到”，显示22:06。原生窗口句柄改变；当前目标标题区域摘要仍匹配原针脚，但账号头像区域摘要不同，原守卫停止。没有弱化全局检查，没有点击消息、复制、再次输入或发送；未取得原文回读或平台回执。原Outbox仍SEND_UNKNOWN、实际提交意图仍仅1次、绩效0。不能把视觉出现消息替代完整交付核验。私有观察证据保留本机，不进Git。

整体NOT_READY：真实消息入口时延、测试群完整交付、真实绩效、费用及20任务稳定性仍未验收。后续继续原闭环，不增加生产通道或重构核心保护。

## 一次授权“收到”真实发送尝试与账号安全暂停（2026-10-19）

用户明确授权仅向企业微信“苇中鹤”发送一次“收到”，不向学生群发、不计绩效。使用独立测试PROGRESS Outbox和原Workflow发送台账，不伪造学生消息、首次应答SLA或正式Ack完成。最初受控切窗入口因最大化边界不一致停止；随后用既有App切换，切换后独立读目标进程/会话。显示器拓扑在核验期间改变，旧索引0已指向另一屏，Luna返回STOP；未输入。新尝试按当前前台原生窗口中心匹配唯一显示器1，废弃旧位置；未降低原前台、几何或目标守卫。

REAL：Luna medium定位只给控制位置，原窗口/目标像素针脚和新鲜前台探针由程序检查。固定正文输入后经剪贴板逐字回读，持久化唯一提交意图，实际发送点击一次。随后企业微信弹出设备环境异常的手机扫码安全验证，句柄/界面改变被守卫拒绝；未取得实际发送回执，Outbox保持SEND_UNKNOWN，提交次数1，绩效0。已请求用户完成验证，之后仅只读核验原消息，不重发、不绕过验证。不声称消息已发送成功、学生已读或收到SLA通过。临时MCP连接已关闭，无待返回调用。

本轮最小源码改动仅为既有Luna导航允许显式选择企业微信输入框、发送、回执和复制菜单的定位建议；依旧无模型工具执行权限、无自由收件人，调用方必须批准动作范围。`python -X utf8 -B -m unittest -q tests.test_luna_navigation`：13项UNIT/MOCK通过，0失败、0跳过，新增验证受控动作只是坐标建议、越出本次动作范围被拒绝。真实单次提交后的未知停止和安全验证暂停已观察；真实成功交付回流仍UNVERIFIED。整体NOT_READY。

## 待复核草稿页面真实渲染验证（2026-10-19）

复制隔离练习库，启动只绑定127.0.0.1随机端口的现有工作台；未配置采集、真实生成或真实发送适配器，原库和生产服务不操作。在同一ego任务空间的独立临时页面实际加载该工作台，核验唯一REJECTED题目卡：全文与原捕获一致、method_visibility原提示可见、明确不可发送/尚未交付/不计绩效、卡内无操作按钮。全程DOM读取，不截图，不触发业务操作。

初次浏览器比对失败，核对后为本地导出文件的CRLF与页面LF差异，正文长度和语义没有变化；仅在新验证脚本统一换行后逐字比较通过，未修改业务正文、数据库、检查或断言范围。一次临时脚本写入因PowerShell引号解析失败，随后使用UTF-8文件工具写入新脚本执行；未重放网页业务动作。测试页面已关闭，临时服务已结束。REAL VERIFIED范围仅本机副本工作台渲染，不是学生真实消息/发送/绩效验收。无源码修改，未重复UNIT/MOCK；整体仍NOT_READY。

## 工作台可查看未通过核验的生成稿（2026-10-19）

复用原任务卡与原answers/audit记录展示REJECTED草稿全文、原失败原因和教学检查提示，不再把这种任务显示成“材料待准备”。只增加只读投影和页面文字展示，没有批准、重生成、发送或计量入口，不更改原检查结论。文本以textContent展示；不把附加证据字段或本机路径混进复核提示。一般答案列表保留。

验证：`python -X utf8 -B -m unittest -q tests.test_operator_tasks tests.test_source_question_http tests.test_ego_direct_queue`，46项UNIT/MOCK通过，0跳过、0失败；`node --check helpdesk/static/app.js`通过。对真实第32题追问练习库用mode=ro核验同一get_task投影，全文与原捕获一致、method_visibility提示一致、send_eligible=false、total_changes=0。仅真实本机只读投影，不算浏览器渲染通过；没有启动或重启生产服务，页面真实渲染尚未验证。整体NOT_READY。

## 同题真实追问与质量停止（2026-10-19）

在已生成第32题的隔离OPERATOR_TEST库内，先备份原库，再按相同题面和版本创建操作端练习追问“为什么不选B？”。这不是学生真实新消息，没有伪造身份、原始时间、实际交付或Ack。原任务绑定同一会话；冻结引用首次已核验捕获，sent_history为空，未把上一份未发送稿标为学生已收到。

REAL VERIFIED（网页复用与安全停止）：同一DeepSeek链接实际提交这条追问一次，upload_manifest为空，未重传课程/原图，完整新回复成功捕获；业务库只有一个DeepSeek会话。原ANSWER检查标出method_visibility：首句未看到实际课程方法或知识点，状态REVIEW_REQUIRED；原run进入REJECTED并保留答案和证据，未创建该追问Outbox。重复运行返回REJECTED、resubmitted=false，不再提交。实际交付确认和绩效单位仍为零。没有改写ANSWER、覆盖检查标记、重新要求模型生成或冒充自动通过。

本轮只执行真实网页与本机核验，无源码修改，因此未重复跑UNIT/MOCK。这证明普通追问的会话与文件复用和质量门禁行为，不证明追问教学质量已通过，也不证明学生已收到答案。待复核草稿保存在私有练习目录；生产消息、自动发送、真实交付回流、费用和稳定性试运行仍未完成，整体NOT_READY。

## 新一次取答真实草稿闭环通过（2026-10-19）

用户确认图片上传完成后接回原ego空间。真实DOM证明加载控件仍在，但其祖先opacity为0；此前依据控件存在就判定“仍在转圈”的结论证据不足，本次确认是检测误判，不能把等待超时全部归因于网站上传失败。现只阻止实际可见的加载、busy或进度控件；仍检查附件名称、图片加载与明确失败。受控恢复核对原未提交状态、同一页面、附件数量和本机文件摘要，不重传、不刷新，题号仅“第32题”，自然语言一次取答。

REAL VERIFIED（仅历史练习草稿）：固定ANSWER 1c388a0596b13935ae7c6a1af00834915a8efa33的教学原文与原始JPEG被使用，DeepSeek实际提交一次、完整捕获，返回D；原SOURCE检查SOURCE_READY、原DRAFT检查NO_AUTOMATIC_FLAGS。原业务run为GENERATED，answers=1、ANSWER Outbox=1且PENDING；重复执行原CLI返回resubmitted=false，数量不变。交付确认=0、绩效单位=0，全部发送目的仍禁用。原始学生身份和提问时间未取得，不补造。未执行真实新消息、Ack、群发送、追问、日报或试运行，不将这一题算完整Level 3验收。

真实恢复发现调用台账拒绝UNKNOWN后确认捕获，已补原run的ANSWER_EGO_CALL_RECOVERED追加记录；原UNKNOWN不修改、不重复创建调用。只接受原冻结目录下、原调用对应的CAPTURED证据、摘要及空间/页面绑定；成本读取时重新校验证据，变化则保持未验证。原通用finish_call仍拒绝改写结果。该题实际TEACH调用=1，恢复记录=1；费用和其他未计量项仍UNVERIFIED，不能宣称零成本或达到金额门槛。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_ego_browser tests.test_ego_worker tests.test_ego_generation tests.test_ego_direct_queue tests.test_call_costs`，63项通过，0跳过、0失败，包含12个native worker模拟用例。覆盖隐藏加载层、可见进度拦截、受控恢复不再上传、提交后不能重复恢复、UNKNOWN追加确认及证据篡改后成本失效。整体仍NOT_READY；原上传人工等待已解除，继续验收实际追问、交付、绩效、费用与稳定性。

## 已交付图片进入 ego 追问（2026-10-19）

最小补通原实际交付历史中的图片：从已冻结的原 sent_history 取得人工登记记录，只接受数据库旁既有 delivery-attachments 批准目录内、登记大小与SHA256均匹配的PNG/JPEG/WebP/GIF。原有目录被链接替换、路径越界、文件丢失或内容改变、超过20MB、非图片均停止。无新目录配置、数据库字段、教学规则或消息通道；旧MCP历史投影不改变。普通追问上传必要老师图片，复用原教学与学生原图，不把草稿当已交付。冻结、生成及发送前继续复核原交付上下文与文件；捕获后发现变化，保留捕获证据但不创建可发送新稿。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_ego_direct_queue tests.test_ego_generation tests.test_manual_delivery_registration tests.test_saved_teacher_delivery`，70项通过，0跳过、0失败。新增验证批准图片上传、保留实际交付正文、未重传课程/旧稿、同大小内容篡改、冻结后删除、生成中变化与发送前变化。第一次69项执行中1项原HTTP测试连接被重置，其他68项通过；该项单独重跑通过，追加生成中图片变化用例后整组70项再通过，不隐去首次失败。

REAL：未操作网页或生产数据库，原ego页面仍交还用户检查上传；无新网页提交、真实交付或正式绩效。下面“实际图片交付尚未接通”叙述为历史状态；当前仅上述受控范围已在模拟中接通，仍不能宣布真实闭环完成。整体 NOT_READY。

## 最新联调状态（2026-10-19，取代下文同日早期状态）

当前 ANSWER_COMMIT=1c388a0596b13935ae7c6a1af00834915a8efa33。用户已确认 ego 一次取答，原后台队列和普通追问复用已有接线与模拟验证；旧 b04、两阶段和“队列未接入”叙述仅保留历史，不作为当前基线。

获得重建许可后，真实创建一个 ego TaskSpace，确认 DeepSeek 登录有效。选择原始历史图片中清晰的第32题，使用隔离 OPERATOR_TEST 数据库冻结新教学包、本机题面与浏览器绑定；练习题号由操作端指定，不冒充学生原话，不补造身份或原始时间，全部交付目的禁用，不产生正式绩效。旧第34题已采用草稿没有重复提交或修改。

REAL FAILED：原上传检测在60秒后超时，状态保持 PREPARING，尚未填入题号或按 Enter。只读 DOM 证明教学文件名已显示、原图缩略图存在但仍有上传转圈。发现旧检测只在 innerText 中找图片文件名，无法识别仅有 alt 的缩略图。现改为识别文档文件名或同名且已加载的图片，同时拒绝 loading、busy、progressbar 及上传/解析失败状态。修补后真实只读复查仍为未就绪；没有重传、刷新、新建会话或重放。保留原未知调用记录，不将其伪造为成功或零费用；没有生成新草稿、交付或绩效。准备中断仍须核验，不自动解除页面占用。

后续真实恢复：核对原调用已终止、原页面 URL、空输入框、零回复、PREPARING 和原指纹后，在同一页面进行一次明确记录的刷新与原文件重传；原请求、run 和计量记录不另建。仍在60秒后上传超时，原图缩略图已加载但上传转圈未结束，输入框依旧为空、回复为零，没有按 Enter。该次是准备阶段恢复，不能算成功教学或“未重传”。已 handOff 原 TaskSpace 3，等待用户检查图片上传；不再自动刷新或重传，不接管其他页面。私有恢复记录保留，当前没有仍在运行的浏览器脚本。此项是实际网页失败，不算模拟通过或 verified wait。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_ego_worker tests.test_ego_browser tests.test_ego_generation`，19项通过，包含11个JS模拟用例；0失败、0跳过。首次执行仅因测试包装器仍断言旧10个JS用例而失败，实际11个JS用例全过；更新预期数量后通过。新增覆盖图片名称、未加载、缺图、上传转圈和明确失败。未运行全量，无新增真实群发送。当前仍 NOT_READY；真实一次取答、追问、完整交付与绩效、费用及稳定性尚待验收。

## ego-lite 网页入口（2026-10-19）

追问交付上下文补充回归：实际交付已由原 input_fingerprint 纳入，无需新增校验或重构。新增三例证明老师在追问冻结后、网页生成中、生成后发送前登记实际回复时，分别由原队列、生成后当前上下文检查、发送门禁停止；不提交额外请求、不丢弃已捕获证据、不创建失效草稿的 Outbox。图片交付上下文仍明确转人工，不冒充已上传。`python -X utf8 -B -m unittest -q tests.test_ego_direct_queue tests.test_ego_generation tests.test_automatic_preparation`：39项 UNIT/MOCK 通过，0跳过；这些测试没有操作真实账号或数据库，不解除浏览器人工接管状态。

新增 `python -m tools.run_ego_deepseek`，复用已有 TaskSpace、Page 和绑定会话；仅上传显式批准附件。默认上传原图与固定 ANSWER 原文，`--question` 直接写题号/学生原话，自然语言取答，不上传上下文 JSON、不要求 JSON 输出或 BEGIN/END 标记。内部状态、附件摘要和未知提交记录仅在本机保存；重复读取缓存，提交不确定仅允许只读恢复。

REAL：原阅读题同一会话经 ego-lite 实际上传、提交、完整取答；该次发生于简化格式前，使用原两阶段输入。ANSWER 原检查保留一项 REVIEW_REQUIRED，用户明确“直接采用，没有问题”；不伪造检查通过。新自然语言捕获逻辑另经真实页面只读验证（783字符、没有再次提交）。没有重做这道题或发送群消息。固定 ANSWER 来源仍为 b04ebc26d7fa096404111a0bb12f6c77cc8525b9。

UNIT/MOCK：`python -X utf8 -B -m unittest -v tests.test_ego_browser tests.test_ego_worker`，5项 Python 测试通过，其中包含6项 JS 模拟用例，覆盖上传失败、内容/会话变化、未知提交不重发、只读恢复、完成控件及无标记自然语言取答；无跳过、失败。浏览器调用通过不等于完整业务验收。

边界：该入口尚未接入后台自动答疑队列；现有生产通道未切换，未核验实际交付，不增加绩效。整体仍 NOT_READY。新版纯自然语言提交尚未单独做真实生成，避免重复提交用户已接受的问题。



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
| 14. 重启恢复 | 现有 SQLite、Outbox、`reviewed_question_queue`；相关队列/发送/会话恢复测试、`test_process_restart` | 六断点已补独立进程硬退出、同库恢复的匿名Mock；未知网页动作转人工，已保存稿件和分段按原记录恢复。真实六断点试运行仍缺 |
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

### 后续验证：独立进程中断恢复（2026-10-04）

从 `8bf4fcf` 继续，只新增 `tests/test_process_restart.py` 并更新本进度文件，没有修改生产业务逻辑、配置或数据库。每个匿名场景由独立 Python 子进程执行；在搜索中、MATCH 结果持久化后、第二阶段已提交但未捕获时、生成稿持久化后发送前、第一段已核验后、SEND_UNKNOWN 状态调用 `os._exit(86)`。不运行清理、不主动关闭 SQLite，恢复打开原库；不是删除记录或重建任务。

恢复后重复检查原消息、run 和 ACK ID 不变，不增加绩效。搜索和生成中断检查明确的 EXECUTION_UNCERTAIN、原阶段及“不重放”原因；MATCH 原结果与原证据可以重新核验，尚未完成的教学上传转人工。已保存稿件恢复原 GENERATED run、同一待发 Outbox；第一段成功后从第二段继续，只有一个分段计划及一次 ACK。UNKNOWN 重复调度保持停止，模拟外部回执不增加。

首次运行 1 个测试、六个子场景，1 个子场景失败，88.433 秒：新的进程读取生产 ANSWER 配置，与匿名夹具固定版本不同，ANSWER_SOURCE_PIN_MISMATCH 防护正确拦截。修正仅使匿名恢复使用同一份原测试配置，并补具体停止原因断言；没有关闭版本校验、修改真实配置或教学内容。失败尾日志保留于 `artifacts/verification/20261004-process-restart/first-run-tail.log`。

命令 `python -X utf8 -B -m unittest -v tests.test_process_restart`：1 个测试内六个子场景全部通过。更正匿名配置后的首次通过为 99.604 秒；追加 UNKNOWN 后发送函数零调用断言及子进程隐藏启动后的最终通过为 99.564 秒，0 失败、0 跳过。另实际运行五个已有用例：`test_captured_final_answer_recovers_after_restart_without_desktop_or_resubmit`、`test_captured_answer_requires_unchanged_confirmed_complete_evidence`、`test_capture_restart_and_duplicate_registration_keep_one_result`、`test_one_batch_order_restart_ack_priority_and_only_final_completion`、`test_second_part_unknown_survives_restart_and_readonly_reconciliation`，全部通过，94.918 秒，0 失败、0 跳过；最终输出分别保存在同目录 `process-restart-final-v2.log`、`adjacent-final.log`，先前输出保留。没有重跑全量。

上述均为 **MOCK VERIFIED**：进程退出是真实的，但消息、搜索、浏览器、教学脚本和交付回执均为明确匿名模拟，不是实际 DeepSeek/企业微信恢复验收。REAL 只重新核对 ANSWER 仍为干净的 `b04ebc26d7fa096404111a0bb12f6c77cc8525b9`，以及按插件官方脚本复查 Edge 连接组件仍缺失、退出 1。没有新增网页提交、群发送或正式日报；真实 15 分钟 ACK、实际成本、六断点及 A–H/20 个任务试运行仍未验收，结论保持 **NOT_READY**。等待用户恢复 Browser 连接或明确授权临时控制器后，沿用已提交 MATCH，只读原回复，不重新提交。

### 固定操作 Skill 与原会话恢复（2026-10-04）

从 `aa6f597` 继续，本人明确跳过未接通的 Edge 插件，复用原 Windows-MCP。仅一个 Luna medium 操作端，主 Agent 做 CLI 文件、版本、原始记录和数据库检查。没有修改生产逻辑、配置、数据库结构或 ANSWER；教学仍冻结在干净的 `b04ebc26d7fa096404111a0bb12f6c77cc8525b9`。

新增 `skills/answer-agent-deepseek-web/SKILL.md` 与按需读取的 `references/windows-mcp.md`，同步安装到当前本机 skills 目录。短入口只规定当前 run 的固定步骤；优先已有 CLI、程序文字控件、固定快捷键及当前有效位置，不默认取图，不逐步复述或把几十 KB 原树灌入模型。异常定位才读取细节和截图；当前输入、版本及授权防护保留，任一步错误或未知禁止继续 Enter。ANSWER 教学内容没有变更，也没有声称实际 token 或费用已达标。

REAL：正常恢复原 SID，未重新提交 MATCH；从原生最终回复复制和 Clipboard.get 取得完整原文，原 journal/record 校验及六字段校验通过，记录为 `WEB_MATCH_SCHEMA_VERIFIED_ONLY`。学生原图与原文教学包哈希重新检查通过；检索仍未配置，零候选不作为搜索完成。没有可靠原始提问时间，仍为空。

FAILED/UNVERIFIED：TEACH 附件准备未完成。本机 Type 0.8.5 必须提供 loc 或 label，先前无定位参数的调用返回错误；其后的选择器 Enter 已保留为实际操作记录，不能把工具返回冒充附件上传。改用仅允许两批准路径和固定 prompt 的私有复制助手后，Alt+N 在前台变为 ChatGPT 时被守卫拦截；后续输入被锁住，没有粘贴或确认。主 Agent 的只读 CLI 窗口元数据没有找到同进程的标准“打开”选择器，不能把 Downloads Explorer 当成上传成功。640 字 TEACH prompt 尚未 staged 或提交；已有 TEACH 意图不当成实际调用成功，未保存本轮新草稿。

两个操作连接均正常退出，无 pending；主 Agent 另检查退出状态。保留原 Edge 页面、原提交、错误及证据，未继续循环点击或截图。专项原任务没有完成，交付与正式绩效为零。格式校验使用 `python -X utf8 -B D:/Codex/skills/.system/skill-creator/scripts/quick_validate.py D:/Codex/skills/answer-agent-deepseek-web`，通过；安装与仓库源文件逐字节一致，引用的八个已有程序入口存在。私有助手仅做 AST 检查及上述有限真实操作，不称为 Mock 全链路通过。本轮没有重跑业务回归；此前 SDK/符号链接 SKIPPED 保留。当前核心结论仍为 **NOT_READY**。

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

## 旧网页流程减负（2026-10-19）

新 ego-lite 请求禁止启用旧 BEGIN/END 协议；保留旧请求指纹、历史结果读取及已提交请求只读恢复，避免因清理兼容代码引起重复提交。README 和 Windows-MCP 参考已将旧两阶段准备/上下文上传标为旧通道专用，正常 ego 任务不叠加执行。未拆除仍有调用的旧生产队列，也未声称自动队列已经迁移。防重发、题目版本、ANSWER、交付和绩效检查保持原实现，无数据库迁移或真实外发。

验证：`python -X utf8 -B -m unittest -v tests.test_ego_browser tests.test_ego_worker`，7项 Python 测试通过（包含6项 JS 模拟用例），新增验证旧协议不能新建请求、历史缓存仍可零浏览器读取；无失败、跳过。本轮未做真实网页提交。

## ego 页面串行与未知请求隔离（2026-10-19）

队列接入检查发现：旧入口仅锁 request-id，多个结果目录中的任务可以并发占用同一 TaskSpace/Page。现以用户本地目录中按 space/page 命名的跨进程锁串行网页调用，并保存当前请求状态文件的位置；未知结果持续占用页面，新题被阻断。仅原请求允许只读恢复，完整捕获并验证摘要后释放。缓存可直接读取，读取其他缓存不会释放当前请求的占用。进程中断后占用信息仍保留，不自动清除或重提；准备阶段中断仍须人工核验，暂未提供自动解除入口。

`python -X utf8 -B -m unittest -v tests.test_ego_browser tests.test_ego_worker`：9项 Python 测试通过，包含6项 JS 模拟用例；新增真实本地资源锁竞争、跨结果目录未知任务隔离测试。无失败、跳过。本轮未进行真实网页或群发送验证。后台队列适配仍未完成，整体 NOT_READY。

## ego 教学生成回流原 Workflow（2026-10-19）

新增 `EgoDeepSeekGenerator` 和 `python -m tools.run_ego_answer`，对已冻结的 EGO_LITE_DEEPSEEK run 复用原题目、版本、会话绑定和第一阶段核验；校验固定 ANSWER，原图/教学原文直接上传，学生原话作为请求。明确的自然语言答案字母映射回学生版 option ID，不解题、不由模型输出机器 JSON。题号不符、多个答案或没有明确答案停止待核对。调用写入原 RunMeter，原始回复摘要和绑定写入既有 audit；Workflow.finish 及实际交付前都核验该证据。完整稿进入原 answers/Outbox，未交付不增加绩效。重复回流原 run 不重复创建答案或提交。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_ego_generation tests.test_ego_browser tests.test_ego_worker tests.test_workflow tests.test_call_costs tests.test_delivery_batches tests.test_mcp_test_delivery tests.test_manual_send_policy tests.test_question_session_isolation`，96项 Python 测试通过（其中包含6项 JS 模拟用例），无跳过。新增 SQLite+模拟网页用例验证原 Outbox 回流、重复回流、中途更正、暂停、版本变化、未核验题面、捕获篡改在生成及交付校验被拦截、UNKNOWN 不算草稿。

回归初次发现旧批量交付测试失败：固定的2026-10-03回执时间早于当前 ACK，测试按排序后的最后回执误选“收到”。已按交付计划的 transport_id 唯一定位原片段，保留原交付顺序断言；未修改生产交付排序。修正后上述96项通过。

REAL：本轮未调用 DeepSeek、未操作生产数据库、未真实外发。真实 ANSWER 及第一阶段证据在新适配器上的端到端验证尚缺；测试中这些依赖明确 Mock。金额、时效没有新增真实证明。工作台任务冻结、第一阶段网页执行、普通追问复用及后台默认入口尚未切换到 ego；不能改写旧冻结 run 的 adapter 来绕过。整体 NOT_READY。

## 显式冻结 ego 任务与浏览器绑定（2026-10-19）

OperatorTasks.freeze 增加 ego_space/ego_page；SourceQuestionTasks 继承原冻结方法，保留原学生身份、审核、版本和台账。现有本地 operator_task freeze CLI 可选择 ego，不额外新增入口或配置文件；默认旧通道未变。冻结将 ego 空间、页面、结果目录记录到原 run；重启复用同一 run，后端或浏览器绑定变更直接拒绝。旧 run 不重写身份。生成及恢复前核对当前配置与冻结绑定，避免执行端把正确题目送往别的页面。冻结不打开浏览器、不新建会话、不发消息、不计绩效。

`python -X utf8 -B -m unittest -q tests.test_operator_tasks tests.test_ego_generation tests.test_ego_browser tests.test_ego_worker tests.test_source_question_tasks tests.test_automatic_preparation tests.test_reviewed_question_queue`：80项 Python 测试通过（包含6项 JS 模拟用例），无跳过、失败。新增覆盖 ego 冻结重启、拒绝旧后端改名、换空间/页面/目录前停止。教学内容和网页依赖仍按各 fixture 标注为模拟，不代表真实 ANSWER/DeepSeek 验收。

边界：工作台自动排队和第一阶段网页核验尚未切换；旧自动准备仍只接受原 MCP 身份，避免错误通道执行 ego 任务。本轮未真实外发、未修改生产库。最新用户“直接上传说明题号”与旧验收“两阶段结构化核验”存在流程冲突，已请求用户选定网页口径；在答案到达前不擅自实施冲突的第一阶段协议。整体 NOT_READY。

## 用户确认一次取答，接入原后台队列（2026-10-19）

最新明确选择：“直接上传原图和教学原文，题号/原话一次取答；保留本机版本检查”。该选择覆盖旧验收中两次网页调用和第一阶段结构化输出的冲突部分，历史规则保留，不伪造旧阶段完成。

新 ego run 固定 DIRECT_ORIGINAL。原清晰度审核/学生身份/时间/版本/题目和教学文件核验继续执行；取消新任务对旧 MATCH 回执和上下文 JSON 的依赖，ANSWER 原检查仍保留。现有 admission/queue/runtime 支持 backend=ego_lite，直接进入 GENERATION，不再执行旧 SESSION_CREATION/PREPARATION 三次排队。后端选择保存在原 admission audit，等待 ACK、暂停及重启都沿用，不能换后端绕过冻结。没有数据库迁移。

首次请求可从已管理 ego 页面空白首页开始，一次上传/提交后捕获真实会话 URL 并绑定原任务；未知 Enter 不回主页重新发；已有记录的真实 URL 可只读恢复，首次 Enter 未记录 URL 时要求人工核验身份，不猜测当前页面归属。原图与教学原文按明确允许文件上传，网页只发送学生原话和自然语言正文。新会话拒绝残留回复。已 CAPTURED 而尚未回流的 run 可从本地原结果恢复，UNKNOWN 不自动重放。原调用台账记录实际一次 TEACH；SEARCH/MATCH 按已确认直接模式标记未调用，不冒充实际检索或核验调用。

匿名源库+真实 SQLite/业务校验+Mock ego已验证：审核→排队→一次生成→真实会话绑定→原 Outbox；等待 ACK 后按原后端恢复；缺少审核被拦截；提交未知停止；捕获后中断可复用原调用恢复，不再次提交。Native worker另增模拟新会话、未知首次 Enter 只读恢复和非空首页停止案例。真实上游 ANSWER/DeepSeek 仍未在新直接模式下联合验收；本轮不操作生产库、不外发。

旧“网页口径待选择”“ego 队列尚未接线”是历史状态，本节取代；当前仍缺真实新直接模式联调、普通追问材料复用和完整 Level 3 稳定性/成本/交付验收，整体 NOT_READY。文件导入状态检查尚未修改，未将其称为持续监听。

本轮回归：旧 reviewed_queue / automatic_answer_runtime / automatic_preparation / operator_tasks / ego_generation 共84项通过（541.333秒，包含原通道有界等待）；直接模式与采集工作台、原 Workflow、调用台账的相关回归另129项通过。用例集合有重叠，不累计宣传测试数。Native worker现为10项 JS 模拟用例；原图场景验证只上传教学原文与原图，原话逐字保留，包括首尾空白；另验证同一网页 URL 不可绑定另一个独立题目。新流程的真实网页与生产队列未验证，未把此前旧协议网页成功算作新流程通过。

## ego 普通追问材料复用（2026-10-19）

原 Workflow.start 在首次冻结追问时保存 ego_material_reuse：同一学生绑定、Case、Question、Version、会话、学生题面及冻结教学一致才可复用。链接直接指向首次成功上传的原 run、捕获摘要和选项 ID；连续追问不形成链。不新增表、不重写旧 snapshot。冻结、生成及交付校验都检查原捕获文件和绑定；旧任务当前已过期不影响其静态上传证据，但这不使旧稿重新获得发送或绩效资格。

普通追问不重传相同教学文件或原图；新增且已经审核的本轮附件仍按真实哈希上传。没有新图时只发送学生本轮原话和必要的实际交付正文，不重复题干，不把未发草稿写成已交付。自然语言解释没有重述答案字母时，仅在普通追问的相同版本下沿用原已核验选项；明确改选、出现其他题号或老师实际交付选择与原生成冲突时转人工复核。原 ANSWER 检查继续执行，不因复用而放行质量标记。

当前实际历史附件（例如老师交付的图片）还不能送入完整追问上下文，显式停止转人工，不能只忽略附件继续。新学生原题图片与同题旧图片复用已通过匿名用例；本条未宣称老师交付图片流程已接通。

UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_ego_direct_queue tests.test_ego_generation tests.test_ego_browser tests.test_ego_worker tests.test_operator_tasks tests.test_source_question_tasks tests.test_reviewed_question_queue tests.test_automatic_preparation tests.test_workflow tests.test_call_costs tests.test_manual_delivery_registration tests.test_saved_teacher_delivery`，169项 Python 测试通过（含10项 JS 模拟用例），无失败、跳过。新增覆盖连续追问同会话无重复上传、只补实际老师正文、未交付草稿不冒充历史、原捕获冻结前/后变动阻断、无需重复答案字母、改选冲突以及实际图片交付不能静默省略。修复冻结前 tuple/list 序列化差异，使用规范 JSON 对比，保留真实字段与 ID 全部校验。

REAL：本轮未调用真实 DeepSeek、未外发、未修改生产数据库；实际成本与时效没有新增证明。真实直接模式队列、完整实际交付与绩效、重启稳定性仍需验收，整体 NOT_READY。此前“普通追问材料复用尚未实施”由本节取代，真实验证仍未完成。

## 新验收教学版本与浏览器真实预检（2026-10-19）

按最终验收要求重新核对 ANSWER：`git ls-remote --symref origin HEAD` 证明默认分支 main、远端 HEAD 为 `1c388a0596b13935ae7c6a1af00834915a8efa33`。旧本机 root 的 b04 工作树干净；仅 fetch 更新远端引用，不切换其工作树。为新验收建立独立 detached worktree，HEAD 为 1c388a0、工作树干净，固定 config/teaching-source.toml 的 commit 到该版本；教学原文不作本地改写。

新阅读、完形受检教学包均真实生成并通过原文/依赖/缓存核验，missing_dependencies=[]，原校验脚本仍是 ANSWER 的 gaokao-grammar-fill/scripts/check_lesson.py。复用已批准原题的原始文本，只执行新包原脚本 SOURCE 兼容性检查，退出0；不生成新答案、不改真实消息时间、不把此检查当作真实网页验收。旧 b04 snapshot、原始捕获和数据库均保留；当前全局版本保护会拒绝旧 manifest 继续生成，须明确冻结新任务，不改旧 run 身份或版本。

REAL 浏览器预检：原 ego TaskSpace 1 返回“task space not found”，同一项目名称的空间不存在，listTaskSpaces 返回空列表。没有新建替代空间、没有提交请求；按 ego-browser 运行指南已请求用户授权重建一个空间。该条件不算真实网页通过。等待确认期间继续本机来源准备，未声称运行中的浏览器任务仍在等待。

只读检查旧练习数据库：quick_check=ok，原 run 仍 RUNNING，answers=0、performance_units=0、delivery_checks=0、实际已核验交付=0。原 ego 捕获仍为 DRAFT_SAVED_REVIEW_REQUIRED，用户“直接采用，没有问题”记录为人工采用，不改原检查标记；修正已有私有 checkpoint 中“尚未教学”的过期阶段，防止依据旧说明重放请求。未改旧捕获或业务数据库。

验证：`python -X utf8 -B -m unittest -q tests.test_answer_teaching tests.test_teaching_bundle tests.test_teaching_routes tests.test_lesson_checks`，57项执行，56通过、1 SKIPPED、0失败。单独复核 `tests.test_teaching_bundle.TeachingBundleTests.test_symbolic_link_dependency_is_rejected`，确因“Filesystem cannot create symbolic links”跳过，不算通过，不要求管理员运行或关闭系统安全。其余原路径和junction防护测试保留。新 SOURCE 兼容性检查是本机原脚本真实执行；教学/缓存回归仍以各 fixture 范围为准，不替代新版本 DeepSeek 教学质量验收。

当前 ANSWER_COMMIT=1c388a0596b13935ae7c6a1af00834915a8efa33。浏览器重建确认尚未收到；新直接模式的真实队列、实际发送与绩效、成本和稳定性仍未验收，整体 NOT_READY。

## 自动收到：信号唤醒基础（2026-10-20）

用户要求收到新消息才启动处理，避免持续切群抢焦点。新增 CollectorSupervisor 的可选 SIGNAL 模式和本地固定 collector_wake 动作；默认 POLL 不变，不修改运行配置、业务数据库或教学流程。无信号不读取源，多信号合并，执行期间信号保留，分页继续处理，停止可立即解除等待。新信号不携带原消息或接收目标，不绕过 ACK/Outbox 来源核验。

边界：未接入可用的企业微信通知监听，也未实现用户活动检测。当前客户端 UIA 只能看到顶层窗口与少量 Pane，尚不能据此读取会话新消息；现有 native_clipboard_archive 仍是文件增量导入。状态显式 notification_listener_connected=false，不把等待信号当作已连接聊天。未启动真实自动 ACK，没有群内外发。唤醒提示不持久化，重启后需再次唤醒，原消息/游标继续由原数据库保存；不得将本功能配置为可靠无人值守监听的替代品。

MOCK：tests.test_collector_supervisor 共8项通过，含3项新增信号等待/停止、执行期间合并唤醒、分页测试；相关回归（collector_supervisor、collector_watch、delivery_tasks、automatic_delivery_workbench、collector_ack_native）共39项通过，0失败、0跳过。真实通知/桌面自动收到未验证，未因本次测试宣称上线。

## 三个目标群的收到范围控制（2026-10-20）

用户将本轮目标限定为搜索中发现的三个群：27S💡English【答疑2群】、27S🌟English答疑1群、28E🍊English答疑3群；只发送收到，不发答疑。它们是已观察到的名称，不是已经验证的平台 room_id 或发送配方。

新增可选 collector.ack_room_names 精确名称限制，同时用于 ACK 入队与 AutomaticDeliveryRuntime 发送前的原来源复核。名称仅缩小范围，不授予群或学生身份；未配置保留原行为，空列表拒绝全部。其他群、大小写/空白变体、缺群名均不得通过已配置的范围。不改原时间、来源和接收目标校验。没有迁移数据库，没有放开苇中鹤测试发送工具，也没有将 PROGRESS/TEST_ANSWER 冒作正式群 ACK。

REAL：本机只读 UIA 复核，企业微信5.0.12.6018、HWND1771226仅暴露2个顶层节点（窗口和标题栏），无消息、群头、输入框或发送按钮。当前原生业务发送仍限定苇中鹤；已存在的群发送适配依赖本机未暴露的控件。三个群尚未连接实时入库和真实发送，未启动监听或群自动收到，未外发。

MOCK：python -X utf8 -B -m unittest -q tests.test_collector_watch tests.test_collector_supervisor tests.test_delivery_tasks tests.test_automatic_delivery_workbench tests.test_collector_ack_native：41项通过、0失败、0跳过；新增三群范围、其他群拦截、发送前撤销范围、空/非法范围测试。不能将这些测试计为真实群接通。

## 开发/执行分离与原生群 ACK 适配（2026-10-20）

最新分工：主 Agent 继续开发与修复，GPT-6 Luna low 负责日常执行。此前 Luna 单次发送因工具固定为苇中鹤而停止，没有操作窗口或外发。上一目标轮仅确认分工、未修改程序，属于无进展；本轮已实际新增群 ACK 适配并接入现有自动发送生命周期。

新增 helpdesk/native_group_ack.py：仅接受真实 collector 来源校验通过、固定正文收到、数据库绑定与本机群/学生白名单一致的 ACK；复用 MCPGroupDesktop 的类型保护、Outbox、互斥锁和发送前 guard，不冒作测试联系人，也不以 PROGRESS/TEST_ANSWER 绕过。NativeWeComTransport 按当前前台窗口、视觉 pin、发送按钮状态及草稿回读执行。先保存发送意图，再执行一次发送；不确定保留 UNKNOWN，禁止重放。回执要求新增唯一右侧气泡、旧气泡序列保持、OCR 仅定位复制菜单、复制原文相符且编辑框为空。读取 UNKNOWN 只允许回读动作，不允许粘贴、快捷键或点击发送。

automatic_delivery_runtime.py 增加显式 NATIVE_ACK 后端选择；delivery_tasks.py 按适配器能力仅挑选 ACK，不使等待中的答案失效。状态增加后端和允许目的；NATIVE_ACK 的真实通过数只取该方式的实际回执，其他历史回执不能证明新适配已验证。现有 WINDOWS_MCP 路径、collector 来源/原时间要求、ANSWER、版本、绩效及原测试联系人限制均保留。

测试命令：python -X utf8 -B -m unittest -q tests.test_native_group_ack tests.test_mcp_group_delivery tests.test_automatic_delivery_workbench tests.test_delivery_tasks tests.test_collector_watch tests.test_collector_supervisor tests.test_collector_ack_native。69项通过，0失败、0跳过。13项新增测试为匿名 SQLite、脚本化传输和合成图像 MOCK：绑定/正文/来源、配置边界、失效 pin、草稿、一次提交超时、UNKNOWN 只读回查、像素身份变化、精确新增回执、缺原消息阻止输入、后端验证状态隔离、答案队列不变。最初发现 NotSubmitted 不接受新错误码，已改用现有 PRE_SUBMISSION_CHECK_FAILED 并回归通过。

REAL：本轮没有操作桌面、没有发送、没有修改真实业务数据库或教学文件。此前 Windows Notification Listener 权限 Allowed，实际新群消息出现后仍返回企业微信通知空列表；因此不能将该接口用作已验证收题触发。本轮没有引入通知服务或启动后台群回复。

仍待完成：三个群的真实视觉配置；自动切群/当前会话定位；可可靠定位原消息、学生与原始时间的实时读取；人工输入检测；Luna 真实发送及回读验收。原生适配器目前只处理已经打开且核验一致的目标群，不把以上缺项伪装成后台监听。未达到15分钟 ACK SLA、20任务稳定试运行、完整费用/恢复等最终门槛，整体 NOT_READY。最终目标保持不变。

## Luna 固定命令与后台捕获预检（2026-10-20）

原生群 ACK 已接入原 tools/wecom_cli.py：ack-status 只读查看，ack-tick 处理下一条合格 ACK。调用原 AutomaticDeliveryRuntime，不经过测试联系人 WeComTools，不接受收件人、正文或坐标。tick 仍须显式输入/发送开关、NATIVE_ACK 配置、匹配业务库的 ACK_ONLY 采集配置和可信原消息。status 使用 SQLite mode=ro，不创建新库或迁移旧库；仅输出最多20条未完成 ACK 的状态/目标，不输出原题或长答案。其他群的 ACK 与答案跳过而不改成 STALE；同群复用原生传输时，操作 guard 重新绑定当前 ACK，不残留第一条任务。

UNIT/MOCK：python -X utf8 -B -m unittest -q tests.test_wecom_cli tests.test_native_group_ack tests.test_mcp_group_delivery tests.test_automatic_delivery_workbench tests.test_delivery_tasks tests.test_collector_watch tests.test_collector_supervisor tests.test_collector_ack_native：89项通过，0失败、0跳过。早先87项运行曾发生一次本地 HTTP ConnectionAbortedError（WinError10053）；随后该安全用例单独重跑通过，最终89项全回归通过，未删除断言或以重试改写测试。原因尚未确定，不把早先失败抹掉。另修正了新增测试中 sqlite3 上下文提交后未关闭连接造成的临时目录清理失败；测试显式关闭后通过。

REAL：GPT-6 Luna low 执行只读预检，先核对脚本无输入/激活副作用，再运行 tools/probe_wecom.ps1 的默认 PrintWindow 模式（没有 CaptureVisible）。2026-10-20 16:41 北京时间，WXWork HWND1771226 不在前台且未最小化时，输出可读界面，含目标群红色未读角标1。私有证据 data/private/wecom-controls/luna-background-probe.png；本轮未发送、未修改真实业务数据库。一次额外前台查询碰到 PowerShell 只读变量 pid，未造成输入或状态修改；随后的独立只读查询确认前台进程非 WXWork。

该证据仅证明一次后台可读，未证明最小化时可读或画面及时刷新；不能把截图或角标代替原消息、身份和原始时间。后续可据此实现轻量未读提示唤醒，仍须单独验证刷新/陈旧判断及真实读取入口。三个群尚未开启自动收到；整体 NOT_READY，完整最终验收目标不变。

## 未读提示接入原采集唤醒（2026-10-20）

上一目标轮完成13f0d71提交和Luna后台捕获预检，属于实际进展。本轮新增 helpdesk/unread_wakeup.py，并接入 CollectorSupervisor 生命周期：SIGNAL模式下默认等待，仅按审核ROI检查后台未读提示，不切换或恢复窗口、不调用模型、不执行发送。初次未读、角标变化或持续未读限频重查可触发同一个wake_event，提示合并；采集停止同时停止提示线程。错误显示UNAVAILABLE/unread_hint=null，不当作无消息，freshness_verified、message_source_verified、can_send始终false。文件导入仍是文件导入，所有原消息/时间/收件人/Outbox保护保留。默认配置不开启。

复用 tools/probe_wecom.ps1，新增 MainWindowOnly：限定当前WXWork进程、唯一标题为企业微信的主窗口，拒绝最小化；输出后台捕获方式、几何等元数据。模块只调用固定脚本，不接受命令或正文，子进程有超时，不使用CaptureVisible。PNG每次覆盖当前运行私有目录的一张current.png，不生成逐步审计文件。工作台增加等待/角标状态，不改教学或绩效。

真实预检由GPT-6 Luna low执行。首次严格命令在解析阶段失败（修改脚本时UTF-8 BOM被去掉），没有运行捕获；修复BOM后，同一powershell.exe仅语法解析返回SYNTAX_OK，再由Luna重测成功。失败保留，不计通过。修复后主窗口HWND1771226、未最小化、2564x1384，后台PrintWindow可读；确认左侧消息图标红角标ROI为[35,152,57,174]。一次UnreadWakeMonitor.sample_once使用仅写内存列表的回调，返回HINT_CAPTURED、unread_hint=true、wake_count=1；没有接Collector/HTTP/业务数据库/发送器，没有启动持续循环，没有输入或外发。私有核验配置 data/private/wecom-controls/unread-wakeup-reviewed.json 保持enabled=false，图片 luna-unread-profile.png、luna-unread-roi.png，以及 private/unread-wakeup/ae96547ea7fd4f69b64f28d17070c2cf/current.png 均存在。

回归命令：python -X utf8 -B -m unittest -q tests.test_unread_wakeup tests.test_wecom_cli tests.test_native_group_ack tests.test_mcp_group_delivery tests.test_automatic_delivery_workbench tests.test_delivery_tasks tests.test_collector_watch tests.test_collector_supervisor tests.test_collector_ack_native：102项通过，0失败、0跳过。其中Windows PowerShell Parser测试真实验证静态脚本语法，不执行桌面；其他相关新增覆盖为合成图像、脚本化进程与匿名SQLite：角标变化/限频/延迟信号不丢、持续提示重查、失败恢复、停止、错误/尺寸/来源拒绝、无源配置不误启动、提示唤醒一次同步及联动停止。真实采样与MOCK分开，未据此宣称15分钟SLA通过。

仍待验证：角标对新消息/清零的及时刷新及陈旧判断、最小化环境；三群实时原消息/学生/原始时间读取与群定位配置；Luna真实ACK发送与回读。没有更改真实业务数据库，三个群未开启自动收到，整体NOT_READY，最终完整目标继续保留。

补充检查：tests.test_ack_only_workbench 与 tests.test_current_demo_launcher 共13项通过，0失败、0跳过；node --check helpdesk/static/app.js 和 git diff --check 通过。界面状态调整仅做静态语法/现有HTTP回归，未声称真实浏览器画面验收。


2026-10-20: Main developer retains implementation/integration; GPT-6 Luna low owns daily desktop execution. Added explicit REVIEWED_GROUP_READ_ONLY session to existing WeComTools/CLI session configuration: reviewed=true, reviewer, allowed_group_names containing display_name, and matching unexpired pin are required. Group mode rejects allow_send and direct prepare_reply/send_reply/verify_reply before desktop effects. Existing contact sending remains separate. Group observations retain unknown platform identity, no ACK eligibility, and do not inherit test-only timestamp confirmations. No new service or database schema.

Luna real read-only UIA diagnostic with IncludeOwnedHelpers still returned only two top-level controls; no useful chat controls. This is negative evidence, not a working message reader. Optional probe remains diagnostic only. Regression command: python -X utf8 -B -m unittest -q tests.test_unread_wakeup tests.test_collector_supervisor tests.test_collector_ack_native tests.test_wecom_tools tests.test_wecom_cli tests.test_native_group_ack tests.test_mcp_group_delivery: 113 passed, no failures/skips. Includes group allowlist type checks, direct send/prepare/receipt rejection before desktop effects, changed pin rejection, observation deduplication without creating messages/ACK/performance, and isolation from contact test-only timestamp confirmation. Unit/mock results do not prove a working live reader. Windows PowerShell Parser separately returned SYNTAX_OK for probe_wecom_controls.ps1 without executing desktop actions. No production DB changes or sends this round. Three-group automatic ACK remains NOT_READY pending trusted live ingress and real delivery verification.

Luna real group preflight: fixed MainWindowOnly PrintWindow captured the current English group in private/wecom-controls/luna-group-read-preflight.png and luna-group-session-current.png; main HWND1771226, rendered image2564x1384. Existing private session recipes were all bound to the test contact, not the group. Foreground process was ChatGPT; Luna did not activate WeCom or calibrate a new foreground recipe from an incompatible background image. No state/messages CLI invocation, new group session, diagnostic database, clipboard action, or send occurred. Live group reading remains UNVERIFIED; background capture alone is not a verified message source.

2026-10-20 continuation: fixed the actual navigation subprocess from gpt-6-luna/medium to gpt-6-luna/low per the user's daily-execution choice; CLI argument and recorded usage share EFFORT so metadata cannot silently diverge. Existing tool isolation and model restrictions remain. No cost reduction is claimed without measured billing. Luna's real group state validation revealed another defect: Windows GBK redirected stdout could raise UnicodeEncodeError on an emoji group name after successful verification. wecom_cli.main now explicitly uses UTF-8 stdout when reconfiguration is available. The encoding regression verifies a GBK-initialized stream returns UTF-8 JSON and calls the operation exactly once. Command: python -X utf8 -B -m unittest -q tests.test_wecom_cli tests.test_luna_navigation tests.test_call_costs: 53 passed, zero failures/skips, anonymous fixtures; no real model request or real send in these tests. Broader regression completed: python -X utf8 -B -m unittest -q tests.test_automatic_answer_runtime tests.test_luna_navigation tests.test_call_costs tests.test_wecom_tools tests.test_wecom_cli: 125 passed in 569.312s, zero failures/skips (UNIT/MOCK only). This suite started before the new UTF-8 test was added; the later 53-test targeted run includes that encoding regression. No real webpage generation or group send is proved by these tests.

REAL VERIFIED, subsequent Luna low group reading: one controlled activation of previously verified HWND1771226, native foreground Screenshot2560x1440 over display bounds[0,0,2560,1440], actual window geometry[2564,1384,1771226]. New private group-27s-english-readonly-session.json and isolated diagnostics/group-readonly-diagnostic.sqlite are under data/private/wecom-controls. CLI state returned VERIFIED_CURRENT_CHAT, editor_blank=true, permanent_platform_identity=false. One CLI messages read successfully copied a student question from the current group using the native Clipboard backend; source_sent_at/platform_message_id remained null, continuous_listener=false, complete_history=false. Evidence: data/private/wecom-native/5c1a2b08844647c7a403d39f76a82b86.json. Main independently read this result metadata (Clipboard/WECOM_NATIVE/is_error=false) and opened diagnostic SQLite read-only: messages0/outbox0/performance_units0/audit5. No formal source ingestion, ACK or performance completion is claimed. Initial wrong window geometry, GBK stdout failure, and first selected-region hash failure are retained as failures; fresh real geometry, UTF-8 output fix and one clearing click with newly reviewed hash resolved the subsequent reads, without relaxing guards. Group source identity/full original timestamp and continuous collection remain UNVERIFIED; no message was sent.

Real timestamp follow-up: one time_hover attempt returned copied text again, but source_time_evidence.reason=TIME_LABEL_NOT_UNIQUE_OR_UNAVAILABLE; source_sent_at=null and formal_statistics_eligible=false. The visible 16:12 separator is not accepted as this message's full original time. Result: data/private/wecom-controls/diagnostics/time-hover-attempt-result.txt; raw native frame c10227ad3e4f45909ba58f6baede196c.png and time crop time-39387c25b85d57c30c48df0d5b13607f471ad597e9f9deb628097f38016a09af.png remain private evidence. Timestamp acquisition FAILED, no fabricated date/year and no additional retries. Next actual source work is to inspect this failed hover evidence and obtain verifiable original-time and sender-identity facts before promoting any group observation into ACK eligibility. Overall NOT_READY; the full Level 3 goal and 20-task real acceptance requirement remain outstanding.

2026-10-20 timestamp diagnosis correction: c10227... was a subsequent copy-menu frame, not the timestamp hover frame. Luna low traced Move5182d5... to actual Screenshot a5c17bdf973a4519a3332e8aa6e1d7fa. That genuine frame contains a month/day+second timestamp. The old broad crop included sender text on the same OCR line. Offline crop[496,994,600,1022] passed the existing strict PowerShell TimeLabelOnly parser, returning one label with year=null; this is old-image calibration, not LIVE or verified full original time. No parser relaxation or current-year substitution.

Minimal fix in wecom_tools._hover_time: optional locally reviewed time_hover.label_box, bound through message_box exactly matching this recipe's region_box. Only a screen-contained, <=512x128 region near the same bubble's upper edge is accepted; invalid or different-message configurations stop before any input. Default crop remains compatible. Failed OCR now returns the actual hover source_record and crop, preventing confusion with later menu frames. README updated. Regression command: python -X utf8 -B -m unittest -q tests.test_wecom_tools tests.test_wecom_control_ocr tests.test_wecom_cli: 67 passed, zero failures/skips. Tests use anonymous images/SQLite/mocked OCR; actual old-image OCR is separately verified.

Subsequent real test: initial state returned WECOM_WINDOW_CHANGED. Luna independently confirmed the same group and HWND with fresh probes; actual geometry changed from[2564,1384,1771226] at(-2,-2) to[2560,1380,1771226] at(0,0). Fresh pin and message/blank hashes were re-reviewed, not blindly renewed. One CLI messages attempt copied text again but did not display a timestamp tooltip anywhere in its actual hover frame; configured crop was blank. Main inspected private diagnostics/time-hover-fresh-attempt-result.txt: status=OK for text, source_sent_at=null, time_status=REQUIRES_ORIGINAL_TIME_EVIDENCE, reason=TIME_LABEL_NOT_UNIQUE_OR_UNAVAILABLE, formal_statistics_eligible=false, actual source_record a461686308b849ea80cb448c94788aaa.json, label_box[496,994,600,1022]. The crop configuration and failure-evidence fix are verified; reliable live tooltip acquisition is FAILED/UNVERIFIED, not fixed by the offline success. No further retry, send or production database write. Next diagnosis must distinguish actual hover display conditions before attempting another read; no current date/year or separator-time substitution is permitted. Overall NOT_READY.

2026-10-20 continuation / latest user decision: classify directly by names. The business grouping policy is now GROUP_AND_DISPLAY_NAME (group name + student display name), not waiting for stable platform account IDs. Existing WeComTools copies accept a reviewed adjacent sender_name region and return original names plus explicit local name-group/name-student keys. Cross-group identical names remain separate. Known same-group ambiguous names can be held via ambiguous_sender_names; renamed students are not silently merged. poll observation dedup includes group/student name so different students' identical text is not collapsed. Names are business classification, never fabricated platform IDs or automatic performance completion. No new database/table/service.

Relevant actual integration fixes: scan now preserves the unique reviewed sender recipe for the same bubble; missing copy-menu calibration returns INCOMING_COPY_MENU_NOT_REVIEWED before desktop input instead of generic KeyError. Anonymous regression command: python -X utf8 -B -m unittest -q tests.test_wecom_tools tests.test_wecom_cli tests.test_wecom_native tests.test_wecom_control_ocr: 84 passed, zero failures/skips. Covers name scope/ambiguity, raw-name preservation, distinct student observations, adjacent-label verification, scanning propagation, missing/ambiguous recipes, and existing send/time guards.

REAL VERIFIED name classification: Luna low created private named-records-only.session.json with discovery and time-hover disabled for this one reviewed text, rechecked fresh WXWork geometry/frame and all target/name hashes, refreshed the pin from new evidence and immediately ran one CLI poll. Result diagnostics/named-records-only-poll-result.json is OBSERVATIONS_SAVED, new_observation_count=1, name_classification=CLASSIFIED_BY_NAME. Main independently verified the actual stored WECOM_VISIBLE_OBSERVATION in data/private/wecom-controls/diagnostics/group-readonly-diagnostic.sqlite.transport.db: one observation, audit4, messages/outbox/performance_units all0. The original diagnostics/group-readonly-diagnostic.sqlite has audit10 and no named observation; it is not the successful poll database. Private diagnostics remain separate from production. Earlier generic configuration failure and pin-expiry failure were retained; neither caused a send. Raw source_sent_at remains null, automatic_ack_eligible=false. One-off script console summary had GBK emoji failure after the CLI successfully saved UTF-8 JSON; not a failed/duplicated poll.

Timestamp alternatives exhausted for this sample, not claimed solved: interior copy-point hover [490,1045] still gave no tooltip (source record86b17e502489444ead246e70179df908). Standard Clipboard HTML was read only after same-lock Unicode text hash matched this already authorized message; the 230-byte CF_HTML payload passed offset/UTF-8/fragment checks but contained only the message text, no sender or date. Private-format contents were not read. Main verified raw payload SHA256 dd44577537d4879305ca4f566c4657569201f6e6167cbb86f69eb7f68d06a842. No further profile/ID probing after the user's name-classification instruction. Identity classification is now usable by name; original time, live incremental collection, real ACK and full Level 3 acceptance remain NOT_READY.

2026-10-20 continuation: source admission review confirms ACK enqueue and pre-send validation both currently require a verified original timestamp; the existing native observations do not qualify. Full timestamp alone would not prove a message is newly arrived, and merely reading an old message or matching its name must not enqueue ACK. No admission guard was removed and no read timestamp was substituted. Reliable live-source/new-arrival evidence remains a real integration gap, separate from performance's original-time requirement.

Implemented bounded pointer evidence in the existing NativeWeComTransport: GetCursorPos before/after ImageGrab is saved as pointer_before/pointer_after in the existing capture JSON, no extra files/services or input gestures. Native _hover_time requires both actual points to match the reviewed request; missing/moved points stop before further OCR/clipboard copying. This observes the two capture instants only, not continuous inactivity. Tests: python -X utf8 -B -m unittest -q tests.test_native_group_ack tests.test_wecom_native tests.test_wecom_tools tests.test_wecom_cli tests.test_wecom_control_ocr tests.test_mcp_group_delivery: 117 passed, zero failures/skips, anonymous mocks/SQLite and local images. Covers pointer query failure, negative coordinates, captured movement, fail-before-copy, plus existing names/delivery guards.

REAL VERIFIED telemetry only: Luna low read HKCU MouseHoverTime=400 without modifying it, checked the unique existing WXWork/current approved group and performed one authorized activation when initially idle. Native Screenshot add32781f81a40c68e4fc27544b71051.json has backend=WECOM_NATIVE/is_error=false, actual pointer_before=pointer_after=[1660,493], independently confirmed by main from the saved JSON. A later idle/held-input check stopped the desktop test; idle was reported as18.6s and no held keys, which is not proof of current human typing. No messages/hover attempt followed, no session or diagnostic DB mutation, no send. The new pointer guard's real stop behavior during hover is UNVERIFIED; timestamp acquisition and automatic ACK remain NOT_READY. No other subagent is currently running desktop work. Next critical source work is a real, persistent new-message boundary, not another promotion of the same historical sample.

2026-10-20 continuation: optional persistent visible-text boundary implemented in existing WeComTools/settings, no schema migration or new source/service. First view is history, unchanged reads survive process restart, unique suffix/prefix continuation yields candidate indices only. Changed scope, ambiguous/repeated text, backwards clock, excessive observation gap and lost/truncated continuity latch review until explicit CLI baseline reset (existing audit event). Default disabled; names required. Raw observations continue to persist separately. No native observation is promoted to LIVE, no original time is synthesized, automatic_ack_eligible/new_message_verified remain false. This does not establish chat-bottom position, source completeness, teacher filtering or a verified new-message producer; real positive arrival and 15-minute ACK remain outstanding.
Tests: python -X utf8 -B -m unittest -q tests.test_wecom_visible tests.test_wecom_tools tests.test_wecom_cli tests.test_native_group_ack tests.test_wecom_native tests.test_wecom_control_ocr tests.test_mcp_group_delivery: 132 passed, zero failures/skips (UNIT/MOCK). Includes durable restart boundary, candidate-only append, explicit history reset, review latching, scope/clock/gap/repetition/corruption guards, config rejection before input, CLI watch stopping, and unchanged formal message/Outbox/performance counts. Real boundary test delegated to existing Luna low; not yet claimed verified. Overall NOT_READY.

REAL boundary attempt outcome: Luna low confirmed the same approved group and historical question using fresh native Screenshot a9cabae25d7746b8b0b639a0ff3084ec (backend WECOM_NATIVE, is_error=false; main independently inspected image/metadata). One authorized window activation occurred. Current-message region could not be uniquely matched to the reviewed recipe, so no session refresh or baseline/poll CLI execution followed. Real boundary persistence remains UNVERIFIED, not a pass; positive append path is MOCK ONLY. Existing diagnostic DB was backed up to group-readonly-diagnostic.sqlite.transport.pre-arrival-20261020T101947Z.backup.db; main independently checked backup integrity=ok. Main read-only verification of actual group-readonly-diagnostic.sqlite.transport.db after attempt: messages0/outbox0/performance_units0/audit4, visible boundary settings0. No send, production writes or fabricated arrival. Next source diagnosis is the concrete region-match failure, not another expiry retry or promotion of historical messages.

2026-10-20 subsequent source diagnosis: the failed one-off calibration asserted boxes.count(old_recipe.region_box)==1, not a production hash or identity guard failure. Main independently ran incoming_bubbles against the genuine a9caba... capture and obtained [407,1023,663,1065], versus old [405,1025,661,1067]. The old reviewed sender hash matches exactly after dx=2/dy=-2; old body pixels were selected and the new body unselected. Production layout/content guards were not relaxed. Added an anonymous regression confirming small translation returns current candidate coordinates rather than a fictitious old box. Relevant command (same seven modules as previous entry): 133 passed, zero failures/skips, UNIT/MOCK.

REAL VERIFIED boundary mechanics: Luna low stored a bounded private calibration script (not a production startup entry), re-reviewed name at the actual translated position, bound exact clipboard text_sha256 and both post-copy visual state and current reviewed session, then ran baseline and poll as separate CLI processes. Result diagnostics/arrival-validation-cfee2d32a6744b789c13662d4fecb317.json: baseline exit0/NO_NEW_OBSERVATIONS/BASELINE_ESTABLISHED/candidates0; poll exit0/NO_NEW_OBSERVATIONS/VIEW_UNCHANGED/candidates0; new_message_verified=false and automatic_ack_eligible=false both. Main independently read result JSON and actual group-readonly-diagnostic.sqlite.transport.db: persisted boundary version1/review_required=false; messages0/outbox0/performance_units0/performance_events0/audit7. Unique pre-run backup quick_check=ok. No send, no source-time substitution, no production writes. The temporary menu ROI edit after verification was restored to the exact original tested values; no extra CLI retry.

Evidence limit: main and Luna independently inspected fresh c6f47e126ed24c18873a75a41c8da888.png and saw the correct group/student/problem, but this visual title inspection occurred AFTER the CLI run; the one-off script refreshed header/avatar hashes without an independent pre-input title assertion. Thus the real result proves baseline/restart/dedup mechanics in that observed group, not unattended safe target recalibration. Do not reuse that private calibration script as an automatic source. Normal WeComTools requires already-reviewed fresh pins and keeps exact target/content checks. Positive new-arrival path is still MOCK ONLY; native continuous source, original full time, ACK SLA, complete authorized real-task batch and full goal remain NOT_READY. Next meaningful source work is safe repeatable target/read coverage and actual new-arrival evidence, not promoting this historical baseline to LIVE.

2026-10-20 continuation: added explicit CLI renew for an existing NATIVE REVIEWED_GROUP_READ_ONLY session with allow_send=false, no input/send flags. It temporarily proposes only a ten-minute expiry, runs existing fresh get_chat_state with all original native process/window/geometry/header/avatar checks, then atomically persists only that expiry; it never rehashes, relocates, activates, copies, changes arrival state or renews sending authority. Failed verification and concurrent user-file edits preserve the existing file. This replaces repeated manual pin expiry edits for an unchanged read target, not the missing continuous producer. README documents the one-command use. Existing send/source/version/time guards unchanged.
UNIT/MOCK: python -X utf8 -B -m unittest -q tests.test_wecom_cli tests.test_wecom_tools tests.test_wecom_native tests.test_wecom_control_ocr tests.test_native_group_ack tests.test_mcp_group_delivery tests.test_wecom_visible: 138 passed, zero failures/skips. New tests cover expired input renewal, unchanged templates, wrong target, actual anonymous frame-contract mismatch, no input operations, rejected send/input scope, invalid expiry, atomic persistence and concurrent-edit preservation. Real WeComTools-contract fixture records only PowerShell/Screenshot/PowerShell; no clipboard/click.
REAL VERIFIED unchanged-target renewal: Luna low ran renew exactly once, exit0/READ_ONLY_SESSION_RENEWED, no activation (WXWork already foreground), no input or send. Evidence private diagnostics/renew-readonly-session-655e8eb0d39746b2984e5a4e6fcdeea8.json; actual native Screenshot243d08437348452d84409eb938eb964c. Main independently compared saved before/after session JSON and raw prior hash: only pin.expires_at changed; both old visual hashes match the actual fresh screenshot exactly, backend=WECOM_NATIVE/is_error=false. Main checked unique diagnostic SQLite backup quick_check=ok and live DB messages0/outbox0/performance_units0/performance_events0/audit8 (one verified state audit appended). No model image interpretation was needed for this renewal. The real prior pin was still valid; expired-pin branch is UNIT/MOCK VERIFIED only, not a separate real expired recovery. No production writes or sends. Original full time, positive new-arrival evidence and continuous native intake remain unverified; automatic ACK for three groups and full goal remain NOT_READY.

2026-10-20 continuation: native scan can explicitly opt into match_sender_templates. It reuses exact reviewed adjacent name hashes from this group's message recipes at each current bubble, with only +/-2px vertical line-baseline rounding; no name guessing, preceding-sender inheritance, fuzzy score, platform ID or auto-send admission. Conflicting/unreviewed templates stop; missing/blank/unmatched headers remain unclassified. Name observations now retain actual frame-attempt/box/hash evidence. New reviewed copy_menu_offset_box scopes Copy OCR relative to each bubble, within existing 512x256 limits. Default fixed recipes remain compatible. No new service, source, database or sending policy.
Offline actual-image diagnosis: c6f... incoming lane yielded three historical bubbles and all three exact original name signatures matched ([407,909,498,931], [407,997,498,1019], [407,1084,498,1106]); offline artifact diagnostics/name-template-offline-validation.json. This is saved-image validation, not a real new arrival. Initial real scan returned COPY_OCR_NOT_UNIQUE_OR_UNAVAILABLE; genuine b953... menu showed vertical screen-edge positioning. Reviewed corrected offset[-50,-220,160,30] produced exactly one Copy label in offline Windows OCR. No parser relaxation.
Further genuine batch bug: in the later real scan, third message was selected in the initial ec745... frame, then unselected in af38... after earlier messages were copied; initial batch recipe became stale. Added opt-in clear_selection: validate a reviewed small empty chat region/hash/point above editor, click once, then obtain fresh frame and construct all recipes. This normalizes UI selection BEFORE capturing the batch, rather than allowing changed content or skipping hashes. Paused/input guards and original sender/body checks remain. Anonymous tests verify changed/unreviewed blanks never click, capture happens after clearing, exact name matching/ambiguity/unknowns and relative-menu bounds. Command: python -X utf8 -B -m unittest -q tests.test_wecom_visible tests.test_wecom_tools tests.test_wecom_cli tests.test_wecom_native tests.test_wecom_control_ocr tests.test_native_group_ack tests.test_mcp_group_delivery: 146 passed, zero failures/skips (UNIT/MOCK).
REAL limits: one intermediate read logged VISIBLE_RECORDS_READ count3 (audit13), but its private wrapper crashed before saving raw CLI output; do not claim its exit code or full classified JSON. Other wrapper defects (duplicate reason argument; missing same-transport foreground probe before Escape; undefined summary variable) were diagnosed from code/native journals and fixed only in the private harness. A Shortcut attempt journal is not proof of successful Escape. Raw child stdout/exit now save before summaries. Subsequent preserved scan-cli-dda58047...stdout.json/exit.json proves exit2/COPY_OCR_NOT_UNIQUE_OR_UNAVAILABLE; no batch completion claimed. After the selection fix, latest attempt 6c24316e... stopped BEFORE scan during renewal; main independently confirmed audit16 reason WECOM_WINDOW_CHANGED and old header/avatar hashes no longer match Screenshot62cb22f800fb49acb9874d433b24e058. No blind rehash/retry followed. Real clear-selection batch verification remains UNVERIFIED; a freshly reviewed current window is required. Main independently verified diagnostic messages/outbox/performance_units/performance_events remain0, audit16. No messages sent, no production writes, no new task/performance admission, original full time still missing. Overall NOT_READY; full real positive arrival/ACK SLA and complete 20-task acceptance remain outstanding.

2026-10-20 continuation: renew now preserves the existing verifier's safe uppercase failure code (e.g. WECOM_WINDOW_CHANGED), while arbitrary exception text is still withheld. This removes an audit-only diagnostic detour; failed renewal still preserves the file. Tests: python -X utf8 -B -m unittest -q tests.test_wecom_cli tests.test_wecom_tools tests.test_wecom_native tests.test_wecom_control_ocr tests.test_wecom_visible tests.test_native_group_ack tests.test_mcp_group_delivery: 147 passed, zero failures/skips, UNIT/MOCK. Includes safe failure propagation without arbitrary text exposure, plus existing input/send/version/source guards.
REAL VERIFIED current three-text batch: Luna low independently reviewed current Native frame6847acf36b1847688d9d449e97c221d6 before input, confirmed actual target title/student/history, and created a separate private pin for actual geometry[2564,1384,1771226], native display image2560x1440. Old session/known sender hash unchanged. First scan-cli-8101... returned NO_VISIBLE_TEXT_CANDIDATES/count0 despite exit0: a new already-present teacher reply had moved the student rows outside the old small diagnostic viewport. Main inspected actual after-frame61fd... and did not mislabel that result as an empty chat or successful batch read. Only the reviewed visible incoming-text viewport was expanded to[380,90,1500,1180], within the existing pixel bound; outgoing teacher text/images/history outside the lane remain excluded.
Subsequent scan-cli-926fa022257f4f44a9a492c0dc06b87b.stdout.json + .exit.json prove exit0/statusOK/messages3, all CLASSIFIED_BY_NAME for the same reviewed display name, native Copy readback exact, source_sent_at=null and platform_identity_verified=false. Main independently opened each actual native Clipboard record and each name-evidence frame: all three text results and exact region hashes agree. Thus name-template reuse, normalized selection and scoped Copy OCR passed this actual current three-text batch. This does not prove a new arrival, attachment capture, all-student coverage or continuous source admission.
REAL VERIFIED durable full-view observations: after a unique checked SQLite backup, baseline and poll ran in separate processes against the same diagnostic .sqlite.transport.db with the same wide-view session. Raw baseline-cli-2c1f573c8853432aab4e64a231601400.stdout.json/exit.json: exit0, OBSERVATIONS_SAVED, new_observation_count3, BASELINE_ESTABLISHED/candidates0. Raw poll-cli-2c1f573c8853432aab4e64a231601400.stdout.json/exit.json: exit0, NO_NEW_OBSERVATIONS/count0, VIEW_UNCHANGED/candidates0. Both new_message_verified/automatic_ack_eligible=false. Main independently read persisted settings: three fingerprints, review_required=false; backup quick_check=ok. Diagnostic messages/outbox/performance_units/performance_events remain0, audit27. Total observation audit events4 includes the earlier fixed-recipe observation; it is not a count of four platform messages. No teacher reply was sent by these read-only commands, no task/performance was created, no production DB was changed. Original full time/year, genuine positive new-arrival producer and ACK15min SLA remain UNVERIFIED; overall NOT_READY and full real task count remains1/20.

2026-10-20 name-binding continuation: Helpdesk.bind_names now reuses the exact existing classify_names group/student keys in the existing bindings table. This gives downstream code a persistent local-name binding entry point without platform account IDs; no production source is connected by this method. Same names in different groups and renamed display names remain separate; known same-group ambiguity rejects before writes. Default verification remains false; repeated registration never silently upgrades an existing unverified binding. No Outbox, schema migration, source admission, send authorization or production DB write. UNIT/MOCK: python -X utf8 -B -m unittest -q tests.test_name_bindings tests.test_wecom_tools tests.test_source_question_tasks: 79 passed, zero failures/skips. New temporary-file tests cover restart reuse, exact keys shared with real native classifications, cross-group/rename separation, ambiguity rejection and no send creation. REAL: latest history-panel exploration stopped before any hover/click because foreground QQEX and input idle15ms showed actual user activity; full-date filter remains UNVERIFIED. Full-time evidence/new-arrival source/three-group ACK SLA remain incomplete; NOT_READY.

2026-10-20 existing GUI adapter name mode: GUIMessageSource accepts optional group_display_name/ambiguous_names and requires the configured room key to equal classify_names for that group. Exact observed group label must agree. Local student keys reuse Helpdesk.bind_names; provider items remain unchanged, raw copied evidence explicitly records name_classification/platform_identity_verified=false. Existing foreground/unlocked/provenance/continuity/header identity/time/attachment checks remain. Known ambiguity yields low trust; missing full time remains pending_time_verification through NormalizedMessage. Default platform-ID mode unchanged. This is an adapter entry point, not a running source: native transport observations are NOT relabeled official_windows_mcp, no actual producer configured, no production DB or send. UNIT/MOCK: python -X utf8 -B -m unittest -q tests.test_gui_name_source tests.test_collector_integration tests.test_name_bindings tests.test_source_question_tasks: 57 passed, zero failures/skips. Initial new test expected generic pending_review but actual existing NormalizedMessage correctly specializes missing time to pending_time_verification; assertion corrected, implementation time guard unchanged. Tests include same-text two-student separation, shared binding keys, ambiguous name held, missing evidence held, wrong group/config rejected and no mutation of original provider item. REAL native intake/date evidence/ACK SLA and complete acceptance remain UNVERIFIED/NOT_READY.

2026-10-20 name-mode integration verification: added anonymous temporary-SQLite integration through GUIMessageSource -> CollectorStore.persist_batch -> CollectorDispatcher -> existing business binding/Outbox. Explicit reviewed name binding plus verified source header/full original time creates exactly one PENDING fixed ACK and one original collector task. Recreated adapter and changed observation capture evidence replay the same logical message without duplicate task/ACK; original22:58 retained, performance_units remains0. Separate negative cases confirm known teacher role and missing original time produce ACK_HELD with existing precise reasons and no Outbox. No sending or desktop operation. UNIT/MOCK command: python -X utf8 -B -m unittest -q tests.test_gui_name_source tests.test_collector_integration tests.test_name_bindings tests.test_source_question_tasks tests.test_native_group_ack: 74 passed, zero failures/skips. This proves the adapter-to-original-queue contract, not a running actual native producer or real SLA. Asked user whether desktop input can resume for actual date-filter evidence; no answer assumed. Full acceptance remains NOT_READY.

2026-10-20 real read-only menu/date exploration (Luna low): user explicitly released input focus. Unique WXWork HWND1771226, geometry[-2,-2,2564,1384], confirmed target group fresh header/avatar; input idle/held checks passed before one activation and hover operations. Group top ellipsis had no tooltip; circular control actual tooltip is 问大圆; top person-plus and editor-right split icon yielded no date/history tooltip. No click/send/DB write. Later fresh group-target check detected current 苇中鹤 @微信 and did not reuse group pin. Explicit authorized-contact read-only exploration confirmed current contact header and idle147250ms/held empty, but this layout has no top ellipsis; circular tooltip again 问大圆. Evidence private diagnostics/chat-history-control-probe-resume.png, chat-history-menu-probe.png, contact-menu-probe.png and native a92913f7cedd484a98758b9abaf1334b.png plus tooltip frames. No history/date panel was opened, no original year inferred, no group fact taken from contact. Date-filter route remains UNVERIFIED. Existing native shortcut whitelist lacks ctrl+f; a bounded verified shortcut experiment is a next possible route, not yet implemented or tested.
Full regression python -X utf8 -B -m unittest discover -q started at2026-10-20 19:18:49, process44628/tool session69970. Latest polls confirm still running, no final summary/exit yet; do not count as passed or restart merely because silent. No source-code changes during this run. Full goal remains NOT_READY.

2026-10-20 bounded native search shortcut: added only ctrl+f to NativeWeComTransport's fixed shortcut whitelist. Existing foreground/window/interactive-desktop/held-key checks remain; no Enter/arbitrary-key capability. UNIT/MOCK: python -X utf8 -B -m unittest -q tests.test_wecom_native tests.test_wecom_tools tests.test_wecom_cli: 97 passed, zero failures/skips; new test checks actual inserted/released key sequence and rejection before key input when foreground process changes. REAL VERIFIED by Luna low: current authorized 苇中鹤 @微信, same WXWork HWND1771226, foreground/idle/held verified, exactly one ctrl+f attempt203bf57b1829494cba1c52ab8aa27c7e (TOOL_RETURNED/backendWECOM_NATIVE/is_errorfalse). Beforeffb314b5.../after9cf605f8... and diagnostics/ctrl-f-difference.png show left search field/suggestions and 全局搜索 Ctrl+Alt+F, NOT a history/date panel. Main independently checked raw journal/result and diff image. No text entered or sent, no DB writes. Subsequent Escape then one selected-contact-row right-click showed only unread/mute/pin/mark/tag/hide/clear-history; no menu item clicked. Candidate editor toolbar paper icon tooltip was 文档, not history; no click. Idle4688ms immediately after own Escape was rechecked once rather than treated as proof of human takeover; later28078ms/heldempty/sameforeground allowed one hover. All dates/year remain unverified. Requested next bounded read-only search using already copied real question text in an explicitly verified search field, not message editor. No automatic group sending enabled.
Full regression tool session69970/process44628 remains live, no terminal result as of this entry. Its start predates ctrl+f change; this run cannot by itself certify the added code/test. The97-test targeted run above covers that change. Full goal NOT_READY.

2026-10-20 full regression TERMINAL: original session69970/process44628 exited1, command python -X utf8 -B -m unittest discover -q, 1535 tests/1572.123s, failures2/errors1/skipped3 (1529 passed, skipped NOT passed). Failures: direct followup expected EXECUTION_UNCERTAIN/no preserved reject draft but actual authoritative run deterministically REJECTED/NEEDS_ATTENTION; text preparation expected old A-D error while current guard supports question-kind option sets and emits all verified options of the question kind. Error: saved-teacher-delivery HTTP authorization test got WinError10053 connection abort. No claim this full run passed; code changed after run began, so it is not a current immutable-source full verification.
Investigation independently executed anonymous mocked fixture: original C answer is retained STALE after followup context update; attempted D answer retained REJECTED, run.error=EGO_FOLLOWUP_SELECTION_CHANGED_REVIEW. Existing production guards are correct and intentionally retain rejected evidence. Updated stale test expectations and strengthened exact historical states/text, rejection reason, absence of any rejected-run Outbox, and repeated tick produces no further browser calls. Incomplete question test now asserts existing kind-aware error and retains fake.calls==[]/tampered text checks. No production logic weakened/records deleted. HTTP test passed immediate isolated reproduction and full related-module run, but transient connection cause remains UNVERIFIED, not declared fixed or hidden behind retries. Final related command python -X utf8 -B -m unittest -q tests.test_ego_direct_queue tests.test_mcp_preparation_text tests.test_saved_teacher_delivery: 48 passed/17.613s, zero failures/skips. No new full suite yet; real ingress/date/search/ACK SLA and full objective remain NOT_READY.

2026-10-20 actual search/date evidence: Luna low used the exact previously copied question text only in the verified search field, entered the existing global search chat-records view, and obtained one matching result. Main independently inspected raw native ee72e8ffbcc34788beb6619556d3e961.png: full original group header remains visible behind the search window, result sender/body agree and original result time is10/20 16:12:45, with NO year. Actual chat-records filter offers Send time/Custom; custom-date panel opened but range was NOT completed/applied. Default calendar2026-10-20 is NOT original-year proof. No original time field, business task, performance or send was promoted from this evidence.
Actual layout obstacle: date FloatWindow32572916 rect[1601,380,1976,880] extends beyond SearchTabWnd33231718 rect[730,340,1831,1041]. Read-only Win32 owner-chain.json (independently read by main) confirms same WXWork PID42344 and direct owner/parent/rootowner33231718; main HWND1771226 is separate root. Native rejected outside-parent point before input. Added optional reviewed_popup window metadata/owner_handle/reviewed_at to existing transport, default absent means original strict boundary. Only exact live geometry/process, direct GW_OWNER equal unchanged current foreground, point inside that specific popup and review age0..30s permit outside-parent actions; foreign owner/process, moved popup, expired/future review, outside popup or absent review fail BEFORE cursor movement/click. No automatic popup discovery, arbitrary target or expanded parent rectangle. UNIT/MOCK command python -X utf8 -B -m unittest -q tests.test_wecom_native tests.test_wecom_tools tests.test_wecom_cli tests.test_native_group_ack tests.test_mcp_group_delivery:128 passed, zero failures/skips. Actual popup-input branch remains UNVERIFIED: latest single fresh foreground check was QQ HWND13568576 (native24c8f11b2f9f42199250a08c4ae44fb9.json), so Luna did not activate/capture/click. No stale owner data reused as current authorization. All real desktop inputs remain stopped until a suitable idle authorized foreground is available.
Skip reasons separately reproduced with exact individual cases: test_teaching_bundle.TeachingBundleTests.test_symbolic_link_dependency_is_rejected => Filesystem cannot create symbolic links; test_provision_archive_sdk.ProvisionArchiveSdkTests.test_provisioned_official_dll_metadata_is_read_without_execution => official SDK files have not been provisioned; test_worker_browser_session.DedicatedBrowserSessionTests.test_untrusted_account_default_escape_and_symlink_rejected => Symlink creation unavailable. Three SKIPPED, zero pass claims for them; no admin/security-setting changes requested. Full ingress producer/new arrival/ACK15min/two-stage real tutoring acceptance remain incomplete. NOT_READY.

2026-10-20 two-stage code check / actual seven-five compatibility fix: existing domain Question.option_labels/complete and compare support seven_five_blank A-G, but question_matching.validate_result still hardcoded4 when checking the program-derived option bijection. Changed only cardinality to len(student.option_labels), keeping exact expected mapping equality and unique targets, candidate/version/comparison checks unchanged. New synthetic test covers seven-five identity/reverse/rotation, missing mapping, duplicate target and incomplete student options; existing24 four-option permutations and first/second-stage stop/restart tests retained. Final command python -X utf8 -B -m unittest -q tests.test_question_matching:15 passed/46.500s, zero failures/skips, UNIT/MOCK only; no actual browser/desktop/source/production DB touched. Initial additional negative test attempted tuple.pop and failed in test setup; corrected to immutable slicing, then complete module rerun passed.
Important remaining integration fact: Workflow.start still hardcodes ego_policy=DIRECT_ORIGINAL for EGO_LITE_DEEPSEEK (workflow.py309); reviewed_question_queue.py349 enforces that policy, while automatic_answer_runtime.py88 requires VERIFY_THEN_TEACH only for windows_mcp. Therefore current ego queue has NOT implemented the objective's first reference/version check call followed by teaching call. Existing two-stage MCP implementation/mock tests do not prove ego two-stage integration or real verification. Next backend work must connect a distinct frozen two-stage policy using existing matching receipts/run/queue/cost ledgers, without relabeling ego proof as Windows-MCP, silently changing old frozen runs, or bypassing source/version/session/delivery checks. Real date-filter apply remains pending after foreground QQ stop; no new send. Full goal NOT_READY.

2026-10-20 lightweight ego stage-one evidence adaptation: extended existing question_matching.py and its original run/audit/receipt methods to accept actual ego capture/state files. No new service, schema, queue, provider or model call added. Outbound native match request contains original images and the existing frozen Top2 payload, no ANSWER uploads/solutions; attempt is persisted before browser dispatch. Native capture must agree with original request fingerprint, output hash, owned conversation, upload list and complete-reply evidence. Backend tags must match the recorded attempt; ego cannot pass via a fabricated MCP snapshot. Original receipt remains the single version-check record and is revalidated on restart/replay. New functions require MATCH_THEN_TEACH, but queue/freeze/generator policy integration is NOT YET connected; this is not an enabled production two-stage flow or real-browser validation.
Applied latest lightweight direction during implementation: removed redundant browser configuration validation already owned by the existing ego bridge, duplicate resolved-URL metadata checking, and repeated checks of fixed delivered/formal-statistics display flags. Actual delivery/performance remain controlled by existing Outbox/Delivery records, not those flags. Kept required source-file/path, request/capture, binding/version and duplicate-attempt boundaries. UNIT/MOCK command python -X utf8 -B -m unittest -q tests.test_ego_matching tests.test_question_matching:24 passed/48.135s, zero failures/skips. Native fixtures cover original-only uploads, changed original, request/output/state changes, wrong space/page/incomplete response, missing exact chat URL, confidence/extra fields, backend relabeling, path rejection before content read, and one receipt across replay/restart; original ACK unchanged, no new answer Outbox/performance. No desktop, DeepSeek request, production DB or send performed. Full goal remains NOT_READY; next step is narrow integration into the existing frozen two-stage queue policy, not more validation layers.

2026-10-20 lightweight native two-stage integration: MATCH_THEN_TEACH is now an explicit frozen ego policy in the existing admission/run/queue/runtime. DIRECT_ORIGINAL remains unchanged for old tasks; policy changes cannot silently upgrade frozen runs. Existing MATCHING and GENERATION steps share one owned session, original matching receipt, call meter and Outbox. Original images upload in matching; ANSWER originals upload in teaching. Ordinary followups and bounded revisions reuse the original verified material/session, without another search or matching call. No new service, schema, dependency, approval layer or completion ledger.
Restart/resume performs one reconciliation pass through existing queue recovery. Persisted CAPTURED results can advance without resubmission; SUBMITTED/UNKNOWN stays held. Drafts do not count as delivery/performance. Corrections during generation retain the rejected evidence and do not produce a sendable old answer. No production configuration, database, desktop, browser request or sending was changed by this integration.
Latest UNIT/MOCK command: python -X utf8 -B -m unittest -q tests.test_ego_two_stage_queue tests.test_ego_direct_queue tests.test_ego_process_restart tests.test_ego_generation tests.test_ego_revision => 59 tests passed /20.910s, zero failures/skips. Includes 14 new synthetic two-stage cases covering first-stage/teaching capture recovery, unknown no-replay after restart, photo upload split, candidate evidence, followup/revision reuse, policy freeze and correction stops. Earlier broader related batch:128 passed /598.385s before the final startup recovery additions; it is not a full current-suite claim. git diff --check and node --check helpdesk/static/app.js passed. Real two-stage browser, reliable original-year/new-arrival source, three-group automatic ACK/SLA, real task/cost/performance acceptance remain UNVERIFIED/NOT_READY. Earlier full-suite failures and three skips remain separately recorded above; no full-suite success claimed.

2026-10-20 execution-instruction alignment: the repository and installed answer-agent-deepseek-web Skill still instructed new native tasks to use legacy single-shot DIRECT_ORIGINAL. Updated only these existing instructions to the frozen MATCH_THEN_TEACH acceptance flow, kept old-run recovery, and corrected exceptional desktop actor to GPT-6 Luna low. No ANSWER teaching files changed. tools.operator_task freeze now exposes --ego-policy so the existing CLI can actually freeze the new policy instead of only the Python/config route. No new queue, service, validation layer or model call.
UNIT: python -X utf8 -B -m unittest -q tests.test_operator_tasks =>19 passed/1.114s, zero failures/skips. New CLI test executes main against an anonymous existing SQLite fixture, confirms native policy/space/page persist, one run, no generated answer/new Outbox/evidence directory/browser call. Initial assertion expected zero Outbox and failed because the fixture already contains its independent ACK; corrected to assert the original count stays unchanged, preserving ACK independence. Both installed/repository Skill quick_validate passed; CLI freeze --help and git diff --check passed. This is CLI/instruction validation only, no real browser/source/send acceptance or production configuration change. Full goal remains NOT_READY.

2026-10-20 current real-source revalidation (no new architecture): git ls-remote --symref origin HEAD in the clean detached ANSWER worktree again confirms default main and remote/local HEAD 1c388a0596b13935ae7c6a1af00834915a8efa33; git status --short empty. Rebuilt the existing reading task-checked bundle with tools.prepare_teaching_bundle --repository E:/作业帮/.tools/ANSWER-acceptance-1c388a0 --for-generation. No missing dependencies; manifest SHA256 remains b32042ed7395f257d43b9643ba6872ac082222b41b89d658be3c20098d2f9606, exactly the original latest-1350 frozen task hash. Cache reuse did not alter its manifest or teaching text. SOURCE/DRAFT per-task checks remain required; source rebuild is not actual teaching acceptance.
Regression command python -X utf8 -B -m unittest -q tests.test_answer_teaching tests.test_teaching_routes tests.test_teaching_bundle:48 total/74.081s =>47 passed,1 skipped,0 failed. Separately reproduced skip: test_teaching_bundle.TeachingBundleTests.test_symbolic_link_dependency_is_rejected, Filesystem cannot create symbolic links. Not counted as passed; no privilege changes. No production DB, message timestamp, sender or target modified.
REAL read-only native metadata was refreshed once by the existing Luna low actor. Main read diagnostics/current-wecom-metadata.json: foreground ChatGPT HWND265708; WXWork main1771226 and background global-search33231718 still exist, former date-popup32572916 no longer exists. No activation/screenshot/input/send. Previous search/date layout is not reusable as live evidence; original-year proof and real intake remain pending. Full objective remains NOT_READY.
REAL browser preparation continuation: Luna used the installed ego-browser 0.6.0.12 CLI, first listTaskSpaces returned empty (private current-readonly-inventory.json). Under the user's standing lost-space rebuild authorization, created exactly one new project space10/pagep1 and opened the DeepSeek homepage. Main read deepseek-home-readonly.json: actual pageInfo/dom href is https://chat.deepseek.com/, one enabled textarea with the DeepSeek message placeholder, authControls empty, submissionPerformed=false. A visible editor/absence of login controls does not establish account authentication or successful generation. Original historical run/browser bindings were not changed; no uploads, prompts, history replay, submissions or WeCom sending. This prepares the browser only; actual two-stage task validation remains pending. No CLI upgrade or additional service.

2026-10-20 real-process recovery test for native two-stage queue: extended existing tests/test_ego_process_restart.py, no production logic or runtime check added. Separate Python workers create anonymous source/ACK/admission/run records using the existing fixtures, then os._exit(86) immediately after mocked native MATCH or TEACH capture writes, before queue/cost completion. A new process context reopens the original SQLite/receipt files; repeated advance recovers matching without a new call, or teaching into exactly one pending answer Outbox. No recreated tasks/cleared DB, no remaining STARTED attempt, original call counts1/2 remain unchanged, call results CONFIRMED, performance0. Browser requests remain MOCK; Python termination/SQLite reopening are actual process behavior, not real DeepSeek/WeCom acceptance.
Command python -X utf8 -B -m unittest -q tests.test_ego_process_restart tests.test_ego_two_stage_queue =>16 passed/7.644s, zero failures/skips. Initial new assertion incorrectly expected an UNKNOWN-to-CONFIRMED recovery audit after abrupt death; hard death leaves START with no UNKNOWN result, so changed assertion to verify actual CONFIRMED results and finalized queue attempts instead. Business recovery behavior already passed; no guard weakened. git diff --check passed. Full goal remains NOT_READY pending real source/SLA/two-stage task/delivery/performance/cost/stability acceptance.

2026-10-20 matching ambiguity state fix: reused the existing NEEDS_ATTENTION queue phase for the existing structured MATCH_REQUIRES_REVIEW outcome, including recovery of a saved unresolved native capture. It was previously mislabeled EXECUTION_UNCERTAIN despite a definite model reply. No new check, model call, table or automatic retry. The captured reply remains evidence; teaching/answer Outbox/performance stay blocked. Generic candidate conflict is not automatically interpreted as a blurry photo or sent as REQUEST_IMAGE. Existing service.py incomplete-source path still creates its original REQUEST_IMAGE; no second clarification ledger or guessed field interpretation added. True browser submission/capture failures remain EXECUTION_UNCERTAIN and are not blindly replayed.
Regression: tests.test_ego_two_stage_queue + tests.test_ego_process_restart + tests.test_reviewed_question_queue =>37 passed/10.212s before the last added interruption case; final tests.test_ego_two_stage_queue =>15 passed/5.295s including saved-unresolved capture recovery, restart and manual-advance no-replay, zero failures/skips. git diff --check passed. Anonymous fixtures/mock browser only; no real account/browser/desktop/send or production DB touched. Full acceptance remains NOT_READY.

2026-10-20 minimal native window activation fix, prepared in isolated worktree while the unchanged main checkout's full regression runs: added CLI activate-window using existing NativeWeComTransport.activate(). It activates only the session-bound exact WXWork main window titled 企业微信, checks existing input/pause/interactive-desktop guards and shares the existing desktop lock. It does not use a fresh chat visual pin merely to focus the application; it neither renews that pin nor verifies/selects a chat. Default _pin checks remain unchanged for all message/frame/prepare/send operations. Expired sessions still block those operations. No raw HWND argument, arbitrary window action, new service or automatic retry.
UNIT/MOCK tests.test_wecom_tools tests.test_wecom_cli tests.test_wecom_native =>102 passed/2.800s, zero failures/skips. Tests exercise expired-pin activation without renewal or message input, stop/input-disabled/binding-change stops, exact WXWork/title refusal before native focus, no click/type, and CLI route without send permission. Real activation via this new CLI remains UNVERIFIED; no real desktop or production DB used. This isolated patch is not part of the currently running pre-patch full suite and will require targeted integration checks after that suite finishes.

2026-10-20 COMPLETE UNIT/MOCK REGRESSION: unchanged base a75ba47110acafeca58073c90f4562017476fddd ran python -X utf8 -B -m unittest discover -q in the original session41180/PID32028, no restart:1564 total/1575.487s, exit0 =>1561 passed,3 skipped,0 failures/errors. Original private log data/private/verification/regression-20261020-two-stage.log SHA256 2c6ea364eb398396e4202bdad4dc28be6c29c40f146bd7cb5e706c1ba9f8ae3b. This replaces the earlier failed full-suite baseline for that commit; it is not real environment acceptance.
After the suite terminated, integrated isolated activation patch ddf1b64 as main20ea0fe. Targeted regression python -X utf8 -B -m unittest -q tests.test_wecom_tools tests.test_wecom_cli tests.test_wecom_native:102 passed/2.725s, zero failures/skips; git diff --check passed. The full run predates this narrow activation patch, so do not label the entire current tree full-suite verified. Separately reproduced all3 skips: teaching_bundle symbolic-link dependency => Filesystem cannot create symbolic links; provision_archive_sdk official DLL metadata => official SDK files have not been provisioned; worker_browser_session escaped symlink => Symlink creation unavailable. None count as passed; no administrator privileges/security changes or SDK provisioning added.
User explicitly selected background-only until they hand over the computer. Existing Luna actor was told to stop all real desktop/browser activation and probing. Latest attempted isolated-patch real verification stopped before importing/calling it because idle_ms16 (private activate-window-preflight.json); native activate-window REAL remains UNVERIFIED. Do not automatically resume desktop work. No real sender/service or source producer enabled. Source original-year/new-arrival/ACK15min, admitted actual search, real two-stage task and full delivery/followup/performance/cost/20-task stability remain incomplete; NOT_READY.

2026-10-20 Youdao incremental integration (base3b9380f/main, original worktree clean): official API/price/FAQ pages were read before implementation; official erase Python demo was downloaded/read as source text only, never executed. Protocol notes: ocr_writing_erase uses HTTPS form POST, q/appKey/salt/curtime/sign/signType=v3, optional angle0/1, JSON eraseEnhanceImg/requestId/errorCode. The erase parameter table's shorter formula omits curtime; the explicit formula and official v3 demo include it, which this client follows. ocrquestionapi independently implements signType=v2 with type=1/searchType=img, JSON errorCode/data.text/data.questions[].id/content/answer/analysis; no documented requestId or independent material/stem/options fields are invented. Search FAQ limits single-question images to shortest side>10 and longest<2048px. Both accept JPEG/PNG/BMP. Implementation conservatively caps both raw/encoded search input below1,000,000 bytes; erase encoded input below5,000,000 bytes, plus a local20MP decode cap. No automatic original-image resizing/replacement.
Pricing review: official erase pricing describes successful calls, monthly tiers and prepaid packages; search pricing distinguishes returned-question/no-result calls and packages (including differing no-result package rate). No tariff is hardcoded as an actual fee. Explicit reviewed per-call ceilings are required as budget reservations; actual amount remains null until existing call_costs billing-proof confirmation. Official references are linked in README: API sjsxtcc/jptk, price sjsxtcc/jptk and FAQ jptk. Direct search price-page web fetch timed out; the official page's indexed content was available and read, not treated as account-specific current billing evidence.
Implementation: new independent handwriting/photo clients share only bounded I/O, approved-file checks, optional private credentials, durable cache intent and budget handling in youdao_api.py. Fixed HTTPS host/path, explicit connect/read limits, response cap, no redirects, environment proxy or POST retries. Missing credentials =>CREDENTIALS_NOT_CONFIGURED/no network; disabled by default. Image/protocol/context/config/account/provenance-bound cache, canonical default parameters, original-image pending guard across changed contexts/config/parameters, UNKNOWN never replayed. Raw response and derivative hashes revalidated; expired payloads are purged by non-dry CLI while minimal intents remain to prevent expiry-based recharging. No raw question/answer text remains duplicated in retained intent summaries. Injected transport evidence is marked and cannot be reused as a real-transport success.
Pipeline: existing run/attachment hash and current question/context are checked; cleanup is explicit, default skipped. Since erase returns no print transcript, successful image decoding alone is insufficient: processed print requires an explicitly reviewed current transcript/hash and existing domain.compare; altered NOT/number/options stop before search. Only full, unambiguous existing-parser candidates are compared/registered with ReferenceResolution, at most2, unconfirmed and using student-letter mapping. Missing/unsupported HTML stays MANUAL_REVIEW_REQUIRED. Candidate answer/analysis remains only private API evidence; pipeline returns an explicit safe subset. It does not start DeepSeek or create answer Outbox/performance. Linked API calls use existing SEARCH/OTHER audit kinds; passage budget aggregates the student's existing material scope across versions/subquestions/followups. Unverified other costs cannot be assumed zero. Standalone CLI has only single-image reservations, not a claimed formal per-passage cost ledger. No new database/table/schema/migration/framework/dependency; CLI opens only an existing current schema without auto-migration.
Validation:22 new offline test methods in tests.test_youdao_apis and tests.test_youdao_pipeline. Final command python -X utf8 -B -m unittest -q tests.test_youdao_apis tests.test_youdao_pipeline tests.test_reference_lookup tests.test_reference_providers tests.test_reference_resolution tests.test_call_costs =>112 passed/6.129s,0 failures/errors/skips (22 new+90 existing). Covers independent v2/v3 signatures at20-char boundary; Unicode paths; missing credentials/disabled/dry-run zero HTTP; invalid/oversize/corrupt/false-extension/dimension/path input;403/429/500/business202/malformed JSON/timeout; invalid erase result; content-hash cache including renamed files, default-param reuse, same-name changed bytes, unknown no replay and injected-cache separation; Top2/reordered options/external-answer exclusion; original preservation/cleanup transcript review; stale correction no application; budget stop; existing-task CLI and unchanged schema. Initial pipeline test caught full search-response answer/analysis leaking into its return object; fixed the return boundary (kept private evidence), then reran all named tests. No assertion removed to mask it. Earlier invalid-regex escape warnings were fixed.
REAL LOCAL ONLY: self-authored anonymous synthetic-single-question.png(700x430), SHA256 e0f8ee5b73d28f5f8bf68e839e41a9589f7899982c0cf9ad082d74cafb495635; erase-only/search-only dry-run returned DRY_RUN,actual_call=false,cache_hit=false,amount_cny=null. Actual paid API requests0; real HTTP/API return/billing/cache-hit remains UNVERIFIED. No real student image uploaded. User's two supplied identifiers were treated as AppKeys and saved only to ignored data/private/youdao-credentials.local.json; preflight confirms both credential pairs incomplete, no AppSecret disclosed or copied into Git. config/youdao.example.toml stays disabled. Stage6 real test requires the user's explicit designated anonymous image and authorization plus local secrets/price ceilings; production Provider enablement remains pending review. Enterprise WeChat/browser controls remain stopped per user's background-only instruction. Existing full Level3 objective remains NOT_READY; this API integration is offline-verified, not production acceptance.


2026-10-20 selected-image offline preflight: user-provided photo was copied byte-for-byte into the approved private test-image directory, SHA256 8d84e9ee0c6ae264a5df01da689d4fa5511331a3ccfd8c7594a92a02e515c4f8, 390669 bytes, 2160x2896. It contains printed questions28-31. No crop, resizing, question reconstruction or external upload performed. Erase dry-run returned DRY_RUN/actual_call=false; photo-search dry-run stopped locally with IMAGE_DIMENSIONS_INVALID under this client's conservative dimension limit; this was not a server rejection. At that point, the operator was asked to choose a printed question; that choice requirement is superseded by the whole-passage clarification below. Both credential pairs remain incomplete; both Providers remain disabled, no actual API/billing evidence.
Fixed a provenance error in the standalone CLI: removed its unconditional synthetic_test=true context label, since an operator-selected photo is not necessarily self-authored synthetic data. No new flag, cache reset, paid request or changed send permission. Existing CLI dry-run test now also checks this boundary. python -X utf8 -B -m unittest -q tests.test_youdao_apis tests.test_youdao_pipeline =>22 passed/3.423s,0 failures/errors/skips; network mocked, no real API claim. Full Level3 objective remains NOT_READY; desktop/browser stays stopped under the user's background-only instruction.


2026-10-20 whole-passage clarification: user selected the complete reading passage plus printed questions28-31. Do not require a single-number choice or schedule four paid searches automatically. First real test is one whole-material request; whether Youdao treats a reading passage with related subquestions as one supported question remains UNVERIFIED. Re-read official https://ai.youdao.com/DOCSIRMA/html/learn/faq/jptk/index.html and API page: FAQ says single-question search rather than whole-page multiple-question search, but does not define reading-question groups. It also says oversized images are scaled by the service and may lose quality; the client's current strict2048-side rejection is a conservative local policy, not proof that the server rejects this image. No local resizing/crop, request, billing result or fee assumed. Current preflight still finds both credential pairs incomplete and both Providers disabled. Real full Level3 acceptance still requires the previously paused desktop handover; this API is not a new mandatory production dependency.


2026-10-21 REAL single-image API test (explicit user authorization received): preserved the original photo SHA256 8d84e9ee0c6ae264a5df01da689d4fa5511331a3ccfd8c7594a92a02e515c4f8,2160x2896,390669 bytes. Python/Pillow produced a proportional whole-content JPEG copy1492x2000,683176 bytes, SHA256 1079938d6cbe0fb486fcee5fad6fbaeeae442776e2c985216340e375f03e5b35; no crop, article and printed questions28-31 retained. Both existing-client local preflights passed. Tested the two independent clients on this same original-derived input, not a claimed cleaned-image pipeline or task completion. Original hash binds both requests and the shared single-image reservation scope. Proposed local test ceilings0.25+0.25,total0.50 were explicitly accepted by user, not recorded as actual fees.
Exactly one official HTTPS request per API, no injected transport: ERASE CLEANUP_FAILED/error108/426ms, SEARCH SEARCH_FAILED/error108/365ms; both actual_call=true/cache_hit=false, charge_status=UNVERIFIED/amount_cny=null. Official error table defines108 as invalid application ID; erase also returned msg=auth failed. No processed image, OCR, candidate or successful teaching result obtained. Both original journals are FINISHED, response hashes were independently rechecked against stored bytes, pending intents0. No second attempt or different-parameter retry. Two actual requests total; account billing not available, so do not claim zero actual cost. Private result summary data/private/youdao/results/real-whole-reading-20261021.json, raw responses only in the existing private TTL cache. No secrets copied into the report or tracked files.
Credential diagnosis: both effective pairs exactly match the local credential file, neither API has environment-variable overrides, neither local ID nor secret has surrounding whitespace. This is evidence of server-side authentication refusal, not proof that the provided IDs are valid or that the secrets match their intended applications. Next external action: verify each application ID/secret pairing and account/service status in the official console; do not resubmit within the already used authorization. Test enablement existed only in the invoking Python process; example and local test config both remain disabled. No production DB/schema, WeCom/DeepSeek operation, delivery or performance update. Existing Mock tests remain22 passed from previous run; no source change or new Mock run this round. Full Level3 acceptance remains NOT_READY, and desktop work remains paused.


2026-10-21 explicitly authorized corrected-credential retest: user clarified original identifiers were application secrets, supplied replacement application IDs, then explicitly approved one additional ERASE and one SEARCH call with additional0.50 reservation/cumulative1.00. Applied that pairing only in the ignored private credential file; older longer secret values were superseded by the latest user clarification, never automatically tried as alternatives. The exact same full reading derivative1492x2000/hash1079938d6cbe0fb486fcee5fad6fbaeeae442776e2c985216340e375f03e5b35 was submitted once per API, no crop, original hash8d84e9ee0c6ae264a5df01da689d4fa5511331a3ccfd8c7594a92a02e515c4f8 unchanged. No new question selection, split requests or automatic retries.
REAL results: ERASE CLEANUP_FAILED/error202/392ms; SEARCH SEARCH_FAILED/error110/376ms. Both actual_call=true/cache_hit=false/injected=false, actual amount null/UNVERIFIED. Official API error table defines202 as signature verification failure and110 as no valid instance for the relevant service. Need official-console verification of the erase application's ID/secret pairing and the search application's premium-question-service instance/binding; the errors alone do not establish the precise account cause. Offline independent formula checks against actual prepared Base64 and configured credential pairs match both existing implementations/signType(v3 erase,v2 search), but they do not verify server acceptance of the secrets. No signature or credential value printed or stored in ordinary logs.
All4 real original-origin journals are FINISHED with individually verified response hashes, pending intents0: first-round108/108 retained, this round202/110 added. Private second summary data/private/youdao/results/real-whole-reading-20261021-corrected-credentials.json. Cumulative actual requests4, this round2, neither produced a processed image/candidate; reading-group support, successful output, successful cache reuse and billing remain UNVERIFIED. Additional cumulative budget1.00 and enablement were only in the authorized process; local/example configurations remain disabled with original0.50 baseline, no ledger reset/deletion. No code/schema change, no new Mock suite, no production DB, WeCom/DeepSeek, actual delivery or performance change. Full goal remains NOT_READY; next API step requires account corrections rather than further unmodified requests, and real desktop handover remains pending independently.


2026-10-21 credential-role correction after user explicitly confirmed a separate AppSecret: previous operator interpretation incorrectly put APIKEY in the APP_SECRET field. Restored the independently supplied AppSecrets and kept APIKEY values separately in the ignored private file; application IDs preserved. User explicitly reconfirmed the erase AppSecret. Existing official clients still sign only with application ID plus AppSecret, no APIKEY/bearer/header substitution. No new service/credential system/code change or request. The earlier202 receipt belongs to the APIKEY-as-secret configuration and is not evidence that the now-corrected independent AppSecret failed. Search110 remains the last actual service-instance error; current secret pairing has not been submitted.
Offline preflight found the same-account old202 failure cache would be reused under the old config revision. An explicitly reviewed test configuration revision, youdao-2026-10-21-distinct-appsecret-v2, is proposed for a single erase retest; no old intent/cache/UNKNOWN record will be removed, and the revision does not bypass the pending-original guard or shared original-image budget. Prior4 real request reservations remain1.00; proposed one additional erase call reserves0.25,cumulative1.25 pending user confirmation. Do not repeatedly test the search110 condition without account/service changes. Both configs remain disabled; current test image/original hashes rechecked, no extra paid request, DeepSeek, WeCom or performance. Full Level3 still NOT_READY; desktop handover is independently pending.


2026-10-21 REAL ERASE SUCCESS, explicitly approved single erase retest using independently confirmed AppSecret: existing client called official endpoint exactly once under reviewed config revision youdao-2026-10-21-distinct-appsecret-v2, result CLEANUP_SUCCEEDED/error null/1730ms/actual_call=true/cache_hit=false/injected=false, requestId3e42ce19-4810-412d-a249-6a6dcb02bfe8. Input remained the whole reading derivative1492x2000/hash1079938d6cbe0fb486fcee5fad6fbaeeae442776e2c985216340e375f03e5b35; original hash8d84e9ee0c6ae264a5df01da689d4fa5511331a3ccfd8c7594a92a02e515c4f8 unchanged. Returned PNG1638x2198/hash e053c8a9a950c78f27b81ddd769a1a06ba7a2ca9b3f050bc1b68c3efffdd9d72, raw response SHA2560baaa232d739b7e488c83f58b6bb8a3026c424ee23b9a39f74a831f90c9a0fdc. Stored in original TTL cache, independently rechecked image/response hashes. Local image inspection shows article and printed28-31 visible, most handwriting removed, some overprinted pen marks remain. User explicitly accepted visual effect. Record that feedback in the existing private summary, not a fabricated platform receipt or exact transcription approval. print_preservation_verified remains false because full original-to-output printed-text comparison was not supplied. No automatic teaching/performance approval.
User then explicitly requested SEARCH as well. Prepared a full-content, uncropped JPEG derivative of the accepted erase result1490x2000,527070 bytes/hash a263a7556c14784e35145fe7a1eaf6aa43ae25a1229d0b9d94cb2e2ca747dbcc, keeping original and erase-output hashes as lineage. Independent actual AppSecret selected, never APIKEY. Exactly1 SEARCH request, result SEARCH_FAILED/error110/325ms/cache_hit=false/injected=false, no candidates. This remains a relevant-service-instance/account-binding issue per the official error table; it is not evidence that a whole reading group is unsupported or that no original question exists. No unmodified retries or guessed provider success. User's fresh request authorized that one extra search;0.25 additional reservation/cumulative1.50 set only in the invoking process, never as an actual fee.
Private summaries: data/private/youdao/results/real-whole-reading-20261021-erase-appsecret-v2.json and real-whole-reading-20261021-search-cleaned-v2.json. Total actual requests6 (ERASE3,SEARCH3): prior108/108 and202/110 retained, now erase success plus search110. All6 existing journal response hashes rechecked; pending intents0, shared retained reservations1.50, actual amounts null/UNVERIFIED pending account billing proof. Original and returned images retained under approved private paths. Real successful erase is now proven; search success, whole-reading candidate matching, verified cleaned-print pipeline, successful cache reuse, and actual billing remain unproven. No source logic/schema/test change, no new Mock run, no DeepSeek/WeCom/Outbox/performance operation. Both disk configurations remain disabled. Full Level3 objective remains NOT_READY pending independent real-source/two-stage/delivery/performance/cost/stability acceptance and desktop handover.


2026-10-21 scope update: user explicitly deferred photo search and accepted handwriting erasure as sufficient for now. Stop service activation, additional search API tests and customer-service consultation; no customer message was sent. Read-only console inspection found the search application bound to whole-question OCR V2 and question segmentation, not a visible premium question-bank service; no application binding, purchase or credential changes were submitted. Browser space 2 remains under user control; no further browser operation is required for this deferred task.
Keep existing search implementation/tests for later use, with search_enabled=false in the existing local configuration and disabled sample. Neither disk API enable flag was changed; no automatic upload, queue or paid request was started. Erase-only CLI remains independent of search; its result now includes the existing private processed_path, with original image retained. README leads with erase-only offline preflight and the current deferred scope. Prior real erase success remains valid; exact printed-text preservation is not newly certified.
Validation: python -X utf8 -B -m unittest -q tests.test_youdao_apis tests.test_youdao_pipeline -- 23 passed in 3.321s, 0 skips/failures/errors. Added mocked erase-only CLI coverage verifies output artifact hash/path, unchanged original and no search call or real network. This round: real API calls 0, real desktop/browser verification 0; previous real results are recorded above. No teaching, delivery, database or performance changes. Full Level3 objective remains NOT_READY.
Follow-up related regression: python -X utf8 -B -m unittest -q tests.test_youdao_apis tests.test_youdao_pipeline tests.test_reference_lookup tests.test_reference_providers tests.test_reference_resolution tests.test_call_costs -- 113 passed in 6.484s, 0 skips/failures/errors; offline/mock only. Existing task artifact inspection identifies separate frozen runs for prior printed-question and question-7 tests, not current-version or real-browser confirmation; do not resume either from an old file alone. Real two-stage browser handover is requested separately; no activation or request made.

2026-10-21 controlled new-message trial: user now explicitly authorized switching to WeCom, waiting for a new question, sending the initial ACK and generating a draft, stopping before answer delivery. Earlier background-only restriction is superseded for this trial. Source inspection was read-only: the two old question-7 runs are REJECTED (one also stale), and the latest generated run is DIRECT_ORIGINAL; none is a valid running two-stage acceptance task. No rejected run/policy was rewritten. Existing reading teaching bundle was independently verified again: ANSWER 1c388a0596b13935ae7c6a1af00834915a8efa33, manifest SHA256 b32042ed7395f257d43b9643ba6872ac082222b41b89d658be3c20098d2f9606, no missing required dependencies.

Added explicit allow_disabled_search boolean to the existing answer runtime, default false. Only an actual DISABLED lookup plus complete reviewed student material may proceed when this option is true; report remains DISABLED/OPERATOR_SKIPPED/reference_search_verified=false with zero invented candidates. Interrupted/unavailable search still fails; first-stage unresolved results still stop. The existing two stages, original queue/run/Outbox and delivery/performance guards remain. This permits the user's current no-search demo without presenting it as successful Reference Search acceptance. UNIT/MOCK: tests.test_question_matching + tests.test_ego_two_stage_queue, 32 passed/53.448s; after a string-type guard and invalid-config assertion, final two-stage tests16 passed/5.749s; configuration boundary and hard-process recovery tests5 passed/2.418s. No real DeepSeek request was submitted this round.

REAL desktop evidence: the existing delegated executor actually activated WeCom and verified current 27S🌟 English答疑1群. A fresh private read-only session was created, never promoted to allow_send=true. Baseline then failed COPY_OCR_NOT_UNIQUE_OR_UNAVAILABLE; no watch process was started and no message was imported or sent. The failed copy screenshot instead contains a ChatGPT taskbar menu, with actual pointer at [1595,1416] rather than the intended message point; a prior capture also records pointer movement. Do not describe this as merely an OCR miss or a running listener. Added actual-pointer equality checks immediately before native click, scroll and type mouse dispatch; a moved pointer now stops USER_POINTER_MOVED_INPUT_PAUSED before the action. UNIT/MOCK native/tools/CLI tests103 passed/2.786s, including moved-pointer/no-click/no-paste coverage. This narrows an observed race window; real guarded execution still requires verification, and user/other automation must not operate concurrently. Subsequent real read-only screenshot shows a teacher reply at11:01 and resulting message-list scroll, not a new student question. Old message hashes must be refreshed from current evidence rather than treated as new arrivals. Full acceptance remains NOT_READY; no ACK/answer/actual delivery/performance was recorded in this trial so far.

Final REAL trial outcome before the user's push request: native Move read back the exact intended [900,650] without clicking. Corrected only the ignored local session's visual regions using the original2560x1440 image rather than its2048x1152 preview: header[402,20,690,62], stable avatar[92,91,122,121], incoming viewport[380,75,1000,1180]. The former header region included the changing chat list and the former viewport missed the bottom of the message pane. Local detection on the saved real frame found the actual historical thank-you bubble[405,1113,495,1155]. These are this window's reviewed configuration, not new hardcoded production selectors. Existing read-only renewal succeeded without changing target, hashes or sending scope. Final hidden watch PID51672 ran11:16:58-11:22:49:50 actual poll results, comprising1 OBSERVATIONS_SAVED/BASELINE_ESTABLISHED for the historical thank-you,48 NO_NEW_OBSERVATIONS, then1 UNAVAILABLE/WECOM_NOT_FOREGROUND. Wrapper retained the real process handle and recorded returncode2; PID absence was independently checked. No automatic reactivation/restart. The first successful text copy and duplicate-stable baseline are real evidence; no new student question appeared, source original time remained unverified, automatic_ack_eligible=false. No ACK, DeepSeek submission, answer delivery or performance change occurred. No watch remains running. Full new-question/two-stage/delivery acceptance remains NOT_READY; do not count this as a complete end-to-end task or production SLA pass.

Final pre-push regression on the complete change: python -X utf8 -B -m unittest -q tests.test_question_matching tests.test_ego_two_stage_queue tests.test_automatic_answer_runtime.AutomaticAnswerConfigurationTests tests.test_ego_process_restart tests.test_wecom_native tests.test_wecom_tools tests.test_wecom_cli -- 140 passed in53.454s, zero skips/failures/errors. UNIT/MOCK coverage, not additional real account testing. Outgoing unpublished history and working diff were scanned against local credential values; no matches and no tracked data/private runtime files. This is a scoped regression, not a new full-suite claim.
