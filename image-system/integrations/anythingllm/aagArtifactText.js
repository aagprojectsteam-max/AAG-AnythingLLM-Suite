"use strict";

// The storage basename is persisted by the artifact publisher, not inferred from
// a URL, prompt, clock or UI position. Legacy physical names remain valid.
function artifactFilename(artifact = {}, file = null) {
  const basename = value => typeof value === "string" && value.length <= 255 &&
    !/[\\/\x00-\x1f\x7f]/.test(value) && /\.(?:png|jpe?g|webp)$/i.test(value) &&
    value !== "." && value !== ".." ? value : null;
  const resolved = file?.state === "AVAILABLE" && file.filename === artifact.storageFilename
    ? basename(file.filename) : null;
  return resolved || basename(artifact.storageFilename) ||
    (typeof artifact.applicationPath === "string" && /^generated-images\/[^/]+$/.test(artifact.applicationPath)
      ? basename(artifact.applicationPath.slice("generated-images/".length)) : null) ||
    basename(artifact.filename) || null;
}

// Presentation only: keep the original response/outputs intact for history,
// diagnostics, and retry policy, including failures that have no image card.
function displayArtifactText(content, outputs = [], role = "assistant", translate = (_key, english) => english) {
  const original = String(content || "");
  if (role === "user") return original;
  const hasImage = outputs.some(output => output?.type === "imageGenerationCard");
  const field = /^[a-z][a-z0-9_]*=[^\r\n]*$/i;
  const lines = original.split(/\r?\n/);
  const result = [];
  let found = false;
  for (let i = 0; i < lines.length; i += 1) {
    const header = lines[i].trim();
    if (!/^AAG_IMAGE_RESULT(?:\s+[a-z][a-z0-9_]*=.*)?$/i.test(header)) { result.push(lines[i]); continue; }
    found = true;
    const fenced = /^```[\w-]*\s*$/.test(result.at(-1)?.trim() || "");
    if (fenced) result.pop();
    const block = [header];
    while (i + 1 < lines.length) {
      const next = lines[i + 1].trim();
      if (fenced && next === "```") { i += 1; break; }
      if (!fenced && !field.test(next) && !/^at\s+/.test(next)) break;
      block.push(lines[++i]);
    }
    const raw = block.join("\n");
    const status = /\bstatus=([a-z_-]+)/i.exec(raw)?.[1]?.toLowerCase();
    const code = /\berror_code=([A-Z0-9_]+)/.exec(raw)?.[1] || "";
    let key = "imageResultUnavailable", message = "Image generation status is unavailable.";
    if (status === "completed") {
      if (hasImage) continue;
      key = "imageResultCompleted"; message = "Image generation completed.";
    } else if (status === "failed" || status === "error") {
      key = "imageResultFailed"; message = "The image could not be generated.";
      if (code.startsWith("EXPERT_")) {
        key = "imageResultExpertFailed"; message = "The image prompt could not be validated. Review your request and the selected style.";
      } else if (/VALIDATION|INVALID_|TAXONOMY|STYLE_ID|COMPOSER_|INTENT_|CONFLICT|PROMPT_|PUBLIC_SCHEMA_/.test(code)) {
        key = "imageResultValidationFailed"; message = "The image request could not be validated. Check the selected settings and your request.";
      } else if (/^(ENGINE_|OUTPUT_)/.test(code)) {
        key = "imageResultEngineFailed"; message = "Image generation could not finish. Please try again later.";
      }
    } else if (status === "partial") {
      key = "imageResultPartial"; message = "Some images could not be generated.";
    } else if (status === "cancelled" || status === "canceled") {
      key = "imageResultCancelled"; message = "Image generation was cancelled.";
    } else if (!status || /^(queued|pending|running|processing|submitted|accepted)$/.test(status)) {
      key = "imageResultPending"; message = "Image generation is in progress.";
    }
    // Never display machine message/error text: it can contain private IDs,
    // invariant names or stack traces. Only fixed, localized copy reaches chat.
    result.push(translate(key, message));
  }
  let text = found ? result.join("\n").replace(/\n{3,}/g, "\n\n").trim() : original;
  if (hasImage) {
    const filenames = new Set(outputs.filter(o => o?.type === "imageGenerationCard")
      .map(o => artifactFilename(o.payload)).filter(Boolean));
    // Each card owns its filename label, including batches and restored cards.
    // Preserve other prose and the human-readable partial/failure messages.
    text = text.split(/\r?\n/).filter(line =>
      line.trim() !== "Image generated successfully." && !filenames.has(line.trim())
    ).join("\n").trim();
  }
  return text;
}

module.exports = { displayArtifactText, artifactFilename };
