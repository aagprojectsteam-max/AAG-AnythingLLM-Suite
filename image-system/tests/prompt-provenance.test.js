'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),os=require('os'),path=require('path'),crypto=require('crypto');
const p=require('../src/prompt-provenance'),store=require('../src/store'),comfy=require('../src/comfy');
function fixture(t,operation='generate'){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'aag-provenance-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const parent=store.createRecord(root,{operation}),child=store.createRecord(root,{operation,parent_job_id:parent.job_id,child_index:1});
 const artifact={artifact_id:'artifact-'+crypto.randomUUID(),child_job_id:child.job_id,sha256:p.hash('pixels')};
 parent.artifacts=[artifact];child.artifacts=[artifact];store.write(root,parent);store.write(root,child);
 return {root,parent,child,artifact,payload:{jobId:parent.job_id,artifactId:artifact.artifact_id,artifactSha256:artifact.sha256}};
}
function record(f,text){return p.capture(f.root,f.child,{prompt:text,boundary:'comfy.CLIPTextEncode.text'});}
test('immutable exact Unicode, CRLF, whitespace and long engine prompt survives independent reads',t=>{
 const f=fixture(t),text='  שלום — a red cube.\r\n'+('Lighting: soft. Materials: brushed copper.\n'.repeat(200))+'\n  ';
 const r=record(f,text),before=fs.readFileSync(path.join(f.root,'prompt-provenance',f.child.job_id+'.json'));
 assert.equal(p.resolve(f.root,f.payload).prompt,text);assert.equal(p.read(f.root,f.child.job_id).final_generation_prompt_sha256,p.hash(text));
 assert.equal(record(f,text).captured_at,r.captured_at);assert.deepEqual(fs.readFileSync(path.join(f.root,'prompt-provenance',f.child.job_id+'.json')),before);
 assert.throws(()=>record(f,text+'changed'),{code:'PROMPT_PROVENANCE_CONFLICT'});
 assert.equal(fs.statSync(path.join(f.root,'prompt-provenance',f.child.job_id+'.json')).mode&0o777,0o600);
});
test('artifact identity, image hash and engine prompt hash must all agree',t=>{
 const f=fixture(t);record(f,'One blue cube.');
 assert.equal(p.resolve(f.root,{...f.payload,artifactId:'artifact-'+crypto.randomUUID()}).available,false);
 assert.equal(p.resolve(f.root,{...f.payload,artifactSha256:p.hash('wrong')}).available,false);
 f.child.engine.final_prompt_sha256=p.hash('wrong');store.write(f.root,f.child);assert.equal(p.resolve(f.root,f.payload).available,false);
});
test('historical proposals are withheld unless exact engine hash already proves the stored text',t=>{
 const f=fixture(t);f.parent.public_arguments={prompt:'earlier draft'};store.write(f.root,f.parent);
 assert.equal(p.resolve(f.root,{...f.payload,prompt:'guess'}).available,false);
 f.child.engine.final_prompt_sha256=p.hash('actual final');store.write(f.root,f.child);
 assert.equal(p.resolve(f.root,f.payload).available,false);
 assert.equal(p.resolve(f.root,{...f.payload,prompt:'actual final'}).source,'historical_engine_hash_match');
 assert.equal(fs.existsSync(path.join(f.root,'prompt-provenance')),false);
});
test('multiple children and batch collections resolve each artifact independently',t=>{
 const a=fixture(t),b=fixture(t);record(a,'Red cube');record(b,'Blue sphere');
 // Put the second normal job under the same private store as a batch would.
 store.write(a.root,b.parent);store.write(a.root,b.child);p.capture(a.root,b.child,{prompt:'Blue sphere',boundary:'comfy.CLIPTextEncode.text'});
 const collection=store.createRecord(a.root,{schema_version:3,operation:'generate'});collection.artifacts=[a.artifact,b.artifact];store.write(a.root,collection);
 assert.equal(p.resolve(a.root,{...a.payload,jobId:collection.job_id}).prompt,'Red cube');
 assert.equal(p.resolve(a.root,{...b.payload,jobId:collection.job_id}).prompt,'Blue sphere');
});
test('enhanced generation labels its original prompt; pure upscale has no new prompt',t=>{
 const f=fixture(t);record(f,'Cube');f.artifact.derived_from_artifact_id='artifact-'+crypto.randomUUID();store.write(f.root,f.parent);store.write(f.root,f.child);
 assert.equal(p.resolve(f.root,f.payload).derived,true);
 f.child.operation='upscale';store.write(f.root,f.child);assert.equal(p.resolve(f.root,f.payload).available,false);
});
test('credentials and hidden envelopes are neither persisted nor exposed as prompt text',t=>{
 for(const text of ['Authorization: test','sk-proj-'+'x'.repeat(30),'AAG_COMPOSER_STRUCTURED_REQUIREMENTS_V1={}', 'Bearer '+'x'.repeat(30),'-----BEGIN PRIVATE KEY-----']){
  const f=fixture(t),r=record(f,text);assert.equal(r.available,false);assert.equal('prompt' in r,false);assert.equal(p.resolve(f.root,f.payload).available,false);
 }
 assert.equal(p.publishable('blue private-credential-value cube',{SOME_API_KEY:'private-credential-value'}),false);
 assert.equal(p.publishable('ordinary cube',{SOME_API_KEY:'private-credential-value'}),true);
});
for(const operation of ['generate','transform'])test('actual '+operation+' graph records exact final text before transport, not the earlier draft',async t=>{
 const f=fixture(t,operation);const prompt='  One cube — שלום.\nSoft light.  ',metadata=[];let handoff;
 const model=comfy.PROFILES.fast;
 const fetch=async(url,options)=>{
  let result;if(url.endsWith('/object_info'))result=Object.fromEntries([...comfy.COMMON_NODES,...comfy.GENERAL_NODES].map(x=>[x,{}]));
  else if(url.includes('/models/'))result=[model.unet,model.clip,model.vae];
  else if(url.endsWith('/upload/image'))result={name:'source.png',subfolder:'test'};
  else if(url.endsWith('/prompt')){handoff=JSON.parse(options.body).prompt[operation==='generate'?'4':'7'].inputs.text;assert.equal(p.read(f.root,f.child.job_id).final_generation_prompt,handoff);result={prompt_id:'stub'};}
  else if(url.endsWith('/history/stub'))result={stub:{status:{completed:true},outputs:{output:{images:[{filename:'fixture.png'}]}}}};
  else if(url.endsWith('/import-comfyui'))result={ok:true,filename:'fixture.png'};
  else throw Error('Unexpected network path '+url);
  return {ok:true,text:async()=>JSON.stringify(result)};
 };
 const reference={bytes:Buffer.alloc(64),width:8,height:8};
 await comfy.execute({operation,prompt,request:'different user text',quality:'fast',seed:1},reference,'test-lease',{fetch,prepareGeneralReference:async()=>reference,onFinalPrompt:value=>p.capture(f.root,f.child,value),onEngineMetadata:v=>metadata.push(v)});
 assert.equal(p.resolve(f.root,f.payload).prompt,handoff);assert.equal(metadata[0].final_prompt_sha256,p.hash(handoff));
 if(operation==='generate')assert.equal(handoff,prompt);else {assert.notEqual(handoff,prompt);assert.match(handoff,/supplied authorized reference/);}
});
test('identity bridges capture the message consumed unchanged by frozen workers',()=>{
 for(const [name,boundary] of [['human-identity','pulid'],['scene-identity','scene']]){
  const source=fs.readFileSync(path.join(__dirname,'../src',name+'.js'),'utf8');
  assert.ok(source.indexOf('deps.onFinalPrompt?.({prompt:message.prompt,boundary:"'+boundary+'.encode_prompt.prompt"})')<source.indexOf('try { atomicJson(path.join(STATE_ROOT, "inbox"'));
 }
});

test('two independent exact fields and hashes; original request is never concatenated into engine text',t=>{
 const f=fixture(t),original='  תעשה תמונה של אופנוע מירוצים.\r\nבצבע כחול — blue!  ',final='A single modern racing motorcycle.\n'+'Soft rim light. '.repeat(300);
 p.captureOriginal(f.root,f.parent,original);record(f,final);
 const value=p.resolve(f.root,f.payload);
 assert.equal(value.original_user_request,original);assert.equal(value.original_user_request_sha256,p.hash(original));
 assert.equal(value.final_generation_prompt,final);assert.equal(value.final_generation_prompt_sha256,p.hash(final));
 assert.equal(value.final_generation_prompt.includes(original),false);
 const old=p.read(f.root,f.parent.job_id,'original-request-provenance');p.captureOriginal(f.root,f.parent,original);
 assert.deepEqual(p.read(f.root,f.parent.job_id,'original-request-provenance'),old);
 assert.throws(()=>p.captureOriginal(f.root,f.parent,'changed'),{code:'PROMPT_PROVENANCE_CONFLICT'});
});
test('missing or corrupt original request does not disable an exact final prompt',t=>{
 const f=fixture(t);record(f,'Exact final');
 assert.equal(p.resolve(f.root,f.payload).original_user_request_available,false);
 p.captureOriginal(f.root,f.parent,'Original');
 fs.writeFileSync(path.join(f.root,'original-request-provenance',f.parent.job_id+'.json'),'{broken');
 const value=p.resolve(f.root,f.payload);assert.equal(value.available,true);assert.equal(value.original_user_request_available,false);assert.equal(value.final_generation_prompt,'Exact final');
});
test('legacy final provenance is read without rewrite and does not guess an original request',t=>{
 const f=fixture(t),legacy={schema:p.LEGACY_SCHEMA,job_id:f.child.job_id,parent_job_id:f.parent.job_id,captured_at:'2026-01-01T00:00:00Z',boundary:'comfy.CLIPTextEncode.text',available:true,prompt:'  Old final\r\n',sha256:p.hash('  Old final\r\n')};
 fs.mkdirSync(path.join(f.root,'prompt-provenance'));const filename=path.join(f.root,'prompt-provenance',f.child.job_id+'.json');const bytes=JSON.stringify(legacy);fs.writeFileSync(filename,bytes);
 const value=p.resolve(f.root,{...f.payload,prompt:'not the user request'});assert.equal(value.final_generation_prompt,legacy.prompt);assert.equal(value.original_user_request_available,false);
 record(f,legacy.prompt);assert.equal(fs.readFileSync(filename,'utf8'),bytes);
});
test('historical request requires same row artifact identity and scope, with existing request hash respected',t=>{
 const f=fixture(t);f.parent.owner={workspace_id:'4',thread_id:'5',user_id:'unknown'};store.write(f.root,f.parent);
 const chat={id:9,workspaceId:4,thread_id:5,user_id:null,prompt:'שלום\nOne cube.',response:JSON.stringify({outputs:[{type:'imageGenerationCard',payload:f.payload}]})};
 const found=p.originalFromHistory(f.root,f.payload,chat);assert.equal(found.original_user_request,chat.prompt);
 assert.equal(p.originalFromHistory(f.root,f.payload,{...chat,thread_id:6}),null);
 assert.equal(p.originalFromHistory(f.root,f.payload,{...chat,response:JSON.stringify({outputs:[]})}),null);
 f.child.expert_prompt={request_sha256:p.hash('different request')};store.write(f.root,f.child);assert.equal(p.originalFromHistory(f.root,f.payload,chat),null);
});
test('secret and internal envelope checks apply independently to original metadata',t=>{
 for(const text of ['Authorization: secret','AAG_COMPOSER_INTENT_SIGNATURE_V1=hidden','sk-proj-'+'x'.repeat(30)]){
  const f=fixture(t);p.captureOriginal(f.root,f.parent,text);record(f,'Safe final prompt');
  const value=p.resolve(f.root,f.payload);assert.equal(value.available,true);assert.equal(value.original_user_request_available,false);assert.equal(value.final_generation_prompt,'Safe final prompt');
  assert.equal(p.read(f.root,f.parent.job_id,'original-request-provenance').original_user_request,undefined);
 }
});
test('signed Composer extraction retains exact free text without structured constraints or hidden instructions',()=>{
 const h=require('../integrations/anythingllm/aagComposerHistory'),original='  שלום — motorcycle.\r\nExactly two wheels.  ';
 const intent={user_request_sha256:p.hash(original),aspect_ratio:'16:9',expert_prompt:{schema:'aag.expert.context.v1',hidden:'unrelated instructions'}};
 const envelope='INTERNAL AUTHORING GUIDANCE\nAAG_COMPOSER_STRUCTURED_REQUIREMENTS_V1='+h.composerCanonicalJson(intent)+'\nUSER_CREATIVE_DIRECTION=\n'+original+'\nAAG_COMPOSER_INTENT_SIGNATURE_V1='+'0'.repeat(64);
 const runtime={};assert.equal(h.composerInvocationPrompt(envelope,runtime),original);assert.equal(runtime.AAG_EXPERT_ENVELOPE,envelope);
 assert.equal(h.composerInvocationPrompt(original),original);
});

test('new explicit final fields retain the previous Golden reader contract for rollback',t=>{
 const f=fixture(t),text='  Final prompt — שלום\r\n';const value=record(f,text);
 assert.equal(value.schema,'aag.final-engine-prompt.v1');assert.equal(value.prompt,text);assert.equal(value.sha256,p.hash(text));
 assert.equal(value.final_generation_prompt,value.prompt);assert.equal(value.final_generation_prompt_sha256,value.sha256);
 assert.equal(p.verified({...value,final_generation_prompt:'changed display text'}),false);
 assert.equal(p.verified({...value,final_generation_prompt_sha256:p.hash('changed')}),false);
});
