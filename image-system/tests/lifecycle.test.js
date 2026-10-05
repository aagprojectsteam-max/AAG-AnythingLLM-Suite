'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {requireReady}=require('../src/lifecycle');
const {lifecycleProxy}=require('../integrations/anythingllm/aagLifecycleProxy');
const fs=require('fs'),path=require('path');
const response=()=>({code:0,setHeader(){},status(v){this.code=v;return this;},json(v){this.value=v;return this;}});
const request=(method='POST',body={})=>({method,body,query:{},headers:{cookie:'session'},header:()=> 'csrf'});
function harness(auth=true){const calls=[];const route=lifecycleProxy({composerSocket:'/private/composer',sessionCookie:()=>auth?'session':null,csrf:()=>auth?'csrf':null,transport:async(...args)=>{calls.push(args);return args[1]==='/composer/authorize'?{status:200,data:{authorized:true}}:{status:200,data:{schema:'aag.image.lifecycle.v1',state:'OFF'}};}});return {calls,route};}
test('runtime rejects every unavailable lifecycle state and malformed responses',async()=>{for(const state of ['OFF','STARTING','STOPPING','ERROR','UNKNOWN',null])await assert.rejects(requireReady(async()=>({schema:'aag.image.lifecycle.v1',state})),/Wait for READY/);await requireReady(async()=>({schema:'aag.image.lifecycle.v1',state:'READY'}));await assert.rejects(requireReady(async()=>{throw Error('secret');}),/status is unavailable/);});
test('lifecycle POST requires existing Composer CSRF authorization before fixed control action',async()=>{const h=harness(),r=response();await h.route(request(),r,'start');assert.equal(r.code,200);assert.deepEqual(h.calls.map(c=>c[1]),['/composer/authorize','/start']);assert.equal(h.calls[1][4],'{}');});
test('unauthorized mutations never reach the control socket',async()=>{const h=harness(false),r=response();await h.route(request(),r,'stop');assert.equal(r.code,403);assert.equal(h.calls.length,0);});
test('invalid action, method, query and command/path arguments rejected',async()=>{for(const [action,req] of [['start;id',request()],['stop',request('GET')],['start',request('POST',{command:'id'})],['stop',request('POST',{path:'/tmp'})],['start',{...request(),query:{cmd:'id'}}]]){const h=harness(),r=response();await h.route(req,r,action);assert.ok([400,404].includes(r.code));assert.equal(h.calls.length,0);}});
test('GET status is read-only and control transport failures are safely bounded',async()=>{const h=harness(),r=response();await h.route(request('GET'),r,'status');assert.equal(h.calls.length,1);assert.equal(h.calls[0][1],'/status');const fail=lifecycleProxy({transport:async()=>{throw Error('/private/secret');}}),out=response();await fail(request('GET'),out,'status');assert.equal(out.code,503);assert.doesNotMatch(JSON.stringify(out.value),/secret/);});
test('native lifecycle endpoints retain every existing auth/workspace/origin middleware',()=>{const s=fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/aagComposerProxy.js'),'utf8');for(const action of ['status','start','stop'])assert.ok(s.includes(`/aag-composer/:slug/lifecycle/${action}', guards,`));for(const token of ['validatedRequest,','sameOriginImageWorkspaceOnly,','flexUserRoleValid([ROLES.all])','validWorkspaceSlug,'])assert.ok(s.includes(token));});
test('Composer lifecycle has accessible state, a keyboard switch, cleanup and no persistence authority',()=>{const full=fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/frontend/AagImageComposerPanel/index.jsx'),'utf8');const s=full.slice(full.indexOf('function LifecycleControl'));for(const token of ['UNKNOWN','OFF','STARTING','READY','STOPPING','ERROR','role="status"','aria-live="polite"','type="button"','controller?.abort()','window.clearTimeout(timer)','removeEventListener','epoch.current','locked.current','lifecycle/status'])assert.ok(s.includes(token),token);assert.doesNotMatch(s,/localStorage|sessionStorage|expertEnabled|setExpertEnabled/);});


test('installed approved verifier fails closed when the approval pointer is missing', () => {
  const vm = require('node:vm');
  const source = fs.readFileSync(path.join(__dirname, '../tools/doctor.js'), 'utf8').split('const version =')[0];
  let calls = 0, exitCode = null;
  const sentinel = new Error('exit');
  const context = {__dirname:path.join(__dirname,'../tools'),require(name){
    if(name==='fs')return {existsSync:p=>p.endsWith('/runtime-control/release_gate.py')};
    if(name==='child_process')return {execFileSync(){calls++;throw new Error('Missing CURRENT.json');}};
    if(name==='./visual-atlas-product')return {verify(){}};
    return require(name);
  },process:{argv:['node','doctor.js','--deployed'],stdout:{write(){}},stderr:{write(){}},exit(code){exitCode=code;throw sentinel;}}};
  assert.throws(()=>vm.runInNewContext(source,context),error=>error===sentinel);
  assert.equal(calls,1);assert.equal(exitCode,1);
});
test('compact lifecycle row has accessible switch, hidden status, focus tooltip and independent retry',()=>{
 const s=fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/frontend/AagImageComposerPanel/index.jsx'),'utf8').split('function LifecycleControl')[1];
 for(const t of ['role="switch"','aria-checked={on}','aria-describedby="aag-lifecycle-description"','title={statusText}','aag-lifecycle-sr','aag-lifecycle-thumb','aag-lifecycle-tooltip','disabled={transitioning}','Retry start'])assert.ok(s.includes(t),t);
 assert.doesNotMatch(s,/>READY<|>OFF<|>Stop<|>Start<|localStorage|sessionStorage|setExpertEnabled/);
 const css=fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/frontend/AagImageComposerPanel/styles.css'),'utf8');
 for(const t of ['[data-state="READY"]','[data-state="STARTING"]','[data-state="STOPPING"]','[data-state="ERROR"]','[dir="rtl"]','.aag-lifecycle button:focus-visible','prefers-reduced-motion','background:#94a3b8'])assert.ok(css.includes(t),t);
});
