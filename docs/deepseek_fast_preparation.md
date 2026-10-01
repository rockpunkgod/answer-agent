# DeepSeek 快速准备

新会话使用 `config/deepseek-preparation-controls.example.json`：
`preparation_mode="FAST_UPLOAD_THEN_GENERATE"`。控件名称仍须按实际页面核验，示例名称不表示现场控件已验证。

先上传课程 Skill 文件，再上传题目附件。仍核对冻结文件哈希、独立会话、页面和附件就绪；不发送任何单独的模型读回请求。准备结果为 `ATTACHMENTS_READY_REQUIRES_SOURCE_REVIEW`，明确 `model_readback_performed=false`，此候选本身不能生成。

使用现有 `tools.review_deepseek_preparation` 工具和已有人工源审核内容继续：课程正文摘录、原题图哈希及题干核验，或原题文本完整字段、冻结指纹及原始来源证据。无需让模型再次读回。快速批准类型为 `ATTACHMENTS_READY_SOURCE_REVIEWED`；它不冒用旧的正文读回批准。该工具兼容的 `--readback-evidence` 参数在快速模式写入的是附件就绪页面证据，批准记录使用 `readiness_evidence/readiness_sha256`，不会称作模型读回证据。

审核后的记录可交给现有 `tools.run_prepared_deepseek`。一次生成请求要求 DeepSeek 实际读取 Skill、按 Skill 核对题目与冻结题干和选项，然后解答；附件无法读取时停止猜测。不会自动提交第二次请求，失败或未知保留人工复核。现有 ACK 已确认门槛、会话隔离、最终答案审核与发送规则继续适用，快速准备不批准群发送。

旧严格路径保留：`preparation_mode="STRICT_READBACK"`（缺字段默认此值）；`material_order="COURSE_THEN_QUESTION"` 先课程读回再题目读回；旧配置缺 material_order 时保留 `ALL_THEN_READBACK`。已有冻结材料与批准记录无需改写。

专项测试 `tests/test_mcp_fast_preparation.py` 用模拟桌面验证顺序上传无模型读回、未审核拒绝、来源审核后实际 adapter 单次提交、缺附件及未知上传拒绝、源文件与证据改变拒绝。它不表示真实桌面或群自动收到已运行。
