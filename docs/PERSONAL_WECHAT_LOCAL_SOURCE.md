# 个人微信本地读取原型

隔离分支 `prototype/personal-wechat-db-20261021`，基于 main `7bd61a5`。不注册生产来源，不操作正式业务数据库、发送入口或绩效。前三个 English 群经用户确认；第四个只允许明确绑定的测试群，不能按同名自动替换。停用群保留原始记录和游标。

## 已有实现

`PersonalWeChatSource` 实现现有 MessageSource；复用 CollectorStore、SyncEngine 的事务和游标。来源明确为 `personal_wechat_local`，不会伪装成企业微信消息。Dispatcher 强制禁止 ACK、业务投影和发送回执晋升。

账号由运行进程 CFG 与目录匹配，不按最近修改时间选账号。消息身份包含账号、群、物理分片、local_id、完整类型、序号与服务端 ID；发送者映射来自所在分片。原始时间、采集时间、原始字节和媒体引用分别保存。缺身份或时间保留待核验。

每群、每模式使用现有持久化游标。完整有界快照检查所有消息分片，捕获迟到及同序号消息；提交消息和游标使用同一事务。首次 LIVE 必须接续已提交且完整的 BACKFILL。锚点消失/改变、分片换代、超过上限、读取失败均暂停，不当作空群。默认每群最多一万行，非高吞吐持续监听。

## 上游与隔离

候选：fanyuantaier/wechatauto-replica，提交 `d322c4a9b1124228820bab363e5676016f188fc6`，版本 1.2.7.0，Apache-2.0。仅加载校验过哈希的 db.py/media.py；不执行包初始化、发送、GUI 或 accessibility 内存补丁模块。不改生产依赖，使用私有独立 venv 与缓存。

使用到的数据库/媒体路径会只读访问进程内存以获得本地解密材料，并产生本机明文缓存。缓存可能物理包含该账号其他会话的分片内容，查询范围白名单不等于缓存只含白名单群。缓存/密钥/原始记录置于受限的 `data/private/replica-runtime`，不提交 Git 或写普通日志。

上游 Listener 在回调重试耗尽后仍推进水位，且队列在内存中、仅按序号推进可能漏同序号消息。已用上游真实类与匿名失败回调复现，故不使用它。上游自动切账号、忽略坏分片、降级旧缓存的路径也不启用。

## 运行

私有 `source.local.json` 绑定进程创建时间、账号、已确认群 ID、批准目录和原型 CollectorStore。不得复制到生产配置或把密钥写入命令行。

```powershell
& data/private/replica-runtime/venv/Scripts/python.exe -X utf8 -B tools/personal_wechat_replica_cli.py inventory --config data/private/replica-runtime/source.local.json
& data/private/replica-runtime/venv/Scripts/python.exe -X utf8 -B tools/personal_wechat_replica_cli.py sync --config data/private/replica-runtime/source.local.json --mode BACKFILL
& data/private/replica-runtime/venv/Scripts/python.exe -X utf8 -B tools/personal_wechat_replica_cli.py sync --config data/private/replica-runtime/source.local.json --mode LIVE --iterations 3
```

仅后台读取；不激活、不输入、不发送。当前为有界命令，不是已安装常驻服务。失败返回非零及 UNAVAILABLE；私有 last-result 同步写失败，避免展示旧成功。原始错误不打印账号、载荷或密钥。

## 媒体与真实验证

`media --message-id <已入库图片ID>` 默认只读本地 original，校验精确分片/消息定位、批准路径及哈希。`--image-tier mid|thumb` 是显式衍生图读取，不冒充原图。未在本地缓存的原图不会通过私有 CDN 或 GUI 自动补下载；音视频/附件保持引用，未声称验证下载。

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
