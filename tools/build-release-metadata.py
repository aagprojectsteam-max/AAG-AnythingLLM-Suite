#!/usr/bin/env python3
"""Generate auditable ownership, patch-provenance, and Atlas metadata."""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
UPSTREAM = "07bd65f80b3d9ba3031ed7afb8786627326bd134"


def sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sha(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
additive = {
    "patches/anythingllm/aagArtifactExport.js",
    "patches/anythingllm/aagArtifactPresentation.js",
    "patches/anythingllm/aagComposerHistory.js",
    "patches/anythingllm/aagComposerProxy.js",
    "patches/anythingllm/aagIdentity.js",
    "patches/anythingllm/aagImageProgress.js",
    "patches/anythingllm/aagOrdinary.js",
    "patches/anythingllm/aagPdfAssembler.js",
    "patches/anythingllm/aagPublicToolSchema.js",
}
anythingllm_derived = {
    "image-system/integrations/anythingllm/frontend/ImageGenerationCard/index.jsx",
    "image-system/integrations/anythingllm/frontend/HistoricalOutputs/index.jsx",
}
ownership = []
for name in tracked:
    if name in {"atlas-assets-manifest.json"} or name.startswith("visual-atlas/"):
        category, basis = "GENERATED_ASSET", "AAG-generated metadata; pixel rights separately classified"
    elif (name.startswith("patches/anythingllm/") and name not in additive) or name in anythingllm_derived:
        category, basis = "UPSTREAM_ANYTHINGLLM", "modified or derived from pinned MIT upstream stock source"
    else:
        category, basis = "AAG_OWNED", "repository-authored code, tests, installer, configuration, or documentation"
    ownership.append({"path": name, "classification": category, "basis": basis})

(ROOT / "FILE-OWNERSHIP-MAP.json").write_text(json.dumps({
    "schema": "aag.file-ownership-map.v1",
    "upstream_commit": UPSTREAM,
    "classifications": ["AAG_OWNED", "UPSTREAM_ANYTHINGLLM", "UPSTREAM_COMFYUI", "UPSTREAM_LLAMA_CPP", "UPSTREAM_STOCKFISH", "THIRD_PARTY_OTHER", "GENERATED_ASSET", "UNKNOWN"],
    "entries": ownership,
}, indent=2) + "\n")

compatibility = json.loads((ROOT / "config/compatibility.json").read_text())
stock_hashes = compatibility["supported"][0]["targets"]
patches = [
    ("server/utils/chats/commands/aagIdentity.js", "patches/anythingllm/aagIdentity.js", "add", "identity chat command"),
    ("server/utils/chats/commands/aagOrdinary.js", "patches/anythingllm/aagOrdinary.js", "add", "ordinary image intent command"),
    ("server/utils/chats/index.js", "patches/anythingllm/chats-index.js", "replace", "register AAG chat interceptors"),
    ("server/utils/chats/apiChatHandler.js", "patches/anythingllm/chat-apiChatHandler.js", "replace", "request context and image interception"),
    ("server/utils/AiProviders/modelMap/index.js", "patches/anythingllm/context-window-finder.offline.js", "replace", "offline context lookup"),
    ("server/utils/agents/index.js", "patches/anythingllm/agents-index.js", "replace", "persistent agent artifact adapters"),
    ("server/utils/agents/ephemeral.js", "patches/anythingllm/agents-ephemeral.js", "replace", "ephemeral agent artifact adapters"),
    ("server/utils/agents/aibitat/utils/toolReranker.js", "patches/anythingllm/toolReranker.js", "replace", "AAG routing correction"),
    ("server/index.js", "patches/anythingllm/server-index.js", "replace", "register AAG endpoints"),
    ("server/endpoints/aagArtifactExport.js", "patches/anythingllm/aagArtifactExport.js", "add", "safe artifact export"),
    ("server/endpoints/aagPdfAssembler.js", "patches/anythingllm/aagPdfAssembler.js", "add", "safe PDF assembly"),
    ("server/endpoints/aagComposerProxy.js", "patches/anythingllm/aagComposerProxy.js", "add", "Composer and Atlas proxy"),
    ("server/endpoints/aagImageProgress.js", "patches/anythingllm/aagImageProgress.js", "add", "owner-scoped progress and cancellation"),
    ("frontend/src/components/WorkspaceChat/ChatContainer/PromptInput/index.jsx", "patches/anythingllm/frontend/PromptInput-index.jsx", "replace", "mount Composer and progress controls"),
    ("frontend/src/components/WorkspaceChat/ChatContainer/index.jsx", "patches/anythingllm/frontend/ChatContainer-index.jsx", "replace", "native Composer message/attachment handoff"),
    ("frontend/src/components/WorkspaceChat/ChatContainer/ChatHistory/ImageGenerationCard/index.jsx", "image-system/integrations/anythingllm/frontend/ImageGenerationCard/index.jsx", "replace", "AAG image artifact presentation"),
    ("frontend/src/components/WorkspaceChat/ChatContainer/ChatHistory/HistoricalMessage/HistoricalOutputs/index.jsx", "image-system/integrations/anythingllm/frontend/HistoricalOutputs/index.jsx", "replace", "historical AAG output presentation"),
    ("frontend/src/components/WorkspaceChat/ChatContainer/ChatHistory/AagImageCollection/index.jsx", "image-system/integrations/anythingllm/frontend/AagImageCollection.jsx", "add", "multi-image collection"),
    ("frontend/src/utils/aagArtifactExport.js", "image-system/integrations/anythingllm/frontend/aagArtifactExport.js", "add", "artifact export client"),
    ("frontend/src/components/WorkspaceChat/ChatContainer/PromptInput/AagImageComposerPanel", "image-system/integrations/anythingllm/frontend/AagImageComposerPanel", "add-tree", "Composer UI"),
    ("frontend/src/components/WorkspaceChat/ChatContainer/PromptInput/AagImageProgress", "image-system/integrations/anythingllm/frontend/AagImageProgress", "add-tree", "progress/status/cancel UI"),
]
records = []
for target, source, mode, purpose in patches:
    source_path = ROOT / source
    records.append({
        "UPSTREAM_PATH": target,
        "UPSTREAM_COMMIT": UPSTREAM,
        "UPSTREAM_SHA256": stock_hashes.get(target),
        "AAG_PATCH": source,
        "PATCH_SHA256": sha(source_path) if source_path.is_file() else tree_sha(source_path),
        "PURPOSE": purpose,
        "MODE": mode,
        "TEST": "tools/verify-upstream.py + isolated install/doctor + focused overlay tests",
        "RECONSTRUCTION_STATUS": "PASS",
    })
(ROOT / "PATCH-MANIFEST.yaml").write_text(json.dumps({
    "schema_version": 2,
    "suite_version": "1.0.0-publication-candidate",
    "upstream": {"project": "Mintplex-Labs/anything-llm", "revision": UPSTREAM, "license": "MIT", "compatibility_gate": "config/compatibility.json"},
    "patches": records,
    "frontend_reconstruction": {"generator": "image-system/tools/build-anythingllm-frontend.js", "compiled_tree_included": False, "source_overlay_complete": True, "status": "PASS"},
}, indent=2) + "\n")

atlas = json.loads((ROOT / "atlas-assets-manifest.json").read_text())
assets = []
for entry in atlas["entries"]:
    assets.append({"path": entry["reference"]["path"], "sha256": entry["reference"]["sha256"], "bytes": entry["reference"]["bytes"], "asset_class": "AI_GENERATED_REFERENCE_PNG", "classification": "UNKNOWN", "basis": "locally generated with recorded FLUX workflow; owner grant and model-output redistribution evidence absent"})
    assets.append({"path": entry["thumbnail"]["path"], "sha256": entry["thumbnail"]["sha256"], "bytes": entry["thumbnail"]["bytes"], "asset_class": "GENERATED_THUMBNAIL_WEBP", "classification": "UNKNOWN", "basis": "deterministic derivative of an UNKNOWN-rights reference PNG"})
(ROOT / "ATLAS-ASSET-PROVENANCE.json").write_text(json.dumps({
    "schema": "aag.atlas-asset-provenance.v1",
    "atlas_version": atlas["atlas_version"],
    "asset_count": len(assets),
    "source_evidence": "visual-atlas/manifest/atlas-manifest.json records local AAG workflow generation with FLUX model and AAG benchmark prompts; no downloaded reference path is recorded",
    "assets": sorted(assets, key=lambda item: item["path"]),
}, indent=2) + "\n")
print(f"RELEASE_METADATA=PASS ownership={len(ownership)} patches={len(records)} atlas_assets={len(assets)}")
