# 企业微信英语答疑与绩效统计

个人自用的 Windows 本机工作台：保存消息原文和附件、核对学生题面、查看 DeepSeek 网页生成的讲解，并按天导出绩效草稿。核心使用 Python 标准库、SQLite 和本地文件，不要求部署 Worker 集群、消息中间件或额外数据库。

**当前可以运行本地工作台；持续监听真实群聊、从持久队列自动执行网页及人工发送后的交付回流尚未贯通。历史单题的真实操作不代表完整自动答疑已上线。**

## 启动

需要 Windows 和 Python 3.11+。在仓库目录打开 PowerShell：

```powershell
.\run_current_demo.ps1
```

打开 <http://127.0.0.1:8767/>。没有采集配置时仍可查看本地台账和日报，页面明确显示未连接，不会自动使用模拟题。已有同模式服务会直接复用，不重复启动或自动恢复暂停的采集。

只打开工作台，暂不启动原文导入：

```powershell
.\run_current_demo.ps1 -NoAutoCollect
```

原生文字导入的配置示例是 [config/collector.windows-native.example.toml](config/collector.windows-native.example.toml)。按本机实际目录、身份与阶段填写后，保存为 `data/private/windows-native-demo/collector.local.toml`，或通过 `-CollectorConfig` 指定配置。这个适配器导入已保存的 Windows-MCP 原文，**不会自行监听客户端、推断学生时间或发送“收到”**。

原消息题面入口使用配置中的 `[stage] source_review_manifest` 指向审核通过的本地教学包。新的教学来源仅使用 [ANSWER](https://github.com/rockpunkgod/ANSWER.git)；仓库不包含私人课程材料、登录状态或模型密钥。实际网页、模型或 Windows-MCP 操作才需要相应工具和登录，不是打开台账的依赖。

## 教学来源与只读预览

[config/teaching-source.toml](config/teaching-source.toml) 固定 ANSWER 提交 `57159d7a8b03a0743225ed27f3f1e6128bbcd45d`。不自动拉取、切换提交或改写教学文件。教学目录存在未提交改动时停止，保留原文件供本人审核；Windows 的 LF/CRLF 换行差异允许核对，预览使用 Git 提交的原始字节。

本机已有该版本的 ANSWER 检出目录，可在业务仓库中运行：

```powershell
python -X utf8 -B -m tools.prepare_teaching_bundle --question-type 阅读理解 --repository "E:\作业帮\.tools\ANSWER-reference"
```

这是**本地原文预览**。工具按 ANSWER 的 README 分工选取当前题型完整文件，合成一个教学输入文件，并保存源文件、提交和哈希；同版本同题型复用缓存。语法使用旧版语法 Skill，写作使用 `gaokao-writing`，不混用主 Skill 内的同名题型文件。结果留在忽略的 `data/private/teaching-bundles/`。

当前 ANSWER 客观题目录缺少 README 和 Agent 定义要求的 `gaokao-english/scripts/check_lesson.py`，预览会明确报告缺项；不借用语法目录的脚本。语法检查脚本虽然存在，本轮未执行课程定位与修稿流程。新预览包均保持 `answer_generation_allowed_by_course=false`，不能通过修改清单把它提升为已验证生成权限。

本轮没有迁移运行中的 `source_review_manifest`、启用新消息渠道或上传教学文件。旧包保留原有历史记录，不作为本次 ANSWER 固定版本验收的证据；新的教学生成需先完成对应课程检查与受控样例验证。停止使用预览命令即可保留当前运行配置；不要用清空数据或覆盖教学文件回退。

## 原题候选与人工确认

原题核对是可选的内部入口，默认关闭，不增加发送权限。沿用现有 `Question`、`reference_candidates` 和审计表，**本次没有数据库迁移**。`domain.compare()` 是唯一比较规则：缺少材料、题干、四个选项或必要图表时为 `INCOMPLETE`；题号调整和无歧义选项换序可以对应，NOT/EXCEPT、数字或条件变化不能被文字相似度覆盖。当前精确映射支持四选项客观题，其他题型继续人工核对。

无需联网或业务数据库的匿名演示：

```powershell
python -X utf8 -B -m tools.lookup_reference --demo
python -X utf8 -B -m tools.crawl_reference --self-test
```

演示只使用自建网页样例，报告中的“一致”表示候选逐字段对应，不表示已确认或已使用真实网站。实际任务使用当前题目 ID、版本和上下文版本，查询失败、访问受限和超时分别记录，不显示成“找不到原题”。完整学生题面仍能独立答疑。

启用前将 [配置示例](config/reference-lookup.example.toml) 复制到忽略的 `data/private/reference-lookup.local.toml`，设置 `enabled=true`、保留 `shadow=true`。在没有同端口旧服务时启动：

```powershell
.\run_current_demo.ps1 -ReferenceLookupConfig .\data\private\reference-lookup.local.toml
```

已有服务不会被此参数自动重启；另选端口或先正常停止自己启动的服务。工作台“原题检索与版本核对”选择已有题目、检索原因及必要候选网址，查看学生版与参考版、来源、时间、哈希、差异及选项映射，填写实际核对人和依据后确认或拒绝。界面不显示内部置信度或调试日志。

候选状态为 `DISCOVERED → COMPARING → MATCHED_CANDIDATE → CONFIRMED / REJECTED`，明确不一致也会被规则直接拒绝。不完整题面不进入确认。**Shadow 可以保存人工确认，但不向答疑提供参考。** 经样例验收后显式设置 `shadow=false`，在工作台确认候选或点击“用于答疑”，才允许符合来源保存许可的参考进入当前题目的冻结输入。学生原题、首次提问时间、实际交付和绩效不因此改变。

确认后的来源、题面、版本、比较结果和依据随同题面文本准备到 DeepSeek；带原图时也准备这份文本。外部答案和解析不用于证明同题，也不进入教学输入。ANSWER 仍是唯一教学来源，学生选项字母优先。更正待核对、内容改变、版本过期或候选被拒绝时，相关旧输入和答案在上传、批准或发送前停止。普通追问继续使用同一题目版本；重复检索、确认和重启不重复建候选。

抓取入口复用 `LocalProvider`（本人放入获准目录的 UTF-8 文件）和 `HttpProvider`（准入、robots、公开地址、固定连接地址和完整超时检查）。`crawl_reference` 不再启动原有未经同等防护验证的浏览器路径；Crawl4AI 浏览器适配暂缓，不要求安装它。提供已有任务信息时，此工具仅建立候选并比较：

```powershell
python -X utf8 -B -m tools.crawl_reference --config .\data\private\reference-lookup.local.toml --local-file demo.html --db .\data\native-demo.db --question-id <已有题目ID> --question-version <当前版本ID> --context-revision <当前上下文版本>
```

本地文件必须位于配置的 `fixture_root`，不是任意文件路径；不要将私人聊天记录放入参考目录。真实网址还需 `network_enabled=true` 及该来源单独批准。Brave 搜索密钥仅从配置指定的环境变量读取，不写入配置或日志；本轮未调用付费搜索。七个列出的题库来源默认未批准自动抓取，完整能力与准入状态见 [当前进度](docs/STATUS.md)。

临时第三方正文只保留在有限时长的内存中；来源明确允许保存业务参考时，必要候选题面才写入现有本机表。关闭 `enabled` 可以停止新增核对，不删除记录；已经用于答疑的参考需先在工作台拒绝以撤销使用，然后关闭开关。不要回退到会自动信任旧参考记录的程序版本；历史未确认记录需要重新比较，不静默升级。

## 日常使用

1. 启动后检查采集状态，先保存原消息、原始发送时间及图片。缺少可靠身份或原时间时补充证据。
2. 在“学生题面确认”核对题面是否清楚。确认后进入同一持久队列；网页执行者未连接时显示等待，不伪造生成。已有 DeepSeek 讲解可以展开和复制完整原文。
3. 按当前阶段配置审核与交付。阶段支持自动“收到”及人工最后发送答案，但真实自动闭环仍待接通。发送前检查任务绑定的群、学生和有效版本；结果不确定时暂停核验，不自动重发。历史题练习保持禁发。
4. 按日期查看统计草稿或下载日报。输出白天综合、语法听力、夜间答题三项数量，以及明细和待核对记录；覆盖不足时明确区分本地小计和未知的正式总数。

人工接管前在工作台停止采集和发送。恢复只解除暂停，不会开启已禁用的发送目的。Ctrl+C 停止自己启动的服务；重启使用同一数据库保留任务与记录。

默认业务数据库是 `data/native-demo.db`，原消息库是 `data/native-messages.db`；有采集配置时沿用其中的数据库。`data/`、附件、浏览器登录状态和本机配置不进入 Git。数据库和重要附件需做可恢复备份；不要通过清空数据库解决异常。

## 业务约束

- 原始发送时间与采集时间分开；重复采集不重复处理。
- 同一学生多题按明确归属关联，学生版本与参考版本分开，选项换序使用学生字母。更正后的过期答案禁止发送。
- 生成、复制、“收到”及发送不确定都不等于实际完成交付。
- 答疑与绩效复用确认后的题目归属，LLM 提出归并建议，程序核验并根据计量台账计算最终数量；追问、补图、纠错不自动增加数量。
- 夜间只看首次独立学生提问的原始时间：23:00（含）至次日07:00（不含），所有适用题型按篇；07:00前归前一业务日。白天语法、听力按小题，白天综合按篇；完成时间和时效独立。单价、零散题归并等未确认规则不自行推断。
- 工具、文件目录及目标受控；验证码、桌面不可用或人工接管时暂停。桌面串行，当前实践仅允许已验证的屏幕2，不重放过期坐标。

规则变更保留在 [绩效规则记录](docs/PERFORMANCE_RULE_HISTORY.md)。可用范围和缺口见 [当前进度](docs/STATUS.md)。

## 测试

```powershell
python -X utf8 -B -m unittest discover -s tests -v
```

业务核心无第三方必需依赖。浏览器和 Windows 图像测试按需安装：

```powershell
python -m pip install -e ".[browser,windows-test]"
python -m playwright install chromium
```

这些测试覆盖匿名数据库、模拟适配器、本地 HTTP 和无界面浏览器；通过不代表真实企业微信发送或全量历史统计。无法创建符号链接时如实 SKIPPED，不计通过，也不要求管理员运行整个项目。

本轮源码审计、官方渠道覆盖结论和验证结果见 [当前进度](docs/STATUS.md)。官方 CLI/SDK 尚未接入，不是启动工作台的依赖。

独立模拟入口是 `.\run_demo.ps1`，端口8765，模拟数据与正式记录分开。
