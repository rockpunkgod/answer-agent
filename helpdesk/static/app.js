"use strict";
let csrfToken = "";
let busy = false;
let realMode = false;
let ackOnly = false;
let collectorStatus = null;
let allowedActions = new Set();
let testDeliveryAvailable = false;
let jobs = {};
let answerReviewPayload = "";
let answerReviewRequired = true;
let questionAutoContinue = false;
let sourceReviewEnabled = false;
let refreshError = false;
async function refreshAnswerReviewPackets(){
 const response=await fetch('/api/answer-review-packets',{cache:'no-store'});
 const data=await response.json();
 const panel=document.getElementById('answer-review-panel');
 const target=document.getElementById('answer-review-packets');
 if(!response.ok){panel.hidden=false;answerReviewPayload="";target.replaceChildren();document.getElementById('answer-review-status').textContent=data.error||'读取待发送讲解失败';return;}
 panel.hidden=!data.configured;
 const currentPayload=JSON.stringify(data);
 if(currentPayload===answerReviewPayload)return;
 answerReviewPayload=currentPayload;
 target.replaceChildren();
 const packets=[...data.packets].sort((left,right)=>String(right.copied_at||'').localeCompare(String(left.copied_at||'')));
 for(const packet of packets){
  const item=el('details','entry');
  item.append(el('summary','',`${packet.group_observed} · ${packet.student_observed} · 第${packet.question_number}题 · 原文复制于 ${display(packet.copied_at,'created_at')} · 查看完整讲解`));
  item.append(el('p','',packet.student_words));
  item.append(el('pre','native-text',packet.text));
  const copy=el('button','secondary','复制完整原文');copy.type='button';
  copy.onclick=async()=>{
   const status=document.getElementById('answer-review-status');
   try{
    // Revalidate the saved original at the moment of copying.
    const latestResponse=await fetch('/api/answer-review-packets',{cache:'no-store'});
    const latest=await latestResponse.json();
    if(!latestResponse.ok)throw Error(latest.error||'讲解来源校验失败');
    const current=latest.packets.find(row=>row.packet_id===packet.packet_id);
    if(!current||current.answer_sha256!==packet.answer_sha256||current.text!==packet.text)throw Error('讲解已变化，请刷新后核对');
    await navigator.clipboard.writeText(current.text);
    status.textContent='完整原文已复制，当前未发送、未计绩效。';
   }catch(error){status.textContent=error.message||'复制失败，可从上方原文手动复制';}
  };
  item.append(copy,el('p','','未发送，未计绩效。'));
  target.append(item);
 }
 if(!data.count&&data.configured)target.append(el('p','','暂无已保存的讲解。'));
}
const actionNames = {real_generate:"真实任务生成",real_approve:"真实答案审核",real_dispatch_test:"测试副本交付",new_alice:"Alice 提交样题",new_bob:"Bob 提交样题",dispatch_ack:"确认收到已模拟发送",followup_alice:"Alice 的追问",subquestion_alice:"Alice 的另一小题",correction_alice:"Alice 的否定题干更正",reorder_alice:"Alice 的选项换序更正",dispute_alice:"Alice 的异议",unknown_alice:"消息已转人工处理",generate:"模拟答复已生成",approve:"答案已批准",dispatch:"答案已模拟发送",dispatch_unknown:"已模拟发送状态未知",stop:"发送已停止",resume:"发送已恢复",recover:"恢复检查已完成"};
actionNames.collector_start="采集启动请求";actionNames.collector_stop="采集停止请求";
const stateNames = {MANUAL_SEND_REQUIRED:"等待你按发送",DELIVERY_DISABLED:"当前阶段禁止发送",LINKED:"已关联",NEEDS_REVIEW:"待人工处理",PENDING:"待发送",SENT_UI_CONFIRMED:"模拟发送成功",SEND_UNKNOWN:"发送状态未知",STALE:"已失效",APPROVED:"已批准",UNREVIEWED:"待审核",OPEN:"待处理",RESOLVED:"已处理",RUNNING:"生成中",GENERATED:"已生成",COMPLETE:"已完成",COMPLETED:"已完成",FAILED:"执行失败",READY:"已备齐",REVIEW:"需复核",UNKNOWN:"无法判断",FOLLOWUP:"追问",SUBQUESTION:"另一小题",CORRECTION:"更正",DISPUTE:"异议",NEW:"新题",ACK:"确认收到",ANSWER:"答案",CLARIFICATION:"澄清请求",MOCK_DEEPSEEK:"模拟生成",INTENT_UNKNOWN:"意图无法判断"};
const fieldNames = {raw_text:"消息",text:"内容",body:"内容",intent:"意图",status:"状态",state:"状态",reason:"原因",purpose:"用途",review_status:"审核",created_at:"时间",adapter:"生成方式",context_revision:"上下文版本",answer_revision:"答案版本",question_version:"题目版本",error:"问题",reviewer:"审核人",id:"编号"};
const visibleFields = {messages:["raw_text","intent","status","created_at"],questions:["status","context_revision","current_version","id"],answers:["text","state","question_version","adapter","created_at"],outbox:["purpose","body","review_status","state","created_at"],human_tasks:["reason","state","created_at"],reviews:["reviewer","status","created_at"],runs:["state","error","created_at"]};
function el(tag, className, value){const node=document.createElement(tag);if(className)node.className=className;if(value!==undefined&&value!==null)node.textContent=String(value);return node;}
function showStatus(message, error=false){refreshError=false;const node=document.getElementById("status");node.textContent=message;node.classList.toggle("error",error);}
function showRefreshError(){
 const node=document.getElementById('status');
 if(node.classList.contains('error')&&!refreshError)return;
 showStatus('暂时无法连接工作台，正在自动重试。',true);refreshError=true;
}
async function pollBoard(){
 if(!(realMode||ackOnly)||busy)return;
 try{await refresh();if(refreshError)showStatus('连接已恢复，工作台已更新。');}
 catch(error){if(!busy)showRefreshError();}
}
function display(value,key,row){if(value===null||value===undefined)return "—";const asText=String(value);if(key==="raw_text"&&row?.source==="OPERATOR_TEST"){try{const draft=JSON.parse(asText);if(draft.label==="OPERATOR_TEST")return `本机测试题 · 第${draft.number}题：${draft.stem}`;}catch{}}if(key==="created_at"){const parsed=new Date(asText);if(!Number.isNaN(parsed.valueOf()))return parsed.toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"});}if(asText.startsWith("SEND_UNKNOWN:"))return "发送状态未知，请人工核查";if(asText==="SENT_UI_CONFIRMED"&&(row?row.simulated===0:realMode))return (!row||row.purpose==="TEST_ANSWER")?"界面已确认测试副本":"发送界面已确认（非已读回执）";return stateNames[asText]||asText;}
function shortId(value){return value?String(value).slice(0,8):"—";}
function renderRows(id, rows, people){const target=document.getElementById(id);target.replaceChildren();const heading=target.closest(".panel")?.querySelector("h3");if(heading){heading.querySelector(".count")?.remove();heading.append(el("span","count",Array.isArray(rows)?rows.length:0));}if(!Array.isArray(rows)||rows.length===0){target.append(el("div","empty","暂无记录"));return;}for(const row of rows){const item=el("div","entry");const grid=el("div","kv");if(id==="messages"||id==="outbox"){const person=people.get(row.binding_id);if(person){grid.append(el("div","key","学生"));grid.append(el("div","value",person));}}for(const key of visibleFields[id]||[]){const value=row[key];if(value===null||value===undefined||value==="")continue;const label=key==="current_version"?"当前题目版本":fieldNames[key]||key;grid.append(el("div","key",label));grid.append(el("div","value",["id","current_version","question_version"].includes(key)?shortId(value):display(value,key,row)));}item.append(grid);target.append(item);}}
function deliverySummary(stats){const modes={AUTO:"自动发送",MANUAL:stats?.answer_review_required===false?"等你按发送":"人工审核后发送",DISABLED:"禁止发送"};const policy=stats?.delivery_policy;if(policy)return `当前阶段：${stats.delivery_stage_name||"已配置"} · 收到${modes[policy.ACK||"MANUAL"]} · 答案${modes[policy.ANSWER||"MANUAL"]}`;return stats?.manual_send_required?"由你按发送，程序不发送":"发送方式以当前阶段配置为准";}
function renderHealth(value){const stats=value||{};if(ackOnly){document.getElementById("health").textContent=`${collectorStatus?.configured===false?"采集未配置":collectorStatus?.collection_kind==="NATIVE_CLIPBOARD_IMPORT"?"本机原文导入":"仅采集和排队收到"} · 待收到 ${collectorStatus?.pending_ack_count??0} · 待核验 ${collectorStatus?.held_ack_count??0} · ${stats.stopped?"发送已停止":deliverySummary(stats)}`;return;}const states=stats.outbox_by_state||{};document.getElementById("health").textContent=`${realMode?"真实已准备任务":"模拟环境"} · 活跃案例 ${stats.open_cases??0} · 待人工处理 ${stats.human_tasks??0} · ${stats.stopped?"发送已停止":deliverySummary(stats)} · 发送状态未知 ${states.SEND_UNKNOWN??0}`;}
async function refresh(){const response=await fetch("/api/state",{cache:"no-store"});const data=await response.json();if(!response.ok)throw Error(data.error||"无法读取状态");csrfToken=data.csrf_token;configureMode(data);const board=data.dashboard||{};renderHealth(board.health);const people=new Map((board.bindings||[]).map(row=>[row.id,row.display_name]));for(const id of ["messages","questions","answers","outbox","human_tasks","reviews","runs"])renderRows(id,board[id],people);document.getElementById("updated").textContent=`已更新 ${new Date().toLocaleTimeString()}`;await refreshAnswerReviewPackets();if(questionAutoContinue)await refreshOperatorTasks();}
async function act(action){if(busy)return;busy=true;document.querySelectorAll("button").forEach(button=>button.disabled=true);showStatus("正在执行任务…");try{const response=await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":csrfToken},body:JSON.stringify({action})});const data=await response.json();if(!response.ok)throw Error(data.error||"动作失败");await refresh();const resultState=data.result?.state||data.job?.result?.state||data.job?.state;showStatus(`${actionNames[action]||"动作"}已执行${resultState?` · ${display(resultState)}`:""}`);}catch(error){showStatus(error.message||"动作失败",true);try{await refresh();}catch{}}finally{busy=false;updateButtons();}}
document.querySelectorAll("button[data-action]").forEach(button=>button.addEventListener("click",()=>act(button.dataset.action)));
document.getElementById("refresh").addEventListener("click",async()=>{try{await refresh();showStatus("看板已刷新");}catch(error){showStatus(error.message,true);}});
refresh().then(()=>showStatus(ackOnly?(collectorStatus?.collection_kind==="NATIVE_CLIPBOARD_IMPORT"?"工作台已就绪。可查看原始消息和待发送讲解。":"仅采集和排队收到。请检查账号配置后启动采集；自动答疑关闭。"):realMode?"真实已准备任务就绪。":"模拟环境就绪。请从 Alice 提交完整样题开始。"),showRefreshError);
const reportDate=document.getElementById("report-date");
reportDate.value=new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(Date.now()-7*60*60*1000));
async function readPerformance(){
 if(!reportDate.value)throw Error("请填写统计日期");
 const response=await fetch(`/api/performance?date=${encodeURIComponent(reportDate.value)}`,{cache:"no-store"});
 const data=await response.json();if(!response.ok)throw Error(data.error||"读取失败");
 return data;
}
document.getElementById("performance-refresh").addEventListener("click",async()=>{
 const target=document.getElementById("performance-result");
 try{
  const data=await readPerformance();
  const report=data.report, summary=report.summary||{};
  const complete=report.coverage?.complete===true;
  const subtotal=report.verified_subtotals?.totals||summary;
  target.replaceChildren(el("p","",`${report.report_date} · ${report.timezone} · 自拟草稿 · 待核对 ${summary.pending_records??0} 条`));
  const columns=el("div","performance-columns");
  const values=[
   ["白天综合",subtotal.day_composite_articles==null?"待核验":`${subtotal.day_composite_articles} 篇`,"已核验本地综合篇数小计"],
   ["语法听力",subtotal.grammar_listening_actual_questions==null?"待核验":`${subtotal.grammar_listening_actual_questions} 题`,"已核验本地实际题数小计"],
   ["夜间答题",subtotal.night_articles==null?"待核验":`${subtotal.night_articles} 篇`,"已核验本地夜间篇数小计"],
  ];
  for(const [heading,amount,note] of values){const item=el("div","performance-column");item.append(el("strong","",heading),el("p","",amount),el("small","",note));columns.append(item);}
  target.append(columns);
  target.append(el("p","",complete?"历史覆盖已核验完整；以上数量来自已确认交付的计量单元。":"以上是所选业务日已核验本地记录小计；全部群历史覆盖仍待核验，全量正式总数未知。小计为0只表示当前没有可计入的核实记录。"));
  const verified=report.verified_subtotals;
  if(verified)target.append(el("p","",`已计入 ${verified.included_unit_count} 个计量单元，涉及 ${verified.group_count} 个群；本月已核验小计（${verified.monthly_start_date} 至 ${report.report_date}）：白天综合 ${verified.monthly_totals.day_composite_articles} 篇，语法听力 ${verified.monthly_totals.grammar_listening_actual_questions} 题，夜间答题 ${verified.monthly_totals.night_articles} 篇。`));
  const missing=report.missing_data;
  if(missing)target.append(el("p","",`待补证据：${missing.pending_unit_count} 个计量单元、${missing.unlinked_message_count} 条未归并消息。另有 ${missing.native_acquisition_count} 个原文采集件（原消息日期未知，跨日期暂存范围）；采集件不计题数或绩效，测试副本交付不计绩效。`));
  target.append(el("p","","金额：规则待确认。夜间按学生原始提问时间分类，与完成时间分开；缺失证据不作零值确认。"));
  const list=el("ul","");for(const item of (report.pending||[]).slice(0,20))list.append(el("li","",`${item.student||"待核实学生"}：${item.reason}`));
  target.append(list);
 }catch(error){target.textContent=error.message;}
});
document.getElementById("performance-export").addEventListener("click",async()=>{
 const target=document.getElementById("performance-export-status");
 const button=document.getElementById("performance-export");button.disabled=true;
 target.textContent="正在读取完整日报…";
 try{
  const data=await readPerformance();
  const blob=new Blob([JSON.stringify(data,null,2)],{type:"application/json;charset=utf-8"});
  const url=URL.createObjectURL(blob);
  const link=document.createElement("a");link.href=url;link.download=`${data.report.report_date}-答疑日报.json`;
  document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  target.textContent="日报已下载，包含确认数量、全部明细和待核对记录；未提交、未新增入账。";
 }catch(error){target.textContent=error.message||"日报下载失败";}
 finally{button.disabled=busy;}
});

function updateButtons(){
 const running=Object.values(jobs).some(job=>job.state==="RUNNING");
 document.querySelectorAll("button").forEach(button=>{
  if(button.closest('#manual-delivery-panel'))return;
  const action=button.dataset.action;
  button.disabled=busy||button.dataset.sourceUnavailable==='true'||(ackOnly&&!!button.closest('[data-teaching-entry]')&&!(sourceReviewEnabled&&button.closest('#question-review-panel')))||(action&&!allowedActions.has(action))||(action==="real_dispatch_test"&&!testDeliveryAvailable)||(realMode&&running&&action&&!["stop","resume"].includes(action));
  if(ackOnly&&action==="collector_start"&&(collectorStatus?.configured===false||collectorStatus?.control?.worker_alive))button.disabled=true;
  if(ackOnly&&action==="collector_stop"&&!collectorStatus?.control?.worker_alive)button.disabled=true;
 });
}
document.getElementById("native-export").addEventListener("click",async()=>{
 const target=document.getElementById("native-export-status");
 const button=document.getElementById("native-export");button.disabled=true;
 target.textContent="正在校验并导出已采集原文…";
 try{
  if(!csrfToken)await refresh();
  const response=await fetch("/api/native-records/export",{cache:"no-store",headers:{"X-CSRF-Token":csrfToken}});
  if(!response.ok){const data=await response.json();throw Error(data.error||"原文导出失败");}
  const url=URL.createObjectURL(await response.blob());
  const link=document.createElement("a");link.href=url;link.download="原始记录导出包-partial.zip";
  document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  target.textContent="原始记录导出包已下载。历史覆盖仍为部分，采集件数不能作为题数或绩效数。";
 }catch(error){target.textContent=error.message||"原文导出失败";}
 finally{button.disabled=busy;}
});
document.getElementById("native-refresh").addEventListener("click",async()=>{
 const target=document.getElementById("native-records");
 try{
  const response=await fetch("/api/native-records",{cache:"no-store"});
  const data=await response.json();if(!response.ok)throw Error(data.error||"读取原文失败");
  target.replaceChildren(el("p","",`已暂存 ${data.count} 个采集件；不是题数、篇数或全部历史记录数。`));
  if(!data.records.length)target.append(el("p","","尚未暂存原生文字记录。"));
  for(const record of data.records){
   const item=el("details","entry");
   item.append(el("summary","",`${record.observed_group_label} · 原始时间待核验`));
   item.append(el("p","","待核验：发送人、原始发送时间、群身份、题目归并、附件和解答完成证据。"));
   const text=el("pre","native-text",record.original_text);item.append(text);
   item.append(el("p","",`采集编号 ${shortId(record.acquisition_id)} · 原文校验 ${record.raw_text_sha256}`));
   target.append(item);
  }
 }catch(error){target.textContent=error.message||"读取原文失败";}
});
function configureMode(data){
 answerReviewRequired=data.dashboard?.health?.answer_review_required!==false;
 questionAutoContinue=data.question_auto_continue===true;
 sourceReviewEnabled=data.source_review_enabled===true;
 ackOnly=data.processing_mode==="ACK_ONLY";collectorStatus=data.collector;
 realMode=data.simulation===false&&!ackOnly; allowedActions=new Set(data.allowed_actions||[]);
 testDeliveryAvailable=data.test_delivery_available===true; jobs=data.jobs||{};
 document.querySelectorAll(".simulation-controls").forEach(node=>node.hidden=realMode||ackOnly);
 document.querySelectorAll('[data-teaching-entry]').forEach(node=>node.hidden=ackOnly&&!(sourceReviewEnabled&&node.id==='question-review-panel'));
 document.getElementById('operator-form').hidden=sourceReviewEnabled;
 document.getElementById('question-review-title').textContent=sourceReviewEnabled?'学生题面确认':'录入本机测试题';
 document.getElementById('question-review-description').textContent=sourceReviewEnabled?'只显示已归属并核验来源的学生原题。确认题面清楚后持久排队，等待独立 DeepSeek 会话；草稿不计交付或绩效。':'保存草稿后核对题面，再建立测试任务。这里不代表学生原始消息，不计绩效，也不会向群聊发送。';
 document.querySelectorAll('button[data-action="approve"]').forEach(node=>node.hidden=!answerReviewRequired);
 document.querySelectorAll('[data-performance-entry]').forEach(node=>node.hidden=data.performance_available!==true);
 document.getElementById("collector-panel").hidden=!ackOnly;
 document.getElementById("real-controls").hidden=!realMode||questionAutoContinue;
 document.querySelector('button[data-action="recover"]').hidden=realMode||ackOnly;
 document.getElementById("control-step").textContent=realMode?"02":"04";
 document.getElementById("performance-step").textContent=realMode?"03":"05";
 if(ackOnly){
  document.title="英语答疑 · 消息采集与收到";
  document.getElementById("mode-label").textContent=collectorStatus?.collection_kind==="NATIVE_CLIPBOARD_IMPORT"?"后台模式 · 原文导入与待发送讲解":"采集模式 · 仅排队收到";
  document.getElementById("mode-description").textContent=collectorStatus?.collection_kind==="NATIVE_CLIPBOARD_IMPORT"?"Windows-MCP复制的正文先导入消息库。缺少可靠身份或时间的记录不自动回复、不计正式绩效；发送方式由当前阶段配置决定。":"先验证消息是否可以存档。当前仅为可信学生提问排队收到，自动答疑入口已关闭。";
  document.getElementById("mode-footer").textContent="原文导入与后台核验 · 自动答疑关闭 · 发送方式按当前阶段配置";
  if(collectorStatus?.configured===false){
   document.getElementById("mode-label").textContent="本机工作台 · 采集未配置";
   document.getElementById("mode-description").textContent="可以查看本地台账和下载日报。尚未配置消息采集，不会自动读取群消息、回复或生成讲解。";
   document.getElementById("mode-footer").textContent="本地台账 · 采集未配置 · 自动答疑关闭";
  }
  if(sourceReviewEnabled){
   document.getElementById('mode-label').textContent='本机工作台 · 原文与学生题面确认';
   document.getElementById('mode-description').textContent='原消息归属核验后，只确认一次题面。已核验的原群收到是后续排队前提；网页操作等待执行，答案仍由你发送。';
   document.getElementById('mode-footer').textContent='原消息题面确认 · 持久队列 · 本服务不操作桌面或自动发送答案';
  }
  document.querySelector('.board-head h2').textContent="采集消息与待收到";
  const target=document.getElementById('collector-status');target.replaceChildren();
  const nativeImport=collectorStatus?.collection_kind==="NATIVE_CLIPBOARD_IMPORT";
  const metrics=collectorStatus?.metrics||{};
  const control=collectorStatus?.control||{};
  document.querySelector('button[data-action="collector_start"]').textContent=nativeImport?'启动原文导入':'启动采集';
  document.querySelector('button[data-action="collector_stop"]').textContent=nativeImport?'停止原文导入':'停止采集';
  const controlLabels={IDLE:'尚未启动',STARTING:'正在启动，等待首次同步',RUNNING:'采集循环运行中',DEGRADED:'采集或收到处理失败，正在重试',STOPPING:'正在停止，等待当前调用完成',STOPPED:'已停止，进度保留',BLOCKED:'启动受阻，采集未运行'};
  target.append(el('p','',`${nativeImport?'原文导入':'采集'}控制：${controlLabels[control.state]||control.state||'未知'}`));
  const labels={NEVER_SYNCED:'尚未读取到聊天记录',HEALTHY:'最近同步成功',FAILED:'同步失败',STALE:'同步已超时'};
  target.append(el('p','',`${nativeImport?'原文导入':'采集'}状态：${nativeImport&&metrics.health==='HEALTHY'?'已成功导入原文':labels[metrics.health]||metrics.health||'状态未知'}`));
  target.append(el('p','',nativeImport?'当前使用本机原生复制记录，无需存档账号。原文导入运行不代表桌面自动监听已经接通。':collectorStatus?.account_status==='NOT_CONFIGURED'?'缺少企业存档账号配置：请管理员在本机配置企业ID、存档凭据和解密私钥，并确认答疑群覆盖范围。':'接入配置已填写，真实账号能力仍待验证。'));
  target.append(el('p','',`待收到 ${collectorStatus?.pending_ack_count??0} 条 · 待核验 ${collectorStatus?.held_ack_count??0} 条 · 今日采集 ${metrics.messages_received_today??'未知'} 条`));
  target.append(el('p','',`最后成功同步：${metrics.last_successful_sync_at||'无'} · 未消费事件：${metrics.events_pending??0}`));
  if(control.error_type)target.append(el('p','',control.error_type==='ArchiveNotConfigured'?'聊天尚未接通：重新点击启动不能补齐账号配置。':`采集遇到问题：${control.error_type}，请检查接入配置。`));
  document.getElementById('collector-control-note').textContent=nativeImport?'新增的Windows-MCP原文归档会增量导入；此入口的状态不用于判定桌面监听或群内收到的发送结果。原文核验与发送方式分别处理，以当前阶段配置为准。':control.worker_alive?'采集正在进行，仅保存消息并排队收到。停止会等待当前有界调用完成，保留已确认进度；发送方式以当前阶段配置为准。':'工作台已打开，聊天采集未启动。管理员配置完成后再点击启动采集；发送键由你按。';
 }else if(realMode){
  document.title="英语答疑 · 真实已准备任务";
  document.getElementById("mode-label").textContent=questionAutoContinue?"题面确认后排队":"REAL · 已准备教学会话";
  document.getElementById("mode-description").textContent=questionAutoContinue?"核对一次完整题面后交给后台队列，等待 Luna 操作真实 DeepSeek 网页。生成的完整讲解由你最后按发送。这里录入的本机测试题不计绩效。":"使用已核验的真实 DeepSeek 会话。答案审核后仅交付企业微信苇中鹤测试副本；界面发送确认不代表学生已收到，不计为学生绩效。";
  document.getElementById("mode-footer").textContent=questionAutoContinue?"题面确认一次 · 等待 Luna 执行 · 讲解人工发送":"固定真实任务 · 限定测试联系人 · 群监听和全量历史尚未接入";
  document.getElementById("real-delivery-note").textContent=testDeliveryAvailable?"测试交付凭据已配置，执行时继续核验有效期和联系人。":"尚未配置已核验测试副本与联系人凭据，发送不可用。";
  const target=document.getElementById("real-jobs");target.replaceChildren();
  for(const job of Object.values(jobs))target.append(el("p","",`${actionNames[job.action]||job.action} · ${display(job.state,"state")} ${job.error||job.result?.reason||(job.result?.state?display(job.result.state,"state"):"")}`));
 }
 document.getElementById('operator-flow-note').textContent=questionAutoContinue?'核对完整材料、题干和选项后确认一次。后续步骤交给后台队列，无需再点冻结、生成或答案审批。':'核对完整材料、题干和选项后再批准。冻结只保存生成输入，尚未上传或提交 DeepSeek。';
 updateButtons();
}
const queueNames={WAITING_ACK:'题面已确认，等待收到确认',STOPPED:'已暂停，保留题面确认',READY_FOR_ENQUEUE:'题面已确认，正在排队',WAITING_DESKTOP_EXECUTOR:'已排队，等待 Luna 操作',READY_FOR_PREPARATION:'独立会话已就绪，等待上传材料',ATTACHMENTS_READY:'材料已上传，等待生成',GENERATED:'已生成，等你按发送',SESSION_CREATION_STARTED:'正在新建独立会话',PREPARATION_STARTED:'正在上传材料',GENERATION_STARTED:'正在生成讲解',EXECUTION_UNCERTAIN:'操作结果需核实，已暂停重试',NEEDS_ATTENTION:'题面已确认，准备条件需检查'};
let operatorRenderedState='';
async function refreshOperatorTasks(){
 const response=await fetch('/api/operator-tasks',{cache:'no-store'});
 const data=await response.json();if(!response.ok)throw Error(data.error||'无法读取测试任务');
 const renderState=JSON.stringify([data,answerReviewRequired,realMode,sourceReviewEnabled]);
 if(renderState===operatorRenderedState)return;
 const target=document.getElementById('operator-tasks');
 const expanded=new Set(Array.from(target.querySelectorAll('details[open]')).map(item=>item.dataset.draftId));
 operatorRenderedState=renderState;target.replaceChildren();
 const tasks=new Map(data.tasks.map(task=>[task.id,task]));
 for(const draft of data.drafts){
  const item=el('details','entry');item.dataset.draftId=draft.id;item.open=expanded.has(draft.id);const task=tasks.get(draft.task_id);
  const original=draft.label==='SOURCE_MESSAGE';
  const automatic=data.question_auto_continue===true||!!task?.auto_queue;
  const delivered=task?.actual_delivery||draft.actual_delivery;
  const state=delivered?(delivered.stale?'历史已人工交付，题目已更正':'已人工核验交付'):task?.run_state==='STALE'?'旧输入已失效':task?.auto_queue?(queueNames[task.auto_queue.phase]||'后台准备中'):task?.run_state==='GENERATED'?(answerReviewRequired?'已生成，待答案审核':'已生成，等你按发送'):task?.run_id?(task.preparation_reviewed?'材料已核验，可生成':'已冻结，材料待准备'):task?'题面已审核':'草稿待核对';
  item.append(el('summary','',`第${draft.payload.number||'待核对'}题 · ${state}`));
  if(original){const source=draft.original_source;if(source){item.append(el('p','',`${source.group_name} · ${source.student_display_name} · 提问 ${display(source.student_sent_at,'created_at')}`));for(let i=0;i<source.image_count;i++){const image=document.createElement('img');image.src=`/api/source-question-image?draft_id=${encodeURIComponent(draft.id)}&index=${i}`;image.alt=`学生原题图片 ${i+1}`;image.className='source-question-image';image.loading='lazy';item.append(image);}}else{item.append(el('p','','原消息来源需核对，暂不能继续准备。'));}}
  item.append(el('pre','native-text',`${draft.payload.passage}\n\n${draft.payload.stem}\n${Object.entries(draft.payload.options).map(([key,value])=>`${key}. ${value}`).join('\n')}`));
  if(delivered){item.append(el('p','',`实际交付 ${display(delivered.completed_at,'created_at')} · 人工核验记录已回流，原生成稿保留。`));}
  else if(!task){const button=el('button','secondary',original||automatic?'确认题面清楚并排队':'核对题面并建立测试任务');button.type='button';button.dataset.sourceUnavailable=String(original&&(!automatic||draft.source_valid!==true));button.disabled=busy||button.dataset.sourceUnavailable==='true';button.onclick=()=>operatorRequest({action:'review',draft_id:draft.id,revision:draft.revision,reviewer:document.getElementById('operator-reviewer').value,source_evidence:document.getElementById('operator-evidence').value});item.append(button);}
  else if(!automatic&&!task.run_id&&realMode){const button=el('button','secondary','冻结已审核生成输入');button.type='button';button.onclick=()=>operatorRequest({action:'freeze',task_id:task.id});item.append(button);}
  else if(!automatic&&task.preparation_reviewed&&task.run_state==='RUNNING'&&realMode){const button=el('button','secondary','生成本机测试任务答案');button.type='button';button.onclick=()=>operatorRequest({action:'generate',task_id:task.id});item.append(button);}
  item.append(el('p','',`版本 ${draft.revision} · ${original?(delivered?'沿用学生原消息，实际交付已记录':'沿用学生原消息，草稿尚未交付'):'本机测试，不计绩效'}${task?.run_id?` · 生成编号 ${shortId(task.run_id)}`:''}`));target.append(item);
 }
 if(!data.drafts.length)target.append(el('p','',sourceReviewEnabled?'尚无来源核验通过且已归属的学生题目。原文片段仍在原始记录中，不能自动拼成题面。':'尚无本机录入草稿。'));
}
async function operatorRequest(payload){
 if(busy)return;busy=true;updateButtons();
 try{
  const response=await fetch('/api/operator-tasks',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrfToken},body:JSON.stringify(payload)});
  const data=await response.json();if(!response.ok)throw Error(data.error||'测试任务操作失败');
  await refreshOperatorTasks();await refresh();showStatus(payload.action==='generate'?'真实生成任务已启动，请查看运行状态。':payload.action==='freeze'?'输入已冻结，尚未上传或提交生成。':payload.action==='review'?(data.result?.auto_queue?`${queueNames[data.result.auto_queue.phase]||'题面确认已保存'}。无需再次审核题面。`:'题面审核已保存，测试任务已建立。'):'新题草稿已保存。');return true;
 }catch(error){showStatus(error.message,true);return false;}finally{busy=false;updateButtons();}
}
let operatorRequestId=crypto.randomUUID();
document.getElementById('operator-form').addEventListener('input',()=>{operatorRequestId=crypto.randomUUID();});
document.getElementById('operator-form').addEventListener('submit',async event=>{
 event.preventDefault();const form=new FormData(event.target);
 const payload={question_type:form.get('question_type'),number:form.get('number'),passage:form.get('passage'),stem:form.get('stem'),options:Object.fromEntries(['A','B','C','D'].map(label=>[label,form.get(label)])),attachments:[]};
 await operatorRequest({action:'create',request_id:operatorRequestId,payload});
});
refreshOperatorTasks().catch(error=>showStatus(error.message,true));
document.getElementById('operator-refresh').addEventListener('click',()=>refreshOperatorTasks().catch(error=>showStatus(error.message,true)));
actionNames.operator_generate='本机测试题真实生成';
setInterval(pollBoard,2000);
