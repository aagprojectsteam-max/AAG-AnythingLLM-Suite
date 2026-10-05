'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {decodeResponses,ResponsesAssembly,singleCall}=require('../integrations/anythingllm/provider-streaming/responses-stream');
const call=(id='fc_1',args='{"prompt":"red cube"}')=>({type:'function_call',id,call_id:'call_'+id,name:'aag-image-task',arguments:args,status:'completed'});
const added=(item=call(),index=0)=>({type:'response.output_item.added',output_index:index,item:{...item,arguments:'',status:'in_progress'}});
const delta=(text,item=call(),index=0)=>({type:'response.function_call_arguments.delta',item_id:item.id,output_index:index,delta:text});
const done=(item=call(),index=0)=>({type:'response.function_call_arguments.done',item_id:item.id,output_index:index,arguments:item.arguments});
const completed=(output=[call()])=>({type:'response.completed',response:{id:'resp_1',status:'completed',output,usage:{input_tokens:10,output_tokens:8}}});
const message=text=>({type:'message',content:[{type:'output_text',text}]});
const frame=e=>`event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`;
function response(wire,chunk=Infinity){const bytes=Buffer.from(wire);return {headers:new Headers({'content-type':'text/event-stream; charset=utf-8'}),body:(async function*(){for(let i=0;i<bytes.length;i+=chunk)yield bytes.subarray(i,i+chunk);})()};}
async function parse(wire,chunk,options){const a=new ResponsesAssembly(options);for await(const e of decodeResponses(response(wire,chunk)))a.push(e);return a.finish();}
const run=events=>parse(events.map(frame).join(''));
const rejected=(work,code)=>assert.rejects(work,e=>e.code===code);
test('normal text stream including split UTF-8 and CRLF',async()=>{let text='';const wire=[{type:'response.output_text.delta',delta:'שלום 🧊'},completed([message('שלום 🧊')])].map(frame).join('').replaceAll('\n','\r\n');const r=await parse(wire,1,{onText:x=>text+=x});assert.equal(r.textResponse,text);assert.equal(text,'שלום 🧊');assert.equal(singleCall(r),null);});
test('deltas, done, output item and completed assemble one exact call',async()=>{const c=call();const r=await run([added(c),delta('{"prompt":'),delta('"red cube"}'),done(c),{type:'response.output_item.done',output_index:0,item:c},completed()]);assert.deepEqual(singleCall(r),{id:c.call_id,name:c.name,arguments:{prompt:'red cube'}});});
test('complete arguments supplied only on done',async()=>{assert.deepEqual(singleCall(await run([added(),done(),completed()])).arguments,{prompt:'red cube'});});
test('done can supply a missing suffix, never replace conflicting deltas',async()=>{assert.equal(singleCall(await run([added(),delta('{"prompt":'),done(),completed()])).arguments.prompt,'red cube');await rejected(run([added(),delta('{"wrong":'),done(),completed()]),'OPENAI_ARGUMENTS_CONFLICT');});
test('final authoritative item can supply complete arguments',async()=>assert.equal(singleCall(await run([completed()])).arguments.prompt,'red cube'));
test('exact live regression: event-only done is SSE metadata, not empty JSON',async()=>{const wire=frame(added())+frame(delta(call().arguments))+'event: response.function_call_arguments.done\n\n'+frame(completed());for(const chunk of [1,2,7,31,Infinity])assert.equal(singleCall(await parse(wire,chunk)).arguments.prompt,'red cube');});
test('metadata-only done cannot authorize incomplete execution',async()=>{await rejected(parse(frame(added())+frame(delta('{"prompt":'))+'event: response.function_call_arguments.done\n\n'),'OPENAI_PREMATURE_EOF');});
test('explicit empty JSON data is a protocol error, not ignored',async()=>{await rejected(parse('event: response.function_call_arguments.done\ndata:\n\n'),'OPENAI_EVENT_JSON_INVALID');});
test('SSE comments/id/retry/unknown fields and multiline JSON follow framing',async()=>{const e=completed([]);const wire=': ping\r\nid: 1\r\nretry: 500\r\nx: ignored\r\n\r\nevent: response.completed\r\ndata: {"type":"response.completed",\r\ndata: "response":'+JSON.stringify(e.response)+'}\r\n\r\n';assert.equal((await parse(wire,1)).functionCalls.length,0);});
test('malformed JSON event is explicit and no tool result escapes',()=>rejected(parse('event: response.completed\ndata: {\n\n'),'OPENAI_EVENT_JSON_INVALID'));
test('event name and data type must agree',()=>rejected(parse('event: response.completed\ndata: {"type":"response.failed"}\n\n'),'OPENAI_EVENT_TYPE_MISMATCH'));
test('unfinished frame is rejected even after an otherwise completed response',()=>rejected(parse(frame(completed())+'event: x\ndata: {'),'OPENAI_TRUNCATED_FRAME'));
test('EOF without response.completed rejects valid-looking arguments',()=>rejected(run([added(),done()]),'OPENAI_PREMATURE_EOF'));
test('early DONE marker never authorizes execution',()=>rejected(parse(frame(added())+'data: [DONE]\n\n'),'OPENAI_PREMATURE_EOF'));
test('DONE marker after completion is accepted',async()=>assert.equal((await parse(frame(completed())+'data: [DONE]\n\n')).functionCalls.length,1));
for(const raw of ['{','null','[]','"text"',''])test('invalid argument object rejected: '+JSON.stringify(raw),()=>rejected(run([added(),done(call('fc_1',raw)),completed()]),raw?'OPENAI_ARGUMENTS_INVALID':'OPENAI_ARGUMENTS_MISSING'));
test('semantic done without arguments rejects',()=>rejected(run([added(),{...done(),arguments:undefined}]),'OPENAI_ARGUMENTS_MISSING'));
test('unknown item ID rejects; no accidental cross-call append',()=>rejected(run([added(),delta('{}',call('fc_other'))]),'OPENAI_CALL_ID_UNKNOWN'));
test('delta after done rejects',()=>rejected(run([added(),done(),delta('extra')]),'OPENAI_ARGUMENTS_DELTA_INVALID'));
test('conflicting done records reject',()=>rejected(run([added(),done(),done(call('fc_1','{"prompt":"other"}'))]),'OPENAI_ARGUMENTS_CONFLICT'));
test('multiple interleaved calls retained; serial adapter refuses all rather than losing one',async()=>{const a=call('a','{"n":1}'),b=call('b','{"n":2}');const r=await run([added(a,0),added(b,1),delta(a.arguments,a,0),delta(b.arguments,b,1),done(b,1),done(a,0),completed([a,b])]);assert.deepEqual(r.functionCalls.map(x=>x.arguments.n),[1,2]);assert.throws(()=>singleCall(r),{code:'OPENAI_MULTIPLE_TOOL_CALLS'});});
test('sequential calls across completed responses retain independent IDs',async()=>{const seen=[];for(const id of ['one','two'])seen.push(singleCall(await run([completed([call(id)])])).id);assert.deepEqual(seen,['call_one','call_two']);});
test('duplicate sequence and done events result in one handler execution',async()=>{const d={...delta(call().arguments),sequence_number:2},c=completed();let executions=0;const r=await run([added(),d,d,done(),done(),c,c]);if(singleCall(r))executions++;assert.equal(executions,1);assert.equal(r.functionCalls.length,1);});
test('conflicting duplicate sequence rejects',()=>rejected(run([added(),{...delta('{'),sequence_number:1},{...delta('}'),sequence_number:1}]),'OPENAI_SEQUENCE_CONFLICT'));
test('final response must include all streamed calls',()=>rejected(run([added(),done(),completed([])]),'OPENAI_CALL_INCOMPLETE'));
for(const type of ['error','response.failed','response.incomplete'])test('provider failure propagates: '+type,async()=>{await assert.rejects(run([{type,error:{code:'insufficient_quota',message:'Test provider failure'}}]),e=>e.providerCode==='insufficient_quota'&&e.message==='Test provider failure');});
test('completed event with incomplete status rejects',()=>rejected(run([{...completed(),response:{...completed().response,status:'incomplete'}}]),'OPENAI_COMPLETION_INVALID'));
test('invalid UTF-8 rejects explicitly',async()=>{const r=response('');r.body=(async function*(){yield Buffer.from([0xff]);})();await assert.rejects(async()=>{for await(const e of decodeResponses(r))void e;},{code:'OPENAI_UTF8_INVALID'});});
test('abort and transport errors propagate; no partial function returned',async()=>{const r=response('');r.body=(async function*(){yield Buffer.from(frame(added()));throw Object.assign(new Error('aborted'),{code:'OPENAI_STREAM_ABORTED'});})();await assert.rejects(async()=>{for await(const e of decodeResponses(r))void e;},{code:'OPENAI_STREAM_ABORTED'});});
test('wrong content type rejects',async()=>{const r=response('');r.headers=new Headers({'content-type':'application/json'});await assert.rejects(async()=>{for await(const e of decodeResponses(r))void e;},{code:'OPENAI_CONTENT_TYPE'});});
// Load the actual provider module with SDK transport stubs; do not fork its logic.
function adapter(result){
 const calls=[];class SDK{constructor(){this.responses={create:(req)=>{calls.push(req);return req.stream?{asResponse:async()=>response(result)}:Promise.resolve(result);}};}};
 for(const name of ['AuthenticationError','RateLimitError','InternalServerError','APIError'])SDK[name]=class extends Error{};
 class Base{constructor(client){this.client=client;}providerLog(){}resetUsage(){this.usage=null;}recordUsage(v){this.usage=v;}getUsage(){return this.usage;}}
 const module={exports:{}};const source=fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/provider-streaming/openai.js'),'utf8');
 vm.runInNewContext(source,{module,process:{env:{}},require:name=>({'openai':SDK,'./ai-provider.js':Base,'../error.js':{RetryError:class extends Error{}},uuid:{v4:()=> 'uuid'},'./responses-stream.js':require('../integrations/anythingllm/provider-streaming/responses-stream')})[name]});
 return {provider:new module.exports({model:'selected-model'}),calls};
}
test('native adapter preserves selected model, tool schema, UI streaming and serial contract',async()=>{const {provider,calls}=adapter([added(),done(),completed()].map(frame).join(''));const events=[];const r=await provider.stream([{role:'user',content:'request'}],[{name:'aag-image-task',parameters:{type:'object'}}],(...x)=>events.push(x));assert.equal(r.functionCall.arguments.prompt,'red cube');assert.equal(calls[0].model,'selected-model');assert.equal(calls[0].parallel_tool_calls,false);assert.equal(calls[0].store,false);assert.equal(calls[0].tools[0].strict,false);assert.ok(events.length);});
test('native non-streaming complete validates rather than substituting empty args',async()=>{const {provider}=adapter(completed().response);assert.equal((await provider.complete([])).functionCall.arguments.prompt,'red cube');const bad=adapter(completed([call('fc_1','{')]).response);await assert.rejects(bad.provider.complete([]),{code:'OPENAI_ARGUMENTS_INVALID'});});
test('native provider does not return a function on truncated stream',async()=>{await assert.rejects(adapter([added(),done()].map(frame).join('')).provider.stream([]),{code:'OPENAI_PREMATURE_EOF'});});
test('native provider preserves sequential original call/output history',async()=>{const {provider,calls}=adapter(frame(completed([])));await provider.stream([{role:'function',content:'result',originalFunctionCall:{id:'prior',name:'tool',arguments:{n:1}}}]);assert.equal(calls[0].input[0].call_id,'prior');assert.equal(calls[0].input[1].type,'function_call_output');assert.equal(calls[0].input[1].output,'result');});
