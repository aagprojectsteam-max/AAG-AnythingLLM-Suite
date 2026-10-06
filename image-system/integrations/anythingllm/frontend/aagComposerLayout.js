// Presentation preferences only. No prompt, identity, job or artifact data.
const COMPOSER_LAYOUT_KEY = "aag.image-composer.layout.v1";
const PANEL_MIN = 320, PANEL_DEFAULT = 384, PANEL_MAX = 560;
const CHAT_MIN = 480, PANEL_GAP = 12;
const DEFAULT_LAYOUT = Object.freeze({ mode: "popup", open: true, side: "right", width: PANEL_DEFAULT });
function normalizeLayout(value) {
  const v = value && typeof value === "object" ? value : {};
  return { mode: v.mode === "panel" ? "panel" : "popup", open: v.open !== false,
    side: v.side === "left" ? "left" : "right",
    width: Math.round(Math.max(PANEL_MIN, Math.min(PANEL_MAX,
      typeof v.width === "number" && Number.isFinite(v.width) ? v.width : PANEL_DEFAULT))) };
}
function readLayout(storage) {
  try { return normalizeLayout(JSON.parse(storage.getItem(COMPOSER_LAYOUT_KEY))); }
  catch { return { ...DEFAULT_LAYOUT }; }
}
function saveLayout(storage, layout) {
  try { storage.setItem(COMPOSER_LAYOUT_KEY, JSON.stringify(normalizeLayout(layout))); }
  catch { /* Restricted storage must not prevent layout changes. */ }
}
function panelGeometry(layout, available) {
  const maximum = Math.min(PANEL_MAX, Math.floor(available - CHAT_MIN - PANEL_GAP));
  return { fits: maximum >= PANEL_MIN, maximum: Math.max(PANEL_MIN, maximum),
    width: Math.min(layout.width, Math.max(PANEL_MIN, maximum)) };
}
function panelKeyboardWidth(key, width, maximum, side, shift = false) {
  if (key === "Home") return PANEL_MIN;
  if (key === "End") return maximum;
  if (key === "Enter") return Math.min(PANEL_DEFAULT, maximum);
  if (key !== "ArrowLeft" && key !== "ArrowRight") return null;
  const direction = (key === "ArrowRight" ? 1 : -1) * (side === "left" ? 1 : -1);
  return Math.max(PANEL_MIN, Math.min(maximum, width + direction * (shift ? 40 : 10)));
}
module.exports = { COMPOSER_LAYOUT_KEY, PANEL_MIN, PANEL_DEFAULT, PANEL_MAX, CHAT_MIN, PANEL_GAP, DEFAULT_LAYOUT, normalizeLayout, readLayout, saveLayout, panelGeometry, panelKeyboardWidth };
