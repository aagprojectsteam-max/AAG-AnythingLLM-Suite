'use strict';
// OpenAI Responses only. Chat Completions and other providers keep their own decoders.
// SSE framing: https://html.spec.whatwg.org/multipage/server-sent-events.html
const MAX_FRAME_BYTES = 8 * 1024 * 1024;
const MAX_STREAM_BYTES = 32 * 1024 * 1024;
class ResponsesProtocolError extends Error {
  constructor(code, message) { super(message); this.name = 'ResponsesProtocolError'; this.code = code; this.termination = terminationFor(code); }
}
function terminationFor(code) {
  if(code==='OPENAI_RESPONSE_FAILED')return {state:'RESPONSE_FAILED',reason:'RESPONSE_FAILED'};
  if(code==='OPENAI_RESPONSE_INCOMPLETE')return {state:'RESPONSE_INCOMPLETE',reason:'RESPONSE_INCOMPLETE'};
  if(code==='OPENAI_CLIENT_CANCEL')return {state:'STREAM_ABORTED',reason:'CLIENT_CANCEL'};
  if(code==='OPENAI_TIMEOUT')return {state:'STREAM_ABORTED',reason:'TIMEOUT'};
  if(code==='OPENAI_NETWORK_DISCONNECT')return {state:'STREAM_ABORTED',reason:'NETWORK_DISCONNECT'};
  if(code==='OPENAI_STREAM_ABORTED')return {state:'STREAM_ABORTED',reason:'UNKNOWN_ABORT'};
  if(['OPENAI_PREMATURE_EOF','OPENAI_TERMINAL_PAYLOAD_MISSING','OPENAI_TRUNCATED_FRAME'].includes(code))return {state:'RESPONSE_INCOMPLETE',reason:'PREMATURE_EOF'};
  return {state:'PROVIDER_ERROR',reason:'PROVIDER_ERROR'};
}
function classifyStreamError(error, signal) {
  if(signal?.aborted) {
    const timeout=signal.reason?.name==='TimeoutError';
    return new ResponsesProtocolError(timeout?'OPENAI_TIMEOUT':'OPENAI_CLIENT_CANCEL',timeout?'OpenAI request timed out; no tool call was executed.':'OpenAI request was cancelled by the client; no tool call was executed.');
  }
  if(error instanceof ResponsesProtocolError)return error;
  const code=error?.code || error?.cause?.code;
  if(error?.name==='APIConnectionTimeoutError' || error?.name==='TimeoutError' || ['request-timeout','body-timeout'].includes(error?.type) || ['ETIMEDOUT','UND_ERR_HEADERS_TIMEOUT','UND_ERR_BODY_TIMEOUT','UND_ERR_CONNECT_TIMEOUT'].includes(code))return new ResponsesProtocolError('OPENAI_TIMEOUT','OpenAI transport timed out; no tool call was executed.');
  if(error?.name==='AbortError' || error?.name==='APIUserAbortError')return new ResponsesProtocolError('OPENAI_STREAM_ABORTED','OpenAI transport was aborted without confirmed client cancellation; no tool call was executed.');
  if(['ECONNRESET','EPIPE','ECONNREFUSED','UND_ERR_SOCKET','ENOTFOUND','EAI_AGAIN','ERR_STREAM_PREMATURE_CLOSE'].includes(code) || error?.name==='APIConnectionError' || error?.type==='system')return new ResponsesProtocolError('OPENAI_NETWORK_DISCONNECT','OpenAI transport disconnected; no tool call was executed.');
  return error;
}
function ensureNotAborted(signal){if(signal?.aborted)throw classifyStreamError(null,signal);}
class ResponsesTrace {
  constructor(){this.frames=0;this.events=0;this.bytes=0;this.counts={};this.emptyEvents={};this.lastSseEvent=null;this.lastEvent=null;this.eof=false;this.tail=[];}
  safeType(type){return typeof type==='string' && /^[a-z][a-z0-9._]{0,100}$/.test(type)?type:'unknown';}
  frame(frame){this.frames++;const type=this.safeType(frame.event);this.lastSseEvent=type;if(!frame.hasData)this.emptyEvents[type]=(this.emptyEvents[type]||0)+1;this.tail.push({type,hasData:frame.hasData});if(this.tail.length>16)this.tail.shift();}
  event(event){const type=this.safeType(event.type);this.events++;this.lastEvent=type;this.counts[type]=(this.counts[type]||0)+1;}
  snapshot(){return {frames:this.frames,events:this.events,bytes:this.bytes,counts:this.counts,empty_events:this.emptyEvents,last_sse_event:this.lastSseEvent,last_event:this.lastEvent,eof:this.eof,tail:this.tail};}
}
function check(condition, code, message) { if (!condition) throw new ResponsesProtocolError(code, message); }

async function* decodeResponses(response, {signal=null,trace=null}={}) {
  ensureNotAborted(signal);
  check(response?.body, 'OPENAI_BODY_MISSING', 'OpenAI returned no response stream.');
  check(String(response.headers?.get('content-type') || '').split(';')[0].trim() === 'text/event-stream', 'OPENAI_CONTENT_TYPE', 'OpenAI returned an unexpected streaming content type.');
  const decoder = new TextDecoder('utf-8', {fatal:true});
  let line = '', eventName = '', data = [], hasData = false, frameBytes = 0, totalBytes = 0, cr = false;
  function consumeLine(value) {
    if (value === '') {
      if(eventName || hasData)trace?.frame({event:eventName,hasData});
      const frame = hasData ? {event:eventName, data:data.join('\n')} : null;
      // A frame without data fields is not dispatched by SSE. It is not a
      // Responses "done" event, even if its event-name field says done.
      eventName = ''; data = []; hasData = false; frameBytes = 0; return frame;
    }
    if (value.startsWith(':')) return null;
    const colon = value.indexOf(':');
    const field = colon < 0 ? value : value.slice(0, colon);
    let content = colon < 0 ? '' : value.slice(colon + 1);
    if (content.startsWith(' ')) content = content.slice(1);
    if (field === 'event') eventName = content;
    else if (field === 'data') { hasData = true; data.push(content); }
    return null; // id/retry/unknown SSE fields are transport metadata.
  }
  function* text(value) {
    for (const character of value) {
      if (cr) { cr = false; if (character === '\n') continue; }
      frameBytes += Buffer.byteLength(character);
      check(frameBytes <= MAX_FRAME_BYTES, 'OPENAI_FRAME_TOO_LARGE', 'OpenAI stream event exceeds the transport bound.');
      if (character === '\n' || character === '\r') {
        const frame = consumeLine(line); line = ''; cr = character === '\r';
        if (frame) yield frame;
      } else line += character;
    }
  }
  function parse(frame) {
    if (frame.data === '[DONE]') return {type:'transport.done'};
    let value;
    try { value = JSON.parse(frame.data); }
    catch { throw new ResponsesProtocolError('OPENAI_EVENT_JSON_INVALID', 'OpenAI sent malformed JSON event data.'); }
    check(value && typeof value === 'object' && !Array.isArray(value), 'OPENAI_EVENT_INVALID', 'OpenAI stream event must be an object.');
    if (frame.event === 'error' && !value.type) value = {...value,type:'error'};
    check(typeof value.type === 'string', 'OPENAI_EVENT_TYPE_MISSING', 'OpenAI stream event has no type.');
    check(!frame.event || frame.event === value.type, 'OPENAI_EVENT_TYPE_MISMATCH', 'OpenAI event name and payload type differ.');
    return value;
  }
  try {
    for await (const bytes of response.body) {
      ensureNotAborted(signal);
      totalBytes += Buffer.byteLength(bytes);if(trace)trace.bytes=totalBytes;
      check(totalBytes <= MAX_STREAM_BYTES, 'OPENAI_STREAM_TOO_LARGE', 'OpenAI response exceeds the transport bound.');
      for (const frame of text(decoder.decode(bytes, {stream:true}))) yield parse(frame);
    }
    ensureNotAborted(signal);if(trace)trace.eof=true;
    for (const frame of text(decoder.decode())) yield parse(frame);
    // SSE does not dispatch an unterminated event at EOF. Never turn its
    // partial arguments into a tool call; the assembler requires completion.
    check(!line && !hasData, 'OPENAI_TRUNCATED_FRAME', 'OpenAI stream ended inside an event.');
  } catch (error) {
    if (error instanceof TypeError && /encoded data|encoding/i.test(error.message)) throw new ResponsesProtocolError('OPENAI_UTF8_INVALID', 'OpenAI stream contains invalid UTF-8.');
    throw classifyStreamError(error,signal);
  }
}

function parseArguments(raw) {
  check(typeof raw === 'string' && raw.length > 0, 'OPENAI_ARGUMENTS_MISSING', 'OpenAI function call has no complete arguments.');
  let args;
  try { args = JSON.parse(raw); }
  catch { throw new ResponsesProtocolError('OPENAI_ARGUMENTS_INVALID', 'OpenAI function-call arguments are incomplete or malformed JSON.'); }
  check(args && typeof args === 'object' && !Array.isArray(args), 'OPENAI_ARGUMENTS_INVALID', 'OpenAI function-call arguments must be an object.');
  return args;
}
class ResponsesAssembly {
  constructor({onText=()=>{},onTool=()=>{}}={}) { this.onText=onText; this.onTool=onTool; this.calls=new Map(); this.indices=new Map(); this.ids=new Map(); this.sequences=new Map(); this.content=''; this.completed=false; this.terminated=false; this.usage=null; }
  add(item, index) {
    check(item && typeof item.id==='string' && typeof item.call_id==='string' && typeof item.name==='string' && item.id && item.call_id && item.name && Number.isInteger(index) && index>=0, 'OPENAI_CALL_ID_INVALID', 'OpenAI function-call identity is missing or invalid.');
    if (this.calls.has(item.id)) {
      const old=this.calls.get(item.id);check(old.index===index && old.name===item.name && old.call_id===item.call_id,'OPENAI_CALL_ID_CONFLICT','OpenAI function-call identity changed.');return old;
    }
    check(!this.indices.has(index) && !this.ids.has(item.call_id), 'OPENAI_CALL_ID_CONFLICT', 'OpenAI reused a function-call identity.');
    check(this.calls.size<64,'OPENAI_TOO_MANY_CALLS','OpenAI returned too many function calls.');
    const call={id:item.id,index,call_id:item.call_id,name:item.name,delta:'',raw:null};this.calls.set(item.id,call);this.indices.set(index,item.id);this.ids.set(item.call_id,item.id);return call;
  }
  find(event) {
    const call=this.calls.get(event.item_id);check(call && call.index===event.output_index,'OPENAI_CALL_ID_UNKNOWN','OpenAI argument event does not match a registered function call.');return call;
  }
  seal(call, raw) {
    check(typeof raw==='string' && raw.length>0,'OPENAI_ARGUMENTS_MISSING','OpenAI function call has no complete arguments.');
    check(call.raw===null || call.raw===raw,'OPENAI_ARGUMENTS_CONFLICT','OpenAI supplied conflicting completed arguments.');
    check(!call.delta || raw.startsWith(call.delta),'OPENAI_ARGUMENTS_CONFLICT','OpenAI completed arguments disagree with streamed arguments.');if(call.raw===null)call.arguments=parseArguments(raw);call.raw=raw;
  }
  push(event) {
    check(event && typeof event.type==='string','OPENAI_EVENT_INVALID','OpenAI stream event is invalid.');
    if (event.sequence_number !== undefined) {
      check(Number.isInteger(event.sequence_number) && event.sequence_number>=0,'OPENAI_SEQUENCE_INVALID','OpenAI event sequence is invalid.');
      const serialized=JSON.stringify(event), prior=this.sequences.get(event.sequence_number);
      if (prior!==undefined) { check(prior===serialized,'OPENAI_SEQUENCE_CONFLICT','OpenAI replayed an event with conflicting data.');return; }
      check(this.lastSequence===undefined || event.sequence_number>this.lastSequence,'OPENAI_SEQUENCE_ORDER','OpenAI events arrived out of order.');
      this.lastSequence=event.sequence_number;this.sequences.set(event.sequence_number,serialized);
    }
    if(event.response?.id) {check(!this.responseId || this.responseId===event.response.id,'OPENAI_RESPONSE_ID_CONFLICT','OpenAI response identity changed during the stream.');this.responseId=event.response.id;}
    if (event.type==='error' || event.type==='response.failed' || event.type==='response.incomplete' || event.error) {
      const detail=event.error || event.response?.error || event;
      const error=new ResponsesProtocolError(event.type==='response.incomplete'?'OPENAI_RESPONSE_INCOMPLETE':event.type==='response.failed'?'OPENAI_RESPONSE_FAILED':'OPENAI_PROVIDER_ERROR', typeof detail.message==='string'?detail.message:'OpenAI did not complete the response.');
      error.providerCode=typeof detail.code==='string'?detail.code:null;
      const reason=event.response?.incomplete_details?.reason;
      if(typeof reason==='string' && /^[a-z_]{1,60}$/.test(reason)){error.incompleteReason=reason;error.message='OpenAI response is incomplete ('+reason+'); no tool call was executed.';}
      throw error;
    }
    if (event.type==='transport.done') { check(this.completed,'OPENAI_PREMATURE_EOF','OpenAI terminated before response.completed.');this.terminated=true;return; }
    check(!this.terminated,'OPENAI_EVENT_AFTER_END','OpenAI sent data after stream termination.');
    if (event.type==='response.completed' && this.completed) {check(JSON.stringify(event.response)===this.finalResponse,'OPENAI_COMPLETION_CONFLICT','OpenAI supplied conflicting final responses.');return;}
    check(!this.completed,'OPENAI_EVENT_AFTER_END','OpenAI sent data after response completion.');
    if (event.type==='response.output_text.delta' || event.type==='response.refusal.delta') {
      check(typeof event.delta==='string','OPENAI_TEXT_INVALID','OpenAI text delta is invalid.');this.content+=event.delta;this.onText(event.delta);return;
    }
    if (event.type==='response.output_item.added' && event.item?.type==='function_call') {
      const call=this.add(event.item,event.output_index);
      check(typeof event.item.arguments==='string','OPENAI_ARGUMENTS_MISSING','OpenAI function-call arguments field is missing.');
      if (event.item.arguments) {check(!call.delta || call.delta===event.item.arguments,'OPENAI_ARGUMENTS_CONFLICT','OpenAI changed initial arguments.');call.delta=event.item.arguments;}
      this.onTool(call);return;
    }
    if (event.type==='response.function_call_arguments.delta') {
      const call=this.find(event);check(call.raw===null && typeof event.delta==='string','OPENAI_ARGUMENTS_DELTA_INVALID','OpenAI argument delta arrived after completion or is invalid.');call.delta+=event.delta;this.onTool(call);return;
    }
    if (event.type==='response.function_call_arguments.done') {this.seal(this.find(event),event.arguments);return;}
    if (event.type==='response.output_item.done' && event.item?.type==='function_call') {this.seal(this.add(event.item,event.output_index),event.item.arguments);return;}
    if (event.type==='response.completed') {
      const response=event.response;check(response?.status==='completed' && !response.error && !response.incomplete_details && Array.isArray(response.output),'OPENAI_COMPLETION_INVALID','OpenAI final response is not complete.');
      const finalIds=new Set();let finalText='';
      response.output.forEach((item,index)=>{
        if(item.type==='function_call') {check(!item.status || item.status==='completed','OPENAI_CALL_INCOMPLETE','OpenAI final function call is not complete.');const call=this.add(item,index);this.seal(call,item.arguments);check(!finalIds.has(call.id),'OPENAI_CALL_ID_CONFLICT','OpenAI duplicated a final function call.');finalIds.add(call.id);}
        if(item.type==='message') for(const part of item.content||[]) {if(part.type==='output_text')finalText+=part.text;else if(part.type==='refusal')finalText+=part.refusal;}
      });
      check(finalIds.size===this.calls.size,'OPENAI_CALL_INCOMPLETE','OpenAI omitted a streamed call from its completed response.');
      check(finalText.startsWith(this.content),'OPENAI_TEXT_CONFLICT','OpenAI final text disagrees with its stream.');
      if(finalText.length>this.content.length)this.onText(finalText.slice(this.content.length));this.content=finalText;
      this.usage=response.usage || null;this.completed=true;this.finalResponse=JSON.stringify(response);
    }
    // Other Responses events are progress/content/annotation/reasoning metadata;
    // only response.completed authorizes returning any function to AIbitat.
  }
  finish({signal=null,trace=null}={}) {
    ensureNotAborted(signal);
    if(!this.completed && trace?.emptyEvents['response.completed'])throw new ResponsesProtocolError('OPENAI_TERMINAL_PAYLOAD_MISSING','OpenAI stream ended with a response.completed header but no completion data; completion cannot be verified and no tool call was executed.');
    check(this.completed,'OPENAI_PREMATURE_EOF','OpenAI stream ended before response.completed; no tool call was executed.');
    return {termination:{state:'RESPONSE_COMPLETE',reason:'CLEAN_SUCCESSFUL_TERMINATION'},textResponse:this.content,functionCalls:[...this.calls.values()].sort((a,b)=>a.index-b.index).map(c=>({id:c.call_id,name:c.name,arguments:c.arguments})),usage:this.usage};
  }
}
function singleCall(result) {
  // AIbitat's execution contract is serial and requests parallel_tool_calls:false.
  // Preserve every call in the assembler, but never silently choose the last one.
  check(result.functionCalls.length<=1,'OPENAI_MULTIPLE_TOOL_CALLS','OpenAI returned multiple tool calls to a serial agent; none were executed.');
  return result.functionCalls[0] || null;
}
module.exports={ResponsesTrace,classifyStreamError,ensureNotAborted,decodeResponses,ResponsesAssembly,ResponsesProtocolError,parseArguments,singleCall};
