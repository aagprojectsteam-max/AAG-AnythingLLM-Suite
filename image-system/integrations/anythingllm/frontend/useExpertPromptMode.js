import { useCallback, useEffect, useState } from "react";

export const EXPERT_PROMPT_STORAGE_PREFIX = "aag_expert_prompt_enabled:";
export const EXPERT_PROMPT_CHANGE_EVENT = "aag:expert-prompt-mode-change";

export function validExpertPromptWorkspaceSlug(workspaceSlug) {
  return typeof workspaceSlug === "string" && workspaceSlug.length > 0 &&
    workspaceSlug.length <= 128 && /^[A-Za-z0-9_-]+$/.test(workspaceSlug);
}
export function expertPromptStorageKey(workspaceSlug) {
  return validExpertPromptWorkspaceSlug(workspaceSlug) ? `${EXPERT_PROMPT_STORAGE_PREFIX}${workspaceSlug}` : null;
}
export function defaultExpertPromptEnabled(workspaceSlug, mode = "advanced") {
  return validExpertPromptWorkspaceSlug(workspaceSlug) && mode === "advanced";
}
export function readExpertPromptOverride(workspaceSlug, storage = localStorage) {
  const key = expertPromptStorageKey(workspaceSlug);
  if (!key) return null;
  try {
    const stored = storage.getItem(key);
    if (stored === "true") return true;
    if (stored === "false") return false;
  } catch { /* Keep the mode default if this browser cannot read storage. */ }
  return null;
}
export function readExpertPromptEnabled(workspaceSlug, storage = localStorage, mode = "advanced") {
  if (!defaultExpertPromptEnabled(workspaceSlug, mode)) return false;
  return readExpertPromptOverride(workspaceSlug, storage) ?? true;
}
export function writeExpertPromptEnabled(workspaceSlug, enabled, storage = localStorage) {
  const key = expertPromptStorageKey(workspaceSlug);
  if (!key) return false;
  try { storage.setItem(key, enabled ? "true" : "false"); return true; }
  catch { return false; }
}

export default function useExpertPromptMode(workspaceSlug, mode = "advanced") {
  // Null is a default, true/false are explicit overrides. Keep the existing
  // browser-origin + workspace key, including legacy boolean preferences.
  const [preference, setPreference] = useState(() => ({workspaceSlug, value: readExpertPromptOverride(workspaceSlug)}));
  const override = preference.workspaceSlug === workspaceSlug
    ? preference.value : readExpertPromptOverride(workspaceSlug);
  const enabled = defaultExpertPromptEnabled(workspaceSlug, mode) && (override ?? true);
  useEffect(() => {
    setPreference({workspaceSlug, value: readExpertPromptOverride(workspaceSlug)});
  }, [workspaceSlug]);
  useEffect(() => {
    const sync = event => {
      if (event.type === "storage") {
        if (event.key !== null && event.key !== expertPromptStorageKey(workspaceSlug)) return;
        setPreference({workspaceSlug, value: readExpertPromptOverride(workspaceSlug)});
      } else if (event.detail?.workspaceSlug === workspaceSlug && typeof event.detail.enabled === "boolean") {
        // Same-window explicit choice survives even when storage is denied.
        setPreference({workspaceSlug, value: event.detail.enabled});
      }
    };
    window.addEventListener(EXPERT_PROMPT_CHANGE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(EXPERT_PROMPT_CHANGE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, [workspaceSlug]);
  const setEnabled = useCallback(nextEnabled => {
    if (!expertPromptStorageKey(workspaceSlug)) return;
    const next = Boolean(nextEnabled);
    writeExpertPromptEnabled(workspaceSlug, next);
    setPreference({workspaceSlug, value: next});
    window.dispatchEvent(new CustomEvent(EXPERT_PROMPT_CHANGE_EVENT, {detail: {workspaceSlug, enabled: next}}));
  }, [workspaceSlug]);
  const toggle = useCallback(() => setEnabled(!enabled), [enabled, setEnabled]);
  return {enabled, setEnabled, toggle, override};
}
