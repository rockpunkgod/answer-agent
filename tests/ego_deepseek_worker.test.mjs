import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {run,attachmentsReady,replyStable} from '../helpdesk/ego_deepseek.mjs';

test('read-aloud during streaming is not stable completion',()=>{
  const originalNow=Date.now;let now=1000;let text='partial';
  const previousDocument=globalThis.document;
  globalThis.document={querySelectorAll(){return [{get innerText(){return text},closest(){return {querySelector(){return {}},getAttribute(){return 'new'}}}}]}};
  Date.now=()=>now;
  const args={selector:'.reply',baseline:[],key:'anonymous-stream-test'};
  try {
    assert.equal(replyStable(args),false);
    now+=2100;text='still streaming';assert.equal(replyStable(args),false);
    now+=1000;assert.equal(replyStable(args),false);
    now+=1100;assert.equal(replyStable(args),true);
  } finally {Date.now=originalNow;globalThis.document=previousDocument;}
});

test('reused numeric space with wrong name stops before page access',async()=>{
  const f=await fixture();
  f.request.space_name='expected-space';
  const previous=globalThis.taskSpace;
  globalThis.taskSpace=async()=>({name:'another-space',page(){throw new Error('PAGE_MUST_NOT_BE_ACCESSED')}});
  try {await assert.rejects(run(f.request),/SPACE_IDENTITY_CHANGED/);}
  finally {globalThis.taskSpace=previous;await f.cleanup();}
});

async function fixture(options={}) {
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'ego-fixture-'));
  const file=path.join(directory,'approved.md');
  await fs.writeFile(file,'anonymous fixture');
  const request={space_id:1,page:'p1',session_url:'https://chat.deepseek.com/a/chat/s/fixture',
    prompt:'anonymous prompt',response_token:'fixture',fingerprint:'fixture-hash',
    state_path:path.join(directory,'state.json'),output_path:path.join(directory,'answer.json'),
    timeout_ms:100,capture_only:false,
    files:[{path:file,name:'approved.md',sha256:createHash('sha256').update('anonymous fixture').digest('hex')}]};
  await fs.writeFile(request.state_path,JSON.stringify({fingerprint:request.fingerprint,phase:'PREPARING'}));
  const state={url:request.session_url,value:'',uploads:0,submissions:0,answer:''};
  const node={get innerText(){return state.answer},closest(){return {getAttribute(){return '4'},querySelector(){return options.noFooter?null:{}}}}};
  const old={innerText:'old reply',closest(){return {getAttribute(){return '2'},querySelector(){return {}}}}};
  globalThis.document={
    querySelectorAll(selector){if(selector.includes('.ds-button--primary')){const button={getBoundingClientRect(){return {width:20,height:20}},getAttribute(){return options.disabledSend?'true':null},classList:{contains(){return Boolean(options.disabledSend)}}};return options.ambiguousSend?[button,button]:[button];}return selector.startsWith('textarea')?[{value:state.value}]:[...(options.oldAnswer?[old]:[]),...(state.answer?[node]:[])]},
    querySelector(selector){return selector==='.aaff8b8f'?{
      innerText:options.uploadFailure?'approved.md 上传失败':'approved.md',
      querySelector(){return null},querySelectorAll(selector){return selector==='.ds-animated-size-item'?[{}]:[]}
    }:{value:state.value}}
  };
  globalThis.location={get href(){return state.url}};
  const page={
    async url(){return state.url},
    async goto(url){state.url=options.redirectLogin?'https://chat.deepseek.com/sign_in':url},
    async evaluate(fn,arg){return fn(arg)},
    async setInputFiles(){state.uploads++},
    async waitForFunction(fn,arg){
      if(fn(arg))return;
      if(fn.name==='replyStable') {
        const original=Date.now;const now=original();
        try {Date.now=()=>now+2001;if(fn(arg))return;} finally {Date.now=original;}
      }
      throw new Error('WAIT_CONDITION_FAILED');
    },
    async fill(selector,text){state.value=options.stagedMismatch?'wrong':text;if(options.moveAfterFill)state.url='https://example.org/'},
    async press(){throw new Error('KEYBOARD_SUBMISSION_FORBIDDEN')},
    async click(){state.submissions++;if(options.newChat)state.url='https://chat.deepseek.com/a/chat/s/created';state.answer=options.plain?'第34题：这是自然语言答复。':'BEGIN_fixture\n{"text":"fixture"}\nEND_fixture';if(options.submitError)throw new Error('UNKNOWN_SUBMIT_OUTCOME')}
  };
  globalThis.taskSpace=async()=>({page(){return page}});
  return {request,state,file,cleanup:()=>fs.rm(directory,{recursive:true,force:true})};
}

test('image upload requires named loaded thumbnail and no active progress',()=>{
  const image={alt:'original.jpg',complete:true,naturalWidth:1279};
  let progress=[];
  const composer={innerText:'teaching.md',querySelectorAll(selector){return selector==='img'?[image]:progress}};
  globalThis.getComputedStyle=node=>node.style || {display:'flex',visibility:'visible',opacity:'1'};
  globalThis.document={querySelector(){return composer}};
  assert.equal(attachmentsReady(['teaching.md','original.jpg']),true);
  assert.equal(attachmentsReady(['teaching.md','other.jpg']),false);
  image.complete=false;
  assert.equal(attachmentsReady(['original.jpg']),false);
  image.complete=true;image.naturalWidth=0;
  assert.equal(attachmentsReady(['original.jpg']),false);
  image.naturalWidth=1279;
  const overlay={parentElement:composer,style:{display:'flex',visibility:'visible',opacity:'1'}};
  progress=[{parentElement:overlay,getClientRects:()=>[{}]}];
  assert.equal(attachmentsReady(['teaching.md','original.jpg']),false);
  overlay.style.opacity='0';
  assert.equal(attachmentsReady(['teaching.md','original.jpg']),true);
  overlay.style.opacity='1';overlay.style.display='none';
  assert.equal(attachmentsReady(['teaching.md','original.jpg']),true);
  progress=[];composer.innerText='teaching.md 上传失败';
  assert.equal(attachmentsReady(['teaching.md','original.jpg']),false);
});

test('upload failure prevents prompt submission',async()=>{
  const f=await fixture({uploadFailure:true});try{
    await assert.rejects(run(f.request),/WAIT_CONDITION_FAILED/);
    assert.equal(f.state.submissions,0);
  }finally{await f.cleanup()}
});

test('disabled or ambiguous send control stops before committing submission intent',async()=>{
  for(const options of [{disabledSend:true},{ambiguousSend:true}]){
    const f=await fixture(options);try{
      await assert.rejects(run(f.request),/WAIT_CONDITION_FAILED/);
      assert.equal(f.state.submissions,0);
      assert.equal(JSON.parse(await fs.readFile(f.request.state_path)).phase,'PREPARING');
    }finally{await f.cleanup()}
  }
});

test('explicit staged recovery uses ready original attachments without uploading again',async()=>{
  const f=await fixture({plain:true});try{
    f.request.response_token=null;
    await run(f.request,{resumeStaged:true});
    assert.equal(f.state.uploads,0);assert.equal(f.state.submissions,1);
    await assert.rejects(run(f.request,{resumeStaged:true}),/STAGED_RESUME_REQUIRES_UNSUBMITTED_REQUEST/);
    assert.equal(f.state.submissions,1);
  }finally{await f.cleanup()}
});

test('changed file and staged prompt both stop before submit',async()=>{
  for(const kind of ['file','prompt']){
    const f=await fixture({stagedMismatch:kind==='prompt'});try{
      if(kind==='file')await fs.writeFile(f.file,'changed');
      await assert.rejects(run(f.request),kind==='file'?/ATTACHMENT_CHANGED/:/STAGED_PROMPT_MISMATCH/);
      assert.equal(f.state.submissions,0);
    }finally{await f.cleanup()}
  }
});

test('navigation away during staging stops before submit',async()=>{
  const f=await fixture({moveAfterFill:true});try{
    await assert.rejects(run(f.request),/SESSION_CHANGED/);
    assert.equal(f.state.submissions,0);
  }finally{await f.cleanup()}
});

test('unknown button outcome recovers read-only without another click',async()=>{
  const f=await fixture({submitError:true});try{
    await assert.rejects(run(f.request),/UNKNOWN_SUBMIT_OUTCOME/);
    assert.equal(JSON.parse(await fs.readFile(f.request.state_path)).phase,'SUBMISSION_INTENT');
    f.request.capture_only=true;
    await run(f.request);
    assert.equal(f.state.submissions,1);
    const result=JSON.parse(await fs.readFile(f.request.output_path));
    assert.equal(result.adapter,'EGO_LITE');assert.equal(result.delivered,false);
    // Crash after writing output but before marking CAPTURED remains recoverable.
    await fs.writeFile(f.request.state_path,JSON.stringify({fingerprint:f.request.fingerprint,phase:'SUBMITTED'}));
    await run(f.request);
    assert.equal(f.state.submissions,1);
  }finally{await f.cleanup()}
});

test('end marker without completion footer is never captured',async()=>{
  const f=await fixture({noFooter:true});try{
    await assert.rejects(run(f.request),/WAIT_CONDITION_FAILED/);
    await assert.rejects(fs.stat(f.request.output_path),{code:'ENOENT'});
    assert.equal(JSON.parse(await fs.readFile(f.request.state_path)).phase,'SUBMITTED');
  }finally{await f.cleanup()}
});

test('plain question and natural answer require no JSON or boundary markers',async()=>{
  const f=await fixture({plain:true,oldAnswer:true});try{
    f.request.prompt='第34题';f.request.response_token=null;
    await run(f.request);
    assert.equal(f.state.value,'第34题');
    const result=JSON.parse(await fs.readFile(f.request.output_path));
    assert.equal(result.text,'第34题：这是自然语言答复。');
    assert.equal(result.completion_evidence,'NEW_ASSISTANT_AND_READ_ALOUD_CONTROL');
    assert.equal(f.state.submissions,1);
  }finally{await f.cleanup()}
});


test('one plain request creates and captures a new real chat URL',async()=>{
  const f=await fixture({plain:true,newChat:true});try{
    f.request.new_session=true;f.request.response_token=null;
    f.request.session_url='https://chat.deepseek.com/';f.state.url=f.request.session_url;
    await run(f.request);
    const result=JSON.parse(await fs.readFile(f.request.output_path));
    assert.equal(result.session_url,'https://chat.deepseek.com/a/chat/s/created');
    assert.equal(f.state.submissions,1);
  }finally{await f.cleanup()}
});

test('unknown first submission without a bound URL stops for manual identity verification',async()=>{
  const f=await fixture({plain:true,newChat:true,submitError:true});try{
    f.request.new_session=true;f.request.response_token=null;
    f.request.session_url='https://chat.deepseek.com/';f.state.url=f.request.session_url;
    await assert.rejects(run(f.request),/UNKNOWN_SUBMIT_OUTCOME/);
    f.request.capture_only=true;
    await assert.rejects(run(f.request),/NEW_SESSION_IDENTITY_REQUIRES_MANUAL_CHECK/);
    assert.equal(f.state.url,'https://chat.deepseek.com/a/chat/s/created');
    assert.equal(f.state.submissions,1);
  }finally{await f.cleanup()}
});


test('new request refuses a nonempty homepage instead of reusing another question',async()=>{
  const f=await fixture({plain:true,oldAnswer:true,newChat:true});try{
    f.request.new_session=true;f.request.response_token=null;
    f.request.session_url='https://chat.deepseek.com/';f.state.url=f.request.session_url;
    await assert.rejects(run(f.request),/NEW_CHAT_NOT_EMPTY/);
    assert.equal(f.state.submissions,0);assert.equal(f.state.uploads,0);
  }finally{await f.cleanup()}
});


test('a new chat with a recorded URL resumes capture without another Enter',async()=>{
  const options={plain:true,newChat:true,noFooter:true};
  const f=await fixture(options);try{
    f.request.new_session=true;f.request.response_token=null;
    f.request.session_url='https://chat.deepseek.com/';f.state.url=f.request.session_url;
    await assert.rejects(run(f.request),/WAIT_CONDITION_FAILED/);
    assert.equal(JSON.parse(await fs.readFile(f.request.state_path)).resolved_session_url,
                 'https://chat.deepseek.com/a/chat/s/created');
    options.noFooter=false;f.request.capture_only=true;
    await run(f.request);assert.equal(f.state.submissions,1);
  }finally{await f.cleanup()}
});
