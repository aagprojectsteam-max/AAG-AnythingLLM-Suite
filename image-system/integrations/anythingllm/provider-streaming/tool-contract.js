'use strict';
// Shared completion contract: {textResponse, functionCall:{id,name,arguments}}.
// Wire event names belong exclusively to adapters. AIbitat remains serial.
class ToolProtocolError extends Error {constructor(code,message){super(message);this.name='ToolProtocolError';this.code=code;}}
function requireProtocol(ok,code,message){if(!ok)throw new ToolProtocolError(code,message);}
function argumentsObject(value){
 let args=value;
 if(typeof value==='string'){try{args=JSON.parse(value);}catch{throw new ToolProtocolError('TOOL_ARGUMENTS_INVALID','The selected provider returned incomplete or malformed tool arguments.');}}
 requireProtocol(args!==null&&typeof args==='object'&&!Array.isArray(args),'TOOL_ARGUMENTS_INVALID','Tool arguments must be a complete JSON object.');return args;
}
function normalizeCompletion(value){
 requireProtocol(value&&typeof value==='object','PROVIDER_COMPLETION_INVALID','The selected provider did not return a completion.');
 if(value.termination)requireProtocol(value.termination.state==='RESPONSE_COMPLETE','PROVIDER_COMPLETION_INCOMPLETE','The provider did not prove response completion; no tool was executed.');
 if(!value.functionCall)return value;
 const c=value.functionCall;
 requireProtocol(typeof c.name==='string'&&c.name.trim()&&(!c.id||typeof c.id==='string'),'TOOL_ID_INVALID','The selected provider returned an invalid tool identity.');
 return {...value,functionCall:{...c,arguments:argumentsObject(c.arguments)}};
}
function claimToolCall(call,ledger){
 if(!call.id)return; // Legacy text-only tool adapters have no provider call ID.
 requireProtocol(!ledger.has(call.id),'DUPLICATE_TOOL_CALL','The provider repeated a tool-call ID; it was not executed again.');
 ledger.add(call.id);
}
class SerialToolAssembly{
 constructor(){this.calls=new Map();}
 start(key,{id,name,extra_content}={}){
  requireProtocol(!this.calls.has(key),'TOOL_ID_CONFLICT','The provider reused a tool item.');
  const c={id,name,extra_content,raw:'',args:null,closed:false};this.calls.set(key,c);return c;
 }
 get(key){const c=this.calls.get(key);requireProtocol(c,'TOOL_ID_UNKNOWN','An argument fragment has no matching tool call.');return c;}
 delta(key,text){const c=this.get(key);requireProtocol(!c.closed&&typeof text==='string','TOOL_DELTA_INVALID','Invalid or late tool argument fragment.');c.raw+=text;requireProtocol(c.raw.length<=8*1024*1024,'TOOL_ARGUMENTS_TOO_LARGE','Tool arguments exceed the transport bound.');}
 complete(key,value){const c=this.get(key);if(c.closed){requireProtocol(value===undefined||JSON.stringify(argumentsObject(value))===JSON.stringify(c.args),'TOOL_ARGUMENTS_CONFLICT','Conflicting completed tool arguments.');return;}c.args=argumentsObject(value===undefined?c.raw:value);c.closed=true;}
 finish(ended){requireProtocol(ended,'PROVIDER_STREAM_INCOMPLETE','The provider stream ended before its completion marker; no tool ran.');requireProtocol(this.calls.size<=1,'MULTIPLE_TOOL_CALLS','The selected provider returned multiple tool calls to a serial agent; none ran.');const c=[...this.calls.values()][0];if(!c)return null;requireProtocol(c.closed,'TOOL_CALL_INCOMPLETE','The provider did not complete its tool arguments.');return normalizeCompletion({functionCall:{id:c.id,name:c.name,arguments:c.args,...(c.extra_content?{extra_content:c.extra_content}:{})}}).functionCall;}
}
module.exports={ToolProtocolError,requireProtocol,argumentsObject,normalizeCompletion,claimToolCall,SerialToolAssembly};
