"use strict";

const byId = (id) => document.getElementById(id);
const form = byId("composer-form");
const advanced = byId("advanced-fields");
const operation = byId("operation");
const freeText = byId("free-text");
const previewButton = byId("preview-button");
const submitButton = byId("submit-button");
const statusPanel = byId("status-panel");
const resultOutput = byId("result-output");
const resultWorkspaceLink = byId("result-workspace-link");
const familySelect = byId("visual-family");
const subfamilySelect = byId("visual-subfamily");
const sourcePolicy = byId("source-policy");
const sourceImage = byId("source-image");
const sourceIndex = byId("source-index");
const preservation = byId("preservation");
const countPreset = byId("count-preset");

const RECENTS_KEY = "aag.image-composer.v1.1.recent-styles";
let csrf = null;
let taxonomy = null;
let previousArtifactAvailable = false;
let thumbnailUrls = [];

function currentMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

function setHidden(id, hidden) {
  byId(id).hidden = hidden;
}

function resetAdvanced() {
  operation.value = "create";
  byId("output-purpose").value = "auto";
  byId("batch-relationship").value = "independent";
  byId("family-search").value = "";
  byId("subfamily-search").value = "";
  populateFamilies();
  familySelect.value = "auto";
  populateSubfamilies(true);
  byId("background").value = "auto";
  byId("visible-text").value = "auto";
  byId("aspect-ratio").value = "auto";
  countPreset.value = "3";
  byId("custom-count").value = "6";
  byId("quality").value = "auto";
  sourcePolicy.value = "current_attachment";
  sourceImage.value = "";
  preservation.value = "subject";
  byId("source-instruction").value = "";
  byId("scale").value = "auto";
  byId("seed").value = "";
  clearThumbnails();
  renderSources();
}

function updateMode() {
  const isAdvanced = currentMode() === "advanced";
  if (!isAdvanced) resetAdvanced();
  advanced.hidden = !isAdvanced;
  if (isAdvanced) updateOperation();
}

function ensureBackgroundOptions(isEdit) {
  const select = byId("background");
  const existing = select.querySelector('option[value="preserve_source"]');
  if (isEdit && !existing) {
    const option = document.createElement("option");
    option.value = "preserve_source";
    option.textContent = "Preserve source background";
    select.insertBefore(option, select.options[1]);
  } else if (!isEdit && existing) {
    if (select.value === "preserve_source") select.value = "auto";
    existing.remove();
  }
}

function updateOperation() {
  const selected = operation.value;
  const isBatch = selected === "batch";
  const isEdit = selected === "transform";
  const isUpscale = selected === "upscale";
  const usesSource = isEdit || isUpscale;

  setHidden("batch-relationship-field", !isBatch);
  setHidden("count-field", !isBatch);
  setHidden("custom-count-field", !isBatch || countPreset.value !== "custom");
  setHidden("source-group", !usesSource);
  setHidden("scale-field", !isUpscale);
  setHidden("seed-field", isBatch || isUpscale);
  setHidden("quality-field", isUpscale);
  setHidden("output-purpose-field", isUpscale);
  setHidden("appearance-group", isUpscale);
  setHidden("size-group", isUpscale);
  setHidden("preservation-field", !isEdit);
  setHidden("source-instruction-field", !isEdit);
  ensureBackgroundOptions(isEdit);

  if (!usesSource) {
    sourcePolicy.value = "current_attachment";
    sourceImage.value = "";
    clearThumbnails();
  }
  if (isBatch || isUpscale) byId("seed").value = "";
  if (isUpscale) {
    byId("output-purpose").value = "auto";
    byId("background").value = "auto";
    byId("visible-text").value = "auto";
    byId("aspect-ratio").value = "auto";
    familySelect.value = "auto";
    populateSubfamilies(true);
    byId("quality").value = "auto";
  }
  updateSourcePolicy();
}

function updateSourcePolicy() {
  const previous = sourcePolicy.value === "previous_artifact";
  setHidden("source-upload-field", previous);
  setHidden("source-index-field", previous);
  byId("source-thumbnails").hidden = previous;
  byId("source-help").textContent = previous
    ? "Uses the latest completed image in this Composer conversation."
    : "Upload one or more images, then choose the one to use.";
  if (previous) {
    sourceImage.value = "";
    clearThumbnails();
    if (preservation.value === "identity") preservation.value = "subject";
  }
  const identityOption = preservation.querySelector('option[value="identity"]');
  identityOption.disabled = previous;
  renderSources();
}

function familyMatches(family, query) {
  if (!query) return true;
  const folded = query.toLocaleLowerCase();
  return family.label.toLocaleLowerCase().includes(folded) || family.subfamilies.some((entry) => entry.label.toLocaleLowerCase().includes(folded));
}

function populateFamilies() {
  if (!taxonomy) return;
  const query = byId("family-search").value.trim();
  const previous = familySelect.value || "auto";
  const matches = taxonomy.families.filter((family) => familyMatches(family, query));
  familySelect.replaceChildren(new Option("Auto", "auto"));
  matches.forEach((family) => familySelect.add(new Option(family.label, family.id)));
  familySelect.value = [...familySelect.options].some((option) => option.value === previous) ? previous : "auto";
  byId("family-results").textContent = `${matches.length} of ${taxonomy.families.length} families`;
  populateSubfamilies(false);
}

function selectedFamily() {
  return taxonomy?.families.find((family) => family.id === familySelect.value) || null;
}

function populateSubfamilies(reset) {
  if (!taxonomy) return;
  const family = selectedFamily();
  const previous = reset ? "auto" : (subfamilySelect.value || "auto");
  const query = byId("subfamily-search").value.trim().toLocaleLowerCase();
  const entries = family ? family.subfamilies.filter((entry) => !query || entry.label.toLocaleLowerCase().includes(query)) : [];
  subfamilySelect.replaceChildren(new Option("Auto", "auto"));
  entries.forEach((entry) => subfamilySelect.add(new Option(entry.label, entry.id)));
  subfamilySelect.value = [...subfamilySelect.options].some((option) => option.value === previous) ? previous : "auto";
  subfamilySelect.disabled = !family;
  byId("subfamily-search").disabled = !family;
  byId("subfamily-results").textContent = family ? `${entries.length} of ${family.subfamilies.length} subfamilies` : "Choose a family first";
}

function clearThumbnails() {
  thumbnailUrls.forEach((url) => URL.revokeObjectURL(url));
  thumbnailUrls = [];
  byId("source-thumbnails").replaceChildren();
  sourceIndex.replaceChildren();
}

function renderSources() {
  clearThumbnails();
  if (sourcePolicy.value !== "current_attachment") return;
  const files = [...sourceImage.files];
  files.forEach((file, index) => {
    const url = URL.createObjectURL(file);
    thumbnailUrls.push(url);
    const option = new Option(`Upload #${index + 1} — ${file.name}`, String(index + 1));
    sourceIndex.add(option);
    const card = document.createElement("button");
    card.type = "button";
    card.className = "source-card";
    card.setAttribute("aria-label", `Use upload ${index + 1}: ${file.name}`);
    card.addEventListener("click", () => { sourceIndex.value = String(index + 1); renderSelectedSource(); });
    const image = document.createElement("img");
    image.src = url;
    image.alt = "";
    const label = document.createElement("span");
    label.textContent = `#${index + 1} ${file.name}`;
    card.append(image, label);
    byId("source-thumbnails").append(card);
  });
  if (files.length) sourceIndex.value = "1";
  renderSelectedSource();
}

function renderSelectedSource() {
  const selected = sourceIndex.value;
  byId("source-thumbnails").querySelectorAll(".source-card").forEach((card, index) => {
    card.classList.toggle("selected", String(index + 1) === selected);
  });
}

function countValue() {
  if (operation.value !== "batch") return 1;
  return countPreset.value === "custom" ? Number(byId("custom-count").value) : Number(countPreset.value);
}

function fileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read a source image."));
    reader.onload = () => resolve(reader.result);
    reader.readAsDataURL(file);
  });
}

async function collectPayload() {
  const mode = currentMode();
  const text = freeText.value.trim();
  if (!text) throw new Error("Description is required.");
  if (mode === "auto") return { mode: "auto", free_text: text };

  const uiOperation = operation.value;
  const canonicalOperation = ["create", "batch"].includes(uiOperation) ? "generate" : uiOperation;
  const usesSource = ["transform", "upscale"].includes(uiOperation);
  const currentSource = usesSource && sourcePolicy.value === "current_attachment";
  const count = countValue();
  if (uiOperation === "batch" && (!Number.isInteger(count) || count < 2 || count > 10)) throw new Error("Batch count must be a whole number from 2 through 10.");

  const seedText = byId("seed").value.trim();
  const seed = seedText === "" ? "auto" : Number(seedText);
  if (seed !== "auto" && (!Number.isInteger(seed) || seed < 0 || seed > 2147483647)) throw new Error("Seed must be a whole number from 0 through 2,147,483,647.");

  const files = currentSource ? [...sourceImage.files] : [];
  if (currentSource && (files.length < 1 || files.length > 8)) throw new Error("Choose between 1 and 8 current source images.");
  if (preservation.value === "identity" && files.length !== 1) throw new Error("Recognizable person preservation requires exactly one current source image.");
  if (files.some((file) => !["image/png", "image/jpeg", "image/webp"].includes(file.type))) throw new Error("Sources must be PNG, JPEG, or WebP.");
  if (files.some((file) => file.size > 15 * 1024 * 1024)) throw new Error("Each source image must be 15 MB or smaller.");
  if (files.reduce((total, file) => total + file.size, 0) > 16 * 1024 * 1024) throw new Error("Source images are too large for one protected request.");
  const attachments = [];
  for (const file of files) attachments.push({ name: file.name, mime: file.type, contentString: await fileAsDataUrl(file) });

  return {
    mode: "advanced",
    free_text: text,
    operation: canonicalOperation,
    visual_family: uiOperation === "upscale" ? "auto" : familySelect.value,
    visual_subfamily: uiOperation === "upscale" ? "auto" : subfamilySelect.value,
    aspect_ratio: uiOperation === "upscale" ? "auto" : byId("aspect-ratio").value,
    count,
    quality: uiOperation === "upscale" ? "auto" : byId("quality").value,
    source_policy: usesSource ? sourcePolicy.value : "auto",
    source_index: currentSource ? Number(sourceIndex.value) : "none",
    preservation: uiOperation === "transform" ? preservation.value : "none",
    scale: uiOperation === "upscale" ? (byId("scale").value === "auto" ? "auto" : Number(byId("scale").value)) : "none",
    seed: ["batch", "upscale"].includes(uiOperation) ? "auto" : seed,
    output_purpose: uiOperation === "upscale" ? "auto" : byId("output-purpose").value,
    background: uiOperation === "upscale" ? "auto" : byId("background").value,
    visible_text: uiOperation === "upscale" ? "auto" : byId("visible-text").value,
    batch_relationship: uiOperation === "batch" ? byId("batch-relationship").value : "auto",
    source_instruction: uiOperation === "transform" ? byId("source-instruction").value.trim() : "",
    attachments,
  };
}

function showStatus(kicker, title, badge, content, workspaceUrl = null) {
  statusPanel.hidden = false;
  byId("status-kicker").textContent = kicker;
  byId("status-title").textContent = title;
  byId("status-badge").textContent = badge;
  resultOutput.replaceChildren();
  const lines = Array.isArray(content) ? content : [String(content)];
  lines.forEach((line) => {
    const paragraph = document.createElement("p");
    paragraph.textContent = line;
    resultOutput.append(paragraph);
  });
  if (workspaceUrl) {
    resultWorkspaceLink.href = workspaceUrl;
    resultWorkspaceLink.hidden = false;
  } else {
    resultWorkspaceLink.hidden = true;
  }
  statusPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function establishSession() {
  const response = await fetch("/composer/session", { credentials: "same-origin", cache: "no-store" });
  if (!response.ok) throw new Error("Could not establish the local Composer session.");
  const data = await response.json();
  csrf = data.csrf;
  previousArtifactAvailable = Boolean(data.previousArtifactAvailable);
  const option = byId("previous-artifact-option");
  option.disabled = !previousArtifactAvailable;
  option.textContent = previousArtifactAvailable ? "Most recent Composer image" : "Most recent Composer image — none yet";
}

async function request(path, payload) {
  if (!csrf) await establishSession();
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json", "X-AAG-CSRF": csrf },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({ error: { message: "The local service returned an invalid response." } }));
  if (!response.ok) throw new Error(data?.error?.message || `Request failed with HTTP ${response.status}.`);
  return data;
}

function loadRecents() {
  try {
    const value = JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]");
    return Array.isArray(value) ? value.slice(0, 5) : [];
  } catch {
    return [];
  }
}

function saveRecentStyle() {
  if (familySelect.value === "auto") return;
  const entry = { family: familySelect.value, subfamily: subfamilySelect.value };
  const recents = [entry, ...loadRecents().filter((item) => item.family !== entry.family || item.subfamily !== entry.subfamily)].slice(0, 5);
  localStorage.setItem(RECENTS_KEY, JSON.stringify(recents));
  renderRecents();
}

function renderRecents() {
  const container = byId("recent-style-buttons");
  container.replaceChildren();
  const valid = loadRecents().filter((recent) => taxonomy.families.some((family) => family.id === recent.family && (recent.subfamily === "auto" || family.subfamilies.some((entry) => entry.id === recent.subfamily))));
  valid.forEach((recent) => {
    const family = taxonomy.families.find((entry) => entry.id === recent.family);
    const subfamily = family.subfamilies.find((entry) => entry.id === recent.subfamily);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chip";
    button.textContent = `${family.label}${subfamily ? ` / ${subfamily.label}` : ""}`;
    button.addEventListener("click", () => {
      byId("family-search").value = "";
      populateFamilies();
      familySelect.value = recent.family;
      byId("subfamily-search").value = "";
      populateSubfamilies(true);
      subfamilySelect.value = recent.subfamily;
    });
    container.append(button);
  });
  byId("recent-styles").hidden = valid.length === 0;
}

async function preview() {
  previewButton.disabled = true;
  submitButton.disabled = true;
  showStatus("REQUEST PREVIEW", "Building friendly summary", "WORKING", "Checking the selected settings…");
  try {
    const payload = await collectPayload();
    const data = await request("/composer/preview", payload);
    showStatus("REQUEST PREVIEW", "Ready to send", "VALID", data.summary.lines);
  } catch (error) {
    showStatus("REQUEST PREVIEW", "Request needs attention", "ERROR", error.message);
  } finally {
    previewButton.disabled = false;
    submitButton.disabled = false;
  }
}

async function submit(event) {
  event.preventDefault();
  previewButton.disabled = true;
  submitButton.disabled = true;
  showStatus("AAG IMAGE", "Submitting through Image Generator", "RUNNING", "The request is being checked and processed. Image work can take several minutes.");
  try {
    const payload = await collectPayload();
    const data = await request("/composer/submit", payload);
    previousArtifactAvailable = Boolean(data.previousArtifactAvailable);
    const option = byId("previous-artifact-option");
    option.disabled = !previousArtifactAvailable;
    option.textContent = previousArtifactAvailable ? "Most recent Composer image" : "Most recent Composer image — none yet";
    if (payload.mode === "advanced") saveRecentStyle();
    const failed = Boolean(data.error) || data.type === "abort";
    showStatus("AAG IMAGE", failed ? "Image request did not complete" : "Image request completed", failed ? "ERROR" : "PASS", data.textResponse || data.error || "The request completed without a text response.", data.composerWorkspaceUrl || null);
  } catch (error) {
    showStatus("AAG IMAGE", "Submission failed safely", "ERROR", error.message);
  } finally {
    previewButton.disabled = false;
    submitButton.disabled = false;
  }
}

async function initialize() {
  const response = await fetch("/composer/visual-taxonomy.json", { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) throw new Error("The visual catalog could not be loaded.");
  taxonomy = await response.json();
  populateFamilies();
  populateSubfamilies(true);
  renderRecents();
  await establishSession();
  updateMode();
}

document.querySelectorAll('input[name="mode"]').forEach((input) => input.addEventListener("change", updateMode));
operation.addEventListener("change", updateOperation);
countPreset.addEventListener("change", updateOperation);
sourcePolicy.addEventListener("change", updateSourcePolicy);
sourceImage.addEventListener("change", renderSources);
sourceIndex.addEventListener("change", renderSelectedSource);
familySelect.addEventListener("change", () => { byId("subfamily-search").value = ""; populateSubfamilies(true); });
byId("family-search").addEventListener("input", populateFamilies);
byId("subfamily-search").addEventListener("input", () => populateSubfamilies(false));
[byId("family-search"), byId("subfamily-search")].forEach((input) => input.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { input.value = ""; input.dispatchEvent(new Event("input")); }
}));
freeText.addEventListener("input", () => { byId("char-count").textContent = String(freeText.value.length); });
previewButton.addEventListener("click", preview);
form.addEventListener("submit", submit);
window.addEventListener("beforeunload", clearThumbnails);

initialize().catch((error) => showStatus("LOCAL SERVICE", "Composer is unavailable", "ERROR", error.message));
