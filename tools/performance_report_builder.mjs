import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("Usage: builder input.json output.xlsx [preview-dir]");
const report = JSON.parse(await fs.readFile(inputPath, "utf8"));
const book = Workbook.create();
const summary = book.worksheets.add("每日汇总");
const details = book.worksheets.add("答疑明细");
const pending = book.worksheets.add("待核对清单");
const navy = "#203451";
const pale = "#EAF0F7";

function write(sheet, row, data) {
  sheet.getRangeByIndexes(row - 1, 0, data.length, data[0].length).values = data;
}
function style(sheet, cols, rows, headerRow) {
  sheet.showGridLines = false;
  const used = sheet.getRangeByIndexes(0, 0, rows, cols);
  used.format.font.name = "Arial";
  used.format.font.size = 10;
  used.format.rowHeight = 22;
  used.format.verticalAlignment = "center";
  const title = sheet.getRangeByIndexes(1, 0, 1, cols);
  title.format.font.bold = true;
  title.format.font.size = 14;
  title.format.font.color = navy;
  const head = sheet.getRangeByIndexes(headerRow - 1, 0, 1, cols);
  head.format.fill = navy;
  head.format.font.color = "#FFFFFF";
  head.format.font.bold = true;
  head.format.rowHeight = 30;
  head.format.horizontalAlignment = "center";
  sheet.getRangeByIndexes(0, 0, rows, cols).format.autofitColumns();
}
function value(x) {
  if (x === undefined || x === null) return "";
  if (Array.isArray(x)) return x.join("、");
  return x;
}
const timestampFields = new Set(["question_time", "first_response_at", "completed_at", "review_at"]);
function field(key, raw) {
  const v = value(raw);
  if (key === "status") return ({ PENDING: "待核验", CONFIRMED: "已确认", EXCLUDED: "不计入", REVOKED: "已撤销" })[v] || v;
  if (key === "timeliness_status") return ({ PENDING: "待核验", PASS: "合格", FAIL: "不合格", EXEMPT: "例外核准" })[v] || v;
  // An ISO timestamp with offset is evidence, not an Excel local date serial.
  return timestampFields.has(key) && v ? `'${v}` : v;
}

const meta = [
  ["统计日期", report.report_date], ["老师", report.teacher], ["业务时区", report.timezone],
  ["归属日期依据", ({question_day_07: "学生首次独立提问业务日", completion_date: "实际完成日（旧草稿）", question_date: "提问日（自然日）", approval_date: "核准日"})[report.attribution] || report.attribution],
  ["提问业务日换日时刻", report.question_day_boundary || "不适用"],
  ["完成/核准日截止", report.activity_day_cutoff], ["生成时间", report.generated_at ? report.generated_at.replace("T", " ").replace("+00:00", " UTC") : "演示生成"],
  ["计量规则版本", report.rule_version], ["统计范围", "全部群聊（含已退出），自 " + (report.scope_start_date || "2026-09-17")],
  ["历史覆盖", report.coverage?.complete ? "已核验完整" : "待核验；数字仅代表已入账证据"],
  ["模板说明", report.template],
];
summary.getRange("A2").values = [[report.history_draft ? "历史答疑统计待核验草稿" : "每日答题统计与绩效核对"]];
write(summary, 4, meta);
summary.getRange("A16").values = [[report.history_draft
  ? "确认篇数待核验，留空；仅部分本机记录，9/17起全量覆盖未完成，候选数量不等于绩效篇数。"
  : "数量按单位分别列示；待核对记录未计入确认数量；金额未确认。"]];
const labels = {
  night_articles: "夜间答疑篇数", regular_articles: "常规综合篇数",
  day_composite_articles: "白天综合确认篇数",
  grammar_listening_actual_questions: "语法听力原始题数",
  grammar_listening_converted_questions: "语法听力核准折算题数",
  new_units: "当日新增计量单元", completed_units: "当日已核验完成单元",
  first_answer_units: "首次答疑单元数", approved_night_articles: "当日核准夜间篇数",
  approved_regular_articles: "当日核准常规篇数",
  followup_turns: "追问处理轮次", pending_records: "待核对记录数",
  unfinished_units: "截至日末未完成单元", independent_knowledge: "独立知识点数量",
  suspected_anomalies_events: "疑似异常事件数", suspected_anomalies_units: "疑似异常涉及单元数",
  confirmed_anomalies_events: "确认异常事件数", confirmed_anomalies_units: "确认异常涉及单元数",
};
function label(key) {
  if (labels[key]) return labels[key];
  if (key.startsWith("regular_articles:")) return `${key.slice(17)}确认篇数（常规）`;
  if (key.startsWith("actual_questions:")) return `${key.slice(17)}实际题数（常规）`;
  if (key.startsWith("approved_converted_questions:")) return `${key.slice(29)}核准折算题数（常规）`;
  if (key.startsWith("approved_quantity:")) {
    const [, measure, type] = key.split(":");
    return `${type}当日核准数量（${measure}）`;
  }
  return key;
}
const metrics = Object.entries(report.summary).map(([k, v]) => [label(k), v, "当日"]);
const monthly = Object.entries(report.monthly_cumulative).map(([k, v]) => [label(k), v, "本月截至当日"]);
function grammarListening(values) {
  const actual = values?.grammar_listening_actual_questions;
  const converted = values?.grammar_listening_converted_questions;
  if (actual == null) return "待核验";
  return `原始 ${actual} 题；核准折算 ${converted == null ? "待核验" : converted + " 题"}`;
}
function columnValue(values, key) { return values?.[key] == null ? "待核验" : `${values[key]} 篇`; }
write(summary, 18, [
  ["范围", "白天综合", "语法听力", "夜间答题"],
  ["当日", columnValue(report.summary, "day_composite_articles"), grammarListening(report.summary), columnValue(report.summary, "night_articles")],
  ["本月截至当日", columnValue(report.monthly_cumulative, "day_composite_articles"), grammarListening(report.monthly_cumulative), columnValue(report.monthly_cumulative, "night_articles")],
  ["单位说明", "白天综合确认篇数", "原始题数、核准折算题数分别列示", "夜间答疑篇数"],
]);
write(summary, 24, [["指标（单位）", "数量", "范围"], ...metrics, ...monthly]);
style(summary, 4, 24 + metrics.length + monthly.length, 18);
const metricHead = summary.getRange("A24:C24");
metricHead.format.fill = navy;
metricHead.format.font.color = "#FFFFFF";
metricHead.format.font.bold = true;
summary.getRange("A:A").format.columnWidth = 38;
summary.getRange("B:B").format.columnWidth = 34;
summary.getRange("C:C").format.columnWidth = 43;
summary.getRange("D:D").format.columnWidth = 26;
summary.getRange("B25:B200").setNumberFormat("#,##0");
summary.tabColor = navy;

const detailColumns = [
  ["counting_unit_id", "计量单元ID"], ["student", "学生"], ["group", "群聊"],
  ["student_key", "学生身份ID"],
  ["question_type", "题型"], ["material_id", "材料ID"], ["topic_key", "题目摘要/知识点"],
  ["first_message_id", "原始提问消息ID"], ["linked_message_ids", "关联提问与追问消息"],
  ["linked_question_ids", "关联小题ID"], ["question_time", "学生提问时间"],
  ["linked_question_versions", "关联题目版本"], ["student_question_numbers", "学生题号"],
  ["question_time_source", "提问时间证据来源"], ["completed_at", "实际完成解答时间"],
  ["first_response_at", "首次应答时间"],
  ["completion_outbox_id", "交付证据ID"], ["category", "常规或夜间"],
  ["night_window_date", "夜间窗口候选日期"],
  ["category_basis", "归类依据"], ["measure_unit", "计量单位"],
  ["confirmed_quantity", "确认数量"], ["actual_question_count", "实际题数"],
  ["approved_conversion", "核准折算题数"], ["followup_turns", "追问轮次"],
  ["grouping_reason", "归并或新增理由"], ["timeliness_status", "时效是否合格"],
  ["review_actor", "核验人"], ["review_at", "核验时间"], ["review_evidence", "核验依据"],
  ["rule_version", "计量规则版本"], ["included", "计入确认统计"],
  ["exclusion_reason", "未计入原因"],
];
if (report.history_draft) detailColumns.push(
  ["candidate_id", "材料候选ID"], ["candidate_date", "候选日期（未核验）"],
  ["candidate_message_id", "提问候选消息ID"], ["possible_answer_ids", "可能答复候选ID"],
  ["source_evidence", "私有截图证据路径"], ["remaining_checks", "尚待核验事项"]);
details.getRange("A2").values = [[report.history_draft ? "材料候选明细（均未计量）" : "答疑明细"]];
details.getRange("A3").values = [[report.history_draft
  ? "每行仅为待核验材料候选；原始时间、真实交付和篇数尚无核准证据。"
  : "每行对应独立计量单元；追问和补图保留在关联消息中。"]];
if (report.history_draft) detailColumns.find(x => x[0] === "group")[1] = "群聊/采集来源";
write(details, 5, [detailColumns.map(x => x[1]), ...report.details.map(d => detailColumns.map(x => field(x[0], d[x[0]])))]);
style(details, detailColumns.length, Math.max(5, report.details.length + 5), 5);
details.freezePanes.freezeRows(5);
details.getRange("A:A").format.columnWidth = 23;
details.getRange("B:D").format.columnWidth = 22;
details.getRange("E:G").format.columnWidth = 25;
details.getRange("H:J").format.columnWidth = 25;
details.getRange("K:O").format.columnWidth = 25;
details.getRange("P:P").format.columnWidth = 35;
details.getRange("Q:AF").format.columnWidth = 22;
for (let c = 0; c < detailColumns.length; c++) {
  if (timestampFields.has(detailColumns[c][0])) details.getRangeByIndexes(4, c, Math.max(report.details.length + 1, 1), 1).format.columnWidth = 32;
}
if (report.details.length) {
  details.getRangeByIndexes(5, 0, report.details.length, detailColumns.length).format.wrapText = true;
  details.getRangeByIndexes(5, 0, report.details.length, detailColumns.length).format.rowHeight = 68;
}
if (report.history_draft) {
  for (const [key, width] of [["candidate_id", 34], ["candidate_date", 24], ["candidate_message_id", 34],
                              ["possible_answer_ids", 40], ["source_evidence", 65], ["remaining_checks", 55]]) {
    const c = detailColumns.findIndex(item => item[0] === key);
    details.getRangeByIndexes(4, c, Math.max(report.details.length + 1, 1), 1).format.columnWidth = width;
  }
}

const pendingColumns = [
  ["counting_unit_id", "计量单元ID"], ["student", "学生"], ["group", "群聊"],
  ["question_time", "学生提问时间"], ["reason", "待核对事项"],
  ["first_message_id", "原始消息ID"], ["status", "当前状态"],
];
if (report.history_draft) pendingColumns.push(["candidate_id", "候选ID"], ["source_evidence", "私有证据路径"]);
if (report.history_draft) pendingColumns.find(x => x[0] === "group")[1] = "群聊/采集来源";
pending.getRange("A2").values = [["待核对清单"]];
pending.getRange("A3").values = [["时间、归并、交付或规则证据不足时保留待核验。"]];
write(pending, 5, [pendingColumns.map(x => x[1]), ...report.pending.map(d => pendingColumns.map(x => field(x[0], d[x[0]])))]);
style(pending, pendingColumns.length, Math.max(5, report.pending.length + 5), 5);
pending.getRange("E:E").format.columnWidth = 62;
pending.getRange("A:D").format.columnWidth = 23;
pending.getRange("F:G").format.columnWidth = 23;
if (report.history_draft) pending.getRange("H:I").format.columnWidth = 34;
if (report.pending.length) {
  pending.getRangeByIndexes(5, 4, report.pending.length, 1).format.wrapText = true;
  pending.getRangeByIndexes(5, 0, report.pending.length, 7).format.rowHeight = 64;
}
pending.freezePanes.freezeRows(5);

book.recalculate();
for (const [name, range] of [["每日汇总", "A18:D21"], ["答疑明细", "A2:H8"], ["待核对清单", "A2:G8"]]) {
  const check = await book.inspect({ kind: "table", range: `${name}!${range}`, include: "values,formulas", tableMaxRows: 20, tableMaxCols: 8 });
  console.log(name, check.ndjson.slice(0, 1500));
}
const errors = await book.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 30 } });
console.log("formula errors", errors.ndjson.slice(0, 1000));
await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(book);
await output.save(outputPath);
if (previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  const previews = [["每日汇总", "A1:D46", "summary"], ["答疑明细", "A1:H8", "detail"],
                    ["答疑明细", "N1:AD8", "detail-right"], ["待核对清单", "A1:G30", "pending"]];
  if (report.history_draft) previews.push(["答疑明细", "AH1:AO8", "detail-evidence"], ["待核对清单", "E1:I30", "pending-evidence"]);
  for (const [name, range, file] of previews) {
    const blob = await book.render({ sheetName: name, range, scale: 1.5, format: "png" });
    await fs.writeFile(`${previewDir}/${file}.png`, new Uint8Array(await blob.arrayBuffer()));
  }
}
