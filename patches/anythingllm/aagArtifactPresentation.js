"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const AAG_RESULT_HEADER = "AAG_IMAGE_RESULT";
const AAG_PUBLIC_HOSTS = new Set(["127.0.0.1", "localhost"]);
const AAG_PUBLIC_PORT = "18190";
const AAG_INTERNAL_ORIGIN = "http://172.18.0.1:18190";
const MAX_ARTIFACT_BYTES = 200 * 1024 * 1024;
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

function presentationError(detail) {
  const error = new Error("AAG_ARTIFACT_PRESENTATION_FAILED");
  error.code = "AAG_ARTIFACT_PRESENTATION_FAILED";
  error.detail = detail;
  return error;
}

function parseFields(result) {
  const lines = String(result || "").split(/\r?\n/);
  if (lines.shift() !== AAG_RESULT_HEADER) return null;
  const fields = new Map();
  for (const line of lines) {
    const separator = line.indexOf("=");
    if (separator <= 0) continue;
    const key = line.slice(0, separator);
    if (!/^[a-z0-9_]+$/.test(key) || fields.has(key))
      throw presentationError("invalid canonical result fields");
    fields.set(key, line.slice(separator + 1));
  }
  return fields;
}

function parseCanonicalArtifactUrl(raw) {
  let parsed;
  try {
    parsed = new URL(String(raw || ""));
  } catch {
    throw presentationError("invalid canonical artifact URL");
  }
  if (
    parsed.protocol !== "http:" ||
    parsed.username ||
    parsed.password ||
    !AAG_PUBLIC_HOSTS.has(parsed.hostname) ||
    parsed.port !== AAG_PUBLIC_PORT ||
    parsed.search ||
    parsed.hash
  )
    throw presentationError("non-canonical artifact URL");

  const match = parsed.pathname.match(/^\/files\/([^/]+)$/);
  if (!match) throw presentationError("non-canonical artifact path");
  let filename;
  try {
    filename = decodeURIComponent(match[1]);
  } catch {
    throw presentationError("invalid artifact filename encoding");
  }
  if (
    !/^[A-Za-z0-9][A-Za-z0-9._~-]{0,239}$/.test(filename) ||
    filename.includes("..")
  )
    throw presentationError("unsafe artifact filename");
  return { url: parsed.toString(), filename };
}

function parseDimensions(raw) {
  const match = String(raw || "").match(/^([1-9][0-9]{0,4})x([1-9][0-9]{0,4})$/);
  if (!match) throw presentationError("invalid artifact dimensions");
  const width = Number(match[1]);
  const height = Number(match[2]);
  if (width > 16384 || height > 16384)
    throw presentationError("artifact dimensions exceed presentation bounds");
  return { width, height };
}

function parseAagImageResult(result) {
  const fields = parseFields(result);
  if (!fields || !["completed", "partial", "failed", "cancelled", "timed_out"].includes(fields.get("status"))) return null;
  const count = Number(fields.get("artifact_count"));
  const operation = String(fields.get("operation") || "");
  const isBatch = operation === "multi_generate";
  if (count === 0 && fields.get("status") !== "completed") return null;
  if (!Number.isInteger(count) || count < 1 || count > (isBatch ? 10 : 2))
    throw presentationError("invalid artifact count");
  const jobId = String(fields.get("job_id") || "");
  if (!/^aag-[a-f0-9-]{36}$/i.test(jobId))
    throw presentationError("invalid AAG job ID");

  const artifacts = [];
  for (let index = 1; index <= count; index += 1) {
    const artifactId = String(fields.get(`artifact_${index}_id`) || "");
    if (!/^artifact-[a-f0-9-]{36}$/i.test(artifactId))
      throw presentationError("invalid AAG artifact ID");
    const canonical = parseCanonicalArtifactUrl(
      fields.get(`artifact_${index}_url`)
    );
    const sha256 = String(fields.get(`artifact_${index}_sha256`) || "");
    if (!/^[a-f0-9]{64}$/i.test(sha256))
      throw presentationError("invalid artifact SHA-256");
    const dimensions = parseDimensions(
      fields.get(`artifact_${index}_dimensions`)
    );
    const logicalIndex = isBatch ? Number(fields.get(`artifact_${index}_logical_index`)) : index;
    const childJobId = isBatch ? String(fields.get(`artifact_${index}_child_job_id`) || "") : "";
    if (isBatch && (!Number.isInteger(logicalIndex) || logicalIndex < 1 || logicalIndex > 10 || !/^aag-[a-f0-9-]{36}$/i.test(childJobId)))
      throw presentationError("invalid batch artifact identity");
    artifacts.push({
      artifactId,
      ...canonical,
      sha256: sha256.toLowerCase(),
      ...dimensions,
      logicalIndex,
      childJobId,
    });
  }
  if (new Set(artifacts.map((artifact) => artifact.logicalIndex)).size !== artifacts.length)
    throw presentationError("duplicate logical artifact index");
  const requestedCount = isBatch ? Number(fields.get("requested_count")) : count;
  const completedCount = isBatch ? Number(fields.get("completed_count")) : count;
  const pendingCount = isBatch ? Number(fields.get("pending_count")) : 0;
  const failedCount = isBatch ? Number(fields.get("failed_count")) : 0;
  const cancelledCount = isBatch ? Number(fields.get("cancelled_count")) : 0;
  if (isBatch && (!Number.isInteger(requestedCount) || requestedCount < 2 || requestedCount > 10 || ![completedCount, pendingCount, failedCount, cancelledCount].every((value) => Number.isInteger(value) && value >= 0)))
    throw presentationError("invalid batch progress fields");
  const collectionComplete = isBatch && fields.get("status") === "completed" && count === requestedCount && completedCount === requestedCount;
  return {
    status: fields.get("status"),
    jobId,
    operation,
    workflow: String(fields.get("workflow") || ""),
    release: String(fields.get("release") || ""),
    isBatch,
    collectionId: isBatch ? String(fields.get("collection_id") || jobId) : null,
    planSha256: isBatch ? String(fields.get("plan_sha256") || "") : null,
    requestedCount,
    completedCount,
    pendingCount,
    failedCount,
    cancelledCount,
    collectionComplete,
    artifacts,
  };
}

function inspectPng(bytes) {
  if (
    bytes.length < 33 ||
    !bytes.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE) ||
    bytes.toString("ascii", 12, 16) !== "IHDR"
  )
    throw presentationError("presentation artifact is not a decoded PNG");
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

function presentationFilename(sha256) {
  const value = sha256.slice(0, 32);
  return `img-${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}.png`;
}

function verifyExistingFile(target, sha256) {
  const bytes = fs.readFileSync(target);
  const actual = crypto.createHash("sha256").update(bytes).digest("hex");
  if (actual !== sha256)
    throw presentationError("deterministic presentation file collision");
  return bytes.length;
}

function persistPresentationCopy(generatedImagesPath, bytes, sha256) {
  fs.mkdirSync(generatedImagesPath, { recursive: true });
  const storageFilename = presentationFilename(sha256);
  const target = path.resolve(generatedImagesPath, storageFilename);
  const relative = path.relative(path.resolve(generatedImagesPath), target);
  if (relative.startsWith("..") || path.isAbsolute(relative))
    throw presentationError("presentation target escaped storage");
  try {
    fs.writeFileSync(target, bytes, { flag: "wx", mode: 0o600 });
    return { storageFilename, fileSize: bytes.length, created: true };
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
    return {
      storageFilename,
      fileSize: verifyExistingFile(target, sha256),
      created: false,
    };
  }
}

async function fetchAndVerifyArtifact(artifact, fetchImpl) {
  const response = await fetchImpl(
    `${AAG_INTERNAL_ORIGIN}/files/${encodeURIComponent(artifact.filename)}`,
    { signal: AbortSignal.timeout(15_000) }
  );
  if (!response?.ok)
    throw presentationError("trusted artifact publisher fetch failed");
  const contentType = String(response.headers?.get?.("content-type") || "")
    .split(";", 1)[0]
    .toLowerCase();
  if (contentType !== "image/png")
    throw presentationError("presentation artifact MIME is not image/png");
  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.length < 128 || bytes.length > MAX_ARTIFACT_BYTES)
    throw presentationError("presentation artifact size is invalid");
  const actualSha256 = crypto
    .createHash("sha256")
    .update(bytes)
    .digest("hex");
  if (actualSha256 !== artifact.sha256)
    throw presentationError("presentation artifact SHA-256 mismatch");
  const dimensions = inspectPng(bytes);
  if (
    dimensions.width !== artifact.width ||
    dimensions.height !== artifact.height
  )
    throw presentationError("presentation artifact dimensions mismatch");
  return bytes;
}

function displayName(operation, index, count) {
  const base = operation === "upscale" ? "upscaled-image" : "generated-image";
  return count === 1 ? `${base}.png` : `${base}-${String(index).padStart(3, "0")}.png`;
}

async function buildPresentationOutputs({
  parsed,
  prompt,
  promptsByLogicalIndex = new Map(),
  collectionBrief = "",
  generatedImagesPath,
  fetchImpl,
}) {
  const outputs = [];
  for (let index = 0; index < parsed.artifacts.length; index += 1) {
    const artifact = parsed.artifacts[index];
    const bytes = await fetchAndVerifyArtifact(artifact, fetchImpl);
    const persisted = persistPresentationCopy(
      generatedImagesPath,
      bytes,
      artifact.sha256
    );
    outputs.push({
      type: "imageGenerationCard",
      payload: {
        storageFilename: persisted.storageFilename,
        filename: displayName(
          parsed.operation,
          artifact.logicalIndex,
          parsed.requestedCount
        ),
        fileSize: persisted.fileSize,
        prompt: String(promptsByLogicalIndex.get(artifact.logicalIndex) || prompt || "").slice(0, 2000),
        artifactUrl: artifact.url,
        artifactSha256: artifact.sha256,
        artifactWidth: artifact.width,
        artifactHeight: artifact.height,
        artifactId: artifact.artifactId,
        jobId: parsed.jobId,
        workflow: parsed.workflow,
        release: parsed.release,
        collectionId: parsed.collectionId,
        collectionBrief: String(collectionBrief || "").slice(0, 4000),
        logicalIndex: artifact.logicalIndex,
        requestedCount: parsed.requestedCount,
        completedCount: parsed.completedCount,
        pendingCount: parsed.pendingCount,
        failedCount: parsed.failedCount,
        cancelledCount: parsed.cancelledCount,
        collectionStatus: parsed.status,
        collectionComplete: parsed.collectionComplete,
        artifactExport: parsed.isBatch ? {
          schema: "aag.trusted-artifact-export.v1",
          producer: "aag-image",
          trustClass: "anythingllm-generated-image-v1",
          collectionId: parsed.collectionId,
          logicalIndex: artifact.logicalIndex,
          requestedCount: parsed.requestedCount,
          collectionComplete: parsed.collectionComplete,
          sourceSha256: artifact.sha256,
        } : undefined,
      },
    });
  }
  return outputs;
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizeFinalText(content, artifactUrls) {
  let text = String(content || "");
  for (const url of artifactUrls) {
    const escaped = escapeRegExp(url);
    text = text
      .replace(new RegExp(`!\\[[^\\]]*\\]\\(\\s*${escaped}\\s*\\)`, "gi"), "")
      .replace(new RegExp(`<${escaped}>`, "gi"), "")
      .replace(new RegExp(escaped, "gi"), "");
  }
  text = text.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  return text || "Image generated successfully.";
}

function appendUniqueOutputs(aibitat, outputs) {
  if (!Array.isArray(aibitat._pendingOutputs)) aibitat._pendingOutputs = [];
  const known = new Set(
    aibitat._pendingOutputs
      .filter((output) => output?.type === "imageGenerationCard")
      .map((output) => output?.payload?.artifactSha256)
      .filter(Boolean)
  );
  for (const output of outputs) {
    if (known.has(output.payload.artifactSha256)) continue;
    known.add(output.payload.artifactSha256);
    aibitat._pendingOutputs.push(output);
  }
}

function semanticPrompt(args) {
  let value = args;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      return "";
    }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return "";
  return String(value.request || value.prompt || "");
}

function batchPresentationPlan(args) {
  let value = args;
  if (typeof value === "string") {
    try { value = JSON.parse(value); } catch { return { collectionBrief: "", prompts: new Map() }; }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return { collectionBrief: "", prompts: new Map() };
  const prompts = new Map();
  if (Array.isArray(value.items)) value.items.forEach((item, index) => prompts.set(index + 1, String(item?.prompt || "")));
  return { collectionBrief: String(value.collection_brief || ""), prompts };
}

function installAagArtifactPresentation({
  aibitat,
  generatedImagesPath,
  fetchImpl = globalThis.fetch,
  authorizeOutputs = null,
  log = () => {},
}) {
  if (!aibitat || aibitat._aagArtifactPresentationInstalled) return false;
  const tasks = [...(aibitat.functions?.values?.() || [])].filter(
    (fn) => ["aag-image-task", "aag-image-batch"].includes(fn?.config?.hubId) && typeof fn.handler === "function"
  );
  if (!tasks.length) return false;
  if (typeof fetchImpl !== "function")
    throw presentationError("fetch implementation unavailable");

  aibitat._aagArtifactPresentationInstalled = true;
  aibitat._aagPresentedArtifactUrls = [];
  for (const task of tasks) {
    const originalHandler = task.handler.bind(task);
    task.handler = async (args) => {
      const result = await originalHandler(args);
      const parsed = parseAagImageResult(result);
      if (!parsed) return result;
      const prompt = semanticPrompt(args);
      const plan = batchPresentationPlan(args);
      const outputs = await buildPresentationOutputs({
        parsed,
        prompt,
        promptsByLogicalIndex: plan.prompts,
        collectionBrief: plan.collectionBrief,
        generatedImagesPath,
        fetchImpl,
      });
      if (typeof authorizeOutputs === "function") await authorizeOutputs(outputs);
      appendUniqueOutputs(aibitat, outputs);
      aibitat._aagPresentedArtifactUrls = [
        ...new Set([...aibitat._aagPresentedArtifactUrls, ...parsed.artifacts.map((artifact) => artifact.url)]),
      ];
      log(`Registered ${outputs.length} verified AAG artifact card(s) for job ${parsed.jobId}`);
      return result;
    };
  }

  const originalNewMessage = aibitat.newMessage.bind(aibitat);
  aibitat.newMessage = (message) => {
    const outputs = Array.isArray(aibitat._pendingOutputs)
      ? [...aibitat._pendingOutputs]
      : [];
    if (
      message?.from !== "USER" &&
      outputs.some((output) => output?.type === "imageGenerationCard") &&
      aibitat._aagPresentedArtifactUrls.length > 0
    ) {
      return originalNewMessage({
        ...message,
        // AnythingLLM's live websocket reducer only hydrates native outputs
        // from a typed imageGenerationCard event after agent streaming starts.
        // History hydration reads `outputs` directly, which is why persistence
        // could succeed while the active browser ignored this same message.
        type: "imageGenerationCard",
        content: normalizeFinalText(
          message.content,
          aibitat._aagPresentedArtifactUrls
        ),
        outputs,
      });
    }
    return originalNewMessage(message);
  };
  return true;
}

module.exports = {
  AAG_IMAGE_PROVIDER_POLICY: "OPEN_BY_CAPABILITY",
  parseAagImageResult,
  parseCanonicalArtifactUrl,
  inspectPng,
  presentationFilename,
  persistPresentationCopy,
  fetchAndVerifyArtifact,
  buildPresentationOutputs,
  normalizeFinalText,
  semanticPrompt,
  batchPresentationPlan,
  installAagArtifactPresentation,
};
