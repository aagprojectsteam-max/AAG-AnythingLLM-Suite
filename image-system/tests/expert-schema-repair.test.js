"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),os=require("node:os"),path=require("node:path");
const {repairExpertPlan,validatePublicArguments,withPublicToolSchema}=require("../integrations/anythingllm/aagPublicToolSchema");
const base=require("../schemas/provider-task.schema.json"),plan=require("../expert-prompt/schemas/visual-plan.schema.json");
const schema=structuredClone(base);schema.properties.expert_plan=plan;schema.required.push("expert_plan");
const valid={operation:"generate",prompt:"A red cup.",source_policy:"auto",preservation:"none",expert_plan:{facts:[],fragments:[{id:"required",priority:1,text:"One red cup."},{id:"style",priority:2,text:"Photograph."}],conflicts:[]}};
const broken=()=>({...structuredClone(valid),expert_plan:{...structuredClone(valid.expert_plan),id:"hallucinated-plan-id"}});
async function fix(args,review,options={},s=schema,context="signed current request") {return repairExpertPlan(s,args,validatePublicArguments(s,args).errors,context,review,options);}
test("historical extra plan id is reauthored once and fully revalidated",async()=>{
 let calls=0;const draft=broken();const r=await fix(draft,async messages=>{calls++;assert.equal(messages.length,2);const data=JSON.parse(messages[1].content);assert.equal(data.errors[0].location,"$.expert_plan.id");return JSON.stringify(valid);});
 assert.deepEqual(r,valid);assert.equal(calls,1);assert.equal(draft.expert_plan.id,"hallucinated-plan-id");
});
test("JSON key order can change without changing values",async()=>{const r={expert_plan:valid.expert_plan,preservation:"none",source_policy:"auto",prompt:"A red cup.",operation:"generate"};assert.ok(await fix(broken(),async()=>JSON.stringify(r)));});
test("valid draft makes zero authoring calls",async()=>{let calls=0;assert.equal(await fix(valid,async()=>{calls++;}),null);assert.equal(calls,0);});
test("non-plan defects are rejected without a correction call",async()=>{let calls=0;for(const a of [{...broken(),operation:"invalid"},{...broken(),unexpected:1}])assert.equal(await fix(a,async()=>{calls++;}),null);assert.equal(calls,0);});
test("all routing values and the image prompt stay exact",async()=>{for(const [key,value] of [["operation","transform"],["source_policy","previous_artifact"],["prompt","A blue cup."],["quality","fast"]]){const changed={...structuredClone(valid),[key]:value};assert.equal(await fix(broken(),async()=>JSON.stringify(changed)),null,key);}});
test("still invalid repaired plans fail closed after one attempt",async()=>{let calls=0;assert.equal(await fix(broken(),async()=>{calls++;return JSON.stringify(broken());}),null);assert.equal(calls,1);});
test("malformed and fenced responses never get heuristically executed",async()=>{for(const text of ["not JSON","null","[]","```json\n"+JSON.stringify(valid)+"\n```"])assert.equal(await fix(broken(),async()=>text),null);});
test("review failure and size bounds reject without execution",async()=>{assert.equal(await fix(broken(),async()=>{throw Error("provider failed");}),null);assert.equal(await fix(broken(),async()=>" ".repeat(131073)),null);let calls=0;assert.equal(await fix(broken(),async()=>{calls++;},{},schema,"x".repeat(131073)),null);assert.equal(calls,0);});
test("bounded authoring deadline returns failure",async()=>{assert.equal(await fix(broken(),()=>new Promise(()=>{}),{timeoutMs:10}),null);});
test("abort before or during correction never authorizes execution",async()=>{let aborted=true,calls=0;assert.equal(await fix(broken(),async()=>{calls++;},{aborted:()=>aborted}),null);assert.equal(calls,0);aborted=false;assert.equal(await fix(broken(),async()=>{aborted=true;return JSON.stringify(valid);},{aborted:()=>aborted}),null);});
test("missing context and unavailable callback do not attempt correction",async()=>{let calls=0;assert.equal(await fix(broken(),async()=>{calls++;},{},schema,""),null);assert.equal(await fix(broken(),null),null);assert.equal(calls,0);});
test("nested batch plans use the same correction and preserve item order",async()=>{const s={type:"object",additionalProperties:false,required:["items"],properties:{items:{type:"array",items:schema}}};const a={items:[broken(),structuredClone(valid)]},good={items:[structuredClone(valid),structuredClone(valid)]};assert.deepEqual(await fix(a,async()=>JSON.stringify(good),{},s),good);good.items[0].prompt="other";assert.equal(await fix(a,async()=>JSON.stringify(good),{},s),null);});
function fixture(t,review){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),"aag-plan-repair-"));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const dir=path.join(root,"plugins/agent-skills/aag-image-task");fs.mkdirSync(dir,{recursive:true});fs.writeFileSync(path.join(dir,"provider-task.schema.json"),JSON.stringify(base));fs.writeFileSync(path.join(dir,"expert-plan.schema.json"),JSON.stringify(plan));
 let executions=0,received;const a={functions:new Map(),_aborted:false,skipHandleExecution:false,reviewExpertPrompt:review};
 const configured={setup(target){target.functions.set("aag-image-task",{runtimeArgs:{AAG_EXPERT_ENVELOPE:"signed context"},handler:async args=>{executions++;received=args;return "one job";}});}};
 withPublicToolSchema({name:"aag-image-task",config:{public_schema:"provider-task.schema.json",same_turn_retry:"forbidden"}},configured,{pluginsRoot:root}).setup(a);
 return {a,run:args=>a.functions.get("aag-image-task").handler(args),count:()=>executions,received:()=>received};
}
test("real closed handler calls engine once only after corrected schema passes",async t=>{let reviews=0;const f=fixture(t,async()=>{reviews++;return JSON.stringify(valid);});assert.equal(await f.run(broken()),"one job");assert.equal(reviews,1);assert.equal(f.count(),1);assert.deepEqual(f.received(),valid);assert.equal(f.a.skipHandleExecution,true);});
test("real closed handler preserves failed result and zero jobs on bad correction",async t=>{const f=fixture(t,async()=>JSON.stringify(broken()));assert.match(await f.run(broken()),/PUBLIC_SCHEMA_VIOLATION/);assert.equal(f.count(),0);assert.equal(f.a.skipHandleExecution,true);});
test("real closed handler leaves ordinary valid execution unchanged",async t=>{let reviews=0;const f=fixture(t,async()=>{reviews++;});assert.equal(await f.run(valid),"one job");assert.equal(reviews,0);assert.equal(f.count(),1);});
