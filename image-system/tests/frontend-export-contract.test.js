"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "../integrations/anythingllm/frontend");

test("frontend export is explicit-gesture lazy, Save-As-first, and has safe browser fallback", () => {
  const source = fs.readFileSync(path.join(root, "aagArtifactExport.js"), "utf8");
  const picker = source.indexOf("window.showSaveFilePicker");
  const request = source.indexOf("requestPdf(storageFilenames, mode)", picker);
  assert.ok(picker >= 0 && request > picker, "native Save As must be requested before the server PDF call");
  assert.match(source, /suggestedName/);
  assert.match(source, /ANYTHING-\$\{stamp\}\.pdf/);
  assert.match(source, /saveAs\(blob, suggestedName\)/);
  assert.match(source, /AbortError/);
  assert.match(source, /X-AAG-PDF-Pages/);
  assert.doesNotMatch(source, /filesystem|output_path|directory|host path/i);
});

test("presentation exposes per-image Download/PDF and complete-only collection export", () => {
  const image = fs.readFileSync(path.join(root, "ImageGenerationCard/index.jsx"), "utf8");
  const collection = fs.readFileSync(path.join(root, "AagImageCollection.jsx"), "utf8");
  const history = fs.readFileSync(path.join(root, "HistoricalOutputs/index.jsx"), "utf8");
  assert.match(image, /> Download/);
  assert.match(image, /\} PDF/);
  assert.match(image, /exportArtifactsAsPdf\(\[storageFilename\], "single"\)/);
  assert.match(collection, /Save all as PDF/);
  assert.match(collection, /disabled=\{!complete \|\| exporting\}/);
  assert.match(collection, /logicalIndex/);
  assert.match(collection, /exportArtifactsAsPdf[\s\S]*"collection"/);
  assert.match(history, /AagImageCollection/);
});
