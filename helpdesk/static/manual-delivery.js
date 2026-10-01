/* Local human registration only: no clipboard, desktop or send operation. */
(() => {
  const panel=document.getElementById('manual-delivery-panel');
  const form=document.getElementById('manual-delivery-form');
  const select=document.getElementById('manual-delivery-task');
  const status=document.getElementById('manual-delivery-status');
  const current=document.getElementById('manual-delivery-current');
  const field=name=>document.getElementById('manual-delivery-'+name);
  let tasks=[],submitting=false;
  function text(tag,value){const node=document.createElement(tag);node.textContent=value;return node;}
  const key=row=>row.outbox_id||'turn:'+row.turn_id;
  function task(){return tasks.find(t=>key(t)===select.value);}
  function show(){
    const row=task();current.replaceChildren();
    if(!row)return;
    form.querySelector('button[type="submit"]').disabled=Boolean(row.completed);
    current.append(text('p',row.completed?'该任务已登记完整交付，保留各部分记录供核对。':'尚未登记完整交付。'));
    field('total').value=row.total_parts||1;field('total').disabled=Boolean(row.total_parts);
    field('part').max=field('total').value;
    const numbers=new Set(row.parts.map(p=>p.part_number));
    let next=1;while(numbers.has(next)&&next<Number(field('total').value))next++;
    field('part').value=next;
    field('content').value='';field('time').value='';field('attested').checked=false;
    if(row.stale)current.append(text('p','原任务版本已变化。历史交付可以留存，绩效仍需重新核对。'));
    const detail=document.createElement('details');detail.append(text('summary','查看原生成稿（不代表已交付）'),text('pre',row.draft));current.append(detail);
    for(const part of row.parts){
      const verified=part.state==='SENT_UI_CONFIRMED'&&part.check_status==='SENT_UI_CONFIRMED';
      const block=document.createElement('details');block.append(text('summary',`第${part.part_number}/${part.total_parts}部分 · ${verified?'已由'+part.reviewer+'人工核验':'核验状态需核对'}`));
      block.append(text('p','实际交付时间：'+part.confirmed_at),text('pre',part.actual_content||'附件交付'),
        text('p','依据：'+part.verification_evidence));
      for(const attachment of part.attachments||[])block.append(text('p','附件：'+attachment.name));
      current.append(block);
    }
  }
  async function refresh(){
    const response=await fetch('/api/manual-deliveries',{cache:'no-store'});
    if(!response.ok)throw Error('读取失败');
    const data=await response.json();tasks=data.tasks||[];
    const previous=select.value;select.replaceChildren();
    for(const row of tasks){const option=text('option',row.label);option.value=key(row);select.append(option);}
    if(tasks.some(t=>key(t)===previous))select.value=previous;
    const attachments=field('attachments');attachments.replaceChildren();
    for(const name of data.attachment_files||[]){const option=text('option',name);option.value=name;attachments.append(option);}
    form.hidden=!tasks.length;
    if(!tasks.length){current.replaceChildren();status.textContent='暂无可登记的正式答疑任务。本机练习题不计入正式交付。';}
    show();
  }
  select.addEventListener('change',show);
  field('total').addEventListener('change',()=>{field('part').max=field('total').value;});
  form.addEventListener('submit',async event=>{
    event.preventDefault();const row=task();if(submitting||!row||row.completed||!form.reportValidity()||!field('attested').checked)return;
    const payload={
      ...(row.outbox_id?{original_outbox_id:row.outbox_id}:{turn_id:row.turn_id}),question_version:row.question_version,context_revision:row.context_revision,
      reviewer:field('reviewer').value,verification_evidence:field('evidence').value,delivered_at:field('time').value,
      content:field('content').value,part_number:Number(field('part').value),total_parts:Number(field('total').value),
      attachments:[...field('attachments').selectedOptions].map(o=>o.value)};
    submitting=true;select.disabled=true;
    const buttons=panel.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);
    try{
      const state=await fetch('/api/state',{cache:'no-store'});if(!state.ok)throw Error('令牌读取失败');
      const token=(await state.json()).csrf_token;
      const response=await fetch('/api/manual-deliveries',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify(payload)});
      const data=await response.json();if(!response.ok){status.textContent=data.error||'交付登记失败，请核对任务、时间与实际证据。';return;}
      await refresh();
      status.textContent=data.result.state==='PARTIAL_DELIVERY'?'已保存这一部分，其他部分尚未全部核验，不计完成。':
        data.result.counting_status==='CONFIRMED'?'人工核验交付已回流，沿用原计量单元。':'交付记录已保存，绩效关系或版本仍需核对。';
      field('attested').checked=false;
    }catch(error){status.textContent='登记结果未确认，请刷新记录后核对，避免重复登记。';}
    finally{submitting=false;select.disabled=false;buttons.forEach(b=>b.disabled=false);form.querySelector('button[type="submit"]').disabled=Boolean(task()?.completed);}
  });
  field('attested').checked=false;
  field('time').value='';
  document.getElementById('manual-delivery-refresh').addEventListener('click',()=>refresh().catch(()=>{status.textContent='无法读取交付任务，请检查本地服务。';}));
  refresh().catch(()=>{status.textContent='无法读取交付任务，请检查本地服务。';});
})();
