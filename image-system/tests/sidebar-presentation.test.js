"use strict";
const test = require("node:test"), assert = require("node:assert/strict");
const { SIDEBAR_WIDTH_KEY, DEFAULT_WIDTH, sidebarMaximum, clampSidebarWidth, readSidebarWidth, saveSidebarWidth, sidebarKeyboardWidth } = require("../integrations/anythingllm/frontend/aagSidebarWidth");
const { createThreadOrderStore, threadOrdinal, threadLabel } = require("../integrations/anythingllm/frontend/aagThreadOrder");

test("new profiles retain the current width; malformed or blocked storage remains usable", () => {
  for (const value of [null, "", "NaN", "Infinity", "{broken}"]) {
    assert.equal(readSidebarWidth({ getItem: () => value }), DEFAULT_WIDTH);
  }
  assert.equal(readSidebarWidth({ getItem() { throw Error("denied"); } }), DEFAULT_WIDTH);
  assert.doesNotThrow(() => saveSidebarWidth({ setItem() { throw Error("quota"); } }, 400));
});
test("a chosen width survives a fresh consumer and respects both bounds", () => {
  const data = new Map(), storage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) };
  saveSidebarWidth(storage, 417.4);
  assert.equal(data.get(SIDEBAR_WIDTH_KEY), "417");
  assert.equal(readSidebarWidth({ ...storage }), 417);
  saveSidebarWidth(storage, 9999); assert.equal(readSidebarWidth(storage), 520);
  saveSidebarWidth(storage, -50); assert.equal(readSidebarWidth(storage), 260);
});
test("viewport pressure leaves space for chat without destroying the saved wider preference", () => {
  const storage = { getItem: () => "500" };
  assert.equal(clampSidebarWidth(readSidebarWidth(storage), sidebarMaximum(800)), 320);
  assert.equal(clampSidebarWidth(readSidebarWidth(storage), sidebarMaximum(1440)), 500);
  assert.equal(sidebarMaximum(600), 260);
});
test("keyboard resizing uses physical arrow direction, bounds and reset", () => {
  assert.equal(sidebarKeyboardWidth("ArrowLeft", 300, 520), 290);
  assert.equal(sidebarKeyboardWidth("ArrowRight", 300, 520, true), 340);
  assert.equal(sidebarKeyboardWidth("ArrowLeft", 260, 520), 260);
  assert.equal(sidebarKeyboardWidth("End", 300, 400), 400);
  assert.equal(sidebarKeyboardWidth("Home", 500, 520), 260);
  assert.equal(sidebarKeyboardWidth("Enter", 500, 520), 292);
  assert.equal(sidebarKeyboardWidth("Tab", 300, 520), null);
});
test("English, Hebrew and mixed titles preserve full text as the visible ordinal changes", () => {
  const store = createThreadOrderStore();
  const rows = [
    { id: 807, slug: "first", name: "A very long English title with details" },
    { id: 923, slug: "second", name: "כותרת ארוכה בעברית עם פרטים נוספים" },
    { id: 1066, slug: "third", name: "תכנון API v2 — October 2026" },
  ];
  store.publish("workspace", rows);
  for (const [index, row] of rows.entries()) assert.equal(threadLabel(threadOrdinal(store.get("workspace"), row.slug), row.name), `${index + 1} - ${row.name}`);
  store.remove("workspace", ["first"]);
  assert.equal(threadLabel(threadOrdinal(store.get("workspace"), "second"), rows[1].name), `1 - ${rows[1].name}`);
  assert.deepEqual(store.get("workspace"), rows.slice(1));
  assert.deepEqual(rows.map(row => row.id), [807, 923, 1066]);
});

test("numbering is independent per workspace and follows reordered rows without mutating identity or names", () => {
  const store = createThreadOrderStore();
  const first = [{id:901, slug:"a", name:"Design"}, {id:2031, slug:"b", name:"תכנון"}];
  const second = [{id:9001, slug:"c", name:"Thread safety notes"}, {id:11900, slug:"d", name:"Review"}];
  const snapshot = JSON.stringify([first, second]);
  store.publish("workspace-a", first); store.publish("workspace-b", second);
  assert.equal(threadOrdinal(store.get("workspace-a"), "b"), 2);
  assert.equal(threadOrdinal(store.get("workspace-b"), "c"), 1);
  store.publish("workspace-a", [first[1], first[0]]);
  assert.equal(threadLabel(threadOrdinal(store.get("workspace-a"), "b"), first[1].name), "1 - תכנון");
  assert.equal(threadLabel(threadOrdinal(store.get("workspace-b"), "c"), second[0].name), "1 - Thread safety notes");
  store.remove("workspace-a", ["b"]);
  assert.equal(threadOrdinal(store.get("workspace-a"), "a"), 1);
  assert.equal(threadOrdinal(store.get("workspace-b"), "d"), 2);
  assert.equal(JSON.stringify([first, second]), snapshot);
});
