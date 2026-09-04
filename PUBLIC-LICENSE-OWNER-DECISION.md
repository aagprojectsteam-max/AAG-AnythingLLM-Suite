# Public License Owner Decision

The file-level scope is recorded in `FILE-OWNERSHIP-MAP.json`. AAG-authored installer, integration, service, test, configuration, and documentation files are `AAG_OWNED`. Modified AnythingLLM stock files are `UPSTREAM_ANYTHINGLLM` and retain the upstream MIT notice. No ComfyUI, llama.cpp, or Stockfish source/binary is bundled. Atlas pixels are separately classified `UNKNOWN` and are not covered by the proposed code license.

`OPTION_A=MIT — short permissive grant; compatible with the MIT AnythingLLM modifications and separate external GPL programs; requires preservation of copyright/license notices; no express patent grant.`

`OPTION_B=Apache-2.0 — permissive grant with an express patent license and patent-termination clause; notice/state-change obligations are more detailed; compatible with MIT-derived files and separate external GPLv3 programs, but Apache-2.0 code is not compatible with GPLv2-only combinations.`

`OPTION_C=GPLv3 — reciprocal source license with installation-information and anti-tivoization obligations in covered distributions; compatible with GPLv3 external programs and capable of including MIT-derived code when MIT notices remain, but it imposes copyleft on combined derivative distributions and is unnecessarily restrictive for an installer that integrates separate programs.`

`RECOMMENDED_OPTION=MIT`

`WHY=It matches the pinned AnythingLLM license, minimizes notice complexity for source overlays, permits commercial and private reuse, and creates no conflict with separately installed GPLv3 ComfyUI or Stockfish processes. The absence of an express patent grant is the principal tradeoff versus Apache-2.0.`

`FILES_COVERED=All FILE-OWNERSHIP-MAP.json entries classified AAG_OWNED, excluding any file or portion carrying a separate third-party notice.`

`FILES_NOT_COVERED=UPSTREAM_ANYTHINGLLM portions and notices; external ComfyUI, llama.cpp, Stockfish, models and custom nodes; Atlas pixels; GENERATED_ASSET or UNKNOWN items unless the owner separately grants rights.`

`OWNER_ACTION_REQUIRED=COMPLETE — the owner approved the exact sentence below on 2026-09-04 and the canonical MIT LICENSE was added.`

> I authorize AAG-owned code in this repository to be distributed under the MIT License.

`OWNER_APPROVAL=RECORDED`

This document is a technical compatibility recommendation, not legal advice.
