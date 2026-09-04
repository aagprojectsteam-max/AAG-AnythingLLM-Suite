"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { userFromSession, multiUserMode, safeJsonParse } = require("../utils/http");
const { validatedRequest } = require("../utils/middleware/validatedRequest");
const { flexUserRoleValid, ROLES } = require("../utils/middleware/multiUserProtected");
const { WorkspaceChats } = require("../models/workspaceChats");
const { Workspace } = require("../models/workspace");
const { generatedImagesPath, GENERATED_IMAGE_FILENAME_PATTERN, isWithin } = require("../utils/files");
const { assemblePdf, parsePng, verifyPdf, CONTRACT_ID, MAX_PAGES } = require("./aagPdfAssembler");

const EXPORT_SCHEMA = "aag.trusted-artifact-export.v1";
const CACHE_ROOT = path.resolve(process.env.STORAGE_DIR || "/app/server/storage", "aag-artifact-exports", "pdf");
const MAX_CACHE_ENTRIES = 256;
const REQUEST_KEYS = new Set(["format", "mode", "artifacts"]);

function exportError(status, code, message) {
  const error = new Error(message);
  error.status = status;
  error.code = code;
  return error;
}

function suggestedPdfFilename(now = new Date()) {
  const stamp = now.toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
  return `ANYTHING-${stamp}.pdf`;
}

function normalizeRequest(body) {
  if (!body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).some((key) => !REQUEST_KEYS.has(key))) {
    throw exportError(400, "EXPORT_REQUEST_INVALID", "The export request is invalid.");
  }
  if (body.format !== "pdf" || !["single", "collection"].includes(body.mode)) throw exportError(400, "EXPORT_FORMAT_INVALID", "Only the governed on-demand PDF export modes are supported.");
  if (!Array.isArray(body.artifacts) || body.artifacts.length < 1 || body.artifacts.length > MAX_PAGES) throw exportError(400, "EXPORT_COUNT_INVALID", `Export requires 1..${MAX_PAGES} trusted artifact references.`);
  if (body.mode === "single" && body.artifacts.length !== 1) throw exportError(400, "EXPORT_COUNT_INVALID", "Single-image PDF export requires exactly one artifact.");
  const artifacts = body.artifacts.map((value) => String(value || ""));
  if (artifacts.some((value) => !GENERATED_IMAGE_FILENAME_PATTERN.test(value)) || new Set(artifacts).size !== artifacts.length) {
    throw exportError(400, "EXPORT_REFERENCE_INVALID", "An export artifact reference is invalid or duplicated.");
  }
  return { format: "pdf", mode: body.mode, artifacts };
}

async function authorizedWorkspaceIds(user, isMultiUser) {
  const workspaces = isMultiUser && user ? await Workspace.whereWithUser(user) : await Workspace.where();
  return workspaces.map((workspace) => workspace.id);
}

async function resolvePersistedOutputs(request, { user, isMultiUser }) {
  const workspaceIds = await authorizedWorkspaceIds(user, isMultiUser);
  if (!workspaceIds.length) throw exportError(404, "EXPORT_NOT_FOUND", "No authorized artifact set was found.");
  const chats = await WorkspaceChats.where({
    workspaceId: { in: workspaceIds },
    include: true,
    response: { contains: request.artifacts[0] },
  });
  for (const chat of chats) {
    const outputs = safeJsonParse(chat.response, { outputs: [] })?.outputs || [];
    const byFilename = new Map(
      outputs
        .filter((output) => output?.type === "imageGenerationCard" && GENERATED_IMAGE_FILENAME_PATTERN.test(output?.payload?.storageFilename || ""))
        .map((output) => [output.payload.storageFilename, output])
    );
    if (!request.artifacts.every((filename) => byFilename.has(filename))) continue;
    const selected = request.artifacts.map((filename) => byFilename.get(filename));
    if (request.mode === "single") return { chat, outputs: selected };
    const descriptors = selected.map((output) => output.payload?.artifactExport);
    if (descriptors.some((descriptor) => !descriptor || descriptor.schema !== EXPORT_SCHEMA || descriptor.collectionComplete !== true)) continue;
    const collectionId = descriptors[0].collectionId;
    const requestedCount = Number(descriptors[0].requestedCount);
    if (!collectionId || requestedCount !== selected.length || selected.length < 2 || descriptors.some((descriptor) => descriptor.collectionId !== collectionId || Number(descriptor.requestedCount) !== requestedCount)) continue;
    const ordered = [...selected].sort((left, right) => Number(left.payload.artifactExport.logicalIndex) - Number(right.payload.artifactExport.logicalIndex));
    if (ordered.some((output, index) => Number(output.payload.artifactExport.logicalIndex) !== index + 1)) continue;
    if (new Set(ordered.map((output) => output.payload.storageFilename)).size !== requestedCount) continue;
    return { chat, outputs: ordered };
  }
  throw exportError(404, "EXPORT_NOT_FOUND", "The complete ordered trusted artifact set was not found or is not authorized.");
}

function readTrustedImage(output) {
  const payload = output?.payload || {};
  const filename = String(payload.storageFilename || "");
  if (!GENERATED_IMAGE_FILENAME_PATTERN.test(filename)) throw exportError(400, "EXPORT_REFERENCE_INVALID", "The persisted artifact reference is invalid.");
  const target = path.resolve(generatedImagesPath, filename);
  if (!isWithin(generatedImagesPath, target)) throw exportError(400, "EXPORT_REFERENCE_INVALID", "The artifact reference escaped trusted storage.");
  let fd;
  let bytes;
  try {
    fd = fs.openSync(target, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    const stat = fs.fstatSync(fd);
    if (!stat.isFile()) throw exportError(404, "EXPORT_SOURCE_MISSING", "The trusted source artifact is unavailable.");
    bytes = fs.readFileSync(fd);
  } catch (error) {
    if (error?.status) throw error;
    throw exportError(404, "EXPORT_SOURCE_MISSING", "The trusted source artifact is unavailable.");
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
  const sha256 = crypto.createHash("sha256").update(bytes).digest("hex");
  const persistedHash = String(payload.artifactSha256 || payload.artifactExport?.sourceSha256 || "").toLowerCase();
  if (persistedHash && (!/^[a-f0-9]{64}$/.test(persistedHash) || persistedHash !== sha256)) throw exportError(409, "EXPORT_PROVENANCE_MISMATCH", "The trusted artifact bytes differ from persisted provenance.");
  const dimensions = parsePng(bytes);
  if (payload.artifactWidth && Number(payload.artifactWidth) !== dimensions.width) throw exportError(409, "EXPORT_PROVENANCE_MISMATCH", "The trusted artifact width differs from persisted provenance.");
  if (payload.artifactHeight && Number(payload.artifactHeight) !== dimensions.height) throw exportError(409, "EXPORT_PROVENANCE_MISMATCH", "The trusted artifact height differs from persisted provenance.");
  return { bytes, sha256, width: dimensions.width, height: dimensions.height, storageFilename: filename };
}

function cacheIdentity(images) {
  return crypto.createHash("sha256").update(JSON.stringify({ contract: CONTRACT_ID, format: "pdf", layout: "one-image-per-page-preserve-orientation", sourceHashes: images.map((image) => image.sha256) })).digest("hex");
}

function ensureCacheRoot() {
  fs.mkdirSync(CACHE_ROOT, { recursive: true, mode: 0o700 });
  const stat = fs.lstatSync(CACHE_ROOT);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw exportError(500, "EXPORT_CACHE_UNSAFE", "The export cache is unsafe.");
  fs.chmodSync(CACHE_ROOT, 0o700);
}

function atomicCacheWrite(target, bytes) {
  const temporary = `${target}.${process.pid}.${crypto.randomUUID()}.tmp`;
  let fd;
  try {
    fd = fs.openSync(temporary, "wx", 0o600);
    fs.writeFileSync(fd, bytes);
    fs.fsyncSync(fd);
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
  try { fs.renameSync(temporary, target); }
  catch (error) {
    try { fs.unlinkSync(temporary); } catch {}
    if (error?.code !== "EEXIST") throw error;
  }
  fs.chmodSync(target, 0o600);
}

function pruneCache(currentKey) {
  const entries = fs.readdirSync(CACHE_ROOT)
    .filter((name) => /^[a-f0-9]{64}\.json$/.test(name))
    .map((name) => ({ name, key: name.slice(0, 64), mtime: fs.statSync(path.join(CACHE_ROOT, name)).mtimeMs }))
    .sort((left, right) => right.mtime - left.mtime);
  for (const entry of entries.slice(MAX_CACHE_ENTRIES)) {
    if (entry.key === currentKey) continue;
    for (const extension of [".json", ".pdf"]) {
      try { fs.unlinkSync(path.join(CACHE_ROOT, `${entry.key}${extension}`)); } catch {}
    }
  }
}

function cachedOrAssemble(images) {
  ensureCacheRoot();
  const key = cacheIdentity(images);
  const pdfPath = path.join(CACHE_ROOT, `${key}.pdf`);
  const manifestPath = path.join(CACHE_ROOT, `${key}.json`);
  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    const pdf = fs.readFileSync(pdfPath);
    const sha256 = crypto.createHash("sha256").update(pdf).digest("hex");
    if (manifest.schema === EXPORT_SCHEMA && manifest.cacheKey === key && manifest.pdfSha256 === sha256 && JSON.stringify(manifest.sourceHashes) === JSON.stringify(images.map((image) => image.sha256))) {
      verifyPdf(pdf, images.length);
      return { pdf, key, cached: true, manifest };
    }
  } catch {}
  const pdf = assemblePdf(images, { sourceHashes: images.map((image) => image.sha256) });
  const manifest = {
    schema: EXPORT_SCHEMA,
    contract: CONTRACT_ID,
    cacheKey: key,
    sourceHashes: images.map((image) => image.sha256),
    sourceStorageNames: images.map((image) => image.storageFilename),
    ordering: "persisted-logical-index",
    layout: "one-image-per-page-preserve-orientation",
    pageCount: images.length,
    pdfSha256: crypto.createHash("sha256").update(pdf).digest("hex"),
    createdAt: new Date().toISOString(),
  };
  atomicCacheWrite(pdfPath, pdf);
  atomicCacheWrite(manifestPath, Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`));
  pruneCache(key);
  return { pdf, key, cached: false, manifest };
}

function aagArtifactExportEndpoints(app) {
  if (!app) return;
  app.post(
    "/aag/artifact-export/pdf",
    [validatedRequest, flexUserRoleValid([ROLES.all])],
    async (request, response) => {
      try {
        const normalized = normalizeRequest(request.body);
        const user = await userFromSession(request, response);
        const resolved = await resolvePersistedOutputs(normalized, { user, isMultiUser: multiUserMode(response) });
        const images = resolved.outputs.map(readTrustedImage);
        const exported = cachedOrAssemble(images);
        const filename = suggestedPdfFilename();
        response.setHeader("Content-Type", "application/pdf");
        response.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
        response.setHeader("Content-Length", exported.pdf.length);
        response.setHeader("Cache-Control", "no-store");
        response.setHeader("X-AAG-Export-Contract", CONTRACT_ID);
        response.setHeader("X-AAG-Export-Cache", exported.cached ? "HIT" : "MISS");
        response.setHeader("X-AAG-PDF-Pages", images.length);
        response.setHeader("X-AAG-Suggested-Filename", filename);
        return response.send(exported.pdf);
      } catch (error) {
        const status = Number(error?.status) || 500;
        if (status >= 500) console.error("[AAG Artifact Export]", error?.code || "EXPORT_FAILED", error?.message);
        return response.status(status).json({ error: error?.code || "EXPORT_FAILED", message: status >= 500 ? "The on-demand export failed safely." : error.message });
      }
    }
  );
}

module.exports = {
  EXPORT_SCHEMA,
  CACHE_ROOT,
  suggestedPdfFilename,
  normalizeRequest,
  resolvePersistedOutputs,
  readTrustedImage,
  cacheIdentity,
  cachedOrAssemble,
  aagArtifactExportEndpoints,
};
