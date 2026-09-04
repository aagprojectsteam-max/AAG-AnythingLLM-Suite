"use strict";

const { AagError } = require("./errors");

const PORTRAIT = "portrait-b";
const SCENE = "scene-c";
const PROFILES = Object.freeze({
  "contract-b-portrait": Object.freeze({ contract: PORTRAIT, width: 896, height: 1152, orientation: "portrait" }),
  "scene-c-landscape": Object.freeze({ contract: SCENE, width: 1152, height: 896, orientation: "landscape" }),
  "scene-c-portrait": Object.freeze({ contract: SCENE, width: 896, height: 1152, orientation: "portrait" }),
});

const PORTRAIT_CUE = /(?:\bportrait\b|\bheadshot\b|\bprofile photo\b|\bpassport photo\b|\bstudio portrait\b|דיוקן|תמונת פרופיל)/iu;
const EXPLICIT_SCENE_CUE = /(?:\banother scene\b|\bdifferent scene\b|\bin a scene\b|\bshow .+ (?:in|at|on|beside|with)\b|בסצנה|במקום אחר)/iu;
const ACTION_CUE = /(?:\brid(?:e|es|ing)\b|\bwalk(?:s|ing)?\b|\bstand(?:s|ing)?\b|\bsit(?:s|ting)?\b|\brun(?:s|ning)?\b|\bdanc(?:e|es|ing)\b|\bcook(?:s|ing)?\b|\bdrive(?:s|ing)?\b|\bplay(?:s|ing)?\b|\bhold(?:s|ing)?\b|\bcarry(?:ing|ies)?\b|\bclimb(?:s|ing)?\b|\bswim(?:s|ming)?\b|רוכב|רוכבת|רוכבים|הולך|הולכת|עומד|עומדת|יושב|יושבת|רץ|רצה|רוקד|רוקדת)/iu;
const ENVIRONMENT_CUE = /(?:\bdesert\b|\bgarden\b|\brain\b|\bstreet\b|\bbeach\b|\bforest\b|\bmountain\b|\broom\b|\bkitchen\b|\bpark\b|\bcity\b|\bcar\b|\bcamel\b|\bhorse\b|\bbicycle\b|\bvehicle\b|מדבר|גמל|גן|גשם|רחוב|חוף|יער|הר|מכונית|סוס|אופניים)/iu;
const RELATION_CUE = /(?:\b(?:on|in|at|beside|next to|with|under|behind|inside|outside|through|across)\b|על גבי|ליד|בתוך|בחוץ|עם)/iu;
const UNSUPPORTED_SCENE_CUE = /(?:\bcrowd\b|\bgroup of people\b|\bmultiple primary people\b|\btwo people\b|\bthree people\b|\bback[- ]facing\b|\bface fully (?:hidden|covered)\b|קהל|שני אנשים|שלושה אנשים)/iu;
const UNVALIDATED_IDENTITY_STYLE_CUE = /(?:\b(?:illustrat(?:e|ed|ion|ive)|children(?:'s|s)?[- ]book|storybook|watercolou?r|gouache|comic(?:[- ]book)?|cartoon|anime|manga|oil[- ]paint(?:ed|ing)?|colou?red[- ]pencil|pencil[- ]drawing|line[- ]art|sketch|cel[- ]shad(?:ed|ing)|pixel[- ]art|vector[- ]art|claymation|papercut|paper[- ]cut|origami|low[- ]poly|3d[- ]render)\b|איור|מאויר|מאוירת|מצויר|מצוירת|ספר\s*ילדים|צבעי\s*מים|גואש|קומיקס|קריקטורה|אנימה|מנגה|ציור\s*שמן|עיפרון\s*צבעוני|רישום|סקיצה|אמנות\s*פיקסל|וקטורי|חימר|אוריגמי|תלת[־-]?ממד)/iu;

function identityRenderingConflict(task) {
  const authoritative = String(task?._aag_authoritative_request || task?.request || "");
  return UNVALIDATED_IDENTITY_STYLE_CUE.test(authoritative);
}

function semanticIdentityIntent(task) {
  const text = `${task.request || ""}\n${task.prompt || ""}`.trim();
  const portrait = PORTRAIT_CUE.test(text);
  const explicitScene = EXPLICIT_SCENE_CUE.test(text);
  const action = ACTION_CUE.test(text);
  const environment = ENVIRONMENT_CUE.test(text);
  const relation = RELATION_CUE.test(text);
  const sceneScore = Number(explicitScene) * 2 + Number(action) + Number(environment) + Number(relation);
  return {
    kind: !portrait && sceneScore >= 2 || explicitScene && (action || environment || relation) ? "scene" : "portrait",
    portrait, explicit_scene: explicitScene, action, environment, relation, scene_score: sceneScore,
    unsupported_scene: UNSUPPORTED_SCENE_CUE.test(text),
  };
}

function orientationFromAspect(aspect) {
  if (["landscape", "16:9", "4:3", "3:2"].includes(aspect)) return "landscape";
  if (["portrait", "9:16"].includes(aspect)) return "portrait";
  if (aspect === "1:1") return "square";
  return null;
}

function canonicalizeIdentity(task) {
  if (task.operation !== "transform" || task.preservation !== "identity") return task;
  if (identityRenderingConflict(task)) {
    throw new AagError(
      "IDENTITY_RENDERING_STYLE_UNSUPPORTED",
      "Person identity preservation currently supports validated realistic rendering only. For broader stylization, choose General visual reference."
    );
  }
  if (task.count !== 1) throw new AagError("IDENTITY_COUNT_UNSUPPORTED", "Human Identity accepts exactly one output per request.");
  if (!["auto", "current_attachment"].includes(task.source_policy)) throw new AagError("IDENTITY_SOURCE_POLICY_UNSUPPORTED", "Human Identity requires the trusted current attachment.");
  const intent = semanticIdentityIntent(task);
  const aspectOrientation = orientationFromAspect(task.aspect_ratio);
  const hasWidth = task.width !== undefined;
  const hasHeight = task.height !== undefined;
  if (hasWidth !== hasHeight) throw new AagError("IDENTITY_FRAMING_INCOMPLETE", "Identity framing must omit dimensions or provide both width and height as semantic hints.");
  const dimensionOrientation = hasWidth ? (task.width === task.height ? "square" : task.width > task.height ? "landscape" : "portrait") : null;
  if (aspectOrientation && dimensionOrientation && aspectOrientation !== dimensionOrientation) {
    throw new AagError("IDENTITY_FRAMING_CONFLICT", "The requested identity orientation conflicts with the supplied dimension hints.");
  }
  const requestedOrientation = aspectOrientation || dimensionOrientation;
  if (requestedOrientation === "square") throw new AagError("SCENE_IDENTITY_FRAMING_UNSUPPORTED", "Scene Identity v1 supports bounded portrait and landscape profiles, not square output.");
  if (intent.portrait && requestedOrientation === "landscape") {
    throw new AagError("IDENTITY_FRAMING_CONFLICT", "A portrait identity request cannot also require landscape framing; describe the intended scene or use portrait framing.");
  }
  const scene = intent.kind === "scene";
  if (scene && intent.unsupported_scene) throw new AagError("SCENE_IDENTITY_ENVELOPE_UNSUPPORTED", "Scene Identity v1 supports one primary person with an evaluable face, not crowds, multiple primary people, or a hidden/back-facing identity.");
  const profileName = scene
    ? (requestedOrientation === "portrait" ? "scene-c-portrait" : "scene-c-landscape")
    : "contract-b-portrait";
  const profile = PROFILES[profileName];
  task._aag_identity_contract = profile.contract;
  task._aag_identity_profile = profileName;
  task._aag_identity_intent = intent;
  task._aag_requested_framing = {
    aspect_ratio: task.aspect_ratio,
    width: hasWidth ? task.width : null,
    height: hasHeight ? task.height : null,
    quality: task.quality,
  };
  task._aag_internal_width = profile.width;
  task._aag_internal_height = profile.height;
  task._aag_normalized_orientation = profile.orientation;
  delete task.width;
  delete task.height;
  task.aspect_ratio = "auto";
  task.quality = "auto";
  task.source_policy = "current_attachment";
  return task;
}

module.exports = {
  PORTRAIT, SCENE, PROFILES, UNVALIDATED_IDENTITY_STYLE_CUE, identityRenderingConflict,
  semanticIdentityIntent, orientationFromAspect, canonicalizeIdentity,
};
