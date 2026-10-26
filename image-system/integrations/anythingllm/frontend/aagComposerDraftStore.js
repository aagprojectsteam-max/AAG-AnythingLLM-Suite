// Page-memory presentation drafts only. A lease prevents an old form from
// recreating its temporary draft after the first-message handoff.
function createComposerDraftStore(limit = 24) {
  const drafts = new Map();
  function key(workspace, thread, temporary = null) {
    return JSON.stringify([workspace || null, thread || null, thread ? null : temporary]);
  }
  function acquire(workspace, thread, temporary = null) {
    const id = key(workspace, thread, temporary);
    let draft = drafts.get(id);
    if (!draft) draft = { id, workspace, thread, values: {}, retired: false };
    drafts.delete(id); drafts.set(id, draft);
    while (drafts.size > limit) {
      const oldest = drafts.keys().next().value;
      drafts.get(oldest).retired = true; drafts.delete(oldest);
    }
    return draft;
  }
  function write(draft, field, next) {
    if (draft.retired || drafts.get(draft.id) !== draft) return false;
    draft.values[field] = typeof next === 'function' ? next(draft.values[field]) : next;
    return true;
  }
  function handoff(workspace, thread, temporary = null) {
    if (!workspace || typeof thread !== 'string' || !thread) return false;
    const sourceKey = key(workspace, null, temporary), targetKey = key(workspace, thread);
    const source = drafts.get(sourceKey);
    if (!source || source.retired) return false;
    // An existing persisted draft always wins. Consume the temporary draft
    // either way so a second transition cannot copy it to another thread.
    if (!drafts.has(targetKey)) {
      const target = acquire(workspace, thread);
      target.values = { ...source.values };
    }
    source.retired = true; drafts.delete(sourceKey);
    return true;
  }
  return { acquire, write, handoff };
}
const composerDrafts = createComposerDraftStore();
// Pending native submission stays in page memory, including File/data URLs.
// The native sessionStorage fallback contains only text and a presence flag.
const aagNativeSubmission = { current: null };
module.exports = { createComposerDraftStore, composerDrafts, aagNativeSubmission };
