'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const contract=require('../integrations/anythingllm/provider-streaming/tool-contract');
function fixture(name,events,model){
 const requests=[];let external=0,local=0;
 class SDK{constructor(){this.chat={completions:{create:async r=>{external++;requests.push(r);return stream();}}};this.messages={create:r=>{external++;requests.push(r);return {asResponse:async()=>stream()};}};}}
 for(const n of ['AuthenticationError','RateLimitError','InternalServerError','APIError'])SDK[n]=class extends Error{};
 class Local{async chat(r){local++;requests.push(r);return stream();}}
 async function* stream(){for(const e of events)yield e;}
 class Base{constructor(c){this._client=c;}get client(){return this._client;}providerLog(){}resetUsage(){}recordUsage(){}getCost(){return 0;}optsOutOfNativeToolCallingViaEnv(){return false;}}
 class LocalMetadata{static cacheContextWindows(){}static applyOllamaFetch(){return ()=>{throw Error('unexpected network');};}static promptWindowLimit(){return 8192;}static maxContextWindow(){return 8192;}async getModelCapabilities(){return {tools:true};}}
 const module={exports:{}};const source=fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/provider-streaming',name+'.js'),'utf8');
 const stubs={'./tool-contract.js':contract,'./anthropic-transport.js':{...require('../integrations/anythingllm/provider-streaming/anthropic-transport'),decodeAnthropic:async function*(response){yield* response;}},openai:SDK,'@anthropic-ai/sdk':SDK,'./ai-provider.js':Base,'../error.js':{RetryError:class extends Error{}},uuid:{v4:()=>String(++fixture.id)},'../../../http':{safeJsonParse:(v,d)=>{try{return JSON.parse(v);}catch{return d;}}},'../../../AiProviders/anthropic':{AnthropicLLM:{fetchModelMaxTokens:async()=>4096}},'../../../../endpoints/utils':{getAnythingLLMUserAgent:()=> 'test'},'./helpers/dereferenceSchema':{dereferenceSchema:x=>x},'./helpers/classes.js':()=>Base,'./helpers/untooled.js':class{},'./helpers/tooled.js':{formatFunctionsToTools:x=>x},'../../../AiProviders/ollama':{OllamaAILLM:LocalMetadata},ollama:{Ollama:Local}};
 vm.runInNewContext(source,{module,process:{env:{}},console,require:n=>{assert.ok(n in stubs,'Unexpected dependency '+n);return stubs[n];}});
 return {provider:new module.exports({model}),requests,counts:()=>({external,local})};
}
fixture.id=0;
const gemini=[{choices:[{delta:{content:'Preparing ',tool_calls:[{index:0,id:'g1',function:{name:'gtc__aag-image-task',arguments:'{"prompt":'},extra_content:{google:{thought_signature:'fixture-only'}}}]}}]},{choices:[{delta:{tool_calls:[{index:0,function:{arguments:'"cube"}'}}]}}]},{choices:[{delta:{},finish_reason:'tool_calls'}]}];
const anthropic=[{type:'message_start',message:{usage:{input_tokens:1}}},{type:'content_block_start',index:0,content_block:{type:'tool_use',id:'a1',name:'aag-image-task',input:{}}},{type:'content_block_delta',index:0,delta:{type:'input_json_delta',partial_json:'{"prompt":'}},{type:'content_block_delta',index:0,delta:{type:'input_json_delta',partial_json:'"cube"}'}},{type:'content_block_stop',index:0},{type:'message_delta',delta:{stop_reason:'tool_use'},usage:{output_tokens:2}},{type:'message_stop'}];
const ollama=[{message:{content:'Preparing ',tool_calls:[{function:{name:'aag-image-task',arguments:{prompt:'cube'}}}]},done:false},{done:true,done_reason:'stop',message:{content:''}}];
for(const [name,events,model] of [['gemini',gemini,'gemini-selected'],['anthropic',anthropic,'selected-anthropic'],['ollama',ollama,'selected-local']]){
 test(name+' actual adapter normalizes into the same tool contract',async()=>{const h=fixture(name,events,model);const r=contract.normalizeCompletion(await h.provider.stream([{role:'user',content:'test'}],[]));assert.equal(r.functionCall.name,'aag-image-task');assert.equal(r.functionCall.arguments.prompt,'cube');assert.ok(r.functionCall.id);assert.equal(h.requests[0].model,model);if(name==='gemini')assert.equal(r.functionCall.extra_content.google.thought_signature,'fixture-only');if(name==='ollama')assert.deepEqual(h.counts(),{local:1,external:0});});
 test(name+' rejects EOF before completion',async()=>{const h=fixture(name,events.slice(0,-1),model);await assert.rejects(h.provider.stream([{role:'user',content:'test'}],[]),{code:'PROVIDER_STREAM_INCOMPLETE'});});
 test(name+' next instance follows the newly selected model',async()=>{for(const selected of [model,model+'-next']){const h=fixture(name,events,selected);await h.provider.stream([{role:'user',content:'test'}],[]);assert.equal(h.requests[0].model,selected);}});
}
test('Gemini rejects conflicting IDs rather than discarding fragments',async()=>{const events=structuredClone(gemini);events[1].choices[0].delta.tool_calls[0].id='other';await assert.rejects(fixture('gemini',events,'gemini-selected').provider.stream([]),{code:'TOOL_ID_CONFLICT'});});
test('Gemini malformed arguments never become empty object',async()=>{const events=structuredClone(gemini);events[1].choices[0].delta.tool_calls[0].function.arguments='broken';await assert.rejects(fixture('gemini',events,'gemini-selected').provider.stream([]),{code:'TOOL_ARGUMENTS_INVALID'});});
test('Gemini preserves tool signature in next-turn request',async()=>{const h=fixture('gemini',gemini,'gemini-selected');const first=await h.provider.stream([]);await h.provider.stream([{role:'function',content:'ok',originalFunctionCall:first.functionCall}]);assert.equal(h.requests[1].messages[0].tool_calls[0].extra_content.google.thought_signature,'fixture-only');});
test('Anthropic must close the matching argument block',async()=>{const events=anthropic.filter(x=>x.type!=='content_block_stop');await assert.rejects(fixture('anthropic',events,'selected').provider.stream([]),{code:'TOOL_CALL_INCOMPLETE'});});
test('Anthropic does not mix another block into current arguments',async()=>{const events=structuredClone(anthropic);events[2].index=2;await assert.rejects(fixture('anthropic',events,'selected').provider.stream([]),{code:'TOOL_ID_UNKNOWN'});});
test('Local length exhaustion never executes a tool',async()=>{const events=structuredClone(ollama);events[1].done_reason='length';await assert.rejects(fixture('ollama',events,'local').provider.stream([]),{code:'PROVIDER_STREAM_INCOMPLETE'});});
test('Serial contract rejects multiple calls without choosing one',()=>{const a=new contract.SerialToolAssembly();for(const id of ['a','b']){a.start(id,{id,name:'tool'});a.complete(id,{});}assert.throws(()=>a.finish(true),{code:'MULTIPLE_TOOL_CALLS'});});
test('Neutral execution ledger forbids duplicate IDs but permits sequential new IDs',()=>{const ledger=new Set();contract.claimToolCall({id:'one'},ledger);assert.throws(()=>contract.claimToolCall({id:'one'},ledger),{code:'DUPLICATE_TOOL_CALL'});contract.claimToolCall({id:'two'},ledger);assert.equal(ledger.size,2);});
test('Neutral completion validator preserves metadata but rejects malformed args',()=>{assert.throws(()=>contract.normalizeCompletion({functionCall:{id:'one',name:'tool',arguments:'{'}}),{code:'TOOL_ARGUMENTS_INVALID'});const r=contract.normalizeCompletion({textResponse:'hello',uuid:'m',functionCall:{id:'one',name:'tool',arguments:'{"n":2}',extra_content:{a:1}}});assert.equal(r.functionCall.arguments.n,2);assert.equal(r.functionCall.extra_content.a,1);});
function nativeAgent(){
 const module={exports:{}};const Providers={};
 for(const [key,tag] of [['OpenAIProvider','openai'],['GeminiProvider','gemini'],['AnthropicProvider','anthropic'],['OllamaProvider','ollama']])Providers[key]=class{constructor({model}){this.model=model;this.tag=tag;}attachAbortSignal(signal){this.signal=signal;}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/provider-streaming/aibitat.js'),'utf8'),{module,process:{env:{}},console,AbortController,require:n=>({'./providers/tool-contract.js':contract,events:require('node:events'),'./error.js':{APIError:class extends Error{}},'./providers/index.js':Providers,'../../../models/telemetry.js':{Telemetry:{sendTelemetry(){}}},uuid:{v4:()=> 'uuid'},'./utils/toolReranker.js':{ToolReranker:class{}}})[n]});return new module.exports();
}
test('real AIbitat factory follows each selected provider/model and preserves abort signal',()=>{
 const a=nativeAgent();for(const provider of ['openai','gemini','anthropic','ollama']){const p=a.getProviderForConfig({provider,model:'active-'+provider});assert.equal(p.tag,provider);assert.equal(p.model,'active-'+provider);assert.equal(p.signal,a.abortController.signal);}
});
test('real AIbitat executes once, rejects a repeated call ID and then accepts a new sequential call',async()=>{
 const a=nativeAgent();let executions=0,id='first';a.functions.set('aag-image-task',{handler:async args=>{assert.equal(args.prompt,'cube');executions++;a.skipHandleExecution=true;return 'boundary received';}});a.providerInstance={stream:async()=>({functionCall:{id,name:'aag-image-task',arguments:{prompt:'cube'}},uuid:'m'}),getUsage:()=>({})};
 assert.equal(await a.handleAsyncExecution(),'boundary received');assert.equal(executions,1);
 await assert.rejects(a.handleAsyncExecution(),{code:'DUPLICATE_TOOL_CALL'});assert.equal(executions,1);id='second';await a.handleAsyncExecution();assert.equal(executions,2);
});
test('real AIbitat never dispatches malformed normalized arguments',async()=>{
 const a=nativeAgent();let executions=0;a.functions.set('tool',{handler:()=>executions++});a.providerInstance={stream:async()=>({functionCall:{id:'x',name:'tool',arguments:'{'}})};await assert.rejects(a.handleAsyncExecution(),/malformed/);assert.equal(executions,0);
});
