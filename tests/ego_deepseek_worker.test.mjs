import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {run} from '../helpdesk/ego_deepseek.mjs';

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
  const state={url:request.session_url,value:'',uploads:0,presses:0,answer:''};
  const node={get innerText(){return state.answer},closest(){return {getAttribute(){return '4'},querySelector(){return options.noFooter?null:{}}}}};
  const old={innerText:'old reply',closest(){return {getAttribute(){return '2'},querySelector(){return {}}}}};
  globalThis.document={
    querySelectorAll(selector){return selector.startsWith('textarea')?[{value:state.value}]:[...(options.oldAnswer?[old]:[]),...(state.answer?[node]:[])]},
    querySelector(selector){return selector==='.aaff8b8f'?{innerText:options.uploadFailure?'approved.md 上传失败':'approved.md'}:{value:state.value}}
  };
  const page={
    async url(){return state.url},
    async goto(url){state.url=options.redirectLogin?'https://chat.deepseek.com/sign_in':url},
    async evaluate(fn,arg){return fn(arg)},
    async setInputFiles(){state.uploads++},
    async waitForFunction(fn,arg){if(!fn(arg))throw new Error('WAIT_CONDITION_FAILED')},
    async fill(selector,text){state.value=options.stagedMismatch?'wrong':text;if(options.moveAfterFill)state.url='https://example.org/'},
    async press(){state.presses++;state.answer=options.plain?'第34题：这是自然语言答复。':'BEGIN_fixture\n{"text":"fixture"}\nEND_fixture';if(options.pressError)throw new Error('UNKNOWN_PRESS_OUTCOME')}
  };
  globalThis.taskSpace=async()=>({page(){return page}});
  return {request,state,file,cleanup:()=>fs.rm(directory,{recursive:true,force:true})};
}

test('upload failure prevents prompt submission',async()=>{
  const f=await fixture({uploadFailure:true});try{
    await assert.rejects(run(f.request),/WAIT_CONDITION_FAILED/);
    assert.equal(f.state.presses,0);
  }finally{await f.cleanup()}
});

test('changed file and staged prompt both stop before Enter',async()=>{
  for(const kind of ['file','prompt']){
    const f=await fixture({stagedMismatch:kind==='prompt'});try{
      if(kind==='file')await fs.writeFile(f.file,'changed');
      await assert.rejects(run(f.request),kind==='file'?/ATTACHMENT_CHANGED/:/STAGED_PROMPT_MISMATCH/);
      assert.equal(f.state.presses,0);
    }finally{await f.cleanup()}
  }
});

test('navigation away during staging stops before Enter',async()=>{
  const f=await fixture({moveAfterFill:true});try{
    await assert.rejects(run(f.request),/SESSION_CHANGED/);
    assert.equal(f.state.presses,0);
  }finally{await f.cleanup()}
});

test('unknown Enter outcome recovers read-only without another Enter',async()=>{
  const f=await fixture({pressError:true});try{
    await assert.rejects(run(f.request),/UNKNOWN_PRESS_OUTCOME/);
    assert.equal(JSON.parse(await fs.readFile(f.request.state_path)).phase,'SUBMISSION_INTENT');
    f.request.capture_only=true;
    await run(f.request);
    assert.equal(f.state.presses,1);
    const result=JSON.parse(await fs.readFile(f.request.output_path));
    assert.equal(result.adapter,'EGO_LITE');assert.equal(result.delivered,false);
    // Crash after writing output but before marking CAPTURED remains recoverable.
    await fs.writeFile(f.request.state_path,JSON.stringify({fingerprint:f.request.fingerprint,phase:'SUBMITTED'}));
    await run(f.request);
    assert.equal(f.state.presses,1);
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
    assert.equal(f.state.presses,1);
  }finally{await f.cleanup()}
});
