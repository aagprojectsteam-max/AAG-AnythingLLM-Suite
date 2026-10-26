"use strict";
// Structural scaffold: a workspace-LLM-authored line drawing (strict SVG subset)
// that fixes element counts, symmetry and layout. The engine then restyles it
// through the existing reference (ReferenceLatent) path, so diffusion no longer
// has to "count". The input is untrusted model output: it is tokenized, every
// element and attribute is whitelisted, numbers are bounded, and a canonical
// SVG is re-emitted. The raw string never reaches the rasterizer.
const crypto = require("crypto");
const { AagError } = require("./errors");

const MAX_SOURCE_CHARS = 24_000;
const MAX_ELEMENTS = 1_500;
const MAX_PATH_CHARS = 4_000;
const MAX_COORD = 4_096;
const RENDER_SIZE = 1024;
const STROKE = 8;
const CANVAS = 1000;
const ELEMENTS = Object.freeze({
  rect: ["x", "y", "width", "height", "rx", "ry"],
  circle: ["cx", "cy", "r"],
  ellipse: ["cx", "cy", "rx", "ry"],
  line: ["x1", "y1", "x2", "y2"],
  polyline: ["points"],
  polygon: ["points"],
  path: ["d"],
});
const REQUIRED = Object.freeze({ rect: ["width", "height"], circle: ["cx", "cy", "r"], ellipse: ["cx", "cy", "rx", "ry"], line: ["x1", "y1", "x2", "y2"], polyline: ["points"], polygon: ["points"], path: ["d"] });
const STYLE_ATTRIBUTES = new Set(["fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "opacity", "id", "class", "style"]);
const FILLS = new Set(["none", "white", "#fff", "#ffffff", "black", "#000", "#000000"]);
const PATH_RE = /^[MLHVCSQTAZmlhvcsqtaz0-9eE+\-.,\s]+$/;
const TAG_RE = /<\s*(\/)?\s*([A-Za-z][A-Za-z0-9]*)((?:\s+[A-Za-z_:][A-Za-z0-9_:.\-]*\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*(\/)?\s*>/g;
const ATTR_RE = /([A-Za-z_:][A-Za-z0-9_:.\-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;

function invalid(detail) { return new AagError("SCAFFOLD_INVALID", "The structural scaffold is not a supported line drawing.", false, detail); }

function number(value, name, max = MAX_COORD) {
  const parsed = Number(String(value).trim());
  if (!Number.isFinite(parsed) || Math.abs(parsed) > max) throw invalid(`${name} out of range`);
  return Math.round(parsed * 100) / 100;
}

function points(value) {
  const tokens = String(value).trim().split(/[\s,]+/).filter(Boolean);
  if (tokens.length < 4 || tokens.length % 2 || tokens.length > 2_000) throw invalid("points list is invalid");
  return tokens.map((token, index) => number(token, `point ${index}`)).join(" ");
}

function pathData(value) {
  const data = String(value).trim().replace(/\s+/g, " ");
  if (!data || data.length > MAX_PATH_CHARS || !PATH_RE.test(data)) throw invalid("path data is invalid");
  for (const token of data.match(/-?\d*\.?\d+(?:[eE][+-]?\d+)?/g) || []) number(token, "path coordinate");
  return data;
}

function parse(source) {
  if (typeof source !== "string" || !source.trim()) throw invalid("empty");
  if (source.length > MAX_SOURCE_CHARS) throw invalid("too large");
  // XML comments are harmless annotations LLMs like to add ("<!-- base -->");
  // drop them before the strict scan so only real markup is judged.
  const text = source.replace(/<!--[\s\S]*?-->/g, "").replace(/<(title|desc)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, "").trim();
  if (/<!|<\?|&[#A-Za-z]|<script|<style|<text|<image|<use|<foreignObject|href|url\(|javascript:/i.test(text)) throw invalid("forbidden content");
  const stripped = text.replace(TAG_RE, "");
  if (/[<>]/.test(stripped)) throw invalid("malformed markup");
  if (stripped.trim()) throw invalid("text content is not allowed");
  const elements = [];
  let viewBox = null, depth = 0, sawSvg = false, fills = new Set();
  const tags = [...text.matchAll(TAG_RE)];
  for (const [, closing, rawName, rawAttributes, selfClosing] of tags) {
    const name = rawName.toLowerCase();
    const attributes = {};
    for (const [, key, double, single] of (rawAttributes || "").matchAll(ATTR_RE)) attributes[key.toLowerCase()] = double ?? single ?? "";
    if (name === "svg") {
      if (closing) { depth -= 1; continue; }
      if (sawSvg) throw invalid("nested svg");
      sawSvg = true; depth += 1;
      const box = String(attributes.viewbox || `0 0 ${CANVAS} ${CANVAS}`).trim().split(/[\s,]+/).map(v => number(v, "viewBox"));
      if (box.length !== 4 || box[2] <= 0 || box[3] <= 0 || box[2] > MAX_COORD || box[3] > MAX_COORD) throw invalid("viewBox is invalid");
      viewBox = box;
      continue;
    }
    if (!sawSvg) throw invalid("content before svg root");
    if (name === "g") { if (closing) depth -= 1; else if (!selfClosing) depth += 1; continue; }
    if (closing) continue;
    const allowed = ELEMENTS[name];
    if (!allowed) throw invalid(`element <${name}> is not allowed`);
    const element = { name, attributes: {} };
    for (const [key, value] of Object.entries(attributes)) {
      if (allowed.includes(key)) element.attributes[key] = key === "points" ? points(value) : key === "d" ? pathData(value) : number(value, key);
      else if (STYLE_ATTRIBUTES.has(key)) {
        if (key === "fill") {
          // Only none/white/black are meaningful in a structural line drawing; any other
          // plain color is coerced to none (no fill), while url()/functional values are rejected above.
          const fill = value.trim().toLowerCase();
          if (!/^(none|transparent|[a-z]{1,20}|#[0-9a-f]{3,8})$/.test(fill)) throw invalid("unsupported fill");
          if (FILLS.has(fill) && fill !== "none") { element.fill = fill.startsWith("#f") || fill === "white" ? "white" : "black"; fills.add(element.fill); }
        }
      } else throw invalid(`attribute ${key} is not allowed`);
    }
    for (const required of REQUIRED[name]) if (!(required in element.attributes)) throw invalid(`<${name}> is missing ${required}`);
    elements.push(element);
    if (elements.length > MAX_ELEMENTS) throw invalid("too many elements");
  }
  if (!sawSvg || depth !== 0) throw invalid("unbalanced markup");
  if (!elements.length) throw invalid("no drawable elements");
  return { viewBox, elements, fills: [...fills] };
}

function canonical(parsed) {
  const [minX, minY, width, height] = parsed.viewBox;
  const size = width >= height ? { width: RENDER_SIZE, height: Math.max(64, Math.round(RENDER_SIZE * height / width)) } : { width: Math.max(64, Math.round(RENDER_SIZE * width / height)), height: RENDER_SIZE };
  const stroke = Math.max(2, Math.round(STROKE * Math.max(width, height) / CANVAS * 100) / 100);
  const body = parsed.elements.map(element => {
    const attributes = Object.entries(element.attributes).map(([key, value]) => `${key}="${value}"`).join(" ");
    const fill = element.fill ? ` fill="${element.fill}"` : "";
    return `<${element.name} ${attributes}${fill}/>`;
  }).join("\n");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${minX} ${minY} ${width} ${height}" width="${size.width}" height="${size.height}">\n<rect x="${minX}" y="${minY}" width="${width}" height="${height}" fill="white"/>\n<g fill="none" stroke="black" stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round">\n${body}\n</g>\n</svg>\n`;
  return { svg, width: size.width, height: size.height, stroke };
}

function loadSharp() {
  for (const name of ["sharp", "/app/server/node_modules/sharp"]) { try { return require(name); } catch {} }
  return null;
}

async function render(source, deps = {}) {
  const parsed = parse(source);
  const built = canonical(parsed);
  const sha256 = crypto.createHash("sha256").update(built.svg).digest("hex");
  let image;
  if (deps.renderScaffold) image = await deps.renderScaffold(built);
  else {
    const sharp = loadSharp();
    if (!sharp) throw new AagError("SOURCE_NORMALIZATION_FAILED", "The trusted scaffold rasterizer is unavailable.", true);
    try {
      const output = await sharp(Buffer.from(built.svg), { density: 96, failOn: "warning" }).flatten({ background: "#ffffff" }).png({ compressionLevel: 9 }).toBuffer({ resolveWithObject: true });
      image = { bytes: output.data, width: output.info.width, height: output.info.height, format: "png" };
    } catch (error) {
      throw new AagError("SOURCE_NORMALIZATION_FAILED", "The structural scaffold could not be rasterized.", false, error?.message);
    }
  }
  if (!Buffer.isBuffer(image?.bytes) || image.bytes.length < 32 || !Number.isInteger(image.width) || !Number.isInteger(image.height)) throw new AagError("SOURCE_NORMALIZATION_FAILED", "The rasterized scaffold is invalid.");
  return { ...image, original_format: "svg", alpha_policy: "flatten-white", orientation_policy: "none", metadata_policy: "stripped", scaffold: { sha256, elements: parsed.elements.length, kinds: summarize(parsed.elements), canvas: `${parsed.viewBox[2]}×${parsed.viewBox[3]}` } };
}

function summarize(elements) {
  const counts = {};
  for (const element of elements) counts[element.name] = (counts[element.name] || 0) + 1;
  return Object.entries(counts).sort().map(([name, count]) => `${name}:${count}`).join(", ");
}

// Prompt suffix the engine receives with a scaffold: the LLM-authored creative
// prompt stays authoritative for style; the scaffold is authoritative for layout.
const ENGINE_SUFFIX = "Use the supplied reference line drawing as the exact structural blueprint: keep every element, its count, position, size, symmetry and relative arrangement exactly as drawn. Only redraw the style, line quality and decorative detail according to the description above; never add, remove, merge or move structural elements.";

module.exports = { MAX_SOURCE_CHARS, MAX_ELEMENTS, RENDER_SIZE, ENGINE_SUFFIX, parse, canonical, render, summarize };
