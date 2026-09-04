"use strict";
const { jobAction, AagError } = require("./runtime");
module.exports.runtime = {
  handler: async function(args) {
    try { return await jobAction(args, this.runtimeArgs || {}, { logger: this.logger, introspect: this.introspect, runtimeArgs: this.runtimeArgs || {} }); }
    catch (e) { const err = e instanceof AagError ? e : new AagError("INTERNAL_ERROR", "Unable to inspect image job."); return `AAG_IMAGE_RESULT\nstatus=failed\nerror_code=${err.code}\nmessage=${err.message}\nretryable=${Boolean(err.retryable)}\nsame_turn_retry=forbidden\nartifact_count=0`; }
  }
};
