'use strict';
// Provider-independent framing and validation. Never rewrites an image prompt.
const MAX_REVIEW_BYTES=32768;
const REVIEW_CHECKS=['all_user_requirements','faithful_translation','counts','subjects_actions','attributes_colors','spatial_relationships','visible_text','negative_constraints','selected_style','self_contained','no_source_wrapper','no_analysis_or_plan_leakage','no_contradictions'];
const protectedFact=f=>f.classification==='USER_REQUIRED'||(f.classification==='DO_NOT_INVENT'&&Boolean(f.source_text));
const exactKeys=(o,keys)=>o&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).length===keys.length&&keys.every(k=>Object.hasOwn(o,k));
function uniqueKeys(json){
 // JSON.parse validates grammar first; this second pass detects ambiguous
 // duplicate keys instead of silently accepting the last contradictory value.
 const tokens=json.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g)||[];const stack=[];
 for(const t of tokens){
  if(t==='{'){stack.push({object:true,key:true,keys:new Set()});continue;}
  if(t==='['){stack.push({object:false});continue;}
  if(t==='}'||t===']'){stack.pop();continue;}
  const top=stack.at(-1);if(!top?.object)continue;
  if(t===',')top.key=true;
  else if(t===':')top.key=false;
  else if(t[0]==='"'&&top.key){const k=JSON.parse(t);if(top.keys.has(k))return false;top.keys.add(k);}
 }
 return true;
}
function frame(raw){
 if(typeof raw!=='string'||!raw.trim())return {classification:'INVALID_RESPONSE',reason:'EMPTY_RESPONSE'};
 if(Buffer.byteLength(raw,'utf8')>MAX_REVIEW_BYTES)return {classification:'INVALID_RESPONSE',reason:'REVIEW_TRANSPORT_BOUND'};
 const text=raw.trim(),wholeFence=/^```(?:json)?\s*\n([\s\S]*?)\n```$/i.exec(text);const source=wholeFence?wholeFence[1].trim():text;
 let json=source,mode=wholeFence?'json-fence':'json';
 try{JSON.parse(json);}catch{
  let start=-1,depth=0,quoted=false,escaped=false;const objects=[];
  for(let i=0;i<source.length;i++){
   const c=source[i];if(start<0){if(c==='{'){start=i;depth=1;}continue;}
   if(quoted){if(escaped)escaped=false;else if(c==='\\')escaped=true;else if(c==='"')quoted=false;continue;}
   if(c==='"')quoted=true;else if(c==='{')depth++;else if(c==='}'&&--depth===0){objects.push(source.slice(start,i+1));start=-1;}
  }
  if(start>=0)return {classification:'INCOMPLETE_RESPONSE',reason:'UNFINISHED_JSON'};
  if(objects.length!==1)return {classification:'INVALID_RESPONSE',reason:objects.length>1?'MULTIPLE_JSON_OBJECTS':'JSON_REQUIRED'};
  json=objects[0];mode='prose-wrapped-json';
 }
 try{const value=JSON.parse(json);if(!uniqueKeys(json))return {classification:'INVALID_RESPONSE',reason:'DUPLICATE_JSON_KEY'};return {value,framing:mode};}
 catch{return {classification:'INVALID_RESPONSE',reason:'MALFORMED_JSON'};}
}
function normalizeReview(raw,input){
 const decoded=frame(raw);if(decoded.classification)return decoded;const v=decoded.value;
 const invalid=reason=>({classification:'INVALID_RESPONSE',reason,framing:decoded.framing});
 if(!exactKeys(v,['pass','issues','checks','facts'])||typeof v.pass!=='boolean'||!Array.isArray(v.issues)||v.issues.length>32||v.issues.some(s=>typeof s!=='string'||s.length>500)||!exactKeys(v.checks,REVIEW_CHECKS)||REVIEW_CHECKS.some(k=>typeof v.checks[k]!=='boolean')||!Array.isArray(v.facts))return invalid('SCHEMA_MISMATCH');
 const required=input.facts.filter(protectedFact),byIndex=new Map(required.map(f=>[f.index,f]));
 if(v.facts.length!==required.length||new Set(v.facts.map(f=>f?.index)).size!==required.length)return invalid('INVARIANT_SET_MISMATCH');
 for(const f of v.facts){
  if(!exactKeys(f,['index','preserved','evidence','protected_evidence'])||!Number.isInteger(f.index)||!byIndex.has(f.index)||typeof f.preserved!=='boolean'||typeof f.evidence!=='string'||typeof f.protected_evidence!=='string'||f.evidence.length>8000||f.protected_evidence.length>8000)return invalid('INVARIANT_SCHEMA_MISMATCH');
  if(f.preserved&&(!f.evidence.trim()||!input.final_generation_prompt.includes(f.evidence)||!f.protected_evidence.trim()||!input.protected_prose.some(p=>p.text.includes(f.protected_evidence))))return invalid('EVIDENCE_NOT_BOUND');
 }
 const failed=v.facts.find(f=>!f.preserved),check=REVIEW_CHECKS.find(k=>!v.checks[k]);
 if(!v.pass||v.issues.length||failed||check)return {classification:'VALID_FAIL',status:'fail',reason:failed?'INVARIANT_NOT_PRESERVED':check||'REVIEW_REJECTED',failed_invariant_id:failed?'fact-'+failed.index:null,failed_dimension:failed?byIndex.get(failed.index).dimension:null,value:v,framing:decoded.framing};
 return {classification:'VALID_PASS',status:'pass',value:v,framing:decoded.framing};
}
function providerFailure(error){
 const code=String(error?.code||'');
 if(/INCOMPLETE|PREMATURE|TRUNCAT|ABORT/.test(code)||error?.termination&&error.termination.state!=='RESPONSE_COMPLETE')return {classification:'INCOMPLETE_RESPONSE',reason:'PROVIDER_COMPLETION_INCOMPLETE'};
 if(code==='EXPERT_REVIEW_UNEXPECTED_TOOL'||code==='EXPERT_REVIEW_INVALID_RESPONSE')return {classification:'INVALID_RESPONSE',reason:'TEXT_REVIEW_REQUIRED'};
 return {classification:'PROVIDER_ERROR',reason:'PROVIDER_CALL_FAILED'};
}
module.exports={MAX_REVIEW_BYTES,REVIEW_CHECKS,protectedFact,frame,normalizeReview,providerFailure};
