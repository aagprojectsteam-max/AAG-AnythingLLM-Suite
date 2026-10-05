"use strict";
// Execution snapshots and write-once artifact receipts. Readers never load
// current model profiles, routing, defaults, Atlas labels or workflow code.
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const {AagError}=require('./errors');
const {ensureDirectory,readJson}=require('./util');
const {publishable}=require('./prompt-provenance');
const CATALOG=require('./generation-details-fields.json');
const SCHEMA='aag.generation-details.v1';
const ARTIFACT=/^artifact-[a-f0-9-]{36}$/i;
const STATES=new Set(['RECORDED','UNKNOWN','NOT_RECORDED','NOT_APPLICABLE']);
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
function safe(v){
 if(typeof v==='boolean'||typeof v==='number'&&Number.isFinite(v))return true;
 if(typeof v!=='string'||!v.length||v.length>4000||!publishable(v))return false;
 return !/(?:^|[\s"'=])(?:\/(?:home|mnt|app|tmp|etc|var|root|usr|opt|run|private|Users)\b|[A-Za-z]:[\\/])|file:\/\/|data:image|<\|(?:think|analysis)|chain.of.thought|system.prompt/i.test(v) && !v.startsWith('/');
}
const state=s=>({state:s});
const NA=()=>state('NOT_APPLICABLE');
const field=v=>v==='UNKNOWN'?state('UNKNOWN'):v===undefined||v===null?state('NOT_RECORDED'):safe(v)?{state:'RECORDED',value:v}:state('NOT_RECORDED');
const dimension=(w,h)=>Number.isInteger(w)&&w>0&&Number.isInteger(h)&&h>0?`${w} × ${h}`:undefined;
function ratio(w,h){if(!Number.isInteger(w)||!Number.isInteger(h)||w<1||h<1)return;let a=w,b=h;while(b)[a,b]=[b,a%b];return `${w/a}:${h/a}`;}
function file(root,id){if(!ARTIFACT.test(id))throw new AagError('DETAILS_ID_INVALID','Invalid artifact provenance identity.');return path.join(root,'generation-details',id+'.json');}
async function captureRequest(task,runtime,deps={}){
 let composer=null;
 if(runtime.AAG_COMPOSER_ENVELOPE){
  composer=await (deps.detailsRequest || (body=>require('./expert-prompt').validateLocal(body,'/composer/provenance')))({envelope:runtime.AAG_COMPOSER_ENVELOPE,request:runtime.AAG_INVOCATION_PROMPT});
 }
 const normalized={};for(const key of ['operation','quality','final_output_quality','aspect_ratio','width','height','count','preservation','scale','source_policy'])if(task[key]!==undefined)normalized[key]=task[key];
 let software=runtime.AAG_SOFTWARE_PROVENANCE||null;
 if(!software)try{software=readJson(path.join(__dirname,'runtime-release.json'));}catch{}
 return {schema:'aag.generation-request.v1',composer,normalized,authoring:runtime.AAG_PROMPT_AUTHORING_IDENTITY||null,
   expert:Boolean(task._aag_expert||runtime.AAG_EXPERT_ENVELOPE),software};
}
function executionFromGraph(graph,{model_family,resolved_quality,workflow_revision,reference_dimensions}={}){
 const nodes=Object.values(graph);const inputs=type=>nodes.find(n=>n.class_type===type)?.inputs||{};
 const latent=inputs('EmptyFlux2LatentImage'),schedule=inputs('Flux2Scheduler');
 return {engine:'ComfyUI',model_family,resolved_quality,workflow_revision,workflow_hash:require('./comfy').workflowHash(graph),
   checkpoint:inputs('UNETLoader').unet_name,text_encoder:inputs('CLIPLoader').clip_name,vae:inputs('VAELoader').vae_name,
   seed:inputs('RandomNoise').noise_seed,steps:schedule.steps,sampler:inputs('KSamplerSelect').sampler_name,scheduler:nodes.find(n=>n.class_type==='Flux2Scheduler')?.class_type,
   guidance:inputs('CFGGuider').cfg,batch_size:latent.batch_size,base_dimensions:dimension(latent.width,latent.height),
   adapters:'none',controlnet:'none',denoise_applicable:false,reference_dimensions};
}
function identityExecution(root,requestId,response,lockedContract){
 // Existing frozen workers retain their contract. Read their completed report
 // rather than inventing sampling values from today's identity configuration.
 try{
  if(!/^[a-f0-9-]{36}$/i.test(requestId))return {};
  const r=readJson(path.join(root,'evidence',requestId,'worker.json'));
  if(r.status!=='PASS'||r.request_id!==requestId||r.artifact?.sha256!==response.artifact_sha256)return {};
  const contractHash=r.scene_contract_sha256||r.contract_b_sha256;
  if(contractHash!==lockedContract)return {};
  // PASS proves this governed, hash-frozen worker completed. Its model load
  // asserts this exact checkpoint's SHA256 before sampling; the constants
  // below describe that locked execution, and are snapshotted here only.
  return {engine:'PuLID local worker',image_model:'Juggernaut XL V9 + PuLID v1.1',model_family:'SDXL',checkpoint:'Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors',
   seed:r.seed,steps:r.steps,guidance:r.guidance_scale,base_dimensions:dimension(r.width,r.height),
   adapters:'pulid_v1.1.safetensors',sampler:'sample_dpmpp_2m',scheduler:'Karras rho 7',batch_size:1,workflow_revision:contractHash,
   vae:'checkpoint embedded',text_encoder:'checkpoint embedded SDXL CLIP pair',denoise_applicable:false,
   identity_scale:r.id_scale};
 }catch{return {};}
}
function build(parent,child,artifact,baseArtifact,processing=[]){
 const request=child.generation_request||parent.generation_request||{},c=request.composer||{},n=request.normalized||{},e=child.execution_details||{};
 const creative=c.creative_direction||{},author=request.authoring||{},style=child.atlas?.selections||[];
 const expert=child.expert_prompt||parent.expert_prompt,contract=child.public_arguments?.prompt_contract;
 const reviewed=Boolean(expert?.semantic_review?.classification==='VALID_PASS'||contract?.semantic_review?.classification==='VALID_PASS');
 const up=processing.find(p=>p.stage==='upscale');const values={};
 const put=(k,v)=>values[k]=field(v),na=k=>values[k]=NA();
 for(const [,k] of CATALOG)values[k]=state('NOT_RECORDED');
 const requested=(key,fallback)=>Object.hasOwn(c,key)?c[key]:fallback;
 put('operation',n.operation||child.operation);put('composer_mode',request.composer?'advanced':request.schema?'auto':undefined);
 put('intended_use',creative.output_purpose);put('quantity',requested('count',n.count));put('artifact_index',child.child_index);
 put('requested_aspect',requested('aspect_ratio',n.aspect_ratio));put('requested_dimensions',dimension(n.width,n.height));
 put('base_dimensions',e.base_dimensions);put('generated_dimensions',dimension(baseArtifact.width,baseArtifact.height));
 put('final_dimensions',dimension(artifact.width,artifact.height));put('final_aspect',ratio(artifact.width,artifact.height));
 put('format',artifact.format||artifact.mime?.replace('image/',''));put('mime',artifact.mime);put('file_size',artifact.bytes);put('color_profile',artifact.color_profile);
 for(const key of ['engine','engine_version','model_family','text_encoder','vae','adapters','controlnet','checkpoint','seed','steps','sampler','scheduler','guidance','batch_size'])put(key,e[key]);
 put('image_model',e.image_model||e.checkpoint);e.denoise_applicable===false?na('denoise'):put('denoise',e.denoise);
 put('requested_quality',requested('quality',n.quality));put('normalized_quality',n.quality);put('resolved_quality',e.resolved_quality);
 put('requested_output_quality',requested('final_output_quality',n.final_output_quality));put('resolved_output_quality',n.final_output_quality==='enhanced_2x'&&artifact.derived_from_artifact_id?'enhanced_2x':n.final_output_quality==='standard'?'standard':undefined);
 put('requested_family',creative.visual_family);put('requested_subfamily',creative.visual_subfamily);put('requested_atlas_mode',c.atlas_selection_mode);
 put('resolved_family',style.length?style.map(s=>s.family_label||s.family_id).join(', '):undefined);
 put('resolved_style',style.length?style.map(s=>s.subfamily_label||s.canonical_id||s.subfamily_id).join(', '):undefined);
 put('family_id',style.length?style.map(s=>s.family_id).join(', '):undefined);put('subfamily_id',style.length?style.map(s=>s.subfamily_id).join(', '):undefined);
 put('style_id',style.length?style.map(s=>s.canonical_id||`${s.family_id}/${s.subfamily_id}`).join(', '):undefined);
 put('atlas_version',child.atlas?.atlas_version);put('atlas_profile',child.atlas?.taxonomy_sha256);
 put('background',creative.background);put('visible_text',creative.visible_text);put('language',c.language);
 put('expert',request.schema?Boolean(request.expert):expert?true:undefined);
 if(child.operation==='upscale'){for(const key of ['expert','authoring_provider','authoring_model','semantic_review','review_provider','review_model','seed','steps','sampler','scheduler','guidance','denoise','batch_size','resolved_quality'])na(key);}
 else{put('authoring_provider',author.provider);put('authoring_model',author.model);put('semantic_review',request.schema?reviewed:expert?.semantic_review?reviewed:undefined);if(reviewed){put('review_provider',expert?.semantic_review?.provider||author.provider);put('review_model',expert?.semantic_review?.model||author.model);}else if(request.schema){na('review_provider');na('review_model');}}
 put('upscaling',Boolean(up));if(up){put('upscaler',up.model);put('upscale_factor',up.scale);put('pre_upscale_dimensions',up.input_dimensions);put('post_upscale_dimensions',dimension(artifact.width,artifact.height));}else{for(const key of ['upscaler','upscale_factor','pre_upscale_dimensions','post_upscale_dimensions'])na(key);}
 put('processing_stages',processing.length?processing.map(p=>p.stage).join(' → '):'none');
 const source=child.source||parent.source;
 if(source){put('reference_mode',source.kind);put('reference_count',1);put('reference_role',n.preservation);put('reference_type',source.mime||source.format);put('identity_mode',n.preservation==='identity'?child.capability?.contract_id||'identity':'none');}
 else{for(const key of ['reference_mode','reference_count','reference_role','reference_type','identity_mode'])na(key);}
 const constraints=c.constraints;put('constraints',constraints?Object.entries(constraints).map(([k,v])=>`${k}: ${v}`).join('; '):undefined);
 put('generated_at',artifact.verified_at);put('evidence_source','execution capture');put('checkpoint',e.checkpoint);
 put('workflow_id',child.workflow_id);put('workflow_version',e.workflow_revision);put('workflow_hash',e.workflow_hash);put('resolved_route',e.route||child.engine?.adapter);
 put('job_id',parent.job_id);put('artifact_id',artifact.artifact_id);put('source_artifact_id',artifact.derived_from_artifact_id||source?.artifact_id);
 put('artifact_hash',artifact.sha256);put('aag_release',request.software?.release_id);put('runtime_release',child.release);put('composer_version',c.composer_version);put('schema_version',1);
 return {schema:SCHEMA,schema_version:1,job_id:parent.job_id,child_job_id:child.job_id,artifact_id:artifact.artifact_id,artifact_sha256:artifact.sha256,artifact_index:child.child_index,recorded_at:artifact.verified_at,source:'execution-capture',fields:values};
}
function capture(root,parent,child,artifact,baseArtifact=artifact,processing=[]){
 const record=build(parent,child,artifact,baseArtifact,processing);record.sha256=hash(record);
 const target=file(root,artifact.artifact_id);ensureDirectory(path.dirname(target));const temp=target+'.'+crypto.randomUUID()+'.tmp';
 fs.writeFileSync(temp,JSON.stringify(record)+'\n',{flag:'wx',mode:0o600});
 try{fs.linkSync(temp,target);}catch(error){if(error.code!=='EEXIST')throw error;const previous=readJson(target);if(previous.sha256!==record.sha256)throw new AagError('DETAILS_IMMUTABLE_CONFLICT','Finalized generation details cannot change.');}finally{fs.unlinkSync(temp);}
 artifact.generation_details={schema:SCHEMA,sha256:record.sha256};return record;
}
function valid(record){
 if(record?.schema!==SCHEMA||record.schema_version!==1||!record.fields||typeof record.fields!=='object')return false;
 const {sha256,...unsigned}=record;if(hash(unsigned)!==sha256)return false;
 const allowed=new Set(CATALOG.map(r=>r[1]));
 return Object.keys(record.fields).every(k=>allowed.has(k))&&CATALOG.every(([,k])=>{const f=record.fields[k];return f&&STATES.has(f.state)&&(f.state==='RECORDED'?Object.keys(f).length===2&&safe(f.value):Object.keys(f).length===1);});
}
function historical(parent,child,artifact){
 // Only values already bound to this historical job/artifact are evidence.
 const fields=Object.fromEntries(CATALOG.map(([,k])=>[k,state('NOT_RECORDED')]));const put=(k,v)=>fields[k]=field(v),e=child.engine||{};
 for(const [k,v] of Object.entries({operation:child.operation,artifact_index:child.child_index,final_dimensions:dimension(artifact.width,artifact.height),final_aspect:ratio(artifact.width,artifact.height),format:artifact.mime?.replace('image/',''),mime:artifact.mime,file_size:artifact.bytes,generated_at:artifact.verified_at,job_id:parent.job_id,artifact_id:artifact.artifact_id,artifact_hash:artifact.sha256,source_artifact_id:artifact.derived_from_artifact_id,workflow_id:child.workflow_id,runtime_release:child.release,checkpoint:e.model_file,image_model:e.model_file||e.model,text_encoder:e.text_encoder_file,vae:e.vae_file,steps:e.steps,sampler:e.sampler,scheduler:e.scheduler,guidance:e.cfg,workflow_hash:e.workflow_sha256,resolved_route:e.adapter,evidence_source:'historical persisted evidence',schema_version:1}))put(k,v);
 // The old enhancement callback overwrote engine.model/adapter. Withhold
 // any ambiguous base-generation facts rather than mislabel the upscaler.
 if(!e.model_file&&['auto','fast','balanced','quality','standard'].includes(e.model))fields.image_model=state('NOT_RECORDED');
 if(artifact.derived_from_artifact_id&&e.adapter==='upscale-local-v1')for(const k of ['image_model','resolved_route'])fields[k]=state('NOT_RECORDED');
 return {schema:SCHEMA,schema_version:1,source:'historical-persisted-evidence',artifact_id:artifact.artifact_id,job_id:parent.job_id,fields};
}
function present(record){return CATALOG.reduce((groups,[group,id,en,he])=>{const f=record.fields[id];if(f?.state==='NOT_APPLICABLE')return groups;let g=groups.find(g=>g.id===group);if(!g){g={id:group,rows:[]};groups.push(g);}g.rows.push({id,label:{en,he},...f});return groups;},[]);}
function resolve(root,payload){
 const binding=require('./prompt-provenance').binding(root,payload);if(!binding)return {available:false};
 const {parent,child,artifact}=binding;let record;
 try{record=readJson(file(root,artifact.artifact_id));}catch(error){if(error.code!=='ENOENT')return {available:false};}
 if(record){if(!valid(record)||record.job_id!==child.parent_job_id||record.child_job_id!==child.job_id||record.artifact_sha256!==artifact.sha256||record.artifact_id!==artifact.artifact_id||artifact.generation_details?.sha256!==record.sha256)return {available:false};}
 else{if(artifact.generation_details)return {available:false};record=historical(parent,child,artifact);}
 return {available:true,schema:SCHEMA,schema_version:1,artifact_id:artifact.artifact_id,job_id:payload.jobId,source:record.source,sha256:record.sha256||null,groups:present(record)};
}
module.exports={SCHEMA,CATALOG,STATES,canonical,hash,safe,field,dimension,ratio,file,captureRequest,executionFromGraph,identityExecution,build,capture,valid,historical,present,resolve};
