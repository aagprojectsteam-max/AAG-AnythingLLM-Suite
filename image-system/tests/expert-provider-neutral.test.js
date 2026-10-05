"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { prepare } = require("../src/expert-prompt");
const { composerInvocationPrompt, composerCanonicalJson } = require("../integrations/anythingllm/aagComposerHistory");
const { presentationPrompt } = require("../integrations/anythingllm/aagArtifactPresentation");
const { expertPublicSchema } = require("../integrations/anythingllm/aagPublicToolSchema");
const hash = text => crypto.createHash("sha256").update(text).digest("hex");
function envelope(request="One cup.") {
 const intent = { user_request_sha256: hash(request), expert_prompt: { schema:"aag.expert.context.v1" } };
 return `AAG_COMPOSER_STRUCTURED_REQUIREMENTS_V1=${composerCanonicalJson(intent)}\nUSER_CREATIVE_DIRECTION=\n${request}\nAAG_COMPOSER_INTENT_SIGNATURE_V1=${"a".repeat(64)}`;
}
test("Expert OFF has zero added calls and preserves the same arguments", async () => {
 const args={operation:"generate",prompt:"One cup."}; let calls=0;
 const result=await prepare(args,{},async()=>{calls++;throw Error("must not call");});
 assert.equal(result.args,args);assert.equal(result.records,null);assert.equal(calls,0);
 await assert.rejects(prepare({...args,expert_plan:{}},{}),/signed current-turn/);
});
test("private Expert state resets between turns; provider fields are never copied", () => {
 const runtime={};composerInvocationPrompt(envelope(),runtime);assert.ok(runtime.AAG_EXPERT_ENVELOPE);
 composerInvocationPrompt("ordinary next turn",runtime);assert.equal(runtime.AAG_EXPERT_ENVELOPE,null);
 assert.deepEqual(Object.keys(runtime),["AAG_EXPERT_ENVELOPE", "AAG_COMPOSER_ENVELOPE"]);
});
test("existing native routing follows current chat selection for Expert, across provider changes", async () => {
 for (const name of ["index.js","ephemeral.js"]) {
  const source=fs.readFileSync(path.join(__dirname,"../integrations/anythingllm/runtime-context-bridge",name),"utf8");
  const start=source.indexOf("  async #providerSetupAndCheck() {");const end=source.indexOf("\n  async #resolveRouterProvider",start);
  let method=source.slice(start,end).replace("async #providerSetupAndCheck()", "async function setup()").replaceAll("this.#","this.");
  const setup=Function("composerInvocationPrompt",`return (${method.trim()});`)(composerInvocationPrompt);
  const workspace={chatProvider:"external-a",chatModel:"model-a",agentProvider:"unrelated-agent",agentModel:"unrelated-model"};
  const obj={workspace,invocation:{workspace,prompt:envelope()},prompt:envelope(),invocationUUID:"test",getFallbackProvider(){return {provider:workspace.chatProvider,model:workspace.chatModel};},fetchModel(){return workspace.agentModel;},log(){},checkSetup(){},resolveRouterProvider(){throw Error("unexpected router call");}};
  for (const [provider,model] of [["external-a","model-a"],["external-b","model-b"],["local-test","local-model"]]) {
   Object.assign(workspace,{chatProvider:provider,chatModel:model});await setup.call(obj);assert.equal(obj.provider,provider);assert.equal(obj.model,model);
  }
  obj.prompt="ordinary";obj.invocation.prompt="ordinary";await setup.call(obj);assert.equal(obj.provider,"unrelated-agent");assert.equal(obj.model,"unrelated-model");
 }
});
test("Expert modules have no independent provider client, selector or provider allow-list", () => {
 const files=["../src/expert-prompt.js","../expert-prompt/neutral_context.py","../expert-prompt/compiler.py","../integrations/anythingllm/frontend/useExpertPromptMode.js"];
 for(const file of files){const source=fs.readFileSync(path.join(__dirname,file),"utf8");assert.doesNotMatch(source,/gemini|google|openai|anthropic|generativelanguage|api\.anthropic|new OpenAI|providerAllowList/i);}
 const source=fs.readFileSync(path.join(__dirname,"../src/expert-prompt.js"),"utf8");assert.match(source,/socketPath:/);assert.doesNotMatch(source,/https:|hostname:|\/chat\/completions/);
});
test("legacy 2000-character history remains exact; only hash-bound Expert text is lossless", () => {
 const text="useful prose ".repeat(180);assert.equal(presentationPrompt(text),text.slice(0,2000));
 const record={contract:"aag.expert.prompt.v1",prompt:text,sha256:hash(text),tokens_including_template:450,truncated:false};
 assert.equal(presentationPrompt(text,[record]),text);assert.equal(presentationPrompt(text,[{...record,sha256:"f".repeat(64)}]),text.slice(0,2000));
});
test("public legacy prompt limit stays 1800 for OFF", () => {
 const schema=JSON.parse(fs.readFileSync(path.join(__dirname,"../schemas/provider-task.schema.json")));
 assert.equal(expertPublicSchema(schema,{name:"aag-image-task"},{}),schema);assert.equal(schema.properties.prompt.maxLength,1800);
});

test("dynamic Expert schema expands only the current turn and retains a closed plan", () => {
 const os=require("os");const temp=fs.mkdtempSync(path.join(os.tmpdir(),"aag-expert-schema-"));
 try {
  const dir=path.join(temp,"plugins/agent-skills/aag-image-task");fs.mkdirSync(dir,{recursive:true});
  fs.copyFileSync(path.join(__dirname,"../expert-prompt/schemas/visual-plan.schema.json"),path.join(dir,"expert-plan.schema.json"));
  const base=JSON.parse(fs.readFileSync(path.join(__dirname,"../schemas/provider-task.schema.json")));
  const runtime={AAG_EXPERT_ENVELOPE:envelope()};const expanded=expertPublicSchema(base,{name:"aag-image-task"},runtime,{pluginsRoot:temp});
  assert.equal(expanded.properties.prompt.maxLength,8000);assert.equal(expanded.properties.expert_plan.additionalProperties,false);assert.ok(expanded.required.includes("expert_plan"));
  runtime.AAG_EXPERT_ENVELOPE=null;assert.equal(expertPublicSchema(base,{name:"aag-image-task"},runtime,{pluginsRoot:temp}),base);assert.equal(base.properties.prompt.maxLength,1800);
 } finally {fs.rmSync(temp,{recursive:true,force:true});}
});

test("Expert model history excludes earlier styles and documents without mutating persistence", () => {
 const {composerModelHistory}=require("../integrations/anythingllm/aagComposerHistory");
 const history=[{role:"system",content:"Tool policy"},{role:"user",content:"unrelated old style and document"},{role:"assistant",content:"old response"},{role:"user",content:envelope(),attachments:[{private:"reference"}]}];
 const result=composerModelHistory(history);assert.equal(result.length,2);assert.equal(result[1].content,envelope());assert.equal(result[1].attachments,undefined);assert.equal(history.length,4);assert.ok(history[3].attachments);
 const ordinary=[{role:"user",content:"prior"},{role:"user",content:"current"}];assert.deepEqual(composerModelHistory(ordinary),ordinary);
});

test("an open chat re-reads active selection for every Expert turn and restores OFF routing", async () => {
 const {installExpertModelRouting}=require("../integrations/anythingllm/aagComposerHistory");let selected={provider:"local-test",model:"one"};let reads=0;let legacy=0;
 const agent={resolveRoute:async()=>{legacy++;return {provider:"agent-test",model:"ordinary"};}};
 installExpertModelRouting(agent,async prompt=>{reads++;return prompt===envelope()?{...selected}:{provider:"agent-test",model:"ordinary"};});
 assert.deepEqual(await agent.resolveRoute("ordinary"),{provider:"agent-test",model:"ordinary"});assert.equal(reads,0);
 assert.deepEqual(await agent.resolveRoute(envelope()),selected);selected={provider:"different-test",model:"two"};assert.deepEqual(await agent.resolveRoute(envelope()),selected);
 assert.deepEqual(await agent.resolveRoute("ordinary"),{provider:"agent-test",model:"ordinary"});assert.equal(reads,3);
 await agent.resolveRoute("ordinary again");assert.equal(reads,3);assert.equal(legacy,2);
});

test("Expert provenance persists completely while OFF record shape stays unchanged", () => {
 const os=require("os");const store=require("../src/store");const temp=fs.mkdtempSync(path.join(os.tmpdir(),"aag-expert-persistence-"));fs.mkdirSync(path.join(temp,"jobs"));
 try {
 const evidence={prompt:"Exact author text",sha256:hash("Exact author text"),request_sha256:hash("request"),context_sha256:hash("context"),plan:{facts:[],fragments:[],conflicts:[]},tokens_including_template:50};
 const on=store.createRecord(temp,{expert_prompt:evidence});assert.deepEqual(store.read(temp,on.job_id).expert_prompt,evidence);
 const off=store.createRecord(temp,{});assert.equal(Object.hasOwn(off,"expert_prompt"),false);
 } finally {fs.rmSync(temp,{recursive:true,force:true});}
});

test('AAG workspace resolves active selection afresh with Expert OFF and no second selector',async()=>{
 const {installExpertModelRouting}=require('../integrations/anythingllm/aagComposerHistory');
 let current={provider:'external-a',model:'a'},reads=0;
 const agent={resolveRoute:()=>{throw Error('stale route used');}};
 installExpertModelRouting(agent,async()=>{reads++;return {...current};},{followActiveModel:true});
 for(const choice of [{provider:'external-a',model:'a'},{provider:'external-b',model:'b'},{provider:'local',model:'local-model'}]){current=choice;assert.deepEqual(await agent.resolveRoute('ordinary image request'),choice);}
 assert.equal(reads,3);
});

test('both native AAG entry points use workspace scope and current chat selection with Expert OFF',async()=>{
 for(const name of ['index.js','ephemeral.js']){
  const source=fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/runtime-context-bridge',name),'utf8');
  if(name==='ephemeral.js')assert.ok(source.includes('followActiveModel: this.#workspace?.slug === "image-generator"'));
  const start=source.indexOf('  async #providerSetupAndCheck() {'),end=source.indexOf('\n  async #resolveRouterProvider',start);
  const method=source.slice(start,end).replace('async #providerSetupAndCheck()','async function setup()').replaceAll('this.#','this.');
  const setup=Function('composerInvocationPrompt',`return (${method.trim()});`)(composerInvocationPrompt);
  const workspace={slug:'image-generator',agentProvider:'stale-agent',agentModel:'stale-model',chatProvider:'local-selected',chatModel:'local-one'};
  const state={workspace,invocation:{workspace,prompt:'ordinary image request'},prompt:'ordinary image request',getFallbackProvider(){return {provider:workspace.chatProvider,model:workspace.chatModel};},fetchModel(){return workspace.agentModel;},log(){},checkSetup(){},resolveRouterProvider(){throw Error('Unexpected reroute');}};
  await setup.call(state);assert.equal(state.provider,'local-selected');assert.equal(state.model,'local-one');workspace.chatProvider='external-selected';workspace.chatModel='external-two';await setup.call(state);assert.equal(state.provider,'external-selected');assert.equal(state.model,'external-two');
 }
});

test('local Expert connection failure cannot impersonate engine unavailability',async()=>{
 const os=require('os'),{validateLocal}=require('../src/expert-prompt'),{classifyError}=require('../src/errors');const old=process.env.AAG_COMPOSER_SOCKET;
 process.env.AAG_COMPOSER_SOCKET=path.join(os.tmpdir(),'missing-aag-expert-'+process.pid+'.sock');
 try{await assert.rejects(()=>validateLocal({}),error=>{assert.equal(classifyError(error).code,'EXPERT_VALIDATOR_UNAVAILABLE');assert.equal(error.retryable,true);return true;});}finally{if(old===undefined)delete process.env.AAG_COMPOSER_SOCKET;else process.env.AAG_COMPOSER_SOCKET=old;}
});
