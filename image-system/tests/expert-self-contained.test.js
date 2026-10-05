'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const expert=require('../src/expert-prompt'),contract=require('../integrations/anythingllm/provider-streaming/tool-contract');
const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
const request='תעשה תמונה של בית מגורים בתוך מערה בטבע';
const prompt='A lived-in home dwelling inside a cave in a natural setting. Photorealistic rendering shows rough rock enclosing the rooms, with trees outside the cave entrance.';
const facts=[{dimension:'subjects',classification:'USER_REQUIRED',source_text:'בית מגורים',text:'A lived-in home dwelling'},{dimension:'relationships_contact',classification:'USER_REQUIRED',source_text:'בתוך מערה',text:'inside a cave'},{dimension:'environment',classification:'USER_REQUIRED',source_text:'בטבע',text:'in a natural setting'}];
const record=()=>({contract:'aag.expert.prompt.v1',authoring_contract:'self-contained.v2',semantic_review_required:true,request_sha256:hash(request),sha256:hash(prompt),prompt,selected_style:'photography/photorealistic',plan:{facts,fragments:[{id:'scene',priority:1,text:prompt}],conflicts:[]},truncated:false,tokens_including_template:60});
const verdict=()=>({pass:true,issues:[],checks:Object.fromEntries(expert.REVIEW_CHECKS.map(k=>[k,true])),facts:facts.map((f,index)=>({index,preserved:true,evidence:f.text,protected_evidence:f.text}))});
const args={operation:'generate',prompt,expert_plan:{facts},source_policy:'auto',preservation:'none'};
const validate=async()=>({arguments:structuredClone(args),results:[record()]});
function agent(){
 const module={exports:{}};const dependencies={'./providers/tool-contract.js':contract,events:require('node:events'),'./error.js':{APIError:class extends Error{}},'./providers/index.js':{},'../../../models/telemetry.js':{Telemetry:{sendTelemetry(){}}},uuid:{v4:()=> 'fixture-id'},'./utils/toolReranker.js':{ToolReranker:class{}}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/provider-streaming/aibitat.js'),'utf8'),{module,process:{env:{}},console:{error(){}},AbortController,require:n=>{assert.ok(n in dependencies,n);return dependencies[n];}});return new module.exports();
}
function runtime(review){return {AAG_INVOCATION_PROMPT:request,AAG_EXPERT_ENVELOPE:'signed-local-fixture',AAG_EXPERT_REVIEW:review};}
test('NEW final prompt passes without source prefix only after independent hash-bound semantic review',async()=>{
 let calls=0;
 const r=await expert.prepare(args,runtime(async messages=>{calls++;assert.equal(messages.length,2);const input=JSON.parse(messages[1].content);assert.equal(input.original_user_request,request);assert.equal(input.final_generation_prompt,prompt);assert.deepEqual(input.facts,facts.map((f,index)=>({index,...f})));return JSON.stringify(verdict());}),validate);
 assert.equal(calls,1);assert.equal(r.args.prompt,prompt);assert.equal(r.args.expert_plan,undefined);assert.equal(r.records[0].semantic_review.prompt_sha256,hash(prompt));assert.equal(r.records[0].semantic_review.request_sha256,hash(request));assert.equal(r.records[0].semantic_review_required,false);assert.ok(!r.args.prompt.startsWith(request));
});
test('every independent invariant check fails closed instead of re-appending the original request',async()=>{
 for(const check of expert.REVIEW_CHECKS){const v=verdict();v.checks[check]=false;await assert.rejects(expert.prepare(args,runtime(async()=>JSON.stringify(v)),validate),{code:'EXPERT_SEMANTIC_REVIEW_FAILED'});}
 for(const mutate of [v=>v.pass=false,v=>v.issues=['ambiguous relation'],v=>v.facts[0].preserved=false]){const v=verdict();mutate(v);await assert.rejects(expert.prepare(args,runtime(async()=>JSON.stringify(v)),validate),{code:'EXPERT_SEMANTIC_REVIEW_FAILED'});}
});
test('missing audit, incomplete/provider failures and invalid schema prevent image submission',async()=>{
 await assert.rejects(expert.prepare(args,runtime(),validate),{code:'EXPERT_SEMANTIC_REVIEW_UNAVAILABLE'});
 for(const [output,code] of [['{','EXPERT_SEMANTIC_REVIEW_INCOMPLETE'],['x'.repeat(32769),'EXPERT_SEMANTIC_REVIEW_INVALID_RESPONSE'],[null,'EXPERT_SEMANTIC_REVIEW_INVALID_RESPONSE']])await assert.rejects(expert.prepare(args,runtime(async()=>output),validate),{code});
 await assert.rejects(expert.prepare(args,runtime(async()=>{throw Error('offline');}),validate),{code:'EXPERT_SEMANTIC_REVIEW_PROVIDER_ERROR'});
 for(const mutate of [v=>v.facts.pop(),v=>v.facts[1].index=0,v=>v.checks.extra=true,v=>v.extra='unexpected']){const v=verdict();mutate(v);let calls=0;await assert.rejects(expert.prepare(args,runtime(async()=>{calls++;return JSON.stringify(v);}),validate),{code:'EXPERT_SEMANTIC_REVIEW_INVALID_RESPONSE'});assert.equal(calls,2);}
});
test('local structure/hash/budget validation precedes semantic model calls',async()=>{
 let calls=0;const rt=runtime(async()=>{calls++;return JSON.stringify(verdict());});
 for(const change of [{sha256:'0'.repeat(64)},{request_sha256:'0'.repeat(64)},{tokens_including_template:513},{truncated:true},{authoring_contract:'legacy'}])await assert.rejects(expert.prepare(args,rt,async()=>({arguments:args,results:[{...record(),...change}]})));
 assert.equal(calls,0);
});
test('Expert OFF makes zero extra model or validator calls and permits original equal to final',async()=>{
 const off={operation:'generate',prompt:request};let calls=0;const rt={AAG_INVOCATION_PROMPT:request,AAG_EXPERT_REVIEW:async()=>{calls++;}};
 const r=await expert.prepare(off,rt,async()=>{calls++;});assert.equal(r.args,off);assert.equal(calls,0);
});
test('all active provider fixtures use the same existing instance, tool-free and without streamed audit leakage',async()=>{
 const a=agent();let called=[];let toolsExecuted=0;a.functions.set('aag-image-task',{handler:()=>toolsExecuted++});
 for(const provider of ['openai','anthropic','gemini','ollama']){
  for(const streaming of [true,false]){
   const p={model:'active-'+provider,supportsAgentStreaming:streaming};const run=async(messages,tools,event)=>{assert.equal(a.providerInstance,p);assert.equal(tools.length,0);assert.equal(messages[1].role,'user');called.push(p.model);if(event)event('reportStreamEvent',{type:'textResponseChunk',content:'PRIVATE AUDIT'});return {textResponse:JSON.stringify(verdict()),termination:{state:'RESPONSE_COMPLETE'}};};p.stream=run;p.complete=run;a.providerInstance=p;
   const r=await expert.prepare(args,runtime(messages=>a.reviewExpertPrompt(messages)),validate);assert.equal(r.args.prompt,prompt);
  }
 }
 assert.equal(called.length,8);assert.equal(toolsExecuted,0);assert.deepEqual(called,['active-openai','active-openai','active-anthropic','active-anthropic','active-gemini','active-gemini','active-ollama','active-ollama']);
});
test('semantic review never accepts partial completion, tool requests, cancellation or non-text',async()=>{
 const a=agent();let executions=0;a.functions.set('aag-image-task',{handler:()=>executions++});
 for(const [result,code] of [[{textResponse:JSON.stringify(verdict()),termination:{state:'MISSING_TERMINAL_DATA'}},'EXPERT_SEMANTIC_REVIEW_INCOMPLETE'],[{functionCall:{name:'aag-image-task',arguments:{prompt}}},'EXPERT_SEMANTIC_REVIEW_INVALID_RESPONSE'],[{textResponse:null},'EXPERT_SEMANTIC_REVIEW_INVALID_RESPONSE']]){a.providerInstance={supportsAgentStreaming:true,stream:async()=>result};await assert.rejects(expert.prepare(args,runtime(m=>a.reviewExpertPrompt(m)),validate),{code});}
 a._aborted=true;await assert.rejects(a.reviewExpertPrompt([]));assert.equal(executions,0);
});
test('semantic audit sends only the current source, candidate, selected style and facts',async()=>{
 const r={...record(),secret:'NEVER_SEND',context:{unrelated:'NEVER_SEND'},plan:{...record().plan,unrelated:'NEVER_SEND'}};
 await expert.reviewCandidate(request,r,async messages=>{assert.ok(!JSON.stringify(messages).includes('NEVER_SEND'));assert.deepEqual(Object.keys(JSON.parse(messages[1].content)).sort(),['facts','final_generation_prompt','original_user_request','protected_prose','selected_style','style_guidance']);return JSON.stringify(verdict());});
});

test('actual translated plan validation feeds runtime, recording Comfy adapter and two immutable provenance chains',async()=>{
 const cp=require('node:child_process'),os=require('node:os'),runtimeCore=require('../src/runtime'),store=require('../src/store'),p=require('../src/prompt-provenance'),comfy=require('../src/comfy');
 const python="import sys,json;sys.path.insert(0,'expert-prompt/tests');from test_self_contained import fixture;from neutral_context import validate_candidate; r,i,p,plan,_=fixture(); print(json.dumps({'request':r,'prompt':p,'plan':plan,'record':validate_candidate(r,i,p,plan)}))";
 const f=JSON.parse(cp.execFileSync('python3',['-c',python],{cwd:path.resolve(__dirname,'..'),encoding:'utf8'}));
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'aag-self-contained-'));let handoff,executeCount=0;
 const args={operation:'generate',prompt:f.prompt,source_policy:'auto',preservation:'none',quality:'fast',expert_plan:f.plan};
 const rt={AAG_WORKSPACE_ID:'fixture',AAG_THREAD_ID:'fixture',AAG_USER_ID:'fixture',AAG_INVOCATION_UUID:'fixture',AAG_TURN_ID:'fixture',AAG_IMAGE_AGENT_STATE_ROOT:root,AAG_INVOCATION_PROMPT:f.request,AAG_EXPERT_ENVELOPE:'test-only',AAG_EXPERT_REVIEW:async messages=>{const input=JSON.parse(messages[1].content);assert.equal(input.original_user_request,f.request);assert.equal(input.final_generation_prompt,f.prompt);return JSON.stringify({...verdict(),facts:f.plan.facts.map((x,index)=>({index,preserved:true,evidence:x.text,protected_evidence:x.text}))});}};
 const bytes=Buffer.alloc(160,1);Buffer.from([137,80,78,71,13,10,26,10]).copy(bytes);bytes.writeUInt32BE(8,16);bytes.writeUInt32BE(8,20);
 const deps={expertValidate:async()=>({arguments:args,results:[f.record]}),requireReady:async()=>{},scheduler:{engineActivity:async()=>({active:false}),disableHeartbeat:true},adapters:{execute:async(task,source,runtime,context,id,adapterDeps)=>{
  executeCount++;const model=comfy.PROFILES.fast;return comfy.execute(task,source,id,{...adapterDeps,fetch:async(url,options)=>{let result;if(url.endsWith('/object_info'))result=Object.fromEntries(comfy.COMMON_NODES.map(k=>[k,{}]));else if(url.includes('/models/'))result=[model.unet,model.clip,model.vae];else if(url.endsWith('/prompt')){handoff=JSON.parse(options.body).prompt['4'].inputs.text;result={prompt_id:'recording-only'};}else if(url.endsWith('/history/recording-only'))result={'recording-only':{status:{completed:true},outputs:{output:{images:[{filename:'fixture.png'}]}}}};else if(url.endsWith('/import-comfyui'))result={ok:true,filename:'fixture.png'};else throw Error('Unexpected path');return {ok:true,text:async()=>JSON.stringify(result)};}});
 },fetch:async()=>({ok:true,headers:{get:()=> 'image/png'},arrayBuffer:async()=>bytes}),inspectOutput:async()=>({width:8,height:8,format:'png'})}};
 try{
  const result=await runtimeCore.createTask(args,rt,{deps});assert.match(result,/status=completed/);assert.equal(executeCount,1);assert.equal(handoff,f.prompt);assert.ok(!handoff.startsWith(f.request));
  const jobs=fs.readdirSync(path.join(root,'jobs')).map(n=>store.read(root,n.replace('.json','')));const parent=jobs.find(j=>!j.parent_job_id),child=jobs.find(j=>j.parent_job_id);
  assert.equal(p.read(root,parent.job_id,'original-request-provenance').original_user_request,f.request);assert.equal(p.read(root,child.job_id).final_generation_prompt,handoff);assert.equal(p.read(root,child.job_id).sha256,hash(handoff));assert.equal(parent.expert_prompt.semantic_review.prompt_sha256,hash(handoff));
  const invalidRt={...rt,AAG_TURN_ID:'invalid',AAG_EXPERT_REVIEW:async()=>JSON.stringify({...verdict(),pass:false,issues:['lost cave relationship'],facts:f.plan.facts.map((x,index)=>({index,preserved:false,evidence:'missing',protected_evidence:''}))})};const count=fs.readdirSync(path.join(root,'jobs')).length;
  assert.match(await runtimeCore.createTask(args,invalidRt,{deps}),/EXPERT_SEMANTIC_REVIEW_FAILED/);assert.equal(executeCount,1);assert.equal(fs.readdirSync(path.join(root,'jobs')).length,count);
 }finally{fs.rmSync(root,{recursive:true,force:true});}
});

test('a batch is reviewed sequentially with each item scope and no new provider selection',async()=>{
 let active=0,maxActive=0;const scopes=[];
 const result=await expert.prepare({items:[args,args]},runtime(async messages=>{maxActive=Math.max(maxActive,++active);scopes.push(JSON.parse(messages[1].content).output_scope);await new Promise(r=>setTimeout(r,1));active--;return JSON.stringify(verdict());}),async()=>({arguments:{items:[args,args],collection_brief:'Two views of the cave home'},results:[record(),record()]}));
 assert.equal(result.records.length,2);assert.equal(maxActive,1);assert.deepEqual(scopes.map(s=>s.index),[1,2]);assert.ok(scopes.every(s=>s.count===2));
});


test('optional enrichment can be omitted without weakening protected user facts',async()=>{
 const r=record();r.plan={...r.plan,facts:[...r.plan.facts,{dimension:'lighting',classification:'OPTIONAL_ENRICHMENT',source_text:'',text:'Optional soft fill'}]};
 const v=verdict();const out=await expert.reviewCandidate(request,r,async()=>JSON.stringify(v));assert.equal(out.pass,true);
 v.facts.push({index:3,preserved:false,evidence:'missing',protected_evidence:''});r.plan.facts[3]={dimension:'exclusions',classification:'DO_NOT_INVENT',source_text:'source constraint',text:'Required prohibition'};
 await assert.rejects(expert.reviewCandidate(request,r,async()=>JSON.stringify(v)),{code:'EXPERT_SEMANTIC_REVIEW_FAILED'});
});
