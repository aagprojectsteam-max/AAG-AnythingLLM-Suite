import { createContext, useCallback, useContext, useMemo, useReducer } from "react";
import { composerDrafts } from "@/utils/aagComposerDraftStore";

// Home has a temporary presentation scope, distinct from a real default
// conversation. It is never passed to APIs, sockets or artifact metadata.
const DraftScope = createContext(null);
export const AagComposerDraftScope = DraftScope.Provider;
export default function useAagComposerDraft(workspaceSlug, threadSlug, field, initial) {
  const temporary = useContext(DraftScope);
  const draft = useMemo(() => composerDrafts.acquire(workspaceSlug, threadSlug, temporary),
    [workspaceSlug, threadSlug, temporary]);
  if (!Object.prototype.hasOwnProperty.call(draft.values, field)) {
    composerDrafts.write(draft, field, typeof initial === "function" ? initial() : initial);
  }
  const [, render] = useReducer(count => count + 1, 0);
  const setValue = useCallback(next => {
    // Save synchronously with the selection, before a submit/navigation can
    // unmount this form. No passive effect can overwrite a migrated draft.
    if (composerDrafts.write(draft, field, next)) render();
  }, [draft, field]);
  return [draft.values[field], setValue];
}
