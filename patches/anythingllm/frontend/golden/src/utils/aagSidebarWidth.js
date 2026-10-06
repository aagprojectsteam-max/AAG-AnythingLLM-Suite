const SIDEBAR_WIDTH_KEY = "aag.sidebar.width.v1";
const DEFAULT_WIDTH = 292;
const MIN_WIDTH = 260;
const MAX_WIDTH = 520;

function sidebarMaximum(viewport = 1440) {
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.floor(viewport - 480)));
}
function clampSidebarWidth(value, maximum = MAX_WIDTH) {
  const number = typeof value === "number" ? value : Number(value);
  return Math.round(Math.max(MIN_WIDTH, Math.min(maximum,
    value === null || value === "" || !Number.isFinite(number) ? DEFAULT_WIDTH : number)));
}
function readSidebarWidth(storage) {
  try { return clampSidebarWidth(storage.getItem(SIDEBAR_WIDTH_KEY)); }
  catch { return DEFAULT_WIDTH; }
}
function saveSidebarWidth(storage, width) {
  try { storage.setItem(SIDEBAR_WIDTH_KEY, String(clampSidebarWidth(width))); }
  catch { /* Resizing still works when browser storage is unavailable. */ }
}
function sidebarKeyboardWidth(key, width, maximum, shift = false) {
  if (key === "Home") return MIN_WIDTH;
  if (key === "End") return maximum;
  if (key === "Enter") return clampSidebarWidth(DEFAULT_WIDTH, maximum);
  if (key !== "ArrowLeft" && key !== "ArrowRight") return null;
  return clampSidebarWidth(width + (key === "ArrowRight" ? 1 : -1) * (shift ? 40 : 10), maximum);
}
export { SIDEBAR_WIDTH_KEY, DEFAULT_WIDTH, MIN_WIDTH, MAX_WIDTH, sidebarMaximum, clampSidebarWidth, readSidebarWidth, saveSidebarWidth, sidebarKeyboardWidth };
