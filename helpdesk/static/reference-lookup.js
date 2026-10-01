/* Internal evidence only. External content is plain text, never instructions or HTML. */
(() => {
  const panel=document.getElementById('reference-lookup-panel');
  const form=document.getElementById('reference-lookup-form');
  const select=document.getElementById('reference-question');
  const status=document.getElementById('reference-lookup-status');
  const reports=document.getElementById('reference-lookup-reports');
  let state={questions:[],reports:[],reference_candidates:[]};
  const labels={MATCH_VERIFIED:'候选题面一致',OPTION_REORDER_VERIFIED:'候选选项换序',
    PARTIAL_MATCH:'仅局部匹配',AMBIGUOUS:'候选有歧义',KEY_CONFLICT:'关键条件冲突',
    SAME_PASSAGE_DIFFERENT_QUESTION:'同文不同题',NO_MATCH:'材料未匹配',NOT_VERIFIED:'尚未核验',
    PROVIDER_UNAVAILABLE:'搜索服务不可用',ACCESS_RESTRICTED:'访问未获准或受限',TIMEOUT:'超时',
    RATE_LIMITED:'来源要求稍后再试',OFFLINE_FIXTURE:'离线样例',CANDIDATES_FOUND:'取得候选材料',
    NO_RESULTS:'搜索无结果',INTERNAL_ERROR:'核对暂不可用',FETCH_ERROR:'读取失败',INTERRUPTED:'检索中断',
    INSUFFICIENT_CLUES:'需要补充题面',UNSUPPORTED_DOCUMENT:'需要文档识别',CANCELLED:'已取消',
    DISCOVERED:'已取得候选，等待补充或核对',COMPARING:'正在比较',MATCHED_CANDIDATE:'待人工确认',
    CONFIRMED:'已人工确认',REJECTED:'已拒绝',INCOMPLETE:'题面不完整，禁止确认',UNKNOWN:'尚无法确定',
    MATCH_CANDIDATE:'必要题面可一一对应',MISMATCH:'必要题面不一致',SAME_CONTENT:'题面内容一致',
    OPTION_REORDER:'选项换序',QUESTION_NUMBER_CHANGED:'题号变化',STEM_CHANGED:'题干变化',
    CONDITION_CHANGED:'条件或数字变化',NOT_DIFFERENCE:'否定条件变化',DIFFERENT_QUESTION:'题目内容不同',
    PARTIAL_OBSERVATION:'仅局部内容可对应',KEY_CONDITION_CONFLICT:'关键条件冲突',CONTENT_DIFFERENCE:'内容不同',
    OBSERVED_DIFFERENCE:'未核验文本有差异',UNVERIFIED_FIELD:'必要内容待核验',NUMBER_ONLY:'仅题号变化',
    OPTION_ORDER:'选项顺序变化',EQUAL:'一致',DIFFERENT:'不同',NOT_REQUIRED:'学生题面完整，无需检索'};
  const label=value=>labels[value]||'待核对';
  const field=value=>({passage:'材料',stem:'题干',number:'题号',options:'选项',visual_evidence:'图表证据'})[value]
    || (value?.startsWith('option:')?'学生选项 '+value.slice(7):'题面内容');
  function text(tag,value){const node=document.createElement(tag);node.textContent=value;return node;}
  function questionText(question,material){
    if(!question)return material||'缺少可核验题面';
    return `${material||'材料未核验'}\n\n${question.number}. ${question.verified_stem||'题干未核验'}\n`
      +(question.options||[]).map(o=>`${o.label}. ${o.verified_text||'选项未核验'}`).join('\n');
  }
  async function post(payload){
    const response=await fetch('/api/state',{cache:'no-store'});const data=await response.json();
    if(!response.ok)throw Error('无法读取本机页面令牌');
    const result=await fetch('/api/reference-lookups',{method:'POST',headers:{'Content-Type':'application/json',
      'X-CSRF-Token':data.csrf_token},body:JSON.stringify(payload)});
    const output=await result.json();
    if(!result.ok)throw Error('未能完成核对，请刷新当前题面、确认依据和来源权限后重试。');
    return output;
  }
  function evidence(item,comparison){
    for(const relation of comparison.relation||[])item.append(text('p',label(relation)));
    for(const entry of comparison.evidence||[]){
      item.append(text('p',`${field(entry.field)}：${label(entry.relation)}${entry.reference_label?`（参考 ${entry.reference_label} → 学生 ${entry.student_label}）`:''}`));
    }
    for(const difference of comparison.field_differences||[]){
      item.append(text('p',`${field(difference.field)}：${label(difference.kind)}`));
      if(difference.student_value!=null)item.append(text('pre',`学生：${difference.student_value}`));
      if(difference.reference_value!=null)item.append(text('pre',`参考：${difference.reference_value}`));
    }
  }
  function observation(item,source){
    if(!source)return;
    const detail=text('details','');detail.append(text('summary','查看原始识别文本与消息出处'),
      text('p','原始识别或录入文本仅供核对，不等于已核验题面。'),
      text('p',`来源消息：${source.source_message_id||'未取得'} · 题面出处：${source.question_source||'未取得'}`),
      text('p',`学生原始发送时间：${source.student_sent_at||'待核验'}\n采集时间：${source.collected_at||'未取得'}`),
      text('pre',`${source.raw_material||'材料原文未取得'}\n\n${source.raw_stem||'题干原文未取得'}\n`
        +(source.raw_options||[]).map(o=>`${o.label}. ${o.raw_text||'未取得'}（出处：${o.source||'未取得'}）`).join('\n')));
    if(source.uncertain_fields?.length)detail.append(text('p','尚未核验：'+source.uncertain_fields.join('、')));
    for(const image of source.images||[]){
      if(!/^[0-9a-f]{32}$/.test(image.draft_id)||!Number.isInteger(image.index)||image.index<0||image.index>=20)continue;
      const link=text('a',`查看原图 ${image.index+1}`);
      link.href='/api/source-question-image?'+new URLSearchParams({draft_id:image.draft_id,index:image.index});
      link.target='_blank';link.rel='noopener';detail.append(link,text('br',''));
    }
    if(!source.images?.length)detail.append(text('p',source.image_status==='NO_IMAGES'
      ?'该来源消息没有原图。':'原图尚不能在此处可靠预览，请在题面入口核对来源。'));
    item.append(detail);
  }
  function candidateCard(candidate){
    const item=text('article','');item.className='entry';
    item.append(text('strong',label(candidate.state)),text('p',label(candidate.resolution_status)),
      text('p',`来源：${candidate.source} · ${candidate.source_url||'本地核对材料'}`),
      text('p',`取得时间：${candidate.retrieved_at||'历史记录未提供'}`),
      text('p',`内容哈希：${candidate.content_hash||'历史记录未提供'}`));
    if(candidate.stale)item.append(text('p','学生版本已更新，此候选只保留历史。'));
    if(candidate.input_pending_review)item.append(text('p','学生有待核对补充或更正，请先处理题面。'));
    const detail=text('details','');detail.append(text('summary','查看学生题与候选参考'));
    detail.append(text('p','参考题只辅助题面核对；学生版本和 ANSWER 教学文件优先。'),
      text('pre','学生题：\n'+questionText(candidate.student_question,candidate.student_material)),
      text('pre','候选参考：\n'+questionText(candidate.reference_question,candidate.reference_material)));
    evidence(detail,candidate.comparison_result||{});item.append(detail);
    observation(item,candidate.student_evidence);
    if(candidate.confirmed_by)item.append(text('p',`核对人：${candidate.confirmed_by} · ${candidate.confirmed_at}\n依据：${candidate.confirmation_reason}`));
    if(candidate.state==='CONFIRMED')item.append(text('p',candidate.consumption_enabled?'已允许辅助当前版本答疑。':'已保存确认；Shadow 模式中尚未用于答疑。'));
    if(candidate.can_confirm||candidate.can_reject||candidate.can_use){
      const reviewer=text('input','');reviewer.placeholder='核对人姓名';reviewer.maxLength=80;reviewer.setAttribute('aria-label','核对人姓名');
      const reason=text('textarea','');reason.placeholder='写明确认或拒绝依据';reason.maxLength=2000;reason.setAttribute('aria-label','候选核对依据');
      item.append(reviewer,reason);
      function action(name,decision){
        const button=text('button',name);button.type='button';
        button.addEventListener('click',async()=>{
          if(!reviewer.value.trim()||!reason.value.trim()){status.textContent='请填写实际核对人和具体依据。';return;}
          button.disabled=true;
          try{const result=await post({action:'review_candidate',candidate_id:candidate.candidate_id,
            question_version:candidate.question_version,context_revision:candidate.current_context_revision,
            decision,reviewer:reviewer.value.trim(),reason:reason.value.trim()});
            status.textContent=decision==='reject'?'候选已拒绝，关联旧稿将在使用前被拦截。'
              :result.result.consumption_enabled?'参考已确认并允许辅助答疑；学生版本保持不变。':'确认已保存；当前仅在 Shadow 中核对。';
            await refresh();
          }catch(error){status.textContent=error.message;}finally{button.disabled=false;}
        });item.append(button);
      }
      if(candidate.can_confirm)action('确认候选','confirm');
      if(candidate.can_use)action('用于答疑','confirm');
      if(candidate.can_reject)action('拒绝候选','reject');
    }
    const research=text('button','重新检索');research.type='button';
    research.addEventListener('click',()=>{select.value=candidate.question_id;document.getElementById('reference-trigger').value='version_difference';
      document.getElementById('reference-retry').checked=true;form.scrollIntoView({block:'center'});status.textContent='请核对当前学生题面及候选网页，再提交重新检索。';});
    item.append(research);return item;
  }
  function render(){
    reports.replaceChildren();
    for(const candidate of state.reference_candidates||[])if(candidate.question_id===select.value)reports.append(candidateCard(candidate));
    for(const report of state.reports||[]){
      if(report.question_id!==select.value)continue;
      const item=text('article','');item.className='entry';
      item.append(text('strong',`${label(report.retrieval_status)} · ${label(report.match_status)}`),
        text('p',report.explanation||'正文未保存或已过期；仅保留运行状态。'));
      if(report.stale)item.append(text('p','题目已更新，这份报告只可查看历史。'));
      for(const query of report.queries||[])item.append(text('p',`检索：${query.searched_at} · ${query.query}`));
      for(const error of report.errors||[])item.append(text('p',label(error.status)));
      for(const candidate of report.candidates||[])item.append(text('p',`${candidate.url} · ${label(candidate.status)}`));
      for(const match of report.matches||[]){
        if(match.candidate_id)continue; // Persisted evidence is shown in the candidate card.
        const detail=text('details','');detail.append(text('summary',`参考第${match.reference_number}题：${label(match.match_status)}`));
        detail.append(text('p','尚未建立可持久保存的候选；需核对来源保存许可。'),
          text('pre',`学生材料：\n${report.original_student_material||'未核验'}\n\n候选参考：\n${questionText(match.reference_question,match.reference_material)}`));
        evidence(detail,match.comparison_result||{});item.append(detail);
      }
      reports.append(item);
    }
  }
  async function refresh(){
    const response=await fetch('/api/reference-lookups',{cache:'no-store'});const data=await response.json();
    if(!response.ok)throw Error('无法读取原题核对状态');
    panel.hidden=!data.enabled;if(!data.enabled)return;
    state=data;document.getElementById('reference-lookup-mode').textContent=data.shadow
      ?'Shadow：可以确认或拒绝候选；不会自动用于答疑。':'候选须先核对来源和逐字段证据，人工确认后才可辅助答疑。';
    const previous=select.value;select.replaceChildren();
    for(const q of state.questions||[]){const option=text('option',q.label);option.value=q.question_id;select.append(option);}
    if(state.questions.some(q=>q.question_id===previous))select.value=previous;
    form.querySelector('button').disabled=state.questions.length===0;render();
    if(!state.questions.length)status.textContent='暂无已建题任务；先完成题面归属，再核对原题。';
  }
  select.addEventListener('change',render);
  form.addEventListener('submit',async event=>{
    event.preventDefault();const q=state.questions.find(q=>q.question_id===select.value);if(!q)return;
    const button=form.querySelector('button');button.disabled=true;status.textContent='正在核对；此操作不会发送消息。';
    try{const candidate_urls=document.getElementById('reference-urls').value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean);
      const data=await post({action:'lookup',question_id:q.question_id,question_version:q.question_version,context_revision:q.context_revision,
        trigger:document.getElementById('reference-trigger').value,candidate_urls,retry:document.getElementById('reference-retry').checked});
      document.getElementById('reference-retry').checked=false;status.textContent=data.result.explanation||'内部报告已保存。';await refresh();
    }catch(error){status.textContent=error.message;}finally{button.disabled=state.questions.length===0;}
  });
  document.getElementById('reference-lookup-refresh').addEventListener('click',()=>refresh().catch(()=>{status.textContent='无法刷新原题核对状态。';}));
  refresh().catch(()=>{status.textContent='原题核对暂不可用，其他流程继续使用。';});
})();
