"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("crypto");
const zlib = require("zlib");
const { assemblePdf, parsePng, verifyPdf } = require("../integrations/anythingllm/aagPdfAssembler");

function chunk(type, data) {
  return Buffer.concat([Buffer.from([data.length >>> 24, data.length >>> 16, data.length >>> 8, data.length]), Buffer.from(type, "ascii"), data, Buffer.alloc(4)]);
}

function png(width, height, rgba) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 6;
  const scanlines = [];
  for (let row = 0; row < height; row += 1) {
    const line = Buffer.alloc(1 + width * 4);
    for (let column = 0; column < width; column += 1) Buffer.from(rgba).copy(line, 1 + column * 4);
    scanlines.push(line);
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(Buffer.concat(scanlines))),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function source(width, height, rgba) {
  const bytes = png(width, height, rgba);
  return { bytes, width, height, sha256: crypto.createHash("sha256").update(bytes).digest("hex") };
}

test("deterministic local PDF assembly preserves orientation, exact page count, and ordered provenance", () => {
  const portrait = source(2, 5, [255, 0, 0, 255]);
  const landscape = source(7, 3, [0, 80, 255, 180]);
  const first = assemblePdf([portrait, landscape], { sourceHashes: [portrait.sha256, landscape.sha256] });
  const second = assemblePdf([portrait, landscape], { sourceHashes: [portrait.sha256, landscape.sha256] });
  assert.deepEqual(first, second);
  assert.deepEqual(verifyPdf(first, 2), { pageCount: 2 });
  const text = first.toString("latin1");
  assert.match(text, /\/MediaBox \[0 0 2 5\]/);
  assert.match(text, /\/MediaBox \[0 0 7 3\]/);
  assert.ok(text.indexOf(portrait.sha256) < text.indexOf(landscape.sha256));
  assert.match(text, /\/SMask/);
});

test("single-image PDF is one page and source validation fails closed", () => {
  const image = source(4, 4, [20, 30, 40, 255]);
  const pdf = assemblePdf([image], { sourceHashes: [image.sha256] });
  assert.equal(verifyPdf(pdf, 1).pageCount, 1);
  assert.deepEqual({ width: parsePng(image.bytes).width, height: parsePng(image.bytes).height }, { width: 4, height: 4 });
  assert.throws(() => parsePng(Buffer.alloc(64)), /bounded PNG/);
  assert.throws(() => assemblePdf([]), /requires 1\.\.10/);
  assert.throws(() => assemblePdf([{ ...image, width: 5 }]), /persisted provenance/);
});
