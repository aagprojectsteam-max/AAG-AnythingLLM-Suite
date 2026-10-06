// The array order is the native sidebar order. Identity keys remain opaque;
// numbering is derived again whenever the list changes.
function threadOrdinal(threads, slug) {
  if (!slug) return null;
  const index = threads.findIndex(thread => thread.slug === slug);
  return index < 0 ? null : index + 1;
}
function threadLabel(ordinal, name = "") {
  if (!ordinal) return name;
  return `Thread ${ordinal}${name && name !== "Thread" ? ` · ${name}` : ""}`;
}
function createThreadOrderStore() {
  const lists = new Map(), listeners = new Set(), empty = Object.freeze([]);
  return {
    get: workspace => lists.get(workspace) || empty,
    known: workspace => lists.has(workspace),
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    publish(workspace, threads) {
      if (!workspace || !Array.isArray(threads)) return;
      const next = threads.map(({id, slug, name}) => ({id, slug, name}));
      if (lists.has(workspace) && JSON.stringify(next) === JSON.stringify(lists.get(workspace))) return;
      lists.set(workspace, next); for (const listener of listeners) listener();
    },
    remove(workspace, slugs) {
      if (lists.has(workspace)) this.publish(workspace, this.get(workspace).filter(t => !slugs.includes(t.slug)));
    },
  };
}
const threadOrder = createThreadOrderStore();
module.exports = { threadOrdinal, threadLabel, createThreadOrderStore, threadOrder };
