'use strict';
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const { AagError } = require('./errors');
const { ensureDirectory, readJson } = require('./util');
const SCHEMA = 'aag.image-prompt-provenance.v2';
const LEGACY_SCHEMA = 'aag.final-engine-prompt.v1';
const ORIGINAL_SCHEMA = 'aag.original-user-request.v1';
const ID = /^aag-[a-f0-9-]{36}$/i;
const hash = text => crypto.createHash('sha256').update(text, 'utf8').digest('hex');
function publishable(text, env = process.env) {
  if (typeof text !== 'string' || !text.length || Buffer.byteLength(text, 'utf8') > 262144) return false;
  // Withhold unsafe disclosure rather than redact it and claim exactness.
  if (/(?:\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|\bAIza[A-Za-z0-9_-]{20,}|\b(?:Authorization|Proxy-Authorization|x-api-key)\s*:|\bBearer\s+[A-Za-z0-9._~-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|AAG_COMPOSER_(?:STRUCTURED_REQUIREMENTS|INTENT_SIGNATURE)_V1=)/i.test(text)) return false;
  for (const [key, value] of Object.entries(env)) if (/(?:SECRET|TOKEN|PASSWORD|API_KEY|CREDENTIAL)/i.test(key) && typeof value === 'string' && value.length >= 8 && text.includes(value)) return false;
  return true;
}
function file(root, jobId, directory = 'prompt-provenance') {
  if (!ID.test(jobId)) throw new AagError('PROMPT_PROVENANCE_INVALID', 'Invalid prompt provenance identity.');
  return path.join(root, directory, jobId + '.json');
}
function publish(target, record, agrees) {
  ensureDirectory(path.dirname(target));
  const tmp = target + '.' + crypto.randomUUID() + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(record) + '\n', {flag:'wx',mode:0o600});
  try { fs.linkSync(tmp, target); }
  catch (error) {
    if (error.code !== 'EEXIST') throw error;
    const existing = readJson(target);
    if (!agrees(existing)) throw new AagError('PROMPT_PROVENANCE_CONFLICT', 'Existing image provenance cannot be changed.');
    return existing;
  } finally { fs.unlinkSync(tmp); }
  return record;
}
function finalText(record) { return record.schema === LEGACY_SCHEMA ? record.prompt : record.final_generation_prompt; }
function finalHash(record) { return record.schema === LEGACY_SCHEMA ? record.sha256 : record.final_generation_prompt_sha256; }
function aliasesAgree(record) {
  return record.schema!==LEGACY_SCHEMA || (!Object.hasOwn(record,'final_generation_prompt') && !Object.hasOwn(record,'final_generation_prompt_sha256')) ||
    (record.final_generation_prompt===record.prompt && record.final_generation_prompt_sha256===record.sha256);
}
function capture(root, child, { prompt, boundary, captured_at = new Date().toISOString() }) {
  if (!ID.test(child.job_id) || !ID.test(child.parent_job_id) || !['comfy.CLIPTextEncode.text','pulid.encode_prompt.prompt','scene.encode_prompt.prompt'].includes(boundary)) throw new AagError('PROMPT_PROVENANCE_INVALID', 'Invalid image-engine prompt boundary.');
  const available = publishable(prompt);
  // Keep the existing reader contract for rollback. Explicit v2 field names
  // are immutable aliases of the same final text, never a second prompt.
  const record = { schema:LEGACY_SCHEMA, field_schema:SCHEMA, job_id:child.job_id, parent_job_id:child.parent_job_id, captured_at, boundary, available,
    ...(available ? {prompt,sha256:hash(prompt),final_generation_prompt:prompt,final_generation_prompt_sha256:hash(prompt)} : {reason:'not_recorded'}) };
  return publish(file(root,child.job_id),record,existing =>
    [SCHEMA,LEGACY_SCHEMA].includes(existing.schema) && aliasesAgree(existing) && existing.job_id===child.job_id && existing.parent_job_id===child.parent_job_id && existing.available===available && finalText(existing)===finalText(record) && finalHash(existing)===finalHash(record) && existing.boundary===boundary);
}
function captureOriginal(root, parent, original, evidence = {}) {
  if (!ID.test(parent.job_id)) throw new AagError('PROMPT_PROVENANCE_INVALID','Invalid original request identity.');
  const available=publishable(original);
  const record={schema:ORIGINAL_SCHEMA,job_id:parent.job_id,captured_at:new Date().toISOString(),source:evidence.chat_id?'persisted_chat_artifact_binding':'trusted_current_invocation',
    ...(evidence.chat_id?{chat_id:evidence.chat_id}:{}),available,
    ...(available?{original_user_request:original,original_user_request_sha256:hash(original)}:{reason:'not_recorded'})};
  return publish(file(root,parent.job_id,'original-request-provenance'),record,existing =>
    existing.schema===ORIGINAL_SCHEMA && existing.job_id===parent.job_id && existing.available===available && existing.original_user_request===record.original_user_request && existing.original_user_request_sha256===record.original_user_request_sha256);
}
function read(root, jobId, directory) { try { return readJson(file(root,jobId,directory)); } catch (error) { if(error.code==='ENOENT')return null; throw error; } }
function verified(record) {
  return [SCHEMA,LEGACY_SCHEMA].includes(record?.schema) && aliasesAgree(record) && record.available===true && publishable(finalText(record)) && finalHash(record)===hash(finalText(record)) && ID.test(record.job_id) && ID.test(record.parent_job_id);
}
function binding(root,payload) {
  if (!ID.test(payload?.jobId) || !/^artifact-[a-f0-9-]{36}$/i.test(payload?.artifactId||'')) return null;
  const store=require('./store'),parent=store.read(root,payload.jobId);
  const artifact=parent.artifacts?.find(a=>a.artifact_id===payload.artifactId && a.sha256===payload.artifactSha256);
  if(!artifact || !ID.test(artifact.child_job_id))return null;
  const child=store.read(root,artifact.child_job_id);
  if(!child.artifacts?.some(a=>a.artifact_id===artifact.artifact_id && a.sha256===artifact.sha256))return null;
  const ownerParent=child.parent_job_id===parent.job_id?parent:store.read(root,child.parent_job_id);
  return {parent,artifact,child,ownerParent};
}
// Offline adoption requires the very chat row containing this exact artifact,
// matching owner scope, and any existing request hash. Never inspect neighbours.
function originalFromHistory(root,payload,chat) {
  const bound=binding(root,payload);if(!bound || !chat || !publishable(chat.prompt))return null;
  const {ownerParent,child}=bound,owner=ownerParent.owner;
  if(!owner || String(chat.workspaceId)!==owner.workspace_id || String(chat.thread_id)!==owner.thread_id || String(chat.user_id??'unknown')!==owner.user_id)return null;
  let outputs;try{outputs=JSON.parse(chat.response).outputs;}catch{return null;}
  if(!outputs?.some(o=>o.type==='imageGenerationCard'&&o.payload?.jobId===payload.jobId&&o.payload?.artifactId===payload.artifactId&&o.payload?.artifactSha256===payload.artifactSha256))return null;
  const requestHash=child.expert_prompt?.request_sha256||ownerParent.expert_prompt?.request_sha256;
  if(requestHash && requestHash!==hash(chat.prompt))return null;
  return {job_id:ownerParent.job_id,original_user_request:chat.prompt,original_user_request_sha256:hash(chat.prompt),chat_id:chat.id};
}
function originalFor(root,ownerParent) {
  let record;
  try { record=read(root,ownerParent.job_id,'original-request-provenance'); }
  catch { return {original_user_request_available:false}; }
  if(!record || record.schema!==ORIGINAL_SCHEMA || record.job_id!==ownerParent.job_id || record.available!==true || !publishable(record.original_user_request) || record.original_user_request_sha256!==hash(record.original_user_request))return {original_user_request_available:false};
  return {original_user_request_available:true,original_user_request:record.original_user_request,original_user_request_sha256:record.original_user_request_sha256,original_user_request_source:record.source};
}
function resolve(root, payload) {
  const bound=binding(root,payload);if(!bound)return {available:false};
  const {artifact,child,ownerParent}=bound;
  if(child.operation==='upscale')return {available:false};
  const recorded=read(root,child.job_id);
  let record;
  if(recorded) {
    if(!verified(recorded) || recorded.job_id!==child.job_id || recorded.parent_job_id!==child.parent_job_id)return {available:false};
    if(child.engine?.final_prompt_sha256 && child.engine.final_prompt_sha256!==finalHash(recorded))return {available:false};
    record={prompt:finalText(recorded),sha256:finalHash(recorded),captured_at:recorded.captured_at};
  } else {
    const digest=child.engine?.final_prompt_sha256;
    if(!/^[a-f0-9]{64}$/.test(digest||''))return {available:false};
    const candidates=[child.expert_prompt?.prompt,ownerParent.expert_prompt?.prompt,child.public_arguments?.prompt,ownerParent.public_arguments?.prompt,payload.prompt];
    const text=candidates.find(s=>publishable(s) && hash(s)===digest);
    if(text===undefined)return {available:false};
    record={prompt:text,sha256:digest,captured_at:child.engine?.submitted_at||child.started_at,source:'historical_engine_hash_match'};
  }
  // Legacy response aliases refer to the same final text for existing clients.
  return {available:true,prompt:record.prompt,sha256:record.sha256,final_generation_prompt:record.prompt,final_generation_prompt_sha256:record.sha256,
    ...originalFor(root,ownerParent),job_id:payload.jobId,child_job_id:child.job_id,artifact_id:artifact.artifact_id,artifact_index:payload.logicalIndex||child.child_index||1,generation_timestamp:record.captured_at,source:record.source||'immutable_engine_handoff',derived:!!artifact.derived_from_artifact_id};
}
module.exports={SCHEMA,LEGACY_SCHEMA,ORIGINAL_SCHEMA,hash,publishable,capture,captureOriginal,read,verified,binding,resolve,originalFromHistory};
