'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
function setup(){
 const source=fs.readFileSync(path.join(__dirname,'../integrations/anythingllm/frontend/AagArtifactPrompt.jsx'),'utf8');
 const code=source.slice(source.indexOf('  function isBackdrop'),source.indexOf('  function close()'));
 let closed=0;const node={getBoundingClientRect:()=>({left:100,right:500,top:100,bottom:600})},dialog={current:node},press={current:null};
 const f=Function('dialog','backdropPress','close',code+';return {startBackdropPress,moveBackdropPress,finishBackdropPress};')(dialog,press,()=>closed++);
 const e=(where,more={})=>({target:where==='backdrop'?node:{tagName:'PRE'},clientX:where==='backdrop'?50:120,clientY:200,button:0,isPrimary:true,pointerId:1,...more});
 return{f,e,node,press,closed:()=>closed,source};
}
test('selection beginning inside cannot dismiss when its synthesized click targets dialog',()=>{
 const t=setup();t.f.startBackdropPress(t.e('inside'));t.f.moveBackdropPress(t.e('backdrop'));t.f.finishBackdropPress(t.e('backdrop'));assert.equal(t.closed(),0);
 assert.doesNotMatch(t.source,/onClick=\{e=>\{if\(e.target===dialog.current\)close/);
});
test('only stationary primary backdrop press and release with same pointer dismiss',()=>{
 const t=setup();t.f.startBackdropPress(t.e('backdrop'));t.f.finishBackdropPress(t.e('backdrop'));assert.equal(t.closed(),1);assert.equal(t.press.current,null);
 for(const [start,end] of [['backdrop','inside'],['inside','inside']]){const x=setup();x.f.startBackdropPress(x.e(start));x.f.finishBackdropPress(x.e(end));assert.equal(x.closed(),0);}
 for(const extra of [{button:2},{isPrimary:false}]){const x=setup();x.f.startBackdropPress(x.e('backdrop',extra));x.f.finishBackdropPress(x.e('backdrop',extra));assert.equal(x.closed(),0);}
 const x=setup();x.f.startBackdropPress(x.e('backdrop'));x.f.finishBackdropPress(x.e('backdrop',{pointerId:2}));assert.equal(x.closed(),0);
});
test('crossing content, dragging, dialog border, pointer cancellation and stale releases do not dismiss',()=>{
 for(const middle of ['inside','backdrop']){const t=setup();t.f.startBackdropPress(t.e('backdrop'));t.f.moveBackdropPress(t.e(middle,{clientY:300}));t.f.finishBackdropPress(t.e('backdrop'));assert.equal(t.closed(),0);}
 const t=setup();t.f.startBackdropPress(t.e('backdrop',{clientX:101}));t.f.finishBackdropPress(t.e('backdrop',{clientX:101}));assert.equal(t.closed(),0);
 t.f.startBackdropPress(t.e('backdrop'));t.press.current=null;t.f.finishBackdropPress(t.e('backdrop'));assert.equal(t.closed(),0);
 assert.match(t.source,/onPointerCancel/);assert.match(t.source,/onCancel=\{e=>\{e.preventDefault\(\);close\(\);\}\}/);
});
