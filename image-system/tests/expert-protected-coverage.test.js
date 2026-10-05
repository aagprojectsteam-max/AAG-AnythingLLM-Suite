'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {reviewCandidate}=require('../src/expert-prompt');
const read=name=>JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/expert-protected-coverage',name+'.json')));
test('original production plan still fails protected coverage despite a complete final prompt and compatible lighting',async()=>{
 const f=read('original-failure');let calls=0;
 await assert.rejects(()=>reviewCandidate(f.request,f.record,async()=>f.responses[calls++]),e=>{
  assert.equal(e.code,'EXPERT_SEMANTIC_REVIEW_FAILED');assert.equal(e.failed_dimension,'lighting');
  const judgment=e.review_attempts.at(-1).value;
  assert.equal(judgment.checks.no_contradictions,true);assert.equal(judgment.checks.selected_style,true);
  assert.deepEqual(judgment.facts.filter(f=>!f.preserved).map(f=>f.index),[3,5,6]);return true;
 });assert.equal(calls,2);
});
for(const name of ['product-exact','watercolor-translation','noir-compatible','vector-compatible'])test(`recorded real review accepts protected source requirements and semantic variation: ${name}`,async()=>{
 const f=read(name);let calls=0;
 const review=await reviewCandidate(f.request,f.record,async messages=>{
  const input=JSON.parse(messages[1].content);assert.equal(input.selected_style,f.record.review_style.canonical_id);
  assert.ok(input.protected_prose.every(p=>p.priority<=2));return f.responses[calls++];
 });assert.equal(review.classification,'VALID_PASS');assert.equal(calls,1);assert.equal(review.checks.all_user_requirements,true);assert.equal(review.checks.selected_style,true);assert.equal(review.checks.no_contradictions,true);
});
test('a changed protected plan cannot reuse valid evidence from the original complete plan',async()=>{
 const f=read('product-exact');f.record.plan.fragments.filter(p=>p.priority===1).forEach(p=>p.text='One mug.');let calls=0;
 await assert.rejects(()=>reviewCandidate(f.request,f.record,async()=>{calls++;return f.responses[0];}),e=>e.code==='EXPERT_SEMANTIC_REVIEW_INVALID_RESPONSE');assert.equal(calls,2);
});
test('real recorded lighting contradiction remains a semantic rejection before engine submission',async()=>{
 const f=read('genuine-lighting-conflict');let calls=0;
 await assert.rejects(()=>reviewCandidate(f.request,f.record,async()=>f.responses[calls++]),e=>{
  assert.equal(e.code,'EXPERT_SEMANTIC_REVIEW_FAILED');assert.equal(e.failed_dimension,'lighting');assert.equal(e.review_attempts.at(-1).value.checks.no_contradictions,false);return true;
 });assert.equal(calls,1);
});
