# ego-lite 固定网页步骤

先按安装的 ego-browser Skill 运行 `ego-browser skill`。一项用户目标只使用一个 TaskSpace；登录或人工接管时 handOff，用户确认后再接管原空间。不开新 Profile，不从其他浏览器复制登录数据，不在失败时新建空间。

项目入口：`python -m tools.run_ego_deepseek --help`。Python 会从 PATH 或本机 `.local/share/ego/active_version_dir/ego-browser.exe` 找已安装 CLI；无需重装或修改系统 PATH。

执行端固定当前 run 的已绑定会话 URL、request-id、TaskSpace/Page 和私有结果目录。上传只用原图及冻结 ANSWER 原文，文字直接用 `--question "第34题"` 或学生原话；也可从 `--prompt-file`原样读取。不上传内部 JSON，不要求 JSON 输出，不加 BEGIN/END 或其他机器标记。只向明确批准的本地文件授予上传能力，教学不能改成这份操作说明。

程序用当前页面已验证的 `textarea[name="search"]`、`input[type="file"]`、`.ds-assistant-message-main-content` 操作：核验会话和空编辑器 → 哈希核对附件 → 直接上传并等解析完成 → 填入原话、逐字核对 → 保存已有回复身份和提交意图 → Enter 一次 → 等唯一新回复及其“朗读”完成控件 → 保存自然语言原文。旧回复不会当作本次答案。DOM 变化、附件失败、登录或控制权丢失时停止；不要猜新选择器或改用盲点。

每次脚本是新 UTF-8 `.mjs` 文件，仍复用同一 TaskSpace/Page。未知提交只用原参数加 `--capture-only` 读取，不重传、重提或改编号。已 CAPTURED 时直接验证哈希并返回原文件，零浏览器调用。

回复与 CLI 输出保存在私有目录；正常只向模型返回状态、检查结论和证据路径。原生 ego 证据标 `EGO_LITE`，不能伪装成 Windows-MCP 原始 record。此入口仅获取草稿，原业务层仍负责题目版本、教学质量、实际交付和绩效；尚未接入后台自动队列。

旧标记协议仅能读取已有结果或恢复已提交请求，禁止用它建立新请求。正常任务不再额外执行旧 Windows-MCP 的准备、上下文 JSON 上传和第二套生成命令。

同一 TaskSpace/Page 由程序跨进程串行；上一请求未核验时新请求报 EGO_PAGE_HAS_UNRESOLVED_REQUEST。只恢复原请求，不删除占用文件、不换 TaskSpace 绕过。准备中断需人工核验，不自动解除。
