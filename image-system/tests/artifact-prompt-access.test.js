'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('vm'),fs=require('fs'),path=require('path');
function load({workspaces=[{id:1}],chats=[]}={}){
 const module={exports:{}},deps={
 '../utils/http':{safeJsonParse:JSON.parse},'../utils/middleware/validatedRequest':{},'../utils/middleware/multiUserProtected':{},
 '../models/workspace':{Workspace:{where:async()=>workspaces,whereWithUser:async()=>workspaces}},
 '../models/workspaceChats':{WorkspaceChats:{where:async clause=>{assert.deepEqual(Array.from(clause.workspaceId.in),workspaces.map(w=>w.id));return chats;}}},
 '../utils/files':{GENERATED_IMAGE_FILENAME_PATTERN:/^fixture\.png$/},'./aagArtifactFormat':require('../integrations/anythingllm/aagArtifactFormat'),'./aagPdfAssembler':{}
 };
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/aagArtifactExport.js'),'utf8'),{module,process:{env:{}},require:n=>deps[n]||require(n)});
 return module.exports;
}
const card=(job,id)=>({type:'imageGenerationCard',payload:{storageFilename:'fixture.png',jobId:job,artifactId:id}});
test('prompt access uses persisted authorized workspace outputs and both artifact identities',async()=>{
 const outputs=[card('job-a','artifact-a'),card('job-b','artifact-b')],api=load({chats:[{response:JSON.stringify({outputs})}]});
 const request={mode:'single',artifacts:['fixture.png'],identity:{jobId:'job-a',artifactId:'artifact-a'}};
 assert.equal((await api.resolvePersistedOutputs(request,{isMultiUser:true,user:{id:2}})).outputs[0].payload.artifactId,'artifact-a');
 await assert.rejects(api.resolvePersistedOutputs({...request,identity:{jobId:'job-a',artifactId:'artifact-b'}},{}),e=>e.status===404);
 await assert.rejects(load({workspaces:[],chats:[{response:JSON.stringify({outputs})}]}).resolvePersistedOutputs(request,{isMultiUser:true,user:{id:2}}),e=>e.status===404);
});
test('existing PDF single resolution remains compatible without prompt identity',async()=>{
 const api=load({chats:[{response:JSON.stringify({outputs:[card('job','artifact')]})}]});
 assert.equal((await api.resolvePersistedOutputs({mode:'single',artifacts:['fixture.png']},{})).outputs.length,1);
 assert.doesNotThrow(()=>api.aagArtifactExportEndpoints(null));
});
