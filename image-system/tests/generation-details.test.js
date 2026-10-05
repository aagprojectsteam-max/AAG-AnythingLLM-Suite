"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto');
const details=require('../src/generation-details'),store=require('../src/store'),comfy=require('../src/comfy'),runtimeCore=require('../src/runtime'),batch=require('../src/batch');
const owner={AAG_WORKSPACE_ID:'details',AAG_THREAD_ID:'details',AAG_USER_ID:'details',AAG_INVOCATION_UUID:'details',AAG_TURN_ID:'details'};
const prompt='A professional photograph of one red ceramic bowl on a wooden studio table. The focal subject has a clear silhouette and coherent scale. Soft daylight illuminates the matte ceramic glaze and wood grain, with restrained shadows defining the curved form. A low camera view and moderate depth of field separate the bowl from a neutral background. Balanced composition and natural color rendering keep the material and spatial relationships readable.';
function temp(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'aag-details-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;}
function bytes(n=1){const b=Buffer.alloc(160,n);Buffer.from([137,80,78,71,13,10,26,10]).copy(b);b.writeUInt32BE(512,16);b.writeUInt32BE(512,20);return b;}
const val=(record,key)=>record.fields[key]?.value;
function rows(result){return Object.fromEntries(result.groups.flatMap(g=>g.rows).map(r=>[r.id,r]));}
function fixture(t,quality='quality'){
 const root=temp(t),parent=store.createRecord(root,{operation:'generate',generation_request:{schema:'aag.generation-request.v1',normalized:{operation:'generate',quality,final_output_quality:'standard',count:1,aspect_ratio:'16:9'},composer:{quality,aspect_ratio:'16:9',creative_direction:{visual_family:'auto',visual_subfamily:'auto',background:'white'},composer_version:'1.2',language:'he'},authoring:{provider:'gemini',model:'gemini-3.8-flash'},software:{release_id:'fixture-release'},expert:true}});
 const child=store.createRecord(root,{operation:'generate',parent_job_id:parent.job_id,child_index:1,workflow_id:'generation.text.quality.v1',release:'fixture-runtime'});
 const artifact={artifact_id:'artifact-'+crypto.randomUUID(),job_id:parent.job_id,child_job_id:child.job_id,width:768,height:448,bytes:160,mime:'image/png',format:'png',sha256:details.hash('pixels'),verified_at:'2026-10-04T20:00:00.000Z'};
 child.execution_details=details.executionFromGraph(comfy.generationGraph({model:comfy.PROFILES.quality,prompt,seed:123,width:768,height:448,prefix:'fixture'}),{resolved_quality:'quality',model_family:'FLUX.2 Klein',workflow_revision:'comfy.klein.v2'});
 child.expert_prompt={semantic_review:{classification:'VALID_PASS',provider:'gemini',model:'gemini-3.8-flash'}};child.atlas={atlas_version:'1.0.0',taxonomy_sha256:'a'.repeat(64),selections:[{family_id:'photography',family_label:'Photography',subfamily_id:'sports-action',subfamily_label:'Sports/action'}]};
 const payload={jobId:parent.job_id,artifactId:artifact.artifact_id,artifactSha256:artifact.sha256};
 const finalize=(base=artifact,processing=[])=>{const receipt=details.capture(root,parent,child,artifact,base,processing);parent.artifacts=[artifact];child.artifacts=[artifact];store.write(root,parent);store.write(root,child);return receipt;};return {root,parent,child,artifact,payload,finalize};
}
test('execution graph equals persisted record equals displayed facts, including zero and requested/resolved Auto style',t=>{
 const f=fixture(t),r=f.finalize(),ui=rows(details.resolve(f.root,f.payload));assert.ok(details.valid(r));
 for(const [key,value] of Object.entries({checkpoint:comfy.PROFILES.quality.unet,text_encoder:comfy.PROFILES.quality.clip,vae:comfy.PROFILES.quality.vae,seed:123,steps:4,guidance:1,sampler:'euler',scheduler:'Flux2Scheduler',base_dimensions:'768 × 448',final_dimensions:'768 × 448',file_size:160,artifact_hash:f.artifact.sha256,requested_family:'auto',resolved_family:'Photography',requested_subfamily:'auto',resolved_style:'Sports/action',authoring_model:'gemini-3.8-flash',image_model:comfy.PROFILES.quality.unet,expert:true,semantic_review:true})){
  assert.equal(val(r,key),value,key);assert.equal(ui[key].value,value,key);
 }
 assert.notEqual(val(r,'image_model'),val(r,'authoring_model'));assert.ok(!ui.denoise);assert.equal(ui.engine_version.state,'NOT_RECORDED');
});
test('write-once receipts survive restart, current routing changes and reject conflicting edits or hash/binding tampering',t=>{
 const f=fixture(t),r=f.finalize(),before=fs.readFileSync(details.file(f.root,f.artifact.artifact_id));assert.equal(f.finalize().sha256,r.sha256);assert.deepEqual(fs.readFileSync(details.file(f.root,f.artifact.artifact_id)),before);
 f.child.execution_details.seed=42;assert.throws(()=>f.finalize(),{code:'DETAILS_IMMUTABLE_CONFLICT'});
 const reloaded=require('../src/generation-details');assert.equal(rows(reloaded.resolve(f.root,f.payload)).seed.value,123);
 assert.equal(details.resolve(f.root,{...f.payload,artifactSha256:'b'.repeat(64)}).available,false);assert.equal(details.resolve(f.root,{...f.payload,artifactId:'artifact-'+crypto.randomUUID()}).available,false);
 const edited=JSON.parse(before);edited.fields.steps.value=100;fs.writeFileSync(details.file(f.root,f.artifact.artifact_id),JSON.stringify(edited));assert.equal(details.resolve(f.root,f.payload).available,false);
});
test('historical full/partial/empty records read only persisted evidence and never create receipts or infer defaults',t=>{
 const f=fixture(t);f.parent.artifacts=[f.artifact];f.child.artifacts=[f.artifact];f.child.engine={model_file:'historical.safetensors',steps:27,cfg:0,sampler:'historical-sampler'};f.child.seed=123;store.write(f.root,f.parent);store.write(f.root,f.child);
 const before=fs.readFileSync(store.jobFile(f.root,f.child.job_id));let result=details.resolve(f.root,f.payload);assert.equal(result.source,'historical-persisted-evidence');let ui=rows(result);assert.equal(ui.checkpoint.value,'historical.safetensors');assert.equal(ui.steps.value,27);assert.equal(ui.guidance.value,0);
 for(const k of ['seed','requested_quality','normalized_quality','resolved_quality','requested_family','expert'])assert.equal(ui[k].state,'NOT_RECORDED',k);
 assert.deepEqual(fs.readFileSync(store.jobFile(f.root,f.child.job_id)),before);assert.ok(!fs.existsSync(path.join(f.root,'generation-details')));
 f.child.engine={};store.write(f.root,f.child);ui=rows(details.resolve(f.root,f.payload));assert.equal(ui.checkpoint.state,'NOT_RECORDED');assert.equal(ui.final_dimensions.value,'768 × 448');
 f.child.engine={model:'fast'};store.write(f.root,f.child);assert.equal(rows(details.resolve(f.root,f.payload)).image_model.state,'NOT_RECORDED');
});
test('derived artifacts retain base model and sampling while recording actual upscale model and final dimensions',t=>{
 const f=fixture(t),base=structuredClone(f.artifact);f.parent.generation_request.normalized.final_output_quality='enhanced_2x';f.artifact.derived_from_artifact_id=base.artifact_id;f.artifact.width=1536;f.artifact.height=896;f.artifact.artifact_id='artifact-'+crypto.randomUUID();
 const r=f.finalize(base,[{stage:'upscale',model:'realesrgan-x4plus',scale:2,input_dimensions:'768 × 448'}]);assert.equal(val(r,'checkpoint'),comfy.PROFILES.quality.unet);assert.equal(val(r,'upscaler'),'realesrgan-x4plus');assert.equal(val(r,'upscale_factor'),2);assert.equal(val(r,'pre_upscale_dimensions'),'768 × 448');assert.equal(val(r,'post_upscale_dimensions'),'1536 × 896');assert.equal(val(r,'resolved_output_quality'),'enhanced_2x');assert.equal(val(r,'source_artifact_id'),base.artifact_id);
});
test('upscale sampling is not applicable; transform/reference and identity use safe metadata without private images',t=>{
 const f=fixture(t);f.child.operation='upscale';f.parent.generation_request.normalized.operation='upscale';f.child.execution_details={engine:'Upscayl / ncnn',image_model:'realesrgan-x4plus'};f.child.source={kind:'current_attachment',mime:'image/png',private_path:'/home/private.png',bytes:'private-image',width:768,height:448};
 const r=f.finalize(f.artifact,[{stage:'upscale',model:'realesrgan-x4plus',scale:4,input_dimensions:'192 × 112'}]);for(const k of ['seed','steps','sampler','guidance','expert'])assert.equal(r.fields[k].state,'NOT_APPLICABLE');assert.equal(val(r,'reference_count'),1);assert.ok(!JSON.stringify(r).includes('/home/'));assert.ok(!JSON.stringify(r).includes('private-image'));
});
test('safe allowlist withholds credentials, absolute paths, hidden prompts and reasoning without truncating values',t=>{
 const f=fixture(t);f.child.execution_details={...f.child.execution_details,checkpoint:'/mnt/private/model.safetensors',image_model:'sk-proj-'+'x'.repeat(32),engine_version:'Authorization: Bearer secret',hidden_prompt:'system secret',analysis:'reasoning'};
 f.parent.generation_request.authoring.model='a'.repeat(4001);const r=f.finalize();for(const key of ['checkpoint','image_model','engine_version','authoring_model'])assert.equal(r.fields[key].state,'NOT_RECORDED');assert.ok(!JSON.stringify(details.present(r)).includes('system secret'));
 for(const v of ['C:\\Users\\private','file:///private/file','/secret/model','data:image/png;base64,private','chain of thought'])assert.equal(details.safe(v),false,v);
});
test('signed requested Composer intent and normalized tool choices are captured separately; invalid signature cannot reach engine',async t=>{
 const task={operation:'generate',quality:'fast',count:1,final_output_quality:'standard'},rt={AAG_COMPOSER_ENVELOPE:'signed',AAG_INVOCATION_PROMPT:'original',AAG_PROMPT_AUTHORING_IDENTITY:{provider:'local',model:'local-model'}};
 const r=await details.captureRequest(task,rt,{detailsRequest:async body=>{assert.deepEqual(body,{envelope:'signed',request:'original'});return {quality:'auto',creative_direction:{visual_family:'auto'}};}});assert.equal(r.composer.quality,'auto');assert.equal(r.normalized.quality,'fast');assert.equal(r.authoring.model,'local-model');
 await assert.rejects(details.captureRequest(task,rt,{detailsRequest:async()=>{throw Error('invalid signature');}}));
});
function adapter(quality,graphs,outputDimensions=[512,512]){
 const profile=quality==='quality'?comfy.PROFILES.quality:comfy.PROFILES.fast;
 return {execute:async(task,source,rt,ctx,lease,deps)=>comfy.execute(task,source,lease,{...deps,fetch:async(url,opt)=>{
  let body;if(url.endsWith('/object_info'))body=Object.fromEntries([...comfy.COMMON_NODES,...comfy.GENERAL_NODES].map(k=>[k,{}]));
  else if(url.includes('/models/'))body=[profile.unet,profile.clip,profile.vae];
  else if(url.endsWith('/prompt')){graphs.push(JSON.parse(opt.body).prompt);body={prompt_id:'recorded-'+graphs.length};}
  else if(url.includes('/history/')){const id=url.split('/').at(-1);body={[id]:{status:{completed:true},outputs:{output:{images:[{filename:id+'.png'}]}}}};}
  else if(url.endsWith('/import-comfyui'))body={ok:true,filename:'image-'+graphs.length+'.png'};
  else if(url.endsWith('/upload/image'))body={name:'source.png',subfolder:'AAG-Image-Agent-Source'};
  else throw Error('Unexpected recording request '+url);
  return {ok:true,text:async()=>JSON.stringify(body)};
 }}),prepareGeneralReference:async source=>source,fetch:async url=>({ok:true,headers:{get:()=> 'image/png'},arrayBuffer:async()=>bytes(url.includes('image-2')?2:1)}),inspectOutput:async()=>({width:outputDimensions[0],height:outputDimensions[1],format:'png'})};
}
test('actual runtime recording adapters prove all four quality routes, per-artifact seeds/hashes and no real images',async t=>{
 for(const q of ['auto','fast','balanced','quality']){
  const root=temp(t),graphs=[];const rt={...owner,AAG_IMAGE_AGENT_STATE_ROOT:root,AAG_INVOCATION_PROMPT:'one red ceramic bowl',AAG_PROMPT_AUTHORING_IDENTITY:{provider:'gemini',model:'authoring-model'}};
  const result=await runtimeCore.createTask({operation:'generate',prompt,quality:q,count:2,seed:0},rt,{deps:{requireReady:async()=>{},scheduler:{engineActivity:async()=>({active:false}),disableHeartbeat:true},adapters:adapter(q,graphs)}});assert.match(result,/status=completed/);
  const jobs=store.listJobs(root),parent=jobs.find(j=>!j.parent_job_id);assert.equal(graphs.length,2);for(const [i,a] of parent.artifacts.entries()){
   const r=readReceipt(root,a),ui=rows(details.resolve(root,{jobId:parent.job_id,artifactId:a.artifact_id,artifactSha256:a.sha256}));assert.equal(r.fields.seed.value,graphs[i]['9'].inputs.noise_seed);assert.equal(ui.seed.value,i);assert.equal(ui.artifact_hash.value,a.sha256);assert.equal(ui.checkpoint.value,q==='quality'?comfy.PROFILES.quality.unet:comfy.PROFILES.fast.unet);assert.equal(ui.steps.value,4);assert.equal(ui.requested_quality.value,q);assert.equal(ui.resolved_quality.value,q==='quality'?'quality':'fast');assert.equal(ui.expert.value,false);assert.equal(ui.semantic_review.value,false);
  }assert.notEqual(parent.artifacts[0].sha256,parent.artifacts[1].sha256);
 }
});
function readReceipt(root,artifact){return JSON.parse(fs.readFileSync(details.file(root,artifact.artifact_id),'utf8'));}
test('actual multi-image batch stores item dimensions and immutable artifact receipts across resume/read boundaries',async t=>{
 const root=temp(t),graphs=[],rt={...owner,AAG_IMAGE_AGENT_STATE_ROOT:root,AAG_INVOCATION_PROMPT:'one red ceramic bowl'};
 const result=await batch.createBatch({operation:'multi_generate',collection_brief:prompt,count:2,quality:'balanced',items:[{prompt,aspect_ratio:'1:1',seed:10},{prompt,aspect_ratio:'16:9',seed:20}]},rt,{deps:{requireReady:async()=>{},scheduler:{engineActivity:async()=>({active:false}),disableHeartbeat:true},adapters:adapter('balanced',graphs)}});
 assert.equal(result.job.status,'COMPLETED');for(const [i,a] of result.job.artifacts.entries()){const r=readReceipt(root,a);assert.equal(val(r,'seed'),i?20:10);assert.equal(val(r,'requested_aspect'),i?'16:9':'1:1');assert.equal(val(r,'artifact_index'),i+1);assert.equal(val(r,'quantity'),2);assert.equal(rows(details.resolve(root,{jobId:result.job.job_id,artifactId:a.artifact_id,artifactSha256:a.sha256})).checkpoint.value,comfy.PROFILES.fast.unet);}
});
test('actual subject transform graph and upscale response supply their own provenance',async t=>{
 for(const operation of ['transform','upscale']){
  const root=temp(t),graphs=[],rt={...owner,AAG_IMAGE_AGENT_STATE_ROOT:root,AAG_INVOCATION_PROMPT:operation==='transform'?'one red ceramic bowl':'upscale image',AAG_INVOCATION_ATTACHMENTS:[{name:'reference.png',mime:'image/png',contentString:'data:image/png;base64,'+bytes().toString('base64')}]};
  const deps={requireReady:async()=>{},scheduler:{engineActivity:async()=>({active:false}),disableHeartbeat:true},normalizeImage:async parsed=>({bytes:parsed.bytes,width:512,height:512,format:'png',original_format:'png'}),adapters:adapter('quality',graphs)};
  let args={operation,request:rt.AAG_INVOCATION_PROMPT,prompt:operation==='transform'?prompt:'',preservation:operation==='transform'?'subject':'none',source_policy:'current_attachment',quality:'quality'};
  if(operation==='upscale'){
   args.scale=2;delete deps.adapters.execute;deps.adapters.inspectOutput=async input=>({width:input.equals(bytes())?512:1024,height:input.equals(bytes())?512:1024,format:'png'});
   deps.adapters.fetch=async(url,opt)=>url.endsWith('/upscale')?{ok:true,json:async()=>{const sent=JSON.parse(opt.body);assert.equal(sent.model,'standard');assert.equal(sent.scale,2);return {ok:true,filename:'upscale.png',public_path:'/files/upscale.png',model:'realesrgan-x4plus',scale:2};}}:{ok:true,headers:{get:()=> 'image/png'},arrayBuffer:async()=>bytes(3)};
  }
  const result=await runtimeCore.createTask(args,rt,{deps});assert.match(result,/status=completed/);const parent=store.listJobs(root).find(j=>!j.parent_job_id),r=readReceipt(root,parent.artifacts[0]);assert.equal(val(r,'operation'),operation);assert.equal(val(r,'reference_count'),1);
  if(operation==='transform'){assert.equal(val(r,'seed'),graphs[0]['13'].inputs.noise_seed);assert.equal(val(r,'checkpoint'),graphs[0]['1'].inputs.unet_name);assert.equal(val(r,'steps'),graphs[0]['12'].inputs.steps);}
  else{assert.equal(val(r,'image_model'),'realesrgan-x4plus');assert.equal(val(r,'upscaler'),'realesrgan-x4plus');assert.equal(val(r,'final_dimensions'),'1024 × 1024');assert.equal(r.fields.seed.state,'NOT_APPLICABLE');}
 }
});
test('identity receipt uses a hash-bound completed frozen-worker report and cannot substitute another artifact or contract',t=>{
 const root=temp(t),id=crypto.randomUUID(),directory=path.join(root,'evidence',id);fs.mkdirSync(directory,{recursive:true});const report={request_id:id,status:'PASS',artifact:{sha256:'a'.repeat(64)},contract_b_sha256:'b'.repeat(64),seed:456,steps:25,guidance_scale:7,id_scale:.8,width:896,height:1152};fs.writeFileSync(path.join(directory,'worker.json'),JSON.stringify(report));
 const e=details.identityExecution(root,id,{artifact_sha256:'a'.repeat(64)},'b'.repeat(64));assert.equal(e.seed,456);assert.equal(e.steps,25);assert.equal(e.checkpoint,'Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors');assert.equal(e.sampler,'sample_dpmpp_2m');assert.equal(e.scheduler,'Karras rho 7');
 assert.deepEqual(details.identityExecution(root,id,{artifact_sha256:'c'.repeat(64)},'b'.repeat(64)),{});assert.deepEqual(details.identityExecution(root,id,{artifact_sha256:'a'.repeat(64)},'c'.repeat(64)),{});
});
