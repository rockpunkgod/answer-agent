// Executed only by the installed ego-browser Node runtime, not ordinary Node.
import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';

const editor = 'textarea[name="search"]';
const answers = '.ds-assistant-message-main-content';
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

export function attachmentsReady(names) {
  const composer=document.querySelector('.aaff8b8f');
  if(!composer) return false;
  const visible=node=>{
    for(let current=node;current;current=current.parentElement) {
      const style=getComputedStyle(current);
      if(style.display==='none'||style.visibility==='hidden'||style.visibility==='collapse'||Number(style.opacity)===0) return false;
      if(current===composer) break;
    }
    return node.getClientRects().length>0;
  };
  if([...composer.querySelectorAll('.ds-loading, [aria-busy="true"], [role="progressbar"]')].some(visible)) return false;
  const text=composer.innerText || '';
  if(/上传中|解析中|上传失败|解析失败|文件过大|uploading|processing|upload failed|parse failed/i.test(text)) return false;
  const images=[...composer.querySelectorAll('img')];
  return names.every(name=>text.includes(name) || images.some(image=>
    image.alt === name && image.complete && image.naturalWidth > 0));
}

export function readReply({selector,token,baseline}) {
  const nodes=[...document.querySelectorAll(selector)].filter(node=>{
    const row=node.closest('[data-virtual-list-item-key]');
    if(!row?.querySelector('[aria-label="朗读"]')) return false;
    const text=node.innerText.trim();
    if(token) return text.startsWith('BEGIN_'+token+'\n') && text.endsWith('END_'+token);
    const key=row.getAttribute('data-virtual-list-item-key');
    return key !== null && !baseline.includes(key) && text.length > 0;
  });
  return nodes.length === 1 ? nodes[0].innerText.trim() : null;
}

async function saveState(request, values) {
  const current = JSON.parse(await fs.readFile(request.state_path, 'utf8'));
  if (current.fingerprint !== request.fingerprint) throw new Error('REQUEST_CHANGED');
  const path = request.state_path + '.tmp';
  await fs.writeFile(path, JSON.stringify({...current,...values}), 'utf8');
  await fs.rename(path,request.state_path);
}

export async function run(request, {resumeStaged=false}={}) {
  const persisted=JSON.parse(await fs.readFile(request.state_path,'utf8'));
  if(persisted.fingerprint !== request.fingerprint) throw new Error('REQUEST_CHANGED');
  if(request.capture_only && !['SUBMISSION_INTENT','SUBMITTED'].includes(persisted.phase)) throw new Error('NOT_SUBMITTED');
  if(resumeStaged && (request.capture_only || persisted.phase!=='PREPARING')) throw new Error('STAGED_RESUME_REQUIRES_UNSUBMITTED_REQUEST');
  const task = await taskSpace(request.space_id);
  const page = task.page(request.page);
  const expected=persisted.resolved_session_url || request.session_url;
  const isChat=url=>/^https:\/\/chat\.deepseek\.com\/a\/chat\/s\/[A-Za-z0-9-]+$/.test(url);
  if(resumeStaged && await page.url() !== expected) throw new Error('STAGED_PAGE_CHANGED');
  if(request.new_session && request.capture_only && !persisted.resolved_session_url) {
    // Unknown first Enter: observe the original page, never navigate to a blank chat.
    throw new Error('NEW_SESSION_IDENTITY_REQUIRES_MANUAL_CHECK');
  } else {
    if (await page.url() !== expected) await page.goto(expected);
    if (await page.url() !== expected) throw new Error('LOGIN_OR_SESSION_MISMATCH');
  }
  if(request.new_session && !request.capture_only) {
    const count=await page.evaluate(selector=>document.querySelectorAll(selector).length,answers);
    if(count !== 0) throw new Error('NEW_CHAT_NOT_EMPTY');
  }
  if (!request.capture_only) {
    const state = JSON.parse(await fs.readFile(request.state_path, 'utf8'));
    if (state.fingerprint !== request.fingerprint || state.phase !== 'PREPARING') throw new Error('PRIOR_ATTEMPT');
    const current = await page.evaluate(selector => {
      const nodes=document.querySelectorAll(selector);
      return {count:nodes.length,value:nodes[0]?.value};
    }, editor);
    if (current.count !== 1 || current.value) throw new Error('EDITOR_NOT_EMPTY_OR_AMBIGUOUS');
    for (const file of request.files) {
      if (digest(await fs.readFile(file.path)) !== file.sha256) throw new Error('ATTACHMENT_CHANGED');
    }
    if (request.files.length) {
      if (await page.url() !== request.session_url) throw new Error('SESSION_CHANGED');
      if(resumeStaged) {
        const count=await page.evaluate(()=>document.querySelector('.aaff8b8f')?.querySelectorAll('.ds-animated-size-item').length);
        if(count !== request.files.length) throw new Error('STAGED_ATTACHMENTS_CHANGED');
      }
      if(!resumeStaged) await page.setInputFiles('input[type="file"]',request.files.map(f=>f.path));
      await page.waitForFunction(attachmentsReady, request.files.map(f=>f.name), {timeout:60_000});
    }
    if (await page.url() !== request.session_url) throw new Error('SESSION_CHANGED');
    await page.fill(editor,request.prompt);
    const staged=await page.evaluate(selector=>document.querySelector(selector)?.value,editor);
    if (staged !== request.prompt) throw new Error('STAGED_PROMPT_MISMATCH');
    if (await page.url() !== request.session_url) throw new Error('SESSION_CHANGED');
    const baseline=await page.evaluate(selector=>[...document.querySelectorAll(selector)].map(
      node=>node.closest('[data-virtual-list-item-key]')?.getAttribute('data-virtual-list-item-key')),answers);
    if(baseline.some(key=>key === null || key === undefined)) throw new Error('ANSWER_IDENTITY_UNAVAILABLE');
    await saveState(request,{phase:'SUBMISSION_INTENT',baseline_answer_keys:baseline}); // Persist before Enter.
    await page.press(editor,'Enter');
    if(request.new_session) {
      await page.waitForFunction(()=>/^https:\/\/chat\.deepseek\.com\/a\/chat\/s\/[A-Za-z0-9-]+$/.test(location.href),null,{timeout:15_000});
      const created=await page.url();
      if(!isChat(created)) throw new Error('NEW_SESSION_IDENTITY_REQUIRES_MANUAL_CHECK');
      await saveState(request,{phase:'SUBMITTED',resolved_session_url:created});
    } else await saveState(request,{phase:'SUBMITTED'});
  }
  const saved=JSON.parse(await fs.readFile(request.state_path,'utf8'));
  if(!request.response_token && !Array.isArray(saved.baseline_answer_keys)) throw new Error('MISSING_REPLY_BASELINE');
  const capture={selector:answers,token:request.response_token,baseline:saved.baseline_answer_keys || []};
  await page.waitForFunction(readReply,capture,{timeout:request.timeout_ms});
  const resolved=await page.url();
  if(request.new_session) {
    if(!isChat(resolved) || (saved.resolved_session_url && saved.resolved_session_url !== resolved)) throw new Error('SESSION_CHANGED');
    await saveState(request,{resolved_session_url:resolved});
  } else if(resolved !== request.session_url) throw new Error('SESSION_CHANGED');
  const response=await page.evaluate(readReply,capture);
  if(!response) throw new Error('OUTPUT_NOT_UNIQUE');
  const result={adapter:'EGO_LITE',space_id:request.space_id,page:request.page,
    session_url:resolved,response_token:request.response_token,text:response,
    complete:true,completion_evidence:request.response_token?'END_MARKER_AND_READ_ALOUD_CONTROL':'NEW_ASSISTANT_AND_READ_ALOUD_CONTROL',
    upload_manifest:request.files,delivered:false,formal_statistics_eligible:false};
  const raw=JSON.stringify(result,null,2);
  try {
    await fs.writeFile(request.output_path,raw,{encoding:'utf8',flag:'wx'});
  } catch(error) {
    if(error.code !== 'EEXIST' || await fs.readFile(request.output_path,'utf8') !== raw) throw error;
  }
  await saveState(request,{phase:'CAPTURED',output_sha256:digest(Buffer.from(raw,'utf8'))});
  console.log(JSON.stringify({status:'CAPTURED',output_path:request.output_path}));
}
