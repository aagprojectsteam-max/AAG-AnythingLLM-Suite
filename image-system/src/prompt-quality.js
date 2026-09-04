"use strict";

const crypto = require("crypto");
const { AagError } = require("./errors");

const CONTRACT_ID = "aag.prompt-quality.v1";
const MAX_PROMPT_CHARS = 1800;
const MIN_PRODUCTION_WORDS = 45;

const MODE_PATTERNS = Object.freeze({
  "coloring-page": /(?:\bcolou?ring[- ]page\b|\bline[- ]art\b|דף צביעה)/iu,
  photorealistic: /(?:\bphotoreal(?:istic|ism)?\b|\brealistic photograph\b|\bphotographic\b|\brealistic\b|צילום|פוטוריאליסטי(?:ת)?)/iu,
  illustration: /(?:\billustrat(?:ion|ed)\b|\bcartoon\b|\bhand[- ]drawn\b|\bdrawing\b|מצויר(?:ת|ים|ות)?|מצוייר(?:ת|ים|ות)?|איור)/iu,
});

const DIMENSION_PATTERNS = Object.freeze({
  requested_action: /(?:\bactive(?:ly)?\b|\baction\b|\bengaged\b|\binteract(?:ion|ing)?\b|\bpose\b|\bstance\b|\brid(?:e|ing)\b|\bbox(?:ing)?\b|\bhold(?:ing)?\b|\brun(?:ning)?\b|\bwalk(?:ing)?\b|\bfly(?:ing)?\b|רוכב|מחזיק|אגרוף|פעולה)/iu,
  subject_object_relationships: /(?:\bone another\b|\beach other\b|\bagainst\b|\bbetween\b|\bfacing\b|\bbeside\b|\bholding\b|\briding\b|\bwearing\b|\binteraction\b|\bcontact\b|\binside\b|\bnext to\b|זה מול זה|אוחז|רוכב)/iu,
  environment: /(?:\bsetting\b|\bbackground\b|\bforeground\b|\benvironment\b|\bsurroundings\b|\blocation\b|\binside\b|\boutside\b|\barena\b|\bring\b|\broom\b|\bstreet\b|\blandscape\b|\bdesert\b|\bforest\b|\bgarden\b|\bstudio\b|\bbeach\b|\bcity\b|\bcountryside\b|רקע|סביבה|זירה|מדבר|יער|גינה)/iu,
  composition: /(?:\bcomposition\b|\bframing\b|\bfocal point\b|\bstrong focus\b|\bsubject separation\b|\bclearly visible\b|\bshow both\b|\bforeground\b|\bbackground\b|\bview\b|\bangle\b|\bmedium[- ]wide\b|\bclose[- ]up\b|\bfull (?:figure|body)\b|קומפוזיציה|מסגור)/iu,
  physical_spatial_coherence: /(?:\bcoherent\b|\bbelievable\b|\bbalance\b|\bstance\b|\bposes?\b|\banatom(?:y|ical)\b|\bperspective\b|\bphysical contact\b|\bspatial\b|\bproportions?\b|אנטומיה|פרספקטיבה)/iu,
  sufficient_visual_detail: /(?:\bpolished\b|\brefined\b|\bdetailed\b|\bcrisp\b|\bclean\b|\bprofessional\b|\bexpressive\b|\bvisual detail\b|\bsubject readability\b|מלוטש|מפורט)/iu,
  lighting: /(?:\blighting\b|\blit\b|\bshadows?\b|\bcontrast\b|\bhighlights?\b|\bdaylight\b|\bsunlight\b|תאורה|צללים)/iu,
});

const STOPWORDS = new Set(["a", "an", "and", "the", "of", "to", "in", "on", "at", "with", "for", "from", "this", "that", "image", "picture", "make", "create", "generate", "please"]);

function words(value) {
  return String(value || "").match(/[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu) || [];
}

function mode(value) {
  const text = String(value || "");
  for (const [name, pattern] of Object.entries(MODE_PATTERNS)) if (pattern.test(text)) return name;
  return "unspecified";
}

function quantities(value) {
  // Aspect ratios and pixel dimensions are governed structured choices, not
  // subject counts. Do not require the workspace LLM to duplicate them in the
  // creative prompt after it selected the matching public-schema field.
  const text = String(value || "").toLowerCase()
    .replace(/\b\d+\s*:\s*\d+\b/gu, " ")
    .replace(/\b\d+\s*[x×]\s*\d+(?:\s*(?:px|pixels?))?\b/giu, " ");
  const found = new Set(text.match(/\b\d+\b/gu) || []);
  for (const [number, pattern] of [
    ["1", /(?:\bexactly one\b|\bone\b|\bsingle\b|אחד|אחת)/iu],
    ["2", /(?:\bexactly two\b|\btwo\b|שניים|שתיים|שני|שתי)/iu],
    ["3", /(?:\bexactly three\b|\bthree\b|שלושה|שלוש)/iu],
    ["4", /(?:\bexactly four\b|\bfour\b|ארבעה|ארבע)/iu],
  ]) if (pattern.test(text)) found.add(number);
  return [...found].sort();
}

function script(value) {
  const text = String(value || "");
  if (/\p{Script=Hebrew}/u.test(text)) return "hebrew";
  if (/\p{Script=Latin}/u.test(text)) return "latin";
  return "other";
}

function contentTokens(value) {
  return words(String(value || "").toLowerCase()).filter(token => token.length > 2 && !STOPWORDS.has(token));
}

function semanticFidelity(authoritative, candidate, options = {}) {
  const requestedMode = mode(authoritative);
  const candidateMode = mode(candidate);
  const requiredQuantities = quantities(authoritative);
  const candidateQuantities = new Set(quantities(candidate));
  const missingQuantities = requiredQuantities.filter(value => !candidateQuantities.has(value));
  const stylePreserved = requestedMode === "unspecified" || candidateMode === requestedMode;
  const identityRequired = Boolean(options.identityScene) || /(?:\bsame (?:person|face|identity|girl|boy|woman|man|child)\b|אות(?:ו|ה) (?:אדם|איש|אישה|ילד|ילדה|תינוק|תינוקת)|אותם פנים)/iu.test(authoritative);
  const identityPreserved = !identityRequired || /(?:\bsame (?:recognizable )?(?:person|face|identity|girl|boy|woman|man|child)\b|\bidentity\b|אות(?:ו|ה)|אותם פנים)/iu.test(candidate);
  const sourceScript = script(authoritative);
  const candidateScript = script(candidate);
  let lexicalPreserved = true;
  let lexicalStatus = "CROSS_LANGUAGE_BOUNDED";
  if (sourceScript === candidateScript && sourceScript !== "other") {
    const required = contentTokens(authoritative);
    const present = new Set(contentTokens(candidate));
    const hits = required.filter(token => present.has(token)).length;
    lexicalPreserved = required.length < 3 || hits >= Math.min(2, Math.ceil(required.length * 0.3));
    lexicalStatus = lexicalPreserved ? "PASS" : "FAIL";
  }
  const checks = {
    authoritative_semantics_bounded: lexicalPreserved,
    quantities_preserved: missingQuantities.length === 0,
    requested_style_preserved: stylePreserved,
    identity_requirement_preserved: identityPreserved,
  };
  return {
    status: Object.values(checks).every(Boolean) ? "PASS" : "FAIL",
    lexical_status: lexicalStatus,
    checks,
    missing_quantities: missingQuantities,
  };
}

function qualityDimensions(prompt, authoritative, options = {}) {
  const count = words(prompt).length;
  const checks = {
    primary_subject: contentTokens(prompt).length >= 2,
    exact_important_counts: quantities(authoritative).every(value => quantities(prompt).includes(value)),
    requested_action: !DIMENSION_PATTERNS.requested_action.test(authoritative) || DIMENSION_PATTERNS.requested_action.test(prompt),
    subject_object_relationships: DIMENSION_PATTERNS.subject_object_relationships.test(prompt),
    environment: DIMENSION_PATTERNS.environment.test(prompt),
    composition: DIMENSION_PATTERNS.composition.test(prompt),
    visual_style: mode(prompt) !== "unspecified",
    physical_spatial_coherence: DIMENSION_PATTERNS.physical_spatial_coherence.test(prompt),
    sufficient_visual_detail: count >= MIN_PRODUCTION_WORDS && DIMENSION_PATTERNS.sufficient_visual_detail.test(prompt),
  };
  if (options.identityScene) checks.identity_preservation = /(?:\bsame (?:recognizable )?(?:person|face|identity|girl|boy|woman|man|child)\b|\bidentity\b|אות(?:ו|ה)|אותם פנים)/iu.test(prompt);
  const missing = Object.entries(checks).filter(([, present]) => !present).map(([name]) => name);
  const advisoryMissing = DIMENSION_PATTERNS.lighting.test(prompt) ? [] : ["lighting_if_applicable"];
  return { status: missing.length ? "UNDER_SPECIFIED" : "PRODUCTION_READY", checks, missing_dimensions: missing, advisory_missing_dimensions: advisoryMissing, word_count: count };
}

function validate({ authoritative, proposal, identityScene = false } = {}) {
  const source = String(authoritative || "").trim();
  const prompt = String(proposal || "").trim();
  if (!source) throw new AagError("PROMPT_AUTHORITATIVE_INPUT_REQUIRED", "The trusted original user request is required.");
  if (!prompt) throw new AagError("PROMPT_REQUIRED", "The workspace language model must author a production-ready image prompt.");
  if (prompt.length > MAX_PROMPT_CHARS || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(prompt)) throw new AagError("PROMPT_INVALID", "The workspace language model prompt is outside safe text bounds.");
  const fidelity = semanticFidelity(source, prompt, { identityScene });
  if (fidelity.status !== "PASS") throw new AagError("PROMPT_SEMANTIC_DRIFT", "The workspace language model prompt does not preserve the authoritative user request.", false, JSON.stringify(fidelity));
  const quality = qualityDimensions(prompt, source, { identityScene });
  return {
    prompt,
    contract: {
      id: CONTRACT_ID,
      backend_role: "validation-normalization-only",
      author: "workspace-llm",
      strategy: "preserved-llm-authored-prompt",
      status: quality.status,
      fidelity_status: fidelity.status,
      fidelity_checks: fidelity.checks,
      lexical_status: fidelity.lexical_status,
      structure_status: quality.status === "PRODUCTION_READY" ? "PASS" : "UNDER_SPECIFIED",
      structure_checks: quality.checks,
      missing_dimensions: quality.missing_dimensions,
      advisory_missing_dimensions: quality.advisory_missing_dimensions,
      word_count: quality.word_count,
      final_prompt_sha256: crypto.createHash("sha256").update(prompt).digest("hex"),
    },
  };
}

function applyToTask(task) {
  if (!task || task.operation === "upscale") return task;
  if (task.operation === "transform" && task.preservation === "identity" && task._aag_identity_contract !== "scene-c") return task;
  const result = validate({ authoritative: task._aag_authoritative_request || task.request, proposal: task.prompt, identityScene: task.operation === "transform" && task.preservation === "identity" });
  task._aag_prompt_contract = result.contract;
  task.prompt = result.prompt;
  return task;
}

module.exports = { CONTRACT_ID, MAX_PROMPT_CHARS, MIN_PRODUCTION_WORDS, MODE_PATTERNS, DIMENSION_PATTERNS, words, mode, quantities, semanticFidelity, qualityDimensions, validate, applyToTask };
