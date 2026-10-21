# 个人微信本地读取原型

隔离分支 `prototype/personal-wechat-db-20261021`，基于 main `7bd61a5`。不注册生产来源，不操作正式业务数据库、发送入口或绩效。前三个 English 群经用户确认；第四个只允许明确绑定的测试群，不能按同名自动替换。停用群保留原始记录和游标。

## 已有实现

`PersonalWeChatSource` 实现现有 MessageSource；复用 CollectorStore、SyncEngine 的事务和游标。来源明确为 `personal_wechat_local`，不会伪装成企业微信消息。Dispatcher 强制禁止 ACK、业务投影和发送回执晋升。

账号由运行进程 CFG 与目录匹配，不按最近修改时间选账号。消息身份包含账号、群、物理分片、local_id、完整类型、序号与服务端 ID；发送者映射来自所在分片。原始时间、采集时间、原始字节和媒体引用分别保存。缺身份或时间保留待核验。

每群、每模式使用现有持久化游标。完整有界快照检查所有消息分片，捕获迟到及同序号消息；提交消息和游标使用同一事务。首次 LIVE 必须接续已提交且完整的 BACKFILL。锚点消失/改变、分片换代、超过上限、读取失败均暂停，不当作空群。默认每群最多一万行，非高吞吐持续监听。

## 上游与隔离

候选：fanyuantaier/wechatauto-replica，提交 `d322c4a9b1124228820bab363e5676016f188fc6`，版本 1.2.7.0，Apache-2.0。普通数据库/媒体命令仅加载校验过哈希的 db.py/media.py；不执行包初始化、发送或 GUI 驱动。另有显式启用的 accessibility 诊断，见下节，其临时进程写入不属于纯只读进程访问。不改生产依赖，使用私有独立 venv 与缓存。

使用到的数据库/媒体路径会只读访问进程内存以获得本地解密材料，并产生本机明文缓存。缓存可能物理包含该账号其他会话的分片内容，查询范围白名单不等于缓存只含白名单群。缓存/密钥/原始记录置于受限的 `data/private/replica-runtime`，不提交 Git 或写普通日志。

上游 Listener 在回调重试耗尽后仍推进水位，且队列在内存中、仅按序号推进可能漏同序号消息。已用上游真实类与匿名失败回调复现，故不使用它。上游自动切账号、忽略坏分片、降级旧缓存的路径也不启用。

## 运行

2026-10-21补充真实验证：用户单次授权后，新独立入口实际执行一次 `poll`（LIVE），四群读取成功，耗时4.405秒，新增0/冲突0。与执行前私有SQLite备份比较，原始四表内容哈希一致，17条记录未重入库。没有桌面传输/UIA/激活/输入、DeepSeek、外发或正式绩效写入；未启动常驻循环。此结果仅证明新CLI后台读取和既有游标复用，不证明工作台父监督器真实持续运行、真实新消息到达或ACK SLA；没有独立观测前后台窗口句柄。单次授权已使用，不能据此继续追加轮询。以下早期“未运行真实入口”的说明保留其当时测试边界。

### 接入项目工作台

现有 CollectorSupervisor 根据 `source.kind = "personal_wechat_local"` 调用隔离采集子进程，复用工作台的启动、停止、唤醒和关闭管理；不另建守护服务。每轮使用 `poll` 接续原始消息库的已有游标，不调用原 ACK Dispatcher，也不创建另一份业务完成台账。

TOML 示例为 `config/collector.personal-wechat.example.toml`；本机已按现有 JSON 路径建立私有 `data/private/replica-runtime/collector.local.toml` 并离线核对同库路径，未启动账号读取。JSON 内容发生变化后停止并要求重新启动，不能在运行中默默更换账号、群或数据库。缺完整历史基线仍由原来源检查拒绝。

```powershell
# 先打开现有工作台；“启动采集”后开始后台读取。
./run_current_demo.ps1 -Port 8768 -CollectorConfig data/private/replica-runtime/collector.local.toml -NoAutoCollect
# 若明确需要随工作台自动采集，去掉 -NoAutoCollect。
```

不用同时运行独立 `watch`。工作台显示“个人微信只读采集”，原文区复用 `/api/native-records` 展示最多250条本库个人微信记录，包括之前授权但已停用的测试群历史；这不表示继续采集停用群。原始时间可靠时直接显示原值，缺时间不补当前时间。原文记录不等于答疑题数或已完成交付，图像仍可只有媒体引用。账号未实测时不显示“已验证”；状态不再误标企业存档。旧工作台若使用不同采集配置，启动器会拒绝静默复用，需换端口或先关闭旧服务。

接线范围：后台采集 → 原 CollectorStore → 工作台只读展示与暂停/恢复控制。个人微信原型到语义归属、AnswerTask、ACK、DeepSeek、Outbox及正式绩效的晋升限制保持，本轮没有将其切为完整生产来源。真实原图自动补下载和下游全链路仍未验收。

本批回归：`python -X utf8 -B -m unittest -q tests.test_personal_wechat_polling tests.test_collector_supervisor tests.test_personal_wechat_collect tests.test_personal_wechat_source tests.test_current_demo_launcher tests.test_demo_http`，64项通过、0失败/跳过（24.541秒）。包含匿名本地HTTP展示、模拟子进程、原采集控制与Windows启动脚本；不是账号真实验收。`node --check helpdesk/static/app.js` 通过。初次发现原GET方法内后置 sqlite3 导入使原文分支出现局部变量错误，已删除重复局部导入并复用原模块导入；同时修正新增测试的命令索引误期望，未删断言。本轮未执行真实采集或外发。

### 独立命令

新增 `tools/personal_wechat_collect.py`，可脱离答疑主程序单独运行，但仍复用仓库已有的 MessageSource、CollectorStore 和 SyncEngine；不是另建数据库或发布完整上游 fork。默认只加载固定哈希的 db.py，不加载 media.py、UIA、原生桌面传输、上游包初始化或业务 Dispatcher。最低采集第三方运行依赖为现有隔离环境中的 cryptography、psutil、zstandard；不需要 Pillow、numpy、uiautomation、Playwright 或 Windows-MCP。数据库解密所需的只读进程访问和私有明文缓存边界仍然存在，不等于官方接口。

```powershell
# 首次须完成历史基线；不会为历史消息生成 ACK。
& data/private/replica-runtime/venv/Scripts/python.exe -X utf8 -B tools/personal_wechat_collect.py history --config data/private/replica-runtime/source.local.json
# 单次增量，要求已存在完整历史基线。
& data/private/replica-runtime/venv/Scripts/python.exe -X utf8 -B tools/personal_wechat_collect.py poll --config data/private/replica-runtime/source.local.json
# 持续采集；Ctrl+C 暂停，重启同一命令接续原游标。
& data/private/replica-runtime/venv/Scripts/python.exe -X utf8 -B tools/personal_wechat_collect.py watch --config data/private/replica-runtime/source.local.json --interval 5
```

这里的“监听”是定时增量轮询，不是消息到达推送。每轮使用同一隔离解释器的新子进程，避免重复加载上游和残留状态；间隔默认5秒，但实际延迟还包含数据库读取耗时，不能声称5秒 SLA。可用 `--cycles 3` 限定轮数；单轮默认超时120秒，最大可设600秒。失败、超时、冲突或未完成分页会停止，不自动跳消息、重建游标或反复重试；已提交页保留，重启沿用原游标。最新失败覆盖原私有 last-result，不能继续展示旧成功。

不调用上游 Listener：其 db.py 实际在回调重试耗尽后仍 `_commit_watermark`，且队列在内存中。独立入口使用现有“消息与游标同事务”路径，原有同序号、迟到消息及失败回滚保护保持。每群默认一万条的完整快照上限仍在；达到上限停止人工核对，不宣称无限历史覆盖或高吞吐实时能力。

本轮仅后台代码与离线验证，未启动常驻采集、未访问真实客户端测试新入口、未更改生产来源。`python -X utf8 -B -m unittest -q tests.test_personal_wechat_collect tests.test_personal_wechat_source tests.test_personal_wechat_accessibility tests.test_wecom_native`：57项通过、0失败/跳过，1.284秒。包括新子进程轮询、失败不重试、历史/增量区分、失败覆盖旧成功，以及不加载媒体/交互模块；不能替代真实新消息及持续运行验收。图片保留媒体引用，实际原图读取仍通过原 `media` 命令按需执行，本入口不自动下载缺失原图。

私有 `source.local.json` 绑定进程创建时间、账号、已确认群 ID、批准目录和原型 CollectorStore。不得复制到生产配置或把密钥写入命令行。

```powershell
& data/private/replica-runtime/venv/Scripts/python.exe -X utf8 -B tools/personal_wechat_replica_cli.py inventory --config data/private/replica-runtime/source.local.json
& data/private/replica-runtime/venv/Scripts/python.exe -X utf8 -B tools/personal_wechat_replica_cli.py sync --config data/private/replica-runtime/source.local.json --mode BACKFILL
& data/private/replica-runtime/venv/Scripts/python.exe -X utf8 -B tools/personal_wechat_replica_cli.py sync --config data/private/replica-runtime/source.local.json --mode LIVE --iterations 3
```

仅后台读取；不激活、不输入、不发送。当前为有界命令，不是已安装常驻服务。失败返回非零及 UNAVAILABLE；私有 last-result 同步写失败，避免展示旧成功。原始错误不打印账号、载荷或密钥。

## 媒体与真实验证

`media --message-id <已入库图片ID>` 默认只读本地 original，校验精确分片/消息定位、批准路径、实际图片解码及哈希。损坏或截断文件保留证据并返回 MEDIA_READ_UNVERIFIED，不标记可用。此检查依赖隔离环境已有的 Pillow。`--image-tier mid|thumb` 是显式衍生图读取，不冒充原图。未在本地缓存的原图不会通过私有 CDN 或 GUI 自动补下载；音视频/附件保持引用，未声称验证下载。

### 已停用的 UIA 诊断及历史边界（2026-10-21）

本轮精简已将 `probe-accessibility` 和 `locate-image` 从采集 CLI 移除；旧私有配置中的实验开关不再接通这些命令。采集入口也不再构造桌面传输或读取前台窗口元数据。未成功的取图流程不作为日常步骤，诊断代码、保护测试、原始结果及未知操作意图仍保留，不能重新执行旧意图冒充恢复。

以下是此前实验的历史记录，不是当前操作指南：两项命令当时仅在私有配置显式启用并绑定窗口/进程后可用，不激活、不输入、不发送；定位只允许明确测试群，核对唯一群名/ID、当前聊天标题和输入区身份，以精确消息序列唯一匹配。引用卡片、重复序列、缺锚点或截断文字均不猜测，也不遍历其他会话列表。

普通 UIA 实测仅暴露空的 Qt 外层。隔离诊断加载校验哈希的上游 accessibility.py，临时修改固定进程 Weixin.dll 范围内的 Qt 无障碍布尔标志，退出时独立回读恢复；不修改系统屏幕阅读器设置。它需要额外的隔离环境 numpy/uiautomation/comtypes，不是生产依赖。异常退出仍可能无法恢复标志，这项风险尚未真实验证，因此默认关闭，不能当作纯 UIA 或已稳定生产能力。

REAL：当前客户端最小化时，临时开启无障碍后读到测试群标题、消息列表和 Image 图片控件，唯一对齐文字及原图消息成功；前台未变、标志已回读恢复。中文界面实际图片名称是 Image，不能沿用仅匹配“图片”的假设。

REAL 未通过：后台 Invoke 一次、另一次受控前台 Invoke 均未取得可验证预览窗口，状态 PREVIEW_OUTCOME_UNKNOWN；没有重复该次操作或标记取图成功。前台测试期间检测到用户新输入，未强制恢复窗口。原生传输新增有限激活、输入状态及条件释放方法，尚未实现可验收的 CLI 自动补下载。它们不是自动发送授权，也未接入生产。

用户现已要求仅后台开发：停止所有桌面操作及无障碍探测，保留未知结果证据。下一次桌面验证应先只读定位该结果，不能直接再次 Invoke。

本批 UNIT/MOCK：`python -X utf8 -B -m unittest -q tests.test_personal_wechat_accessibility tests.test_wecom_native tests.test_personal_wechat_source`，50项通过、0失败/跳过（1.157秒）。覆盖临时标志恢复及异常、越界拒绝、聊天歧义、唯一消息对齐、损坏媒体拒绝、用户切换后不抢焦点。另对已保存的真实 JPEG 离线完整解码通过（1080×1920、938192字节），不涉及客户端操作；不能替代自动取图验收。

2026-10-21：Windows 11 x64 / Python 3.13.15 x64 / Weixin 4.1.15.13。三个正式目标群本地初始仅各三条系统记录；不能据此声称覆盖历史学生问题。新“测试”群唯一 ID 已核对，读到图片和文字，原始时间分别为 13:57:12、13:57:17（Asia/Shanghai）。这些是在首次基线前已存在的真实记录，不是新 LIVE 到达验收。

新群首次图片检查为 only_thumbnail；用户在客户端加载原图后，后台成功只读取得 original，保存哈希。故**原图自动补下载尚未实现，全自动闭环尚未通过**。不把这次人工步骤隐藏为自动能力。后续网页练习仅生成内部稿，不晋升生产来源、不发 ACK 或正文、不计正式绩效。

后台采集、进程重启重复导入、原图读取等实测均未调用激活或输入，后续观测前台保持不变。首次 inventory 的前后台句柄不一致（期间外部切换），该次不能算前台保持通过。真实新 LIVE 到达、真实采集进程中断恢复及缺原图自动获取仍未验收；存储故障和回调失败恢复为匿名测试。

## 回归

采集相关八组 unittest：130 项通过、无失败/跳过（10.956 秒）。覆盖幂等、原时间、迟到/同序号、分片发送者映射、事务失败回滚、重启、缺锚点暂停及来源禁止晋升。Mock 媒体保存不能代替真实原图验证。

网页桥另增加空间名称核验：重建空间复用编号时，保留旧 PREPARING 记录；旧提交意图或结果未知仍拦截。捕获增加正文稳定窗口，结构化响应还必须通过完整解析；“朗读”出现不再单独视为正文完整。不得删除未知请求来重发。

交接 Dot：原型不启用生产；优先补齐缺原图的自动获取及来源至现有业务任务的受控连接，再验收真正的新消息与恢复。不得将独立网页练习标为完整 Workflow/Outbox 已贯通。

本次新测试群网页结果：真实执行识别、教学生成及唯一一次自动修订，仍有两个 method_visibility 疑点，状态 BLOCKED_DRAFT_CHECK。没有放宽 ANSWER 检查、再次提交修订或创建可发送 Outbox。最终13组 Python 回归162项通过（JS worker 内15项），无跳过/失败；首次合并运行的一项新增测试清理错误已修正并完整重跑。原图人工加载及来源至业务任务的缺口仍在，不能把“脚本取到答案”说成无人值守闭环。

随后真实 LIVE 复测发现：加载原图只改变消息行 `download_status`，原版按整行哈希误判题面变化。游标 v2 仅将该已实测字段排除在消息事实校验外，初次原始值仍保留；其他字段改变、锚点丢失仍暂停。旧 v1 不自动清空或重建：显式运行 `upgrade-cursors --config ...`，先 SQLite 可恢复备份，再逐条核对旧游标哈希、已入库原始行、当前稳定字段及分片身份；仅升级既有 seen 集合，不把新行当成已采集。

REAL：已为当前四群升级8个游标，旧停用测试群的2个游标不动；随后四群 LIVE 均0新增、0冲突，前台保持不变。升级前后 messages/events/message_conflicts/message_media 四表内容哈希完全一致，17条原记录保留。备份及比较结果位于原私有目录。此结果不等于新 LIVE 消息验收，也不等于图片自动补下载已实现。
