import { useEffect, useState } from "react";

// Bounded, page-memory UI drafts keyed by stable slugs. Never persisted job
// state. Existing native prompt storage and Expert preferences stay authoritative.
const drafts = new Map();
export default function useAagComposerDraft(workspaceSlug, threadSlug, field, initial) {
  const key = JSON.stringify([workspaceSlug || null, threadSlug || null]);
  const [value, setValue] = useState(() => {
    const saved = drafts.get(key);
    return saved && Object.prototype.hasOwnProperty.call(saved, field)
      ? saved[field] : typeof initial === "function" ? initial() : initial;
  });
  // Native ChatContainer is keyed by the stable workspace/thread pair. Docking
  // never changes that key. Returning to a scope restores its UI selections.
  useEffect(() => {
    const saved = drafts.get(key) || {};
    drafts.delete(key); drafts.set(key, { ...saved, [field]: value });
    while (drafts.size > 24) drafts.delete(drafts.keys().next().value);
  }, [key, field, value]);
  return [value, setValue];
}
