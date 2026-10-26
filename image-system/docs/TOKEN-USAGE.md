# Token usage display

## Installed feature
The native prompt component displays three separate fields in every workspace:
- Context: estimated input/history usage and remaining configured context capacity.
- Response: most recent AnythingLLM usage metrics, or a clearly marked estimate of streamed visible text. Remaining response budget is shown only when the active ordinary request adapter exposes a known cap.
- Provider allowance: explicitly unavailable when the connected chat adapter does not expose account credit, period quota or reset data. No account balance is inferred from conversation usage.

This is read-only presentation. It does not change model selection, request limits, truncation, reasoning, tool execution, image generation, provider credentials or billing.

## Sources and limits
GET /api/aag-usage/:slug reuses native validatedRequest, role checks and workspace membership. It returns model/provider names and numeric limits only, with no-store headers. No inference or account API calls are made by the endpoint.

Context limits come from the native provider class's promptWindowLimit and its configured/cached data. A separately configured local server may have a different capacity. The bar is not a hard limit preflight. The current local native runtime-context override is not enabled.

Before sending, text is approximated locally. Comparable last-request metrics can anchor the estimate. Images, retrieved context, tools, hidden reasoning and history trimming can change real usage. Base64 attachment bytes are not counted as text. Unknown quantities remain unknown. Model switch, workspace switch, tab focus and reload refresh metadata. Output during streaming is not a progress forecast.

The native model-picker save event is used; there is no model-name allowlist. Agent status events are excluded from output counts. Saved ordinary messages use the native "chart" type and remain eligible for metrics after reload. Live agent usage is tagged to avoid mistaking multi-call totals for a single request's context.

## Source files
- integrations/anythingllm/frontend/AagTokenUsage.jsx
- integrations/anythingllm/frontend/aagTokenUsage.js
- integrations/anythingllm/frontend/aagTokenUsage.css
- integrations/anythingllm/aagComposerProxy.js
- tools/patch-token-usage.js
- tools/build-anythingllm-frontend.js

Build and deploy through the approved release workflow. Never edit a sealed payload in place.
The initial scoped build used the pinned native frontend revision and the byte-verified approved frontend overlay. Earlier unrelated development changes were preserved and excluded.

## Evidence and rollback
Evidence: /home/aag-linux/ARCHIVE/AnythingLLM-token-bar/20261021T0100+0300
Scratch: /home/aag-linux/TEMPORARY/AnythingLLM-token-bar/20261021T0100+0300
The archived validation checks cover unknown limits, model switches, streaming estimates, new threads, attachments, saved messages, status filtering, permissions and secret exclusion.
To return to the version before this entire feature, while image work is idle:
python3 lifecycle/release_deploy.py rollback /mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/evaluation/resilient-lifecycle-20261004/deployment-20261020T230907Z
