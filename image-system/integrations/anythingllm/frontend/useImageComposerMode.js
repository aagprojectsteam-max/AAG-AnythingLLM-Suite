import { useCallback, useEffect, useState } from "react";

export const IMAGE_COMPOSER_STORAGE_PREFIX = "aag_image_composer_enabled:";
export const IMAGE_COMPOSER_CHANGE_EVENT = "aag:image-composer-mode-change";

export function validImageComposerWorkspaceSlug(workspaceSlug) {
  return (
    typeof workspaceSlug === "string" &&
    workspaceSlug.length > 0 &&
    workspaceSlug.length <= 128 &&
    /^[A-Za-z0-9_-]+$/.test(workspaceSlug)
  );
}

export function imageComposerStorageKey(workspaceSlug) {
  if (!validImageComposerWorkspaceSlug(workspaceSlug)) return null;
  return `${IMAGE_COMPOSER_STORAGE_PREFIX}${workspaceSlug}`;
}

export function defaultImageComposerEnabled(workspaceSlug) {
  return workspaceSlug === "image-generator";
}

export function readImageComposerEnabled(workspaceSlug, storage = localStorage) {
  const key = imageComposerStorageKey(workspaceSlug);
  if (!key) return false;
  try {
    const stored = storage.getItem(key);
    if (stored === "true") return true;
    if (stored === "false") return false;
  } catch {
    // Storage can be unavailable in hardened/private browser contexts.
  }
  return defaultImageComposerEnabled(workspaceSlug);
}

export function writeImageComposerEnabled(workspaceSlug, enabled, storage = localStorage) {
  const key = imageComposerStorageKey(workspaceSlug);
  if (!key) return false;
  try {
    storage.setItem(key, enabled ? "true" : "false");
    return true;
  } catch {
    return false;
  }
}

export default function useImageComposerMode(workspaceSlug) {
  const [enabled, setEnabledState] = useState(() =>
    readImageComposerEnabled(workspaceSlug)
  );

  useEffect(() => {
    setEnabledState(readImageComposerEnabled(workspaceSlug));
  }, [workspaceSlug]);

  useEffect(() => {
    const sync = (event) => {
      if (event?.type !== "storage" && event?.detail?.workspaceSlug !== workspaceSlug)
        return;
      setEnabledState(readImageComposerEnabled(workspaceSlug));
    };
    window.addEventListener(IMAGE_COMPOSER_CHANGE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(IMAGE_COMPOSER_CHANGE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, [workspaceSlug]);

  const setEnabled = useCallback(
    (nextEnabled) => {
      const next = Boolean(nextEnabled);
      if (!imageComposerStorageKey(workspaceSlug)) {
        setEnabledState(false);
        return;
      }
      writeImageComposerEnabled(workspaceSlug, next);
      setEnabledState(next);
      window.dispatchEvent(
        new CustomEvent(IMAGE_COMPOSER_CHANGE_EVENT, {
          detail: { workspaceSlug, enabled: next },
        })
      );
    },
    [workspaceSlug]
  );

  const toggle = useCallback(() => setEnabled(!enabled), [enabled, setEnabled]);
  return { enabled, setEnabled, toggle };
}
