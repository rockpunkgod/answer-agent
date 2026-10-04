# Windows-MCP 执行细节

相对路径以当前 answer-agent 仓库为准；安装到全局 skills 后仍先定位业务仓库。以下引用已有实现，不是新的执行服务。

## 启动与定位

- 使用 `helpdesk/mcp_transport.py` 的一个持久 `MCPProcess` 连接；私有日志路径沿用该 run。发布副本缺默认工具环境时，仅在操作进程设置 `HELPDESK_WINDOWS_MCP_HOME` 指向实际已有安装。不要重新安装一份。树限制由 `HELPDESK_WINDOWS_MCP_MAX_TREE_ELEMENTS` 配置，截断不能证明唯一控件或完整输出。
- `DisplayInventory` 取实际索引与设备名，不能把 DISPLAY 后面的数字当成索引。先检查既有只读桌面和前台探针；页面输入的 `expected_foreground_process='msedge'`，不读个人微信或其他应用正文。
- 不默认截图。先使用当前文字控件、既定快捷键和原生窗口信息；仅无法取得可靠定位时用 `Snapshot` 的 `use_vision=true,use_dom=false,use_ui_tree=false,use_annotation=false,display=[实际索引]`。这条快路径跳过窗口枚举，所以 “No active window found” 不证明 Edge 未运行。图片可能缩小，按返回区域和图像比例换算物理坐标，复用已有 `luna_navigation`。正常控件核验用 `use_vision=false`，只把原 record 路径交程序，不把整树写回对话。
- 已可见的 Edge 标题栏使用现有 `ActivateEdgeOnDisplay`，参数仅 `loc` 和严格 `display_device`；它复核真实进程、句柄和几何。激活后独立核对原 DeepSeek 会话网址。原生 App 的短名称是模糊匹配，本机“Microsoft Edge”曾匹配到 Store；不能拿切换返回当身份凭证。
- 必要且已获本轮操作授权时，可从准确安装路径独立打开既有 Edge 的原会话网址；不是新 DeepSeek 会话。不得通过 MCP App launch 把用户浏览器绑定进工具清理进程树，不建立新 Profile 或调试端口。登录、验证码或人工接管时暂停。

## 上传与提交

使用 `helpdesk/mcp_page_contract.py` 核对当前网址、完整树和唯一编辑器，按已有 `mcp_preparation` / `mcp_generation` 控件与状态契约执行。准备、生成命令分别是 `tools.prepare_deepseek_session`、`tools.run_prepared_deepseek`；先读取本轮现有配置，不能从一个命令名推断已授权执行或会恢复未知动作。

打开选择器后确认当前文件选择状态；文件名带 `(N)` 的标准选择器可用 `alt+n` 聚焦。优先固定复制：只把本轮允许目录内、已哈希核验的文件路径写入 `Clipboard mode=set`，聚焦后 `ctrl+v`；从实际输入框 `ctrl+a,ctrl+c` 再 `Clipboard get` 核对路径全文，匹配后才确认。不得允许任意剪贴板内容或外部路径。每一步成功才继续，错误或未知会阻止后续 Enter，不把无条件 stdin 多行当成安全批处理。

本机 Windows-MCP 0.8.5 的 `Type` 必须传 `loc` 或 `label`，不支持仅凭焦点键入；没有这些参数时不得调用。可从文字控件取当前 label/点位，不需要为输入框截图。上传完成与提交前编辑器检查合并，核对文件名、数量和完成状态；不重复上传同版附件。

prompt 从既有本地文件或程序结果读取，用带当前 label/点位的 `Type` 单行键入且 `press_enter=false`，或固定的批准文本复制流程。用 `DeepSeekPage.submit_action` 在新文字快照中核对全文、编辑器焦点、会话及显示器范围；程序允许后 `Shortcut enter` 一次。先持久化提交意图，未知后只读取原页面。文件选择器与网页编辑器是不同状态，不能共用旧坐标。

## 捕获与恢复

完整页面树可直接交 `DeepSeekPage.completed_text`。树缺正文时优先从当前文字控件定位最终回复的复制按钮，点击一次，再用原生 `Clipboard {"mode":"get"}` 取得原文；文字定位也不可用时才补一张定位图。不能用 OCR、手工抄写或模型补齐 JSON。URL 可从独立页面观察或地址栏复制获得，必须和绑定会话一致。

原始树只保存到私有文件。需要操作端阅读时，程序只提取目标控件的 role、name、位置及当前窗口/阶段，按需读取完整输出；不要把几十 KB 的整树、历史回答或课程卡片灌入上下文。

只接受 `data/private/windows-mcp` 中原始 record 及相应 attempt journal；`TOOL_RETURNED`、路径一致及只读 Clipboard 参数由程序检查。副本、输入回显及操作端口头说明不能替代原件。保留 run 专属 BEGIN/END 标记并验证唯一完整边界；解析保存后不把标记发给学生。操作日志仅写完整性元数据；课堂内容和私人链接不进普通日志或 Git。

`MCPTimeout` 且 `bridge.uncertain=true` 时，通过同一连接 `poll_pending`，不关闭重启来重放。已返回的只读捕获失败可以改用上述复制路径；这不允许重发网页请求。连接无 pending 才正常退出；确认工具退出，不关闭用户独立浏览器。

本流程的既有证据边界：历史阅读题曾真实完成原文课程上传、生成及保存草稿；生产持续收题、自动群发送和实际绩效闭环仍不能据此宣称通过。按当前 STATUS 和本轮新增原证据报告 REAL、MOCK、SKIPPED 或 FAILED，不把操作 Skill 当成验收证明。
