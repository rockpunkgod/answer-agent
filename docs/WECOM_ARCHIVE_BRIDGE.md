# 官方存档接入边界（2026-09-30）

当前实现 `helpdesk.wecom_archive`、`helpdesk.message_media` 和真正的本机 `helpdesk.wecom_sdk_bridge`：经明确授权的适配器、ctypes 原生 SDK 调用及可恢复媒体队列。**官方 SDK 已部署并在本机完成加载、导出和对象分配释放检查；尚未 Init、调用账号接口或验证当前企业账号，不能称为真实企业微信抓取已跑通。** 其余测试使用 mock 数据、fake native library、测试专用 RSA 密钥、synthetic签名契约及无权限 bridge 子进程。

## 官方文档核实

来源为直接从企业微信主站取得的 HTML，未依赖转载示例。原始证据在 `private/capability/`，`manifest.json` 记录 URL、SHA256 和获取 UTC 时间；公开文档不含当前账号返回数据。

| 官方页面 | 已核实的约束 |
| --- | --- |
| [获取会话内容](https://developer.work.weixin.qq.com/document/path/91774)（2026/01/19 更新） | `msgid` 是消息唯一标识；`seq` 首次填 0、返回从 seq+1 开始，uint64；每批最多 1000；记录获取不超过 5 天；拉取会话记录不超过 4000 次/分钟。 |
| 同上，消息格式和媒体下载 | 普通消息 `msgtime` 为 UTC 毫秒（没有统一 `senttime` 字段）；`roomid` 单聊为空。`sdkfileid` 媒体由 `GetMediaData` 分片获得，初始 `indexbuf=''`、以后用 `outindexbuf`，`is_finish=1` 才结束；单片最大 512K，媒体不超过 25000 次/分钟。 |
| [使用前帮助](https://developer.work.weixin.qq.com/document/path/91361) | 管理端设置存档范围、调用 IP、加密公钥。内部员工需被告知，外部联系人需同意。开启员工和未开启员工之间可取，双方未开启之间不可取。 |
| [开启成员列表](https://developer.work.weixin.qq.com/document/path/91614) | 使用存档应用 secret 对应 token；返回实际生效成员，超过购买人数的未生效成员不包含。范围可按成员、部门、标签设定。实际版本、付费和生效人数必须由管理员确认。 |
| [同意情况](https://developer.work.weixin.qq.com/document/path/91782) | 单聊/群聊外部联系人同意情况须分别核验，需要存档 secret 对应 token。 |

官方引用/回复目前以文本前缀及原文内容呈现，不能把前缀或解析结果冒充可靠的引用消息 ID。当前适配器保留完整 plaintext `raw_payload`，关系字段留空。切换企业日志使用 `user/time`，独立保存为 `other/system_event`；撤回消息也保留原始负载，不删除原消息。复杂合并聊天记录、表情、会议共享、多附件等原始负载保留，但第一版媒体任务仅支持 image/file/voice/video 的顶层 sdkfileid。

## 当前账号检测结论

`NEEDS_MANUAL_VERIFICATION`：未读取企业管理后台、未调用账号接口，无法判断存档是否已购买或当前学生群是否覆盖。初始盘点未发现 `WECOM_CORP_ID`、`WECOM_ARCHIVE_SECRET`、`WECOM_ARCHIVE_PRIVATE_KEY`、`WECOM_ARCHIVE_SDK_PATH` 环境变量或官方 SDK；后续已按用户指定的官方路线部署库文件到 `data/private/wecom-sdk`，并完成不含账号初始化的本机库检查，仍未配置账号。依赖库 PEM 证书不是企业存档私钥。

`NEEDS_ADMIN_CONFIGURATION`：本地默认不开启；需管理员确认企业存档授权、实际生效员工、学生群范围、外部联系人同意、调用 IP、公钥版本对应私钥、SDK 部署与私密凭据。未据此推断整个企业账号 `UNAVAILABLE`。

官方文件部署命令为 `python -m helpdesk.provision_archive_sdk --target data/private/wecom-sdk`。仅从已核实官方主站直链获取固定 Windows v3 ZIP，校验此前 ZIP SHA256 与 header hash，检查全部ZIP路径和x64 PE架构，只部署4个DLL和C头文件，拒绝覆盖不同的现有库。不运行示例程序，不加载DLL，不调用账号接口。`deployment-manifest.json` 记录每文件hash、架构、PE导入依赖和文件存在情况；Windows API-set标记只表示依赖类型，不代表实际loader验证。

当前主SDK DLL SHA256 为 `b1f73e40b66fe4c1e15573ff2d593584ea793d7ad65932ced0b49a6008abb2a9`；部署manifest的 `dll_loaded=false/account_api_called=false/FILES_PROVISIONED_NOT_EXECUTED` 是明确边界，不能当作接口可用证明。zip hash仍为 `42c056c2ba7dda38c24ba27a7bf8430b7086b551191c620d698c23129187fb97`。本轮检查了x64 Python及DLL、随包依赖和系统VC运行库文件存在，实际动态依赖解析需下阶段授权加载时验证。

随后已完成独立本机检查，结果见 `data/private/acceptance/20260930-official-sdk-local-check.json`：真实加载已部署且hash复核一致的官方DLL，核对17个导出，`NewSdk/DestroySdk`、`NewSlice/FreeSlice`、`NewMediaData/FreeMediaData` 三组对象创建释放通过，状态为 `SDK_LOADED_LOCAL_ALLOCATION_VERIFIED`。报告仍明确 `init_called=false/account_network_called=false/actual_message_pull_verified=false`。部署manifest保留文件部署时的历史状态，未将它改写成后续运行结果。

## Bridge 协议

管理员提供一个本地可执行程序命令（首项必须是绝对路径）。Python 使用 `shell=False` 启动，stdin 输入一份 JSON，stdout 必须输出一份 JSON；stderr 和失败响应不会写入应用日志。密钥只在 bridge 安全配置或继承环境中，不放进 TOML、请求负载、测试夹具或报告。bridge 应自行验证官方 SDK 来源，加载当前平台匹配的官方 DLL/共享库，实现初始化和资源释放。

请求：

```json
{"protocol":"wecom-archive-sdk-v1","operation":"get_chat_data","params":{"seq":"0","limit":1000}}
```

成功响应：

```json
{"protocol":"wecom-archive-sdk-v1","operation":"get_chat_data","sdk_code":0,"errcode":0,"result":{"errcode":0,"chatdata":[]}}
```

所有操作的响应都必须含 `protocol`、`operation`、`sdk_code`、`errcode`、`result` 五项；协议和操作必须匹配请求，`result` 必须是对象，`sdk_code` 与 `errcode` 都必须为 JSON 整数 0。布尔值 false、浮点数 0.0、字符串 "0" 或缺少字段都会被拒绝。bridge 应为没有上层 API 错误码的解密/媒体成功响应明确设置 `errcode=0`。get_chat_data 的 result 内还须带官方整数 `errcode` 和 `chatdata` 数组。

操作仅有三种：

1. `get_chat_data(seq decimal-string,limit)`：使用官方 Init/GetChatData，返回未经伪造的 SDK ChatDatas JSON。seq 在 bridge 转 uint64，不先转 JavaScript 浮点数。
2. `decrypt_message(envelope)`：按 `publickey_ver` 找正确私钥，base64 解码并按官方说明解 RSA `encrypt_random_key`，再调用 SDK DecryptData 解 `encrypt_chat_msg`，返回明文 JSON。私钥缺失/版本未知/解密错误应返回失败；不得跳过单条并推进游标。
3. `get_media_data(sdkfileid,indexbuf)`：调用官方 GetMediaData，result 为 `data_base64`、`outindexbuf` 字符串、`is_finish` 整数 0/1；SDK 非零返回码不能被当成成功。媒体分片 cursor 与消息 seq 独立。

bridge 命令配置和授权布尔标记只是配置证据，不是账号实时验证。只有管理员部署真实 bridge 并核实真实授权响应后，才能报告真实 `AVAILABLE`。Python transport 可注入 mock 以独立验证标准化、分页及数据库可靠性。

## 本机原生 SDK bridge 配置

`helpdesk.wecom_sdk_bridge` 只加载管理员指定的已有 SDK，采用 cdecl `ctypes.CDLL`，不会下载或安装原生库，不含模拟返回兜底。ABI 依据官方页面直接链接的 [Windows SDK v3](https://wwcdn.weixin.qq.com/node/wework/images/sdk_win_v3.zip) 中 `C_sdk/FinanceSdkDemo/WeWorkFinanceSdk_C.h` 核验；证据保存 `private/capability/official-WeWorkFinanceSdk_C.h`。网页工具列表未显示的 `GetSliceLen`、`GetDataLen`、`GetIndexLen` 已从官方头文件验证。二进制用地址和长度读取，不会在图片的 NUL 字节处截断。

管理员取得与 Python 进程架构及系统匹配的官方 SDK，妥善保存 DLL 及其官方依赖。Windows 从 SDK 文件同目录解析依赖；若缺依赖或架构不符，明确返回 `SDK_LOAD_FAILED`，不会启用mock。已安装 `cryptography` 可用于 RSA；未安装时会报 `RSA_DEPENDENCY_MISSING`，可按独立的 `requirements-archive.txt` 安装可选依赖，程序不会自动安装。

配置 `archive.bridge_command` 为 `["Python可执行文件的绝对路径", "-m", "helpdesk.wecom_sdk_bridge"]`，在已安装项目或可导入 helpdesk 的项目目录运行。每次 JSON 请求都会加载 SDK、初始化并在完成或失败后释放；`NewSdk/DestroySdk`、`NewSlice/FreeSlice`、`NewMediaData/FreeMediaData` 成对，native返回码非0停止本次请求。未来如需优化进程启动成本，应保持等价授权和资源边界。

bridge 继承管理员的私密环境变量（不要保存值到公开TOML或日志）：

| 变量名 | 要求 |
| --- | --- |
| `WECOM_CORP_ID`、`WECOM_ARCHIVE_SECRET` | 当前企业ID及真正的会话内容存档 Secret。 |
| `WECOM_ARCHIVE_SDK_PATH` | 官方 DLL/共享库的绝对路径。 |
| `WECOM_ARCHIVE_AUTHORIZED` | 必须由管理员明确设为字符串 `true`，程序不会自行设置。 |
| `WECOM_ARCHIVE_AUTHORIZATION_EVIDENCE` | 管理员授权/范围/同意核验记录引用，非空。 |
| `WECOM_ARCHIVE_PRIVATE_KEYS` | JSON 对象：公钥版本字符串映射到相应私钥 PEM 的绝对路径，如 `{"3":"管理员私钥的绝对路径"}`。 |
| `WECOM_ARCHIVE_PRIVATE_KEY`、`WECOM_ARCHIVE_PRIVATE_KEY_VERSION` | 只使用一个版本时可替代 PRIVATE_KEYS，需同时明确私钥文件和版本，不能猜版本。 |
| `WECOM_ARCHIVE_KEY_PASSWORD` | 可选，加密 PEM 的密码。 |
| `WECOM_ARCHIVE_TIMEOUT` | 可选，native网络超时秒数，默认25，1—120；外层bridge进程timeout应更长。 |
| `WECOM_ARCHIVE_PROXY`、`WECOM_ARCHIVE_PROXY_PASSWORD` | 可选，管理员允许的 SDK 网络代理与凭据。 |

消息密钥根据每条 `publickey_ver` 精确选择，RSA 2048/PKCS1v15 解密 `encrypt_random_key`，再把结果与密文交给官方 `DecryptData`。未知版本不跳过消息、不推进 cursor。bridge没有发送接口，native输出被隔离，响应错误只含整数码和固定错误类别。

这实现了真实 SDK 的调用路径；fake native 测试验证调用参数、uint64、错误返回、二进制NUL、资源释放及正确版号 RSA 解密，不能替代真实 DLL/账号验证。

## ACTUAL 来源证据（独立于官方原始消息）

官方 `raw_payload` 始终保持 SDK 解密明文，采集器不添加 `capture_evidence`、`fixture` 或任何自造官方字段。经管理员明确启用的原生部署会把成功 SDK 页及逐条解密响应保存为私有 sidecar 批次证据；文件按 SHA256 命名，单条消息有独立索引，记录实际 msgid、seq、原始明文 hash 和部署指纹。证据及索引在同目录临时文件完整写入、fsync后，以不覆盖已有文件的原子硬链接发布，并有跨进程锁保护；随后消息入库。数据库失败可能留下孤立证据，不会因此推进消息 cursor。相同证据重试可安全复用，重复拉取前先验证已有第一份索引、文件hash和签名；索引残缺、文件被篡改或不匹配时明确返回 `ARCHIVE_CAPTURE_INDEX_HOLD_REQUIRED`，不会静默跳过或覆盖。

默认 `archive.capture.runtime_mode="FIXTURE"`；它既不能生成 ACTUAL 凭证，也不能通过 ACTUAL ACK gate。明确启用 ACTUAL 时，factory仅接受 `[当前Python绝对路径,"-m","helpdesk.wecom_sdk_bridge"]`，须配置 `[archive.capture]` 中 `runtime_mode="ACTUAL"`、`root="private/archive-capture"`、`deployment_id` 和固定 `sdk_sha256`，并与私密环境匹配。另须由管理员明确配置：

- `WECOM_ARCHIVE_RUNTIME_MODE=ACTUAL`。
- `WECOM_ARCHIVE_DEPLOYMENT_ID`：当前部署标识。
- `WECOM_ARCHIVE_SDK_SHA256`：已核实官方 SDK 文件 hash（具体DLL的hash，区别于SDK ZIP hash）。
- `WECOM_ARCHIVE_EVIDENCE_KEY`：独立随机密钥，base64编码且解码后至少32字节；作为私密签名配置，不输出到日志、TOML或证据文件。

bridge在读取实际SDK文件且hash匹配、调用内置原生loader、SDK响应成功后，以私密 evidence key 对响应和原生部署指纹做 HMAC。fixture/fake-native loader 即使设置 ACTUAL 也会被拒绝，普通自定义mock transport不生成凭证。ACTUAL adapter验HMAC后才能保存批次证据，不能凭自填 `verified_actual=true` 过关。

`lookup_archive_capture(dict(collector_message),root)` 找到该原始消息的独立证据；`verify_archive_capture(dict(collector_message),evidence_path)` 校验部署、HMAC、LIVE模式、SDK页中msgid/seq、逐条plaintext和原始DB payload hash，并返回证据路径/hash及collector_message_id。测试 ACK 层据此冻结原消息和来源证据，发送前再次验证。BACKFILL证据不能被用作LIVE实际测试。

HMAC是**本机可信运行时的完整性凭证，不是腾讯签名或远端授权证明**。管理员及可读取私密evidence key的本机进程处于同一信任边界；证据目录与密钥必须受本机权限保护。更换SDK、部署标识或bridge代码会改变指纹，旧部署证据需保留相应可信配置才能重新核验。负向测试验证fixture拒绝、伪造标记拒绝、fake-native无法签ACTUAL及签名篡改拒绝；正向测试使用隔离测试密钥人工构造的 **synthetic签名响应契约**，验证保存→索引→校验、多条消息、原payload不变、相同重试复用和后续重试保留第一份、索引发布中断后恢复。它们不来自真实SDK拉取，不进入业务DB，临时目录在测试后清理，不能解释为当前账号的ACTUAL成功证据。

## 媒体恢复和边界

消息和媒体任务先在同一数据库事务入库，下载失败仅把任务记为 `FAILED`，消息及源 cursor 保留。`run_pending()` 会再次尝试失败任务。分片先 fsync，再事务提交 media index 和 confirmed bytes；重启会截掉尚未提交的尾部。OS 进程锁防止两个 worker 写同一临时文件，进程死亡自动释放。

目录按完整发送日期创建；未知发送时间时仅使用采集日期安排文件目录，不伪造发送时间。原文件名只保存元数据，文件路径由 SHA256 生成，并检查路径始终在配置根目录内。下载验证大小和官方 MD5（存在时），保存 SHA256。复用下载前要求同一 source_name 的相同 sdkfileid，并再次验证本地大小/hash；hash 不参与消息去重，两名学生的独立消息仍保留。

默认每文件 100 MiB、最多 10000 分片，每次最多 20 个任务，可配置。文件保存 `.bin`，OCR 应读取实际文件内容识别格式；采集器不调用 OCR。历史窗口只覆盖官方允许范围；停机超过 5 天可能永久缺消息，需要人工核验，不能承诺几个月历史补拉。

验证命令：`python -m unittest tests.test_wecom_archive -v`。这些结果仅证明 mock 场景及本地 bridge 协议，不证明真实企业微信 SDK或账号可用。

## 只读账号能力检查

运行 `python -m helpdesk.archive_capability_probe --config config/archive_probe.example.toml --output private/capability/account-probe.json`。检查先验证全部前置条件；缺企业 ID、存档 Secret、SDK 文件、私钥文件或明确管理员授权时，返回 `NEEDS_ADMIN_CONFIGURATION`，不发任何账号请求。

管理员私密环境应提供 `WECOM_CORP_ID`、`WECOM_ARCHIVE_SECRET`；`WECOM_ARCHIVE_PRIVATE_KEY`、`WECOM_ARCHIVE_SDK_PATH` 必须为已有文件的绝对路径，并明确配置 `bridge_command`。只有授权 flags、证据引用以及 `allow_network_read_only=true` 全部满足，才发起官方 gettoken、有效授权成员查询和配置的外部联系人同意情况查询。报告只保存变量存在与否、错误码、人数、同意统计和响应 digest；不输出 token、Secret、企业/成员/群 ID、文件路径或服务端错误文本。SDK bridge 不在此检查中执行，成员接口成功也不意味着真实消息拉取成功。

有效成员查询成功只标记 `account_permission=PERMITTED_MEMBERS_VERIFIED`；该名称不表示学生群或采集器可用。`collector_readiness=NOT_VERIFIED`、`sdk_data_pull_verified=false` 始终保留。所有配置的只读查询成功后设置 `read_only_checks_passed=true`，整体状态仍为 `NEEDS_MANUAL_VERIFICATION`。退出码 **3** 表示只读权限检查通过、真实 SDK 数据采集仍未验证；退出码 **2** 表示前置配置不足或某个查询失败。本只读检查器不返回退出码0，自动化不能将其当作采集器启动就绪检查。若成员查询成功而后续客户同意查询失败，保留成员证据但退出码仍为2。

陈旧 seq 是否过期不能仅从空批判断；适配器没有猜测官方错误码来转换 CursorExpired。超过 5 天停机或远端历史消失需要独立覆盖度审核；空批成功不能证明没有漏历史。明确的外部审核结果可要求 operator review，不能自动回到 0 或擅自推进历史完整性状态。bridge 应在服务端按企业维护总调用限频，Python 本地节流只约束本实例。
