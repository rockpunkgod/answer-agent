# ego-lite 固定网页步骤

先按安装的 ego-browser Skill 运行 `ego-browser skill`。一项用户目标只使用一个 TaskSpace；登录或人工接管时 handOff，用户确认后再接管原空间。不开新 Profile，不从其他浏览器复制登录数据。用户已长期授权：本项目空间确认丢失时可重建一个空间，不再重复询问；遵循主 Skill 的原请求核验和防重放要求，普通报错仍复用现有空间。

项目入口：`python -m tools.run_ego_deepseek --help`。Python 会从 PATH 或本机 `.local/share/ego/active_version_dir/ego-browser.exe` 找已安装 CLI；无需重装或修改系统 PATH。

执行端固定当前 run 的已绑定会话 URL、request-id、TaskSpace/Page 和私有结果目录。正常两阶段任务由现有自动答疑队列调度，不手工拆成两个无关联的 CLI 请求。第一阶段程序构造原图、实际候选及结构化核验提示；第二阶段上传冻结 ANSWER 原文，使用学生原话/题号和核验结果，输出自然语言教学稿。只向明确批准的本地文件授予上传能力，教学不能改成这份操作说明。

程序用当前页面已验证的 `textarea[name="search"]`、`input[type="file"]`、`.ds-assistant-message-main-content` 操作：核验会话和空编辑器 → 哈希核对附件 → 直接上传并等解析完成 → 填入原话、逐字核对 → 保存已有回复身份和提交意图 → Enter 一次 → 等唯一新回复及其“朗读”完成控件 → 保存自然语言原文。旧回复不会当作本次答案。DOM 变化、附件失败、登录或控制权丢失时停止；不要猜新选择器或改用盲点。

每次脚本是新 UTF-8 `.mjs` 文件，仍复用同一 TaskSpace/Page。未知提交只用原参数加 `--capture-only` 读取，不重传、重提或改编号。已 CAPTURED 时直接验证哈希并返回原文件，零浏览器调用。

回复与 CLI 输出保存在私有目录；正常只向模型返回状态、检查结论和证据路径。原生 ego 证据标 `EGO_LITE`，不能伪装成 Windows-MCP 原始 record。此入口仅获取草稿，原业务层仍负责题目版本、教学质量、实际交付和绩效；现有后台队列已支持显式选择 ego_lite，真实联调仍未验收。

旧标记协议仅能读取已有结果或恢复已提交请求，禁止用它建立新请求。正常任务不再额外执行旧 Windows-MCP 的准备、上下文 JSON 上传和第二套生成命令。

同一 TaskSpace/Page 由程序跨进程串行；上一请求未核验时新请求报 EGO_PAGE_HAS_UNRESOLVED_REQUEST。只恢复原请求，不删除占用文件、不换 TaskSpace 绕过。准备中断需人工核验，不自动解除。

已有 ego 冻结 run、原消息清晰度确认及本机版本校验齐全时，可用 `python -m tools.run_ego_answer --help` 回流原 Workflow/Outbox。不得把旧 MCP run 改名为 ego；新任务在首次生成后绑定真实会话链接；后台须显式选择 ego_lite，生产默认配置本轮未更改。原始捕获与 ANSWER 检查不通过时停下，不把草稿计作实际交付。

本地已审核的新验收任务可经 `tools.operator_task --database <原库> freeze <原有参数> --ego-space <编号> --ego-page p1 --ego-policy MATCH_THEN_TEACH` 冻结；具体参数用该命令的 `--help` 查询。自动入口配置 `backend=ego_lite, ego_policy=MATCH_THEN_TEACH, space_id=<已有空间>, page=p1`，复用原 ReferenceLookupConfig 和 AutomaticAnswerConfig。空间、页面、策略和结果目录随后不可换。冻结不打开网页，不能当作网页完成。

最新冻结验收标准覆盖2026-10-19的一次取答约定：新验收使用 MATCH_THEN_TEACH，首次独立问题必须核验后教学；旧 DIRECT_ORIGINAL 保持原冻结策略，仅用于原任务恢复，不能当成两阶段验收证据。两阶段都沿用原 queue/run/receipt/Outbox，无需旧 Windows-MCP 的 PREPARATION 或标记协议。捕获后中断由原队列恢复，SUBMITTED/UNKNOWN 不重复提交。

新会话 Enter 后先记录真实 URL，再等待正文；若首次 Enter 未确认且没有 URL 记录，停在 NEW_SESSION_IDENTITY_REQUIRES_MANUAL_CHECK，不从当前页面猜学生归属。已记录 URL 的原请求可只读恢复，禁止回首页重发。

普通追问：执行端在冻结时引用同学生/同题/同版本/同会话的首次真实上传证据；程序核验通过才省略相同教学与原图。只发本轮原话和实际交付正文，不重复题干，不把旧稿当成已发。原捕获改变、明确改选或实际交付附件尚未接通时停下核对；连续追问不新建空间、不重传全套材料。

联系人联调遇到“明确未提交但已有草稿”，使用原 `tools.dispatch_test_answer` 参数加 `--resume-draft`，不要手改Outbox或重新粘贴。程序只接受有未提交证据的FAILED测试副本，复核原源题/版本/目标、完整草稿并有限恢复；UNKNOWN只读`--inspect`，已核验成功不再操作。正常网页任务不执行这个测试发送入口。

隔离联系人测试的普通追问准备可用 `tools.operator_task --database <原库> followup --parent-task <明确上一任务> --message-file <UTF-8原话> --request-id <稳定编号>`，只创建草稿，复用原题/选项/附件，不手拼题目JSON，不调用模型、不自动审核/生成/发送。新题、更正或归属不明确时不能用这个快捷入口代替判断。
