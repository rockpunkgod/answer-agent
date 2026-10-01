# 每日答题统计草稿

本地报表从持久化计量台账生成，统计范围自 2026-09-17 起涵盖全部有证据的群聊，包括已退出群聊；群聊历史覆盖不足时显示待核验，不能把未检索到的记录当作零。夜间归类依据是计量单元首次独立提问的企业微信原始发送时间；23:00（含）至次日 07:00（不含）提出的已确认材料题统一计 1 篇。完成时间单独显示，不参与夜间判定或提问业务日归属。夜间时效另列，不由夜间类别推导。

原始绩效 PDF 和机构 Excel 填报模板尚未在仓库中找到。当前 Excel 是自拟模板，包含“每日汇总”“答疑明细”“待核对清单”，未确认单价不会被当作收入。报告中的原始消息 ID、原始时间来源、交付证据、核验依据、归并理由、规则版本可供追溯。Excel 的带时区时间以文本保留 ISO 原值，避免 Excel 丢失时区偏移。

最新确认的日报默认按学生**首次独立提问业务日**归属：当地时间 07:00（含）至次日 07:00（不含）归同一日期。例如 9 月 29 日 23:10 与 9 月 30 日 06:59 的新提问都归 9 月 29 日，9 月 30 日 07:00 的新提问归 9 月 30 日。追问不重置原计量单元的提问时间。实际完成时间、完成自然日、核准时间与时效分别显示和统计。旧版按完成日草稿可以用 `--attribution completion_date` 复现；改变归属规则时同一老师的日报进入同一修订链，已提交版默认只得到重算预览和差异。

在仓库根目录运行以下命令。所有文件都只写到指定本地目录，不会发送给群聊或机构。

```powershell
python -m tools.performance_report --db data/your-local-helpdesk.db --teacher "老师姓名" generate 2026-09-30 --xlsx
python -m tools.performance_report --db data/your-local-helpdesk.db --teacher "老师姓名" versions 2026-09-30
python -m tools.performance_report --db data/your-local-helpdesk.db --teacher "老师姓名" catch-up 2026-09-17 2026-09-30 --xlsx
```

如需调整完成/核准活动的自然日截止，可在子命令前增加 `--activity-cutoff 20:00:00`。这不改变已确认的学生提问业务日 07:00 换日时刻；夜间分类和夜间窗口日期仍独立记录。报表里的 `reporting_cutoff` 读取持久化提问业务日规则，`activity_day_cutoff` 单独记录完成/核准活动截止时刻。

确认某版已经实际提交后，可记录负责人及提交证据；此入口只写本地记录，不执行提交：

```powershell
python -m tools.performance_report --db data/your-local-helpdesk.db --teacher "老师姓名" mark-submitted 2026-09-30 1 --actor "负责人" --evidence "机构表格提交记录编号"
```

复制 `config/performance_report.example.toml` 到本地私有配置并填写真实路径后，定时任务每日运行：

```powershell
python -m tools.performance_report_schedule --config config/performance_report.toml
```

可以在 Windows 任务计划程序中添加每天 08:00 的任务，程序为 Python，参数为上面的 `-m ...`，起始目录为本仓库。这个脚本会在前一提问业务日于 07:00 结束后补生成草稿；即使数据库快照已保存而 Excel 导出失败，下次也会补齐本地文件。仓库不会自行注册系统计划任务。`generate_at`、`activity_day_cutoff`、业务时区、老师、输出目录和补生成起点可配置。提问业务日 07:00 截止以持久化规则为准。

演示文件位于 `outputs/performance-demo/演示日报.xlsx`，与真实业务记录无关。运行 `python -m tools.performance_report_demo` 可重新生成。该演示的 9 月 29 日日报中，22:50 提问、23:20 完成的阅读属于常规；23:10 提问、次日 10:00 完成的语法填空仍归 9 月 29 日并计夜间 1 篇，时效保留待核验。没有原始发送时间的听力材料进入待核对清单。

## 本机每日自动草稿已启用（2026-10-01）

唯一计划任务 `EnglishHelpdesk-DailyPerformanceDraft` 已注册并启用，每15分钟在后台检查到期业务日；当前用户登录期间运行。配置 `config/performance_report.local.toml` 已对齐原生采集工作台的 `data/native-demo.db`，08:00补生成前一已结束提问业务日。任务使用真实Python313旁的pythonw；XLSX导出的PowerShell和Node子进程在Windows也隐藏控制台。任务不会操作企业微信桌面、发送消息或向机构提交报表。

每次作业先校验并保存原文包，源包保存在 `outputs/performance-native-sources/原始记录-<SHA256>/`；再生成 `outputs/performance-daily/` 下的JSON/XLSX草稿及数据库日报版本。来源包部分覆盖不阻止生成已核验本地小计，正式全量三栏保持null；采集件、测试副本、ACK和未发送讲解不会自动进入绩效。老师仍为“未指定”，没有修改真实老师身份。

运行日志为 `outputs/performance-daily/run-logs/last-run.json` 和 `events.jsonl`，记录带时区的开始/结束时间、成功/失败、退出码及生成/补齐日期。日志仅保存路径及作业结果，不复制配置内容或密钥。XLSX失败时任务返回非零状态，已保存JSON和版本保留，下次补齐。

安装脚本支持对同项目的同名任务幂等更新与启用，拒绝接管同名的其他命令。需要恢复/更新任务时使用：

```powershell
& tools/install_performance_schedule.ps1 -Config config/performance_report.local.toml
```

真实首次作业于上海时间2026-10-01 01:55完成9月17日至29日的13个v1草稿。9月30日提问窗口到10月1日07:00结束，按08:00生成配置继续等待，未提前计入。自动第二次触发成功，13版本及全部JSON/XLSX字节不变；另由真实任务验证单个XLSX缺失可补齐且不新增版本。相关25项测试通过，原消息、计量单元、发件、交付核验与绑定身份表指纹完全一致。证据目录：`data/private/acceptance/performance-schedule-enabled-20261001/`；旧配置及旧入口文件按原字节保全在同目录。
