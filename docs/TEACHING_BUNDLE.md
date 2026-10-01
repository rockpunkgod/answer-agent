# 本地课程教学包

`python tools/prepare_teaching_bundle.py --question-type 阅读理解` 只在本机生成课程快照。生成结果位于 `data/private/teaching-bundles/`，其中 `manifest.json` 记录每个原始文件与快照的绝对路径、SHA-256、字节数、已审阅课程策略版本、课程覆盖状态，以及可传给 `Workflow(teaching_paths=...)` 的显式文件白名单。使用前可用 `helpdesk.teaching_bundle.verify_bundle(manifest_path)` 复核原件与快照哈希、同源性和字节数。已审阅 Skill 或模块发生变化时，工具会拒绝沿用旧策略，须先重新核对课程边界。

普通阅读答疑包仅含 `SKILL.md`、交付约定、路由、阅读理解模块和证据缺口说明。其他题型模块不会混入。学生询问课程依据、版本差异或课程原名时，显式选择 `--request-kind course_basis` 才加入 `course-evidence.md`；普通答题包的清单会标出它是条件依赖。每个文件须是 Skill 根目录内的普通 UTF-8 Markdown 文件；缺文件、未知引用、越目录引用、符号链接、文件超出工作流大小限制或哈希变化均会拒绝。

题型必须显式选择。听力、综合训练尚未激活，读后续写存在课程内部冲突，不能生成答题包；如只需说明课程覆盖情况，可显式选择 `--request-kind coverage_notice`。完形、语法填空和应用文只有 Skill 已列出的局部方法，清单保留该限制。题型选择不代替对实际题面的核验。

本工具不调用 DeepSeek、不上传、不操作网页或桌面，也不发送给学生。现有 `Workflow` 仍只演示模拟生成；输出白名单是待后续人工核对的本地准备物，不表示真实模型已经接收课程文件。
# ANSWER 课程资料后台检索

## 仓库后续分工更新

用户再次提供仓库后，已快进参考副本到 `57159d7a8b03a0743225ed27f3f1e6128bbcd45d`。新增 README 和三份 Agent 定义明确：阅读、七选五、完形及其方法问答走 gaokao-english；应用文、读后续写走 gaokao-writing；语法填空使用 gaokao-english-formal-backup-20260919-01；课程查证走 kaiming-english-qa。这是新提供的明确分工，取代此前仅凭目录名称将旧版视为纯备份的解释。现有教学包实现尚未迁移到这套分工，不能据此宣称完成接入。

依赖核对发现：客观题 Agent 要求的 gaokao-english/scripts/check_lesson.py 未包含在该提交中；同名脚本只在语法填空所用旧版目录存在。不能自动将旧版脚本当作新版检查器使用或将缺失检查记为通过。新增文件仅在项目独立副本内同步，没有安装到全局 Agent 目录或操作桌面。

工作流支持 `Workflow(store, teaching_manifest=manifest_path)`：每次开始生成时重新校验清单、原件和快照，只接受按已审阅规则允许答题的包，并把课程版本与题型写入生成输入。不能同时传入 `teaching_paths`。这仍是现有模拟生成工作流，不代表真实DeepSeek已接入；显式文件路径入口保留供原有模拟测试使用。

清单校验不会信任其中自报的允许生成、覆盖范围或审核状态：这些字段必须与本地已审阅规则一致。覆盖说明包及未审核自定义来源不能靠改字段取得答题权限。当前只验证显式选择的课程包，题面自动识别与课程路由仍需独立验证。

用户提供的 `https://github.com/rockpunkgod/ANSWER.git` 已作为独立参考副本保存到 `.tools/ANSWER-reference`，当前核对提交为 `ce5f8f5808cfa192146893d8756ddc936311d67c`。没有覆盖共享技能或将备份目录当作当前课程。其 gaokao-english 的13份文件与本机正文一致，原始字节哈希因换行不同而不同，既有快照与哈希策略保持原样。

新增只读检索入口：

```powershell
python tools/search_course_library.py 三化法 --limit 3 --output data/private/course-search.json
```

检索校验包内原件哈希，不跟随目录中记录的旧电脑绝对路径，返回命中上下文、文本行定位、原件与文本哈希和提取警告。行号是提取文本行号，不冒充PDF页码。图像补录独立标记为转述，精确引用仍需回图。命中只用于定位，不能取代完整上下文、原件图表检查或课程版本核对，也不会自动提交DeepSeek。

2026-09-30 实测：78份目录记录的文本及原件均存在，原件哈希全部匹配；三化法检索16处命中，按上限返回3处。五项离线回归测试通过，覆盖来源路径、原件变更、图像补录、上下文与查询约束。

独立 gaokao-writing 与 gaokao-english 的续写规定存在待核对差异：前者提供十句五定写作/批改，后者将名称及PTSD版本冲突列为不能直接生成的原因。当前未自动替换原课程或解除既有停止条件。课程检索入口是证据获取能力，作文路由和真实网页交付尚未完成验证。

