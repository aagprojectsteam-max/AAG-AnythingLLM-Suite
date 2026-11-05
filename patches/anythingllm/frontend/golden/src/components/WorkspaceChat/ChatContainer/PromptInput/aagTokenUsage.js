// Display-only estimates. Never used to reject, trim, route or send a message.
export function finiteTokens(value, allowZero = true) {
  return typeof value === "number" && Number.isSafeInteger(value) &&
    (allowZero ? value >= 0 : value > 0) ? value : null;
}
export function estimateText(value) {
  if (typeof value !== "string" || !value) return 0;
  // Bounded, language-neutral approximation; no image/base64 tokenization.
  const clean = value.replace(/data:[^\s"']+;base64,[A-Za-z0-9+/=]+/g, "");
  let units = 0;
  for (const char of clean) units += char.codePointAt(0) < 128 ? 0.25 : 0.75;
  return Math.ceil(units);
}
export function contentText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.filter(x => x?.type === "text" && typeof x.text === "string")
    .map(x => x.text).join("\n");
}
export function usageView({ history = [], prompt = "", systemPrompt = "", metadata = null,
  streaming = false, agent = false, hasAttachments = false }) {
  const rows = Array.isArray(history) ? history : [];
  const statusTypes = new Set(["statusResponse", "modelRouteNotification", "scheduledJobCreated",
    "toolApprovalRequest", "awaitingFeedback", "wssFailure", "abort"]);
  const conversation = rows.filter(x => x?.role === "user" ||
    (x?.role === "assistant" && !statusTypes.has(x.type)));
  const last = [...conversation].reverse().find(x => x.role === "assistant");
  const limit = finiteTokens(metadata?.contextLimit, false);
  const metrics = last?.metrics || {};
  const configuredReply = !agent && !last?.aagAgentUsage &&
    !last?.outputs?.length && (streaming || metrics.model === metadata?.model) ?
    finiteTokens(metadata?.outputLimit, false) : null;
  // Agent totals can include several requests; never treat them as one context window.
  const comparable = !agent && !last?.aagAgentUsage && !last?.outputs?.length &&
    typeof metrics.model === "string" && metrics.model === metadata?.model;
  const input = comparable ? finiteTokens(metrics.prompt_tokens, false) : null;
  const reportedOutput = finiteTokens(metrics.completion_tokens);
  const lastUserAt = conversation.map(x => x.role).lastIndexOf("user");
  const lastAssistantAt = last ? conversation.indexOf(last) : -1;
  const awaiting = streaming && lastAssistantAt < lastUserAt;
  const output = awaiting ? null : (streaming ? null : reportedOutput) ??
    (last && contentText(last.content) ? estimateText(contentText(last.content)) : null);
  const historyLimit = Number.isSafeInteger(metadata?.historyMessages) ?
    Math.max(0, metadata.historyMessages) : 20;
  const visible = historyLimit ? conversation.slice(-historyLimit) : [];
  const visibleEstimate = visible.reduce((n, x) => n + estimateText(contentText(x.content)) + 4, 0);
  const fromPrior = input !== null && !streaming && lastAssistantAt > lastUserAt;
  const used = fromPrior ? input + (reportedOutput ?? estimateText(contentText(last?.content))) +
    estimateText(prompt) + 4 :
    estimateText(systemPrompt) + visibleEstimate + estimateText(prompt) + 8;
  return {
    context: { used, limit, remaining: limit === null ? null : Math.max(0, limit - used),
      percent: limit === null ? null : Math.min(100, Math.max(0, used / limit * 100)),
      over: limit !== null && used > limit, basis: fromPrior ? "last-request" : "visible-text" },
    output: { used: output, limit: configuredReply, remaining: output === null ||
      configuredReply === null ? null : Math.max(0, configuredReply - output),
      estimated: streaming || reportedOutput === null, active: streaming },
    lastInput: finiteTokens(metrics.prompt_tokens),
    multimodal: hasAttachments || visible.some(x => x.attachments?.length || x.outputs?.length),
    quota: metadata?.quota || { status: "unavailable" },
  };
}
