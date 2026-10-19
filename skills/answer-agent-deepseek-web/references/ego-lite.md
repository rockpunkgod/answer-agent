# ego-lite 固定网页步骤

先按安装的 ego-browser Skill 运行 `ego-browser skill`。一项用户目标只使用一个 TaskSpace；登录或人工接管时 handOff，用户确认后再接管原空间。不开新 Profile，不从其他浏览器复制登录数据，不在失败时新建空间。

项目入口：`python -m tools.run_ego_deepseek --help`。Python 会从 PATH 或本机 `.local/share/ego/active_version_dir/ego-browser.exe` 找已安装 CLI；无需重装或修改系统 PATH。

执行端固定当前 run 的已绑定会话 URL、request-id、TaskSpace/Page 和私有结果目录。上传只用原图及冻结 ANSWER 原文，文字直接用 `--question "第34题"` 或学生原话；也可从 `--prompt-file`原样读取。不上传内部 JSON，不要求 JSON 输出，不加 BEGIN/END 或其他机器标记。只向明确批准的本地文件授予上传能力，教学不能改成这份操作说明。

程序用当前页面已验证的 `textarea[name="search"]`、`input[type="file"]`、`.ds-assistant-message-main-content` 操作：核验会话和空编辑器 → 哈希核对附件 → 直接上传并等解析完成 → 填入原话、逐字核对 → 保存已有回复身份和提交意图 → Enter 一次 → 等唯一新回复及其“朗读”完成控件 → 保存自然语言原文。旧回复不会当作本次答案。DOM 变化、附件失败、登录或控制权丢失时停止；不要猜新选择器或改用盲点。

每次脚本是新 UTF-8 `.mjs` 文件，仍复用同一 TaskSpace/Page。未知提交只用原参数加 `--capture-only` 读取，不重传、重提或改编号。已 CAPTURED 时直接验证哈希并返回原文件，零浏览器调用。

回复与 CLI 输出保存在私有目录；正常只向模型返回状态、检查结论和证据路径。原生 ego 证据标 `EGO_LITE`，不能伪装成 Windows-MCP 原始 record。此入口仅获取草稿，原业务层仍负责题目版本、教学质量、实际交付和绩效；现有后台队列已支持显式选择 ego_lite，真实联调仍未验收。

旧标记协议仅能读取已有结果或恢复已提交请求，禁止用它建立新请求。正常任务不再额外执行旧 Windows-MCP 的准备、上下文 JSON 上传和第二套生成命令。

同一 TaskSpace/Page 由程序跨进程串行；上一请求未核验时新请求报 EGO_PAGE_HAS_UNRESOLVED_REQUEST。只恢复原请求，不删除占用文件、不换 TaskSpace 绕过。准备中断需人工核验，不自动解除。

已有 ego 冻结 run、原消息清晰度确认及本机版本校验齐全时，可用 `python -m tools.run_ego_answer --help` 回流原 Workflow/Outbox。不得把旧 MCP run 改名为 ego；新任务在首次生成后绑定真实会话链接；后台须显式选择 ego_lite，生产默认配置本轮未更改。原始捕获与 ANSWER 检查不通过时停下，不把草稿计作实际交付。

本地已审核任务可经 `tools.operator_task freeze --ego-space <编号> --ego-page p1` 冻结为 ego run；空间、页面和结果目录之后不可换，旧 MCP run 不可改名。冻结不打开网页；原队列可直接上传和生成，不再等待旧 MATCH/PREPARATION；不把“冻结成功”当作网页完成。

2026-10-19 用户确认：默认原图＋ANSWER原文、题号/学生原话，一次自然语言取答。新 ego run 固定 DIRECT_ORIGINAL；取消旧网页 MATCH、上下文 JSON 上传和标记协议依赖，保留本机原消息审核、版本、选项、教学检查和原 Outbox。不要为了符合旧两阶段文档恢复多一次模型调用。

新会话 Enter 后先记录真实 URL，再等待正文；若首次 Enter 未确认且没有 URL 记录，停在 NEW_SESSION_IDENTITY_REQUIRES_MANUAL_CHECK，不从当前页面猜学生归属。已记录 URL 的原请求可只读恢复，禁止回首页重发。

普通追问：执行端在冻结时引用同学生/同题/同版本/同会话的首次真实上传证据；程序核验通过才省略相同教学与原图。只发本轮原话和实际交付正文，不重复题干，不把旧稿当成已发。原捕获改变、明确改选或实际交付附件尚未接通时停下核对；连续追问不新建空间、不重传全套材料。
