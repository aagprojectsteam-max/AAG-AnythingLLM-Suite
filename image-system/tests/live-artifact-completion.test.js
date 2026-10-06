'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {patchLiveArtifacts}=require('../tools/patch-live-artifacts');
const live=require('../integrations/anythingllm/frontend/aagLiveArtifacts');
const presentation=require('../integrations/anythingllm/aagArtifactPresentation');
function reducer(patched=true){
 let source=fs.readFileSync(path.join(__dirname,'fixtures/anythingllm-agent-07bd65f.js.txt'),'utf8');
 if(patched)source=patchLiveArtifacts(source);
 source=source.replace(/^import .*?;\n/gms,'').replace(/export default /g,'').replace(/export /g,'').replaceAll('import.meta.env.VITE_API_BASE','"/api"');
 let sequence=0;const context={...live,v4:()=>`new-${sequence++}`,safeJsonParse:JSON.parse,window:{dispatchEvent(){}},CustomEvent:class{},emitAssistantMessageCompleteEvent(){},console};
 vm.createContext(context);vm.runInContext(source+'\nthis.reduce=handleSocketResponse;',context);
 return (state,data)=>{let next=state;context.reduce({supportsAgentStreaming:true},{data:JSON.stringify(data)},fn=>{next=fn(next)});return next};
}
const output={type:'imageGenerationCard',payload:{artifactId:'artifact-test',jobId:'job-test',storageFilename:'AAG-Image-1-4-20261005-140215.png'}};
const event={type:'imageGenerationCard',chatId:44,content:'',outputs:[output]};
const initial=[{role:'user',content:'One blue mug',chatId:44},{role:'assistant',type:'textResponse',uuid:'stream-1',chatId:44,content:'AAG_IMAGE_RESULT\nstatus=completed\nartifact_count=1'}];
test('pinned upstream reproduction: actual normalized output is persisted but rejected live; patched reducer hydrates same row',()=>{
 const a={functions:new Map([['task',{config:{hubId:'aag-image-task'},handler:async()=>null}]]),_pendingOutputs:[output],trackedChatId:44,newMessage:x=>x};
 presentation.installAagArtifactPresentation({aibitat:a});a._aagPresentedArtifactUrls=['http://127.0.0.1:18190/files/test.png'];
 const emitted=a.newMessage({from:'@agent',to:'USER',content:initial[1].content});
 assert.equal(emitted.content,'');assert.equal(emitted.chatId,44);
 assert.equal(reducer(false)(initial,emitted),initial);
 let state=reducer()(initial,emitted);assert.equal(state.length,2);assert.equal(state[1].uuid,'stream-1');assert.equal(state[1].outputs.length,1);
 state=reducer()(state,emitted);assert.equal(state.length,2);assert.equal(state[1].outputs.length,1);
 assert.equal(state[1].outputs[0].payload.storageFilename,output.payload.storageFilename);
});
test('empty/pending first thread renders one image; later stream events cannot discard artifact-only history',()=>{
 const reduce=reducer();let state=reduce([{role:'user',content:'request'},{role:'assistant',pending:true,content:''}],event);
 assert.equal(state.length,2);assert.equal(state[1].outputs.length,1);
 state=reduce(state,{type:'reportStreamEvent',content:{type:'fullTextResponse',uuid:'next',content:'Next ordinary reply'}});
 assert.equal(state.filter(live.hasImageOutputs).length,1);
 state=reduce(state,{type:'reportStreamEvent',content:{type:'textResponseChunk',uuid:'next',content:' continued'}});
 assert.equal(state.filter(live.hasImageOutputs).length,1);
 state=reduce(state,event);assert.equal(state.filter(live.hasImageOutputs).length,1);
});
test('same pixels different artifact IDs remain distinct; collection retransmission updates metadata',()=>{
 let state=live.mergeImageMessage([],event,'one');
 state=live.mergeImageMessage(state,{...event,outputs:[{...output,payload:{...output.payload,collectionComplete:true}},{...output,payload:{...output.payload,artifactId:'sibling',storageFilename:'other.png'}}]},'two');
 assert.equal(state.length,1);assert.equal(state[0].outputs.length,2);assert.equal(state[0].outputs[0].payload.collectionComplete,true);
});
test('UI completion requires current history plus decoded image; unrelated readiness and failures never fabricate success',()=>{
 const progress={status:'COMPLETED',jobId:'job-test',stages:[{key:'returningToChat',state:'complete'},{key:'complete',state:'complete'}]};
 assert.equal(live.presentationProgress(progress,[],new Set()).stages[1].state,'pending');
 assert.equal(live.presentationProgress(progress,[event],new Set(['unrelated'])).stages[1].state,'pending');
 assert.equal(live.presentationProgress(progress,[event],new Set(['artifact-test'])),progress);
 const failure={...progress,status:'FAILED'};assert.equal(live.presentationProgress(failure,[],new Set()),failure);
 assert.equal(live.mergeImageMessage(initial,{type:'imageGenerationCard',outputs:[]},'none'),initial);
});

test('live publication and recovery serialize the same job; recovery observes committed outputs instead of racing SQLite',async()=>{
 const os=require('node:os'),crypto=require('node:crypto');
 const {fixture}=require('./fixtures/artifact-naming');
 const progress=require('../integrations/anythingllm/aagImageProgress');
 const bytes=Buffer.alloc(128);Buffer.from([137,80,78,71,13,10,26,10]).copy(bytes);bytes.write('IHDR',12);bytes.writeUInt32BE(512,16);bytes.writeUInt32BE(512,20);
 const jobId='aag-11111111-1111-4111-8111-111111111111';
 const result=`AAG_IMAGE_RESULT\nstatus=completed\njob_id=${jobId}\noperation=generate\nworkflow=generation.text.fast.v1\nrelease=test\nartifact_count=1\nartifact_1_id=artifact-22222222-2222-4222-8222-222222222222\nartifact_1_url=http://127.0.0.1:18190/files/race.png\nartifact_1_sha256=${crypto.createHash('sha256').update(bytes).digest('hex')}\nartifact_1_dimensions=512x512`;
 const naming=await fixture(presentation.parseAagImageResult(result));
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'aag-publication-race-'));
 let entered,release;const authorizing=new Promise(r=>entered=r),canCommit=new Promise(r=>release=r);
 let row={id:9,response:'{}'},lookups=0,commits=0;
 const task={config:{hubId:'aag-image-task'},handler:async()=>result};
 const a={functions:new Map([['task',task]]),trackedChatId:9,newMessage:m=>m};
 try{
  presentation.installAagArtifactPresentation({aibitat:a,naming,generatedImagesPath:dir,fetchImpl:async()=>({ok:true,headers:{get:()=> 'image/png'},arrayBuffer:async()=>bytes}),authorizeOutputs:async outputs=>{entered();await canCommit;row.response=JSON.stringify({outputs});commits++;}});
  const live=task.handler({prompt:'One mug'});await authorizing;
  const job={...naming.job,parent_job_id:null,status:'COMPLETED',owner:{workspace_id:3,thread_id:21,user_id:'unknown',invocation_id:'invocation'}};
  const server={presentation,WorkspaceAgentInvocation:{async get(){lookups++;return {workspace_id:3,thread_id:21,user_id:null}}},WorkspaceChats:{async where(){return [row]}}};
  const recovery=progress.bindCompletedJob(job,{store:{write(){}}},server,{stateRoot:dir});
  await Promise.resolve();assert.equal(lookups,0,'Recovery must not open a second publication transaction while the live commit is pending');
  release();await live;const bound=await recovery;
  assert.equal(commits,1);assert.equal(bound.recovered,false);assert.equal(lookups,1);
  const message=a.newMessage({from:'@agent',content:result});assert.equal(message.outputs.length,1);assert.equal(message.chatId,9);
 }finally{release();await naming.close();fs.rmSync(dir,{recursive:true,force:true});}
});

test('publication error releases ownership, unrelated jobs proceed, and retry is not poisoned',async()=>{
 let release;const blocked=new Promise(r=>release=r),order=[];
 const first=presentation.withArtifactPublication('one',async()=>{await blocked;throw Error('controlled commit failure')});
 const failure=assert.rejects(first,/controlled commit failure/);
 const retry=presentation.withArtifactPublication('one',async()=>{order.push('retry')});
 await presentation.withArtifactPublication('other',async()=>{order.push('other')});
 assert.deepEqual(order,['other']);release();await failure;await retry;assert.deepEqual(order,['other','retry']);
});
