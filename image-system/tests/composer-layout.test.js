"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { COMPOSER_LAYOUT_KEY, DEFAULT_LAYOUT, normalizeLayout, readLayout, saveLayout, panelGeometry, panelKeyboardWidth } = require("../integrations/anythingllm/frontend/aagComposerLayout");
test("existing profiles default to Popup; malformed or inaccessible preferences stay usable", () => {
  for (const value of [null, "bad json", 'null', '[]', '{}']) assert.deepEqual(readLayout({ getItem: () => value }), DEFAULT_LAYOUT);
  assert.deepEqual(readLayout({ getItem() { throw Error("denied"); } }), DEFAULT_LAYOUT);
  assert.doesNotThrow(() => saveLayout({ setItem() { throw Error("quota"); } }, DEFAULT_LAYOUT));
});
test("all presentation preferences survive a new reader while transient state is excluded", () => {
  const data = new Map(), storage = { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value) };
  const selected = { mode: "panel", open: false, side: "left", width: 472 };
  saveLayout(storage, { ...selected, job: "private", workspaceId: 999, prompt: "private" });
  assert.deepEqual(readLayout(storage), selected);
  assert.deepEqual(JSON.parse(data.get(COMPOSER_LAYOUT_KEY)), selected);
});
test("normalization bounds only the preference, never a temporary viewport restriction", () => {
  assert.equal(normalizeLayout({ width: -1 }).width, 320);
  assert.equal(normalizeLayout({ width: 9999 }).width, 560);
  assert.equal(normalizeLayout({ width: NaN }).width, 384);
  const selected = normalizeLayout({ mode: "panel", side: "left", width: 550 });
  assert.deepEqual(panelGeometry(selected, 900), { fits: true, maximum: 408, width: 408 });
  assert.equal(panelGeometry(selected, 811).fits, false);
  assert.equal(panelGeometry(selected, 812).fits, true);
  assert.equal(panelGeometry(selected, 1200).width, 550);
  assert.equal(selected.width, 550);
});
test("every fitting layout reserves 480 pixels of chat plus a 12 pixel separation", () => {
  for (let available = 0; available < 2200; available += 7) {
    for (const width of [320, 384, 560]) {
      const result = panelGeometry({ width }, available);
      if (result.fits) assert.ok(available - result.width - 12 >= 480);
    }
  }
});
test("keyboard arrows move the physical inner edge on either side, independently of bidi", () => {
  assert.equal(panelKeyboardWidth("ArrowRight", 384, 560, "left"), 394);
  assert.equal(panelKeyboardWidth("ArrowRight", 384, 560, "right"), 374);
  assert.equal(panelKeyboardWidth("ArrowLeft", 384, 560, "right", true), 424);
  assert.equal(panelKeyboardWidth("Home", 500, 550, "left"), 320);
  assert.equal(panelKeyboardWidth("End", 400, 500, "right"), 500);
  assert.equal(panelKeyboardWidth("Enter", 500, 350, "right"), 350);
  assert.equal(panelKeyboardWidth("Tab", 400, 560, "left"), null);
});
