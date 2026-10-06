import Workspace from "@/models/workspace";
import { API_BASE } from "@/utils/constants";
import { baseHeaders } from "@/utils/request";

let pending = null;
// Capture the same unfiltered order used by the native sidebar. The server
// preserves workspace identities and records the latest ordered thread list.
export async function ensureArtifactSerials(workspaceSlug) {
  if (workspaceSlug !== "image-generator") return;
  if (pending) return pending;
  pending = (async () => {
    const workspaces = Workspace.orderWorkspaces(await Workspace.all());
    const threads = await Promise.all(workspaces.map(async workspace => {
      const result = await Workspace.threads.all(workspace.slug);
      return {workspaceId:workspace.id, threadIds:result.threads.map(thread => thread.id)};
    }));
    const response = await fetch(`${API_BASE}/aag-composer/${workspaceSlug}/artifact-serials`, {
      method:"POST", credentials:"same-origin",
      headers:{...baseHeaders(), "Content-Type":"application/json",
        "X-AAG-Workspace-Slug":workspaceSlug, "X-AAG-Workspace-Path":`/workspace/${workspaceSlug}`},
      body:JSON.stringify({workspaceIds:workspaces.map(w => w.id), threads}),
    });
    if (!response.ok) throw new Error("Artifact numbering is temporarily unavailable. Please try again.");
  })();
  try { await pending; } finally { pending = null; }
}
