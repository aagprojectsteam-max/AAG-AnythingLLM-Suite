"use strict";
const { createBatch, resultEnvelope } = require("./batch");
const { failureEnvelope, stateRoot } = require("./runtime");

module.exports.runtime = {
  handler: async function(args) {
    try {
      const runtime = this.runtimeArgs || {};
      const result = await createBatch(args, runtime, { logger: this.logger, introspect: this.introspect, runtimeArgs: runtime });
      if (result.error) return failureEnvelope(result.error);
      return resultEnvelope(stateRoot(runtime), result.job);
    } catch (error) {
      return failureEnvelope(error);
    }
  }
};
