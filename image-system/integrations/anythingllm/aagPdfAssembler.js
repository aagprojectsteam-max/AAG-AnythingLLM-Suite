"use strict";

const zlib = require("zlib");

const CONTRACT_ID = "aag.artifact-export.pdf.v1";
const MAX_PAGES = 10;
const MAX_SOURCE_BYTES = 64 * 1024 * 1024;
const MAX_DECODED_BYTES_PER_IMAGE = 96 * 1024 * 1024;
const MAX_TOTAL_DECODED_BYTES = 512 * 1024 * 1024;
const MAX_PDF_BYTES = 512 * 1024 * 1024;
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

function exportError(code, message) {
  const error = new Error(message);
  error.code = code;
  return error;
}

function paeth(left, above, upperLeft) {
  const estimate = left + above - upperLeft;
  const leftDistance = Math.abs(estimate - left);
  const aboveDistance = Math.abs(estimate - above);
  const upperLeftDistance = Math.abs(estimate - upperLeft);
  if (leftDistance <= aboveDistance && leftDistance <= upperLeftDistance) return left;
  return aboveDistance <= upperLeftDistance ? above : upperLeft;
}

function parsePng(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 33 || bytes.length > MAX_SOURCE_BYTES || !bytes.subarray(0, 8).equals(PNG_SIGNATURE)) {
    throw exportError("EXPORT_SOURCE_INVALID", "The trusted export source is not a bounded PNG image.");
  }
  let offset = 8;
  let header = null;
  let palette = null;
  let transparency = null;
  const compressed = [];
  while (offset + 12 <= bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const type = bytes.toString("ascii", offset + 4, offset + 8);
    const start = offset + 8;
    const end = start + length;
    if (end + 4 > bytes.length) throw exportError("EXPORT_SOURCE_INVALID", "The PNG chunk structure is invalid.");
    const data = bytes.subarray(start, end);
    if (type === "IHDR") {
      if (length !== 13 || header) throw exportError("EXPORT_SOURCE_INVALID", "The PNG header is invalid.");
      header = {
        width: data.readUInt32BE(0),
        height: data.readUInt32BE(4),
        bitDepth: data[8],
        colorType: data[9],
        compression: data[10],
        filter: data[11],
        interlace: data[12],
      };
    } else if (type === "PLTE") palette = Buffer.from(data);
    else if (type === "tRNS") transparency = Buffer.from(data);
    else if (type === "IDAT") compressed.push(Buffer.from(data));
    else if (type === "IEND") break;
    offset = end + 4;
  }
  if (!header || !compressed.length || header.width < 1 || header.height < 1 || header.width > 16384 || header.height > 16384) {
    throw exportError("EXPORT_SOURCE_INVALID", "The PNG lacks a valid bounded image payload.");
  }
  if (header.bitDepth !== 8 || header.compression !== 0 || header.filter !== 0 || header.interlace !== 0 || ![0, 2, 3, 4, 6].includes(header.colorType)) {
    throw exportError("EXPORT_SOURCE_UNSUPPORTED", "The trusted PNG encoding is not supported by the deterministic PDF assembler.");
  }
  const channels = ({ 0: 1, 2: 3, 3: 1, 4: 2, 6: 4 })[header.colorType];
  const rowBytes = header.width * channels;
  const expected = (rowBytes + 1) * header.height;
  if (expected > MAX_DECODED_BYTES_PER_IMAGE) throw exportError("EXPORT_MEMORY_BOUND", "The decoded image exceeds the export memory bound.");
  let filtered;
  try { filtered = zlib.inflateSync(Buffer.concat(compressed), { maxOutputLength: expected }); }
  catch { throw exportError("EXPORT_SOURCE_INVALID", "The PNG image data could not be decoded safely."); }
  if (filtered.length !== expected) throw exportError("EXPORT_SOURCE_INVALID", "The PNG decoded length is invalid.");
  const raw = Buffer.allocUnsafe(rowBytes * header.height);
  for (let row = 0; row < header.height; row += 1) {
    const filterType = filtered[row * (rowBytes + 1)];
    if (filterType > 4) throw exportError("EXPORT_SOURCE_INVALID", "The PNG uses an invalid scanline filter.");
    const source = filtered.subarray(row * (rowBytes + 1) + 1, (row + 1) * (rowBytes + 1));
    const targetOffset = row * rowBytes;
    for (let column = 0; column < rowBytes; column += 1) {
      const left = column >= channels ? raw[targetOffset + column - channels] : 0;
      const above = row > 0 ? raw[targetOffset + column - rowBytes] : 0;
      const upperLeft = row > 0 && column >= channels ? raw[targetOffset + column - rowBytes - channels] : 0;
      let reconstructed = source[column];
      if (filterType === 1) reconstructed += left;
      else if (filterType === 2) reconstructed += above;
      else if (filterType === 3) reconstructed += Math.floor((left + above) / 2);
      else if (filterType === 4) reconstructed += paeth(left, above, upperLeft);
      raw[targetOffset + column] = reconstructed & 0xff;
    }
  }

  const pixels = header.width * header.height;
  let colorSpace = "DeviceRGB";
  let colors;
  let alpha = null;
  if (header.colorType === 0) {
    colorSpace = "DeviceGray";
    colors = raw;
    if (transparency?.length >= 2) {
      const transparent = transparency.readUInt16BE(0) & 0xff;
      alpha = Buffer.allocUnsafe(pixels);
      for (let index = 0; index < pixels; index += 1) alpha[index] = raw[index] === transparent ? 0 : 255;
    }
  } else if (header.colorType === 2) {
    colors = raw;
    if (transparency?.length >= 6) {
      const transparent = [transparency.readUInt16BE(0) & 0xff, transparency.readUInt16BE(2) & 0xff, transparency.readUInt16BE(4) & 0xff];
      alpha = Buffer.allocUnsafe(pixels);
      for (let index = 0; index < pixels; index += 1) {
        const offset = index * 3;
        alpha[index] = raw[offset] === transparent[0] && raw[offset + 1] === transparent[1] && raw[offset + 2] === transparent[2] ? 0 : 255;
      }
    }
  } else if (header.colorType === 3) {
    if (!palette || palette.length < 3 || palette.length % 3 !== 0) throw exportError("EXPORT_SOURCE_INVALID", "The indexed PNG palette is invalid.");
    colors = Buffer.allocUnsafe(pixels * 3);
    alpha = Buffer.allocUnsafe(pixels);
    let opaque = true;
    for (let index = 0; index < pixels; index += 1) {
      const paletteIndex = raw[index];
      const paletteOffset = paletteIndex * 3;
      if (paletteOffset + 2 >= palette.length) throw exportError("EXPORT_SOURCE_INVALID", "The PNG palette index is invalid.");
      palette.copy(colors, index * 3, paletteOffset, paletteOffset + 3);
      alpha[index] = transparency && paletteIndex < transparency.length ? transparency[paletteIndex] : 255;
      if (alpha[index] !== 255) opaque = false;
    }
    if (opaque) alpha = null;
  } else if (header.colorType === 4) {
    colorSpace = "DeviceGray";
    colors = Buffer.allocUnsafe(pixels);
    alpha = Buffer.allocUnsafe(pixels);
    for (let index = 0; index < pixels; index += 1) {
      colors[index] = raw[index * 2];
      alpha[index] = raw[index * 2 + 1];
    }
  } else {
    colors = Buffer.allocUnsafe(pixels * 3);
    alpha = Buffer.allocUnsafe(pixels);
    for (let index = 0; index < pixels; index += 1) {
      const sourceOffset = index * 4;
      raw.copy(colors, index * 3, sourceOffset, sourceOffset + 3);
      alpha[index] = raw[sourceOffset + 3];
    }
  }
  return { width: header.width, height: header.height, colorSpace, colors, alpha, decodedBytes: raw.length + colors.length + (alpha?.length || 0) };
}

function streamObject(dictionary, data) {
  return Buffer.concat([
    Buffer.from(`${dictionary.replace(/>>\s*$/, "")} /Length ${data.length} >>\nstream\n`, "ascii"),
    data,
    Buffer.from("\nendstream", "ascii"),
  ]);
}

function pdfString(value) {
  return String(value || "").replace(/[^\x20-\x7e]/g, "?").replace(/([\\()])/g, "\\$1").slice(0, 4000);
}

function assemblePdf(images, options = {}) {
  if (!Array.isArray(images) || images.length < 1 || images.length > MAX_PAGES) throw exportError("EXPORT_COUNT_INVALID", `PDF export requires 1..${MAX_PAGES} ordered trusted images.`);
  const decoded = [];
  let decodedTotal = 0;
  for (const image of images) {
    const parsed = parsePng(image.bytes);
    if (image.width && parsed.width !== image.width) throw exportError("EXPORT_PROVENANCE_MISMATCH", "A source image width differs from persisted provenance.");
    if (image.height && parsed.height !== image.height) throw exportError("EXPORT_PROVENANCE_MISMATCH", "A source image height differs from persisted provenance.");
    decodedTotal += parsed.decodedBytes;
    if (decodedTotal > MAX_TOTAL_DECODED_BYTES) throw exportError("EXPORT_MEMORY_BOUND", "The ordered export set exceeds the total decoded-memory bound.");
    decoded.push(parsed);
  }

  const pageDefinitions = [];
  let nextObject = 3;
  for (const image of decoded) {
    const definition = { page: nextObject++, content: nextObject++, image: nextObject++, mask: image.alpha ? nextObject++ : null };
    pageDefinitions.push(definition);
  }
  const infoObject = nextObject++;
  const objects = new Map();
  objects.set(1, Buffer.from("<< /Type /Catalog /Pages 2 0 R >>", "ascii"));
  objects.set(2, Buffer.from(`<< /Type /Pages /Count ${decoded.length} /Kids [${pageDefinitions.map((entry) => `${entry.page} 0 R`).join(" ")}] >>`, "ascii"));
  decoded.forEach((image, index) => {
    const refs = pageDefinitions[index];
    const scale = Math.min(1, 14400 / Math.max(image.width, image.height));
    const pageWidth = Number((image.width * scale).toFixed(4));
    const pageHeight = Number((image.height * scale).toFixed(4));
    const resources = `<< /XObject << /Im0 ${refs.image} 0 R >> >>`;
    objects.set(refs.page, Buffer.from(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidth} ${pageHeight}] /Resources ${resources} /Contents ${refs.content} 0 R >>`, "ascii"));
    const content = Buffer.from(`q\n${pageWidth} 0 0 ${pageHeight} 0 0 cm\n/Im0 Do\nQ\n`, "ascii");
    objects.set(refs.content, streamObject("<< >>", content));
    const compressedColor = zlib.deflateSync(image.colors, { level: 9 });
    const maskReference = refs.mask ? ` /SMask ${refs.mask} 0 R` : "";
    objects.set(refs.image, streamObject(`<< /Type /XObject /Subtype /Image /Width ${image.width} /Height ${image.height} /ColorSpace /${image.colorSpace} /BitsPerComponent 8 /Filter /FlateDecode${maskReference} >>`, compressedColor));
    if (refs.mask) {
      const compressedAlpha = zlib.deflateSync(image.alpha, { level: 9 });
      objects.set(refs.mask, streamObject(`<< /Type /XObject /Subtype /Image /Width ${image.width} /Height ${image.height} /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode >>`, compressedAlpha));
    }
  });
  const provenance = (options.sourceHashes || []).join(",");
  objects.set(infoObject, Buffer.from(`<< /Producer (AAG Artifact Export) /Creator (${CONTRACT_ID}) /Title (On-demand artifact export) /Subject (${pdfString(provenance)}) >>`, "ascii"));

  const chunks = [Buffer.from("%PDF-1.7\n%\xE2\xE3\xCF\xD3\n", "binary")];
  const offsets = [0];
  let position = chunks[0].length;
  for (let objectNumber = 1; objectNumber < nextObject; objectNumber += 1) {
    const body = objects.get(objectNumber);
    if (!body) throw exportError("EXPORT_INTERNAL", "The deterministic PDF object graph is incomplete.");
    offsets[objectNumber] = position;
    const object = Buffer.concat([Buffer.from(`${objectNumber} 0 obj\n`, "ascii"), body, Buffer.from("\nendobj\n", "ascii")]);
    chunks.push(object);
    position += object.length;
  }
  const xrefOffset = position;
  const xref = ["xref", `0 ${nextObject}`, "0000000000 65535 f "];
  for (let objectNumber = 1; objectNumber < nextObject; objectNumber += 1) xref.push(`${String(offsets[objectNumber]).padStart(10, "0")} 00000 n `);
  xref.push(`trailer\n<< /Size ${nextObject} /Root 1 0 R /Info ${infoObject} 0 R >>`, `startxref\n${xrefOffset}`, "%%EOF", "");
  chunks.push(Buffer.from(xref.join("\n"), "ascii"));
  const output = Buffer.concat(chunks);
  if (output.length > MAX_PDF_BYTES) throw exportError("EXPORT_MEMORY_BOUND", "The assembled PDF exceeds the output size bound.");
  verifyPdf(output, decoded.length);
  return output;
}

function verifyPdf(bytes, expectedPages) {
  if (!Buffer.isBuffer(bytes) || !bytes.subarray(0, 8).equals(Buffer.from("%PDF-1.7")) || !bytes.subarray(-16).toString("ascii").includes("%%EOF")) {
    throw exportError("EXPORT_PDF_INVALID", "The assembled PDF envelope is invalid.");
  }
  const text = bytes.toString("latin1");
  const pages = (text.match(/\/Type \/Page(?:\s|\/)/g) || []).length;
  const count = Number(text.match(/\/Type \/Pages \/Count ([0-9]+)/)?.[1]);
  if (pages !== expectedPages || count !== expectedPages) throw exportError("EXPORT_PAGE_COUNT_MISMATCH", "The assembled PDF page count is not exact.");
  return { pageCount: pages };
}

module.exports = {
  CONTRACT_ID,
  MAX_PAGES,
  MAX_SOURCE_BYTES,
  parsePng,
  assemblePdf,
  verifyPdf,
};
