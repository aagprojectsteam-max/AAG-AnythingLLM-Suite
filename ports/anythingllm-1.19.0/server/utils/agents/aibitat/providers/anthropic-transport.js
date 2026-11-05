'use strict';
// Anthropic wire diagnostics only. No request rewriting, retries or filter bypass.
const { createHash } = require('crypto');
const fingerprint = value => {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? null);
  return { bytes: Buffer.byteLength(text), sha256: createHash('sha256').update(text).digest('hex') };
};
const label = value => typeof value === 'string' && /^[a-z_]{1,64}$/.test(value) ? value : null;
class AnthropicTransportError extends Error {
  constructor(code, message, details = {}) {
    super(message); this.name = 'AnthropicTransportError'; this.code = code;
    this.details = { provider: 'anthropic', ...details };
    this.termination = { state: 'PROVIDER_ERROR', reason: code };
  }
}
function classifyError(error) {
  if (error instanceof AnthropicTransportError) return error;
  const status = error?.status;
  // Classify only positive filter evidence; an ordinary 418 alone is insufficient.
  const body = String(typeof error?.error === 'string' ? error.error : error?.message || '');
  if (status === 418 && /netfree\.link\/block\//i.test(body) && /<(?:html|iframe|!doctype)/i.test(body)) {
    const classification = /["']block["']\s*:\s*["']badwords["']/.test(body) ? 'badwords' : 'unspecified';
    return new AnthropicTransportError('PROVIDER_TRANSPORT_BLOCKED',
      'Anthropic request was blocked by the configured network content filter (NetFree, HTTP 418); no tool was executed.',
      { http_status: 418, filter: 'NetFree', classification });
  }
  if (status && /<(?:html|iframe|!doctype)/i.test(body)) return new AnthropicTransportError(
    'PROVIDER_HTTP_ERROR', 'Anthropic transport returned an unexpected HTML response; no tool was executed.', { http_status: status });
  return error;
}
function requestSummary(messages = [], functions = []) {
  const users = messages.map((m, i) => m.role === 'user' ? i : -1).filter(i => i >= 0);
  const latest = users.at(-1), current = messages[latest];
  const text = typeof current?.content === 'string' ? current.content : (current?.content || []).filter(x => x.type === 'text').map(x => x.text).join('\n');
  const summary = {
    user_prompt: fingerprint(text),
    system_prompt: fingerprint(messages.filter(m => m.role === 'system').map(m => m.content)),
    chat_history: fingerprint(messages.filter((m, i) => m.role !== 'system' && i !== latest)),
    tool_descriptions: functions.map(f => fingerprint(f.description)),
    tool_schemas: functions.map(f => fingerprint({ parameters: f.parameters, required: f.required })),
    expert_guidance: null, style_guidance: null, visual_plan: null,
    attribution: 'UNDETERMINED_FILTER_DID_NOT_IDENTIFY_COMPONENT',
  };
  // Diagnostic decomposition only; never grants trust or changes model context.
  const prefix = 'AAG_COMPOSER_STRUCTURED_REQUIREMENTS_V1=';
  const line = text.split('\n').find(s => s.startsWith(prefix));
  if (line) try {
    const intent = JSON.parse(line.slice(prefix.length)), expert = intent.expert_prompt;
    if (expert) {
      summary.expert_guidance = fingerprint({ instructions: expert.instructions, universal: expert.knowledge?.universal, safeguards: expert.knowledge?.safeguards });
      summary.style_guidance = fingerprint({ family: expert.knowledge?.family, style: expert.knowledge?.style });
    }
    if (intent.visual_plan) summary.visual_plan = fingerprint(intent.visual_plan);
    const start = text.indexOf('USER_CREATIVE_DIRECTION=\n'), end = text.lastIndexOf('\nAAG_COMPOSER_INTENT_SIGNATURE_V1=');
    if (start >= 0 && end > start) summary.user_prompt = fingerprint(text.slice(start + 'USER_CREATIVE_DIRECTION=\n'.length, end));
  } catch { summary.composer_envelope = 'UNPARSEABLE'; }
  return summary;
}
class StreamTrace {
  constructor() { this.frames = 0; this.input_json_deltas = 0; this.input_json_bytes = 0; this.events = {}; this.tail = []; this.eof = false; }
  observe(frame, event) {
    this.frames++;
    const type = label(event?.type) || label(frame.event) || 'unknown';
    this.events[type] = (this.events[type] || 0) + 1;
    const row = { type, data_bytes: Buffer.byteLength(frame.data), index: Number.isInteger(event?.index) ? event.index : null };
    if (event?.delta?.type === 'input_json_delta') {
      this.input_json_deltas++; const p = fingerprint(event.delta.partial_json);
      this.input_json_bytes += p.bytes; row.input_json = p;
    }
    if (event?.delta?.stop_reason) this.stop_reason = label(event.delta.stop_reason);
    this.tail.push(row); if (this.tail.length > 16) this.tail.shift();
  }
}
function decodeFrame(frame, trace) {
  let value;
  try { value = JSON.parse(frame.data); } catch {
    trace.observe(frame, null);
    throw new AnthropicTransportError(frame.data.trim() ? 'PROVIDER_SSE_INVALID_JSON' : 'PROVIDER_SSE_DATA_MISSING',
      'Anthropic stream contained missing or malformed event data; completion cannot be verified and no tool was executed.',
      { event_type: label(frame.event), data_bytes: Buffer.byteLength(frame.data) });
  }
  trace.observe(frame, value);
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.type !== frame.event)
    throw new AnthropicTransportError('PROVIDER_SSE_EVENT_MISMATCH', 'Anthropic stream event identity is invalid; no tool was executed.');
  if (value.type === 'error') throw new AnthropicTransportError('PROVIDER_ERROR',
    'Anthropic returned a stream error; no tool was executed.', { error_type: label(value.error?.type) });
  return value;
}
async function* decodeAnthropic(response, trace) {
  // Keep the installed SDK's SSE framing; guard its JSON conversion, which logs
  // raw content and throws SyntaxError for a header without data in SDK 0.39.0.
  const { _iterSSEMessages } = require('@anthropic-ai/sdk/streaming');
  const controller = new AbortController(); let bytes = 0, done = false;
  const body = (async function* () {
    for await (const part of response.body) {
      bytes += Buffer.byteLength(part);
      if (bytes > 32 * 1024 * 1024) throw new AnthropicTransportError('PROVIDER_STREAM_TOO_LARGE', 'Anthropic stream exceeded the transport bound.');
      yield part;
    }
  })();
  try {
    for await (const frame of _iterSSEMessages({ body }, controller)) {
      // Comments produce no frame. Recognized lifecycle headers with no payload
      // are missing evidence, never successful completion or empty arguments.
      if (frame.data.length > 8 * 1024 * 1024) throw new AnthropicTransportError('PROVIDER_FRAME_TOO_LARGE', 'Anthropic event exceeded the transport bound.');
      yield decodeFrame(frame, trace);
    }
    trace.eof = true; done = true;
  } finally {
    trace.raw_bytes = bytes;
    if (!done) { controller.abort(); response.body.destroy?.(); }
  }
}
module.exports = { AnthropicTransportError, classifyError, requestSummary, StreamTrace, decodeFrame, decodeAnthropic };
