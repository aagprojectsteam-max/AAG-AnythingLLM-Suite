'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),cp=require('node:child_process'),path=require('node:path');
const expert=require('../src/expert-prompt'),{frame,normalizeReview,MAX_REVIEW_BYTES}=require('../src/expert-review');
const fact={index:0,dimension:'actions_poses',classification:'USER_REQUIRED',source_text:'בסיבוב החד',text:'negotiating a sharp bend'};
const prompt='A racing motorcycle leans through a tight corner, nearly scraping the track.';
const input={facts:[fact],final_generation_prompt:prompt,protected_prose:[{text:prompt}]};
const good=()=>({pass:true,issues:[],checks:Object.fromEntries(expert.REVIEW_CHECKS.map(k=>[k,true])),facts:[{index:0,preserved:true,evidence:'leans through a tight corner',protected_evidence:'leans through a tight corner'}]});
const record=()=>({prompt,sha256:'test-hash',selected_style:'photography/sports-action',plan:{facts:[fact],fragments:[{priority:1,text:prompt}]}});
test('bounded parser accepts exact JSON, fenced JSON and one prose-wrapped object with identical semantics',()=>{
 const raw=JSON.stringify(good());for(const s of [raw,'```json\n'+raw+'\n```','Review follows.\n'+raw+'\nEnd review.'])assert.equal(normalizeReview(s,input).classification,'VALID_PASS');
});
test('arbitrary prose, malformed JSON, duplicate keys, multiple objects and transport overflow never pass',()=>{
 for(const s of ['Looks good!','{"pass":yes}',JSON.stringify(good()).replace('"pass":true','"pass":false,"pass":true'),JSON.stringify(good())+' '+JSON.stringify(good()),' '.repeat(MAX_REVIEW_BYTES)+JSON.stringify(good())])assert.equal(normalizeReview(s,input).classification,'INVALID_RESPONSE');
 for(const s of ['{','{"pass":true','```json\n{"pass":true'])assert.equal(frame(s).classification,'INCOMPLETE_RESPONSE');
});
test('missing fields, invalid enum/types, fabricated evidence and contradictory verdicts fail closed',()=>{
 for(const mutate of [v=>delete v.checks.counts,v=>v.pass='pass',v=>v.facts[0].preserved='true',v=>v.facts[0].evidence='fabricated quote',v=>v.facts[0].protected_evidence='other plan',v=>delete v.facts[0].evidence]){const v=good();mutate(v);assert.equal(normalizeReview(JSON.stringify(v),input).classification,'INVALID_RESPONSE');}
 const v=good();v.checks.counts=false;assert.equal(normalizeReview(JSON.stringify(v),input).classification,'VALID_FAIL');
});
test('one same-stage format repair, no author rewrite, incomplete/provider/semantic failures do not retry',async()=>{
 let calls=0;const r=await expert.reviewCandidate('רוכב בסיבוב החד',record(),async m=>{calls++;assert.equal(JSON.parse(m[1].content).final_generation_prompt,prompt);if(calls===1)return 'not JSON';assert.equal(m.length,3);assert.match(m[2].content,/Do not change the image prompt/);return JSON.stringify(good());});assert.equal(r.attempt_count,2);assert.equal(calls,2);
 for(const [raw,code] of [['{','EXPERT_SEMANTIC_REVIEW_INCOMPLETE'],[JSON.stringify({...good(),pass:false}),'EXPERT_SEMANTIC_REVIEW_FAILED']]){calls=0;await assert.rejects(expert.reviewCandidate('source',record(),async()=>{calls++;return raw;}),{code});assert.equal(calls,1);}
 calls=0;await assert.rejects(expert.reviewCandidate('source',record(),async()=>{calls++;throw Error('provider unavailable');}),{code:'EXPERT_SEMANTIC_REVIEW_PROVIDER_ERROR'});assert.equal(calls,1);
 calls=0;await assert.rejects(expert.reviewCandidate('source',record(),async()=>{calls++;return 'invalid';}),{code:'EXPERT_SEMANTIC_REVIEW_INVALID_RESPONSE'});assert.equal(calls,2);
});
test('paraphrased protected prose, translated facts and final evidence do not require lexical equality',()=>{
 assert.ok(!prompt.includes(fact.text));assert.ok(!prompt.includes(fact.source_text));assert.equal(normalizeReview(JSON.stringify(good()),input).classification,'VALID_PASS');
});
test('optional user preferences and inference cannot block required semantic preservation',async()=>{
 const r=record();r.plan.facts.push({classification:'USER_PREFERRED',dimension:'lighting',source_text:'if possible, soft lighting',text:'soft lighting'});
 const reviewed=await expert.reviewCandidate('required motorcycle; if possible, soft lighting',r,async m=>{
  assert.equal(JSON.parse(m[1].content).facts.length,1);return JSON.stringify(good());
 });assert.equal(reviewed.classification,'VALID_PASS');
});
test('all multilingual dimensions and genuine omissions reach semantic gate before recording engine handoff',async()=>{
 const python="import sys,json;sys.path.insert(0,'expert-prompt/tests');from test_self_contained import fixture,CASES;from neutral_context import validate_candidate; print(json.dumps([dict(request=r,prompt=p,plan=plan,record=validate_candidate(r,i,p,plan)) for r,i,p,plan,_ in [fixture(n) for n in range(len(CASES))]]))";
 const cases=JSON.parse(cp.execFileSync('python3',['-c',python],{cwd:path.resolve(__dirname,'..'),encoding:'utf8'}));let handoffs=0;
 for(const f of cases){
  const args={operation:'generate',prompt:f.prompt,expert_plan:f.plan},validate=async()=>({arguments:args,results:[f.record]});
  const valid={...good(),facts:f.plan.facts.map((x,index)=>({index,preserved:true,evidence:x.text,protected_evidence:x.text}))};
  const rt={AAG_EXPERT_ENVELOPE:'fixture',AAG_INVOCATION_PROMPT:f.request,AAG_EXPERT_REVIEW:async()=>JSON.stringify(valid)};
  await expert.prepare(args,rt,validate);handoffs++;
  for(let index=0;index<f.plan.facts.length;index++){
   const rejected=structuredClone(valid);rejected.pass=false;rejected.facts[index]={index,preserved:false,evidence:'Requirement absent or changed',protected_evidence:''};
   await assert.rejects(expert.prepare(args,{...rt,AAG_EXPERT_REVIEW:async()=>JSON.stringify(rejected)},validate),e=>e.code==='EXPERT_SEMANTIC_REVIEW_FAILED'&&e.failed_invariant_id==='fact-'+index&&e.failed_dimension===f.plan.facts[index].dimension);
  }
  for(const key of ['no_source_wrapper','selected_style','all_user_requirements']){const negative=structuredClone(valid);negative.checks[key]=false;await assert.rejects(expert.prepare(args,{...rt,AAG_EXPERT_REVIEW:async()=>JSON.stringify(negative)},validate),{code:'EXPERT_SEMANTIC_REVIEW_FAILED'});}
 }
 assert.equal(handoffs,cases.length);
});
test('exact live motorcycle fact uses equivalent scene prose rather than its literal extracted English phrase',async()=>{
 const f=require('./fixtures/expert-live-motorcycle.json');const protectedText=f.plan.fragments.filter(x=>x.priority<=2).map(x=>x.text).join('\n');
 assert.equal(f.plan.facts[1].source_text,'באמצע מירוץ');assert.ok(!protectedText.includes(f.plan.facts[1].text));assert.match(protectedText,/race|racing/i);assert.ok(!f.prompt.startsWith(f.request));
 const r={prompt:f.prompt,sha256:'fixture',selected_style:f.intent.selected_style,plan:f.plan};
 const v={...good(),facts:f.plan.facts.map((x,index)=>({index,preserved:true,evidence:f.prompt,protected_evidence:f.plan.fragments[0].text})).filter(x=>['USER_REQUIRED','USER_PREFERRED'].includes(f.plan.facts[x.index].classification))};
 const reviewed=await expert.reviewCandidate(f.request,r,async()=>JSON.stringify(v));assert.equal(reviewed.classification,'VALID_PASS');
 const failed=structuredClone(v);failed.facts.find(x=>x.index===1).preserved=false;
 await assert.rejects(expert.reviewCandidate(f.request,r,async()=>JSON.stringify(failed)),e=>e.failed_invariant_id==='fact-1'&&e.failed_dimension==='environment');
});
