"use strict";

class AagError extends Error {
  constructor(code, message, retryable = false, detail = "") {
    super(message);
    this.name = "AagError";
    this.code = code;
    this.retryable = Boolean(retryable);
    this.detail = redact(detail);
  }
}

function redact(value) {
  return String(value || "")
    .replace(/\b(?:sk|AIza|ghp|github_pat)-?[A-Za-z0-9_\-]{12,}\b/g, "[REDACTED_TOKEN]")
    .replace(/\b(Bearer|token|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+/gi, "$1=[REDACTED]")
    .replace(/(?:\/mnt\/data|\/app\/server|\/home\/[^/\s]+)(?:\/[^\s:;,)'\"]+)+/g, "[INTERNAL_PATH]")
    .replace(/https?:\/\/[^\s?#]+\?[^\s]+/g, "[INTERNAL_URL]")
    .slice(0, 2000);
}

function classifyError(error) {
  if (error instanceof AagError) return error;
  const raw = String(error?.message || error || "Internal failure");
  const detail = redact(raw);
  if (/fetch failed|ECONNREFUSED|ENOTFOUND|network|socket hang up/i.test(raw)) {
    return new AagError("ENGINE_UNAVAILABLE", "The selected local image engine is unavailable.", true, detail);
  }
  if (/timed out|timeout|AbortError/i.test(raw)) {
    return new AagError("ENGINE_TIMEOUT", "The selected local image engine timed out.", true, detail);
  }
  if (/completed.*no image|no published artifact|returned no image/i.test(raw)) {
    return new AagError("OUTPUT_MISSING", "The engine completed without a verifiable image.", false, detail);
  }
  if (/execution failed|engine failed|second child crash|process exited/i.test(raw)) {
    return new AagError("ENGINE_CRASH", "The selected local image engine failed during execution.", true, detail);
  }
  return new AagError("INTERNAL_ERROR", "The image task failed safely.", false, detail);
}

module.exports = { AagError, classifyError, redact };
