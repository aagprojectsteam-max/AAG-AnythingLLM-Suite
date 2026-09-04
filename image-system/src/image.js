"use strict";

const path = require("path");
const { AagError } = require("./errors");
const { sha256 } = require("./util");

const MAX_BYTES = 50 * 1024 * 1024;
const MAX_PIXELS = 40_000_000;
const MAX_OUTPUT_PIXELS = 200_000_000;
const MAX_SIDE = 16_384;
const MIN_BYTES = 128;

function detectedFormat(bytes) {
  if (bytes.length >= 8 && bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) return "png";
  if (bytes.length >= 3 && bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return "jpeg";
  if (bytes.length >= 12 && bytes.subarray(0, 4).toString("ascii") === "RIFF" && bytes.subarray(8, 12).toString("ascii") === "WEBP") return "webp";
  throw new AagError("SOURCE_CORRUPT", "The attachment is not a supported decodable image.");
}

function strictBase64(value) {
  const compact = String(value || "").replace(/\s/g, "");
  // Check the encoded limit before scanning. The former nested full-string
  // RegExp overflowed V8's regexp stack on otherwise valid multi-megabyte
  // authorized references. This linear scan enforces the same alphabet,
  // quartet and terminal-padding contract without recursive backtracking.
  if (compact.length > Math.ceil(MAX_BYTES / 3) * 4 + 4) {
    throw new AagError("SOURCE_TOO_LARGE", "The attachment exceeds the 50 MiB limit.");
  }
  if (!compact || compact.length % 4 !== 0) {
    throw new AagError("SOURCE_CORRUPT", "The attachment has invalid base64 data.");
  }
  const padding = compact.endsWith("==") ? 2 : compact.endsWith("=") ? 1 : 0;
  const dataEnd = compact.length - padding;
  for (let index = 0; index < dataEnd; index++) {
    const code = compact.charCodeAt(index);
    const valid = (code >= 65 && code <= 90) || (code >= 97 && code <= 122) ||
      (code >= 48 && code <= 57) || code === 43 || code === 47;
    if (!valid) throw new AagError("SOURCE_CORRUPT", "The attachment has invalid base64 data.");
  }
  for (let index = dataEnd; index < compact.length; index++) {
    if (compact.charCodeAt(index) !== 61) {
      throw new AagError("SOURCE_CORRUPT", "The attachment has invalid base64 data.");
    }
  }
  const bytes = Buffer.from(compact, "base64");
  if (bytes.length < MIN_BYTES) throw new AagError("SOURCE_CORRUPT", "The attachment is empty or corrupt.");
  if (bytes.length > MAX_BYTES) throw new AagError("SOURCE_TOO_LARGE", "The attachment exceeds the 50 MiB limit.");
  return bytes;
}

function extensionFormat(name) {
  const ext = path.extname(String(name || "")).toLowerCase();
  if (!ext) return null;
  return ({ ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp" })[ext] || "unsupported";
}

function parseAttachment(item) {
  const raw = item?.contentString || item?.content || item?.data || item?.base64 || "";
  const match = String(raw).match(/^data:image\/(jpeg|jpg|png|webp);base64,([A-Za-z0-9+/=\s]+)$/i);
  if (!match) throw new AagError("SOURCE_FORMAT_UNSUPPORTED", "Only JPG, PNG, and WEBP attachments are supported.");
  const declared = match[1].toLowerCase() === "jpg" ? "jpeg" : match[1].toLowerCase();
  const bytes = strictBase64(match[2]);
  const actual = detectedFormat(bytes);
  const declaredMime = String(item?.mime || "").trim().toLowerCase();
  const normalizedMime = declaredMime === "image/jpg" ? "image/jpeg" : declaredMime;
  if (normalizedMime && normalizedMime !== `image/${actual}`) {
    throw new AagError("SOURCE_FORMAT_UNSUPPORTED", "Attachment MIME type and file contents do not agree.");
  }
  const suppliedName = String(item?.name || "");
  if (/[\\/\0-\x1f\x7f]/.test(suppliedName)) throw new AagError("SOURCE_UNAUTHORIZED", "The attachment filename is unsafe.");
  const named = extensionFormat(item?.name);
  if (declared !== actual || (named && named !== actual)) {
    throw new AagError("SOURCE_FORMAT_UNSUPPORTED", "Attachment type, extension, and file contents do not agree.");
  }
  return { bytes, actual, original_name: String(item?.name || `attachment.${actual === "jpeg" ? "jpg" : actual}`).slice(0, 160) };
}

function loadSharp() {
  for (const name of ["sharp", "/app/server/node_modules/sharp"]) {
    try { return require(name); } catch {}
  }
  return null;
}

async function normalizeBytes(parsed, deps = {}) {
  if (deps.normalizeImage) return deps.normalizeImage(parsed);
  const sharp = loadSharp();
  if (!sharp) throw new AagError("SOURCE_NORMALIZATION_FAILED", "The trusted image decoder is unavailable.", true);
  try {
    const input = sharp(parsed.bytes, { failOn: "warning", limitInputPixels: MAX_PIXELS, sequentialRead: true });
    const meta = await input.metadata();
    const width = Number(meta.width || 0), height = Number(meta.height || 0), pages = Number(meta.pages || 1);
    if (!width || !height || width > MAX_SIDE || height > MAX_SIDE || width * height > MAX_PIXELS) {
      throw new AagError("SOURCE_TOO_MANY_PIXELS", "The attachment exceeds the decoded image limit.");
    }
    if (pages !== 1) throw new AagError("SOURCE_FORMAT_UNSUPPORTED", "Animated or multi-frame images are not supported.");
    if (!["jpeg", "png", "webp"].includes(String(meta.format))) throw new AagError("SOURCE_FORMAT_UNSUPPORTED", "The decoded image format is unsupported.");
    const normalized = await input.rotate().flatten({ background: "#ffffff" }).toColourspace("srgb").png({ compressionLevel: 9, adaptiveFiltering: true }).toBuffer({ resolveWithObject: true });
    if (!normalized?.data || normalized.data.length < 32 || normalized.data.length > MAX_BYTES) {
      throw new AagError("SOURCE_NORMALIZATION_FAILED", "The normalized image size is invalid.");
    }
    return {
      bytes: normalized.data,
      width: normalized.info.width,
      height: normalized.info.height,
      format: "png",
      original_format: parsed.actual,
      alpha_policy: "flatten-white",
      orientation_policy: "exif-transpose",
      metadata_policy: "stripped",
    };
  } catch (error) {
    if (error instanceof AagError) throw error;
    if (/pixel limit|Input image exceeds pixel limit/i.test(String(error?.message))) {
      throw new AagError("SOURCE_TOO_MANY_PIXELS", "The attachment exceeds the decoded image limit.");
    }
    throw new AagError("SOURCE_CORRUPT", "The attachment cannot be decoded safely.", false, error?.message);
  }
}

function imageAttachments(runtime = {}) {
  const all = Array.isArray(runtime.AAG_INVOCATION_ATTACHMENTS) ? runtime.AAG_INVOCATION_ATTACHMENTS : [];
  return all.filter(item => String(item?.mime || "").toLowerCase().startsWith("image/") || /^data:image\//i.test(String(item?.contentString || item?.content || "")));
}

async function currentAttachment(task, runtime, deps = {}) {
  const list = imageAttachments(runtime);
  if (!list.length) throw new AagError("SOURCE_REQUIRED", "A current image attachment is required.");
  if (task.source_index === undefined && list.length !== 1) throw new AagError("SOURCE_AMBIGUOUS", "Select one current image attachment.");
  const index = (task.source_index || 1) - 1;
  if (!list[index]) throw new AagError("SOURCE_REQUIRED", "The selected attachment does not exist.");
  const parsed = parseAttachment(list[index]);
  const normalized = await normalizeBytes(parsed, deps);
  return {
    source: {
      kind: "current_attachment",
      index: index + 1,
      original_name: parsed.original_name,
      original_sha256: sha256(parsed.bytes),
      normalized_sha256: sha256(normalized.bytes),
      width: normalized.width,
      height: normalized.height,
      format: normalized.format,
      original_format: normalized.original_format,
      normalization: { alpha: normalized.alpha_policy, orientation: normalized.orientation_policy, metadata: normalized.metadata_policy },
    },
    normalized,
    runtime: {
      ...runtime,
      AAG_INVOCATION_ATTACHMENTS: [{ name: `${sha256(normalized.bytes)}.png`, mime: "image/png", contentString: `data:image/png;base64,${normalized.bytes.toString("base64")}` }],
    },
  };
}

async function inspectOutput(bytes, deps = {}) {
  if (deps.inspectOutput) return deps.inspectOutput(bytes);
  const sharp = loadSharp();
  if (!sharp) throw new AagError("OUTPUT_INVALID", "The trusted artifact decoder is unavailable.", true);
  try {
    const meta = await sharp(bytes, { failOn: "warning", limitInputPixels: MAX_OUTPUT_PIXELS }).metadata();
    if (!meta.width || !meta.height || Number(meta.pages || 1) !== 1 || !["jpeg", "png", "webp"].includes(String(meta.format))) {
      throw new Error("invalid image metadata");
    }
    return { width: meta.width, height: meta.height, format: meta.format };
  } catch (error) {
    throw new AagError("OUTPUT_INVALID", "The produced artifact cannot be decoded safely.", false, error?.message);
  }
}

module.exports = { MAX_BYTES, MAX_PIXELS, MAX_OUTPUT_PIXELS, detectedFormat, strictBase64, parseAttachment, normalizeBytes, imageAttachments, currentAttachment, inspectOutput };
