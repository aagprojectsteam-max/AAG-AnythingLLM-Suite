const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
test('ordinary connector omits unsupported temperature for verified model without changing other model policy',async()=>{
 const source=fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/provider-streaming/ordinary-openai.js'),'utf8');
 const requests=[];class SDK{constructor(){this.responses={create:async x=>{requests.push(x);return {output_text:'hello',usage:{}};}};}}
 const module={exports:{}};
 vm.runInNewContext(source,{module,console:{log(){}},process:{env:{OPEN_AI_KEY:'test-only'}},require:n=>({uuid:{v4:()=> 'uuid'},'../../EmbeddingEngines/native':{NativeEmbedder:class{}},'../../helpers/abortSignals':{},'../../helpers/chat/responses':{},'../modelMap':{MODEL_MAP:{get:()=>4096}},'../../helpers/chat/LLMPerformanceMonitor':{LLMPerformanceMonitor:{measureAsyncFunction:async p=>({output:await p,duration:1})}},openai:{OpenAI:SDK}})[n]});
 for(const [model,temp] of [['gpt-6-sol',undefined],['gpt-5',1],['gpt-4.1',0.3]]){const llm=new module.exports.OpenAiLLM(null,model);assert.equal((await llm.getChatCompletion([{role:'user',content:'hello'}],{temperature:0.3})).textResponse,'hello');assert.equal(requests.at(-1).temperature,temp);assert.equal(requests.at(-1).model,model);}
});
