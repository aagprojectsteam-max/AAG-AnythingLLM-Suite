// Structured image outputs are message content even when the accompanying text
// is deliberately empty. Use the durable chat identity to hydrate a streamed
// reply in place; retransmission must not append another response or card.
const ARTIFACT_PRESENTED_EVENT = "aag-artifact-presented";
function hasImageOutputs(message) {
  return Array.isArray(message?.outputs) && message.outputs.some(
    o => o?.type === "imageGenerationCard" && typeof o.payload?.storageFilename === "string" && o.payload.storageFilename
  );
}
function hasMessageContent(message) {
  return Boolean(message?.content || hasImageOutputs(message));
}
function mergeImageMessage(history, event, fallbackUuid) {
  if (!hasImageOutputs(event)) return history;
  const ids = new Set(event.outputs.filter(o => o?.type === "imageGenerationCard")
    .map(o => o.payload?.artifactId || o.payload?.storageFilename));
  const matches = m => m.role === "assistant" && (
    (event.chatId != null && m.chatId != null && String(m.chatId) === String(event.chatId) && m.type !== "statusResponse") ||
    (event.uuid && m.uuid === event.uuid) ||
    m.outputs?.some(o => o?.type === "imageGenerationCard" && ids.has(o.payload?.artifactId || o.payload?.storageFilename))
  );
  const existing = history.find(matches);
  const outputs = [], seen = new Map();
  for (const output of [...(existing?.outputs || []), ...event.outputs]) {
    const key = output.type === "imageGenerationCard"
      ? `image:${output.payload?.artifactId || output.payload?.storageFilename}` : JSON.stringify(output);
    if (seen.has(key)) outputs[seen.get(key)] = output;
    else { seen.set(key, outputs.length); outputs.push(output); }
  }
  const message = {
    ...existing, uuid: existing?.uuid || event.uuid || fallbackUuid,
    type: "textResponse", content: event.content || "", outputs,
    chatId: event.chatId ?? existing?.chatId ?? null, role: "assistant",
    sources: existing?.sources || [], closed: true, error: null,
    animate: false, pending: false, metrics: existing?.metrics || {},
  };
  let placed = false;
  const next = [];
  for (const previous of history) {
    if (matches(previous)) { if (!placed) next.push(message); placed = true; }
    else if (hasMessageContent(previous)) next.push(previous);
  }
  if (!placed) next.push(message);
  return next;
}
function presentationProgress(progress, history, presented) {
  if (!progress || !["COMPLETED", "PARTIAL"].includes(progress.status)) return progress;
  const images = history.flatMap(m => m.outputs || []).filter(o =>
    o?.type === "imageGenerationCard" && o.payload?.jobId === progress.jobId);
  const ready = images.length > 0 && images.every(o => presented.has(o.payload.artifactId));
  if (ready) return progress;
  return {...progress, stages: (progress.stages || []).map(stage =>
    stage.key === "complete" ? {...stage, state: "pending"} :
    stage.key === "returningToChat" ? {...stage, state: "current"} : stage)};
}
module.exports = { ARTIFACT_PRESENTED_EVENT, hasImageOutputs, hasMessageContent, mergeImageMessage, presentationProgress };
