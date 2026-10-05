"use strict";

// Keeps AnythingLLM's existing Bree schedule and one-hour retention policy.
const { cleanupGeneratedImages } = require("./aagGeneratedImageCleanup");
async function main() {
  const { log, conclude } = require("./helpers/index.js");
  const { parentPort } = require("node:worker_threads");
  const database = require("../utils/prisma");
  const { generatedImagesPath, GENERATED_IMAGE_FILENAME_PATTERN } = require("../utils/files/index.js");
  const emit = message => parentPort || process.send ? log(message) : console.log(message);
  try {
    if (process.argv.slice(2).some(value => value !== "--dry-run")) throw new Error("Only --dry-run is supported");
    const result = await cleanupGeneratedImages({ root: generatedImagesPath, filenamePattern: GENERATED_IMAGE_FILENAME_PATTERN, database, dryRun: process.argv.includes("--dry-run"), log: emit });
    emit("GENERATED_IMAGE_CLEANUP=" + JSON.stringify({ result: result.result, dryRun: result.dryRun, referenced: result.decisions.filter(r => r.state === "REFERENCED").length, unknown: result.decisions.filter(r => r.state === "UNKNOWN").length, deleted: result.deleted.length, sources: result.sources }));
  } catch (error) { emit("Cleanup skipped: " + error.message); }
  finally { await database.$disconnect(); conclude(); }
}
if (require.main === module) main();
module.exports = { main };
