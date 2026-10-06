import { useEffect, useSyncExternalStore } from "react";
import Workspace from "@/models/workspace";
import { threadOrder, threadOrdinal } from "@/utils/aagThreadOrder";
const pending = new Map();
export default function useAagThreadNumber(workspaceSlug, threadSlug) {
  const threads = useSyncExternalStore(threadOrder.subscribe, () => threadOrder.get(workspaceSlug));
  useEffect(() => {
    if (!workspaceSlug || threadOrder.known(workspaceSlug) || pending.has(workspaceSlug)) return;
    const request = Workspace.threads.all(workspaceSlug).finally(() => pending.delete(workspaceSlug));
    pending.set(workspaceSlug, request);
  }, [workspaceSlug]);
  return {ordinal:threadOrdinal(threads, threadSlug), known:threadOrder.known(workspaceSlug)};
}
