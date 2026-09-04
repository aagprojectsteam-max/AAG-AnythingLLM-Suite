"use strict";
const { createTask, AagError } = require("./runtime");
const { redact } = require("./errors");
const fs = require("fs");

function preserveShadowFailure(error, classified) {
  const target = process.env.AAG_IMAGE_PROVIDER_FAILURE_EVIDENCE;
  if (!target) return;
  const evidence = {
    schema_version: "aag.image-provider.failure-evidence.v1",
    at: new Date().toISOString(),
    exception_type: String(error?.name || error?.constructor?.name || "Error").slice(0, 120),
    message: redact(error?.message || error),
    stack: redact(error?.stack || ""),
    classification: classified.code,
    retryable: Boolean(classified.retryable),
  };
  try { fs.writeFileSync(target, JSON.stringify(evidence, null, 2) + "\n", { mode: 0o600, flag: "wx" }); } catch {}
}

module.exports.runtime = {
  handler: async function(args) {
    try { return await createTask(args, this.runtimeArgs || {}, { logger: this.logger, introspect: this.introspect, runtimeArgs: this.runtimeArgs || {} }); }
    catch (e) {
      const err = e instanceof AagError ? e : new AagError("INTERNAL_ERROR", "Unable to create image task.");
      preserveShadowFailure(e, err);
      return `AAG_IMAGE_RESULT\nstatus=failed\njob_id=\nerror_code=${err.code}\nmessage=${err.message}\nretryable=${Boolean(err.retryable)}\nsame_turn_retry=forbidden\nartifact_count=0`;
    }
  }
};
