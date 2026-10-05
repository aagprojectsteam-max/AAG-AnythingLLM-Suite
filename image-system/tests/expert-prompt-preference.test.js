'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),path=require('path'),vm=require('node:vm');
function harness(initial={}){
 const values=new Map(Object.entries(initial)),listeners=new Map(),state=[],effects=[];let pos=0,pending=[];
 const storage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)};
 const window={addEventListener:(t,f)=>{if(!listeners.has(t))listeners.set(t,new Set());listeners.get(t).add(f);},removeEventListener:(t,f)=>listeners.get(t)?.delete(f),dispatchEvent:e=>{for(const f of listeners.get(e.type)||[])f(e);}};
 const box={localStorage:storage,window,CustomEvent:class{constructor(type,options){this.type=type;Object.assign(this,options);}},useState:init=>{const n=pos++;if(!(n in state))state[n]=typeof init==='function'?init():init;return [state[n],v=>state[n]=typeof v==='function'?v(state[n]):v];},useCallback:f=>f,useEffect:(fn,deps)=>{const n=pos++;if(!effects[n]||deps.some((v,i)=>v!==effects[n].deps[i]))pending.push(()=>{effects[n]?.cleanup?.();effects[n]={deps,cleanup:fn()};});}};
 let source=fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/frontend/useExpertPromptMode.js'),'utf8').replace(/^import .*\n/m,'').replace('export default function','function').replaceAll('export const ','const ').replaceAll('export function ','function ');
 vm.runInNewContext(source+'\nresult={useExpertPromptMode,readExpertPromptOverride,readExpertPromptEnabled,writeExpertPromptEnabled,expertPromptStorageKey};',box);
 return {values,storage,window,h:box.result,render:(workspace='image-generator',mode='advanced')=>{pos=0;pending=[];const result=box.result.useExpertPromptMode(workspace,mode);for(const f of pending)f();return result;}};
}
test('fresh AUTO stays OFF; fresh ADVANCED and AUTO to ADVANCED default ON without writing a preference',()=>{
 const h=harness();assert.equal(h.render('image-generator','auto').enabled,false);assert.equal(h.render().enabled,true);assert.equal(h.values.size,0);assert.equal(h.h.readExpertPromptOverride('image-generator',h.storage),null);
});
test('manual OFF survives rerenders and unrelated style/language/Atlas updates, AUTO round trip, refresh, thread and workspace reopen',()=>{
 const h=harness();h.render().setEnabled(false);
 for(const update of ['rerender','style','language','Atlas','thread']){assert.equal(h.render().enabled,false,update);assert.equal(h.render().override,false);}
 assert.equal(h.render('image-generator','auto').enabled,false);assert.equal(h.render().enabled,false);
 assert.equal(h.render('other').enabled,true);assert.equal(h.render().enabled,false);
 const refreshed=harness(Object.fromEntries(h.values));assert.equal(refreshed.render().enabled,false);assert.equal(refreshed.h.readExpertPromptEnabled('image-generator',refreshed.storage),false);
 assert.equal(h.values.get('aag_expert_prompt_enabled:image-generator'),'false');
});
test('manual ON persists per workspace, while AUTO remains OFF and never overwrites preference',()=>{
 const h=harness({'aag_expert_prompt_enabled:image-generator':'false'});h.render().setEnabled(true);assert.equal(h.render().enabled,true);assert.equal(h.render('image-generator','auto').enabled,false);assert.equal(h.values.get('aag_expert_prompt_enabled:image-generator'),'true');assert.equal(h.render().enabled,true);
});
test('existing true/false preferences honored; malformed or denied storage uses defaults and explicit in-memory override',()=>{
 for(const value of ['true','false']){const h=harness({'aag_expert_prompt_enabled:image-generator':value});assert.equal(h.render().enabled,value==='true');}
 const h=harness({'aag_expert_prompt_enabled:image-generator':'bad'});assert.equal(h.render().enabled,true);h.storage.setItem=()=>{throw Error('denied');};h.render().setEnabled(false);assert.equal(h.render().enabled,false);assert.equal(h.h.expertPromptStorageKey('../bad'),null);assert.equal(h.render('../bad').enabled,false);
});
test('storage events affect only the relevant workspace and refresh the explicit override',()=>{
 const h=harness();h.render();h.values.set('aag_expert_prompt_enabled:image-generator','false');h.window.dispatchEvent({type:'storage',key:'unrelated'});assert.equal(h.render().enabled,true);h.window.dispatchEvent({type:'storage',key:'aag_expert_prompt_enabled:image-generator'});assert.equal(h.render().enabled,false);
});
