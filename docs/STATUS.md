# 当前能力与缺口

截至2026-10-02，源码提交仅包含程序、测试、配置示例与通用说明。本机聊天记录、学生图片、数据库、登录状态、运行凭据及历史验收材料保留在原工作目录，不上传。

## 本轮审计与最小改动

业务检出目录为 `E:\作业帮\tmp\answer-agent-publish-20261001`，本批审计起点为 `main / 126747e1fed4a70c78c6fca46f802dfa9f8984c1`，远端为 `rockpunkgod/answer-agent`。后续提交可从该基线追溯。原工作目录仍运行本机8767工作台，本批不自动部署或进行数据库迁移。

| 环节 | 代码或配置证据 | 实际问题或待验证风险 | 本轮最小处理 | 验证方法 |
|---|---|---|---|---|
| 官方CLI | 上游 `package.json`、`docs/cli-reference.md`、`skills/wecomcli-message/SKILL.md` | 当前文档描述近期机器人会话发送；指定的四份 `wecomcli-msg` 读取文档不存在，实际Schema在线下发 | 记录真实版本；不据缺文档推断工具不存在，不编造读取适配器 | 授权后核对Schema和原群覆盖 |
| 官方SDK | 上游 `aibot/message_handler.py:55`、`client.py:301`、`ws.py:527` | 通用与具体事件均触发；body可覆盖chatid；队列断线清空 | 未启用、未复制上游代码；不能替代业务Outbox | 机器人场景覆盖成立后才做薄适配 |
| 原消息 | `native_message_source.py`、`collector_storage.py:147`、`collector_dispatch.py:71` | 当前读取已保存原文，不是GUI监听；缺可靠原时间会暂挂ACK任务 | 保留去重、原时间证据和事务；不把未采集显示成群无消息 | 保留相关回归；下一批再分离内部处理与绩效时间门槛 |
| 教学来源 | 旧 `teaching_bundle.py` 默认本机Skill；`teaching_routes.py` 已有正确分工 | 旧包未绑定ANSWER提交；主客观题检查脚本缺失；语法还依赖内部修稿说明 | 新增固定提交、按题型完整原文的预览与缓存，补齐语法说明，缺项不借用 | 本地Git原文比对、缓存、篡改、脏文件、目录跳转测试 |
| 桌面 | `mcp_window_probe.py`、`tools/windows_mcp_session.py` | 标题栏被遮挡限制点击；App使用打开窗口缓存做名称匹配 | 可选受控App切换，仅允许缓存中的唯一企业微信与屏幕2原生窗口一致；切换后再核验 | 模拟窗口、PowerShell假窗口、错误目标/移动/超时拦截；新切换未做真实输入验证 |
| 交付/绩效 | `workflow.py`、`delivery.py`、`semantic_decisions.py`、`performance.py` | 真实交付回流仍缺接线，现有版本/计量保护已存在 | 保留共享归属、过期拦截、未知发送核验、夜间与日报规则；不重做数据库 | 完整离线及Mock回归，不把草稿计完成 |

当前原工作目录配置为 `ACK_ONLY`、`helpdesk.native_message_source:create_source`；七个发送目的均为 `DISABLED`。8767服务可访问，消息与Outbox显示为空只说明本地未形成业务记录。网页持久队列线程运行，不代表会话创建/网页准备/生成回调已连接。API调度模型未配置；已有Codex的Luna medium有限导航连接不等于业务语义已接通，也不新增模型投票。

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

## ANSWER版本与预览结果

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

## 已可使用

- 一个 PowerShell 入口启动本地工作台，缺可选采集配置或讲解目录仍能查看台账；复用服务不会自动恢复暂停。
- 原生文字增量导入、原文导出及来源校验；原始时间与采集时间分开，重复采集去重。保存原文不等于实时监听。
- 原消息的一次题面确认、持久队列、题目与上下文版本、选项映射和旧答案拦截。
- 内部原题核对的候选、逐字段证据、人工确认/拒绝与确认结果的冻结输入；默认关闭及Shadow不消费参考，不新增发送权限。匿名与模拟网页路径验证，真实网站尚未验收。
- Outbox、未知发送停止核验、人工接管、目录限制及本机访问。
- 本机登记人工实际交付及多部分结果，回到原任务与原计量台账；不依赖重新生成AI草稿，缺归属时明确待核对。
- 完整讲解展示和复制，三栏日报及明细导出；绩效检查实际交付资格，已提交报表不静默覆盖。
- 本机已验证屏幕2的企业微信标题栏激活、原题文字复制与原生图片保存；一题历史练习已通过真实 DeepSeek 网页上传原教学文件和生成完整讲解。练习没有向群发送，也没有增加绩效。
- 已登录的本机 Codex 可进行 Luna medium 有限导航定位；输出只是控件位置建议，程序仍核验新鲜的窗口身份、物理坐标和目录。此连接未接入持续观察及业务队列。

## 尚未贯通

- 持续读取真实群中新消息，取得可靠学生身份、原始发送时间和附件，并由自动入口完成“收到”。历史现场确认的 ACK 不作为当前实时服务的证明。
- 共享归属语义与真实题面恢复接线，以及持久队列自动运行 DeepSeek 网页的回调。
- 原群草稿与人工发送后的真实交付观察；本机人工登记和计量回流已通过匿名验证，真实操作仍未验收。
- 所有 English 群（含已退出群）9月17日起的完整历史覆盖；部分导出不能视为正式总量，缺原年份不得猜测。

因此，当前工作台可运行，完整新题自动答疑仍未通过真实环境验收。历史题练习保持禁发，不能把模拟回执或生成成功改成正式完成。

## 本轮验证

早期教学来源回归17项通过；屏幕2与相邻保护回归52项通过。真实ANSWER的六类本地原文预览与重复缓存核对通过，范围仅本地来源与文件生成；没有上传DeepSeek、运行教学检查或向企业微信输入消息。当时完整回归为上述996项；当前最终版本为1037项、1034通过、0失败、3跳过，见“MCP安装路径与真实只读核验”，原有有效测试保留。

本轮针对导航、物理窗口、输入契约、传输和审计的47项回归，以及相邻准备/草稿路径的27项回归通过。原消息/附件衔接及页面断连恢复分别完成相关回归，本地服务检查未执行发送或增加计量。

发布副本完整回归实际执行 `python -X utf8 -B -m tools.verify_project --output artifacts/verification/20261001-initial-git-publication`：889项，886通过、0失败、3跳过，127.943秒，退出码0；测试期间源码未变化。219份业务源码、测试、提示词及入口/依赖文件与原工作目录逐字节一致。两个跳过是本机无法创建符号链接，另一个是独立源码副本未提供官方存档SDK的原生DLL，均不计通过。不上传私有SDK文件来消除这项跳过。

测试范围为匿名数据、模拟适配器、本地HTTP、PowerShell入口和无界面浏览器；没有实际企业微信发送、实时新题完整答疑、付费Provider调用或正式日报提交。原始测试日志和源码校验结果保留在本机发布副本的忽略目录中。

真实原图已从客户端保存并校验为原生 JPEG；题面识别清楚，但完整原始提问时间仍待补证据。此前真实 DeepSeek 历史练习使用已核对的文本，不能声称它已使用这份后来取得的图片。

## 暂缓扩建

保留现有轻量接口和 Worker 连接，暂停扩建多节点、主备、微服务、监控平台及自动模型升级。开源更新定时维护暂缓，先解决上述真实闭环。仅在实际多机、多人或远程访问需求出现时恢复相应工作。

教学 Skill 的真实路径与允许目录防护继续保留。符号链接测试受本机权限限制时记为未验证风险；只加载本人审核、目录受控的材料，不开放外部任意 Skill 路径入口。
