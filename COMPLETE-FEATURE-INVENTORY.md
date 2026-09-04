# Complete Feature Inventory

Inventory date: 2026-09-04. Status values reflect the inspected installation; `UNRESOLVED` is used instead of guessing.

## AAG-IMAGE-CORE

```text
FEATURE_ID=AAG-IMAGE-CORE
NAME=Provider-neutral AAG Image Agent
PURPOSE=Generate, transform, upscale and route images with normalized contracts
STATUS=ACTIVE
VERSION=0.9.0-preview.13
AUTHORITATIVE_SOURCE=image-system/{src,skills,schemas,routing}
DEPLOYED_PATHS=AnythingLLM storage/plugins/agent-skills/aag-image-{task,batch,job}
ANYTHINGLLM_PATCHES=runtime context, direct chat commands, artifact presentation
AGENT_SKILLS=aag-image-task,aag-image-batch,aag-image-job
SERVICES=ComfyUI bridge,Image Hub,upscale bridge
SCRIPTS=image-system/bin and image-system/libexec
RUNTIME_DEPENDENCIES=Node.js,Python,AnythingLLM,ComfyUI
MODEL_DEPENDENCIES=config/models.yaml
OTHER_ASSETS=Visual Atlas metadata; pixel bundle external
CONFIGURATION=.env and skill manifests
SECRETS_REQUIRED=optional compatibility token file
TESTS=image-system/tests
ACCEPTANCE_EVIDENCE=source release manifest and live doctor
INSTALL_METHOD=install.sh profile image/full
UPDATE_METHOD=update.sh
ROLLBACK_METHOD=rollback.sh
LICENSE_NOTES=AAG ownership assumed; dependency licenses separate
GITHUB_INCLUDE=YES
EXCLUSION_REASON=
```

## AAG-IMAGE-IDENTITY

```text
FEATURE_ID=AAG-IMAGE-IDENTITY
NAME=Portrait Contract B and bounded Scene Contract C identity pipelines
PURPOSE=Request-bound identity preservation with provenance and quality gates
STATUS=ACTIVE
VERSION=0.9.0-preview.13
AUTHORITATIVE_SOURCE=image-system/human-identity*
DEPLOYED_PATHS=AAG Image source plus user systemd services
ANYTHINGLLM_PATCHES=aagIdentity.js and trusted attachment context
AGENT_SKILLS=aag-image-task,aag-image-batch
SERVICES=aag-human-identity-bridge,aag-human-identity-scene-bridge
SCRIPTS=process_inbox.py and runtime workers
RUNTIME_DEPENDENCIES=Python,ComfyUI,PuLID nodes
MODEL_DEPENDENCIES=PuLID v1.1,Juggernaut XL V9
OTHER_ASSETS=private references excluded
CONFIGURATION=frozen JSON contracts
SECRETS_REQUIRED=none
TESTS=image-system/tests
ACCEPTANCE_EVIDENCE=release hashes
INSTALL_METHOD=image/full profile
UPDATE_METHOD=transactional
ROLLBACK_METHOD=backup manifest
LICENSE_NOTES=model and custom-node licenses unresolved
GITHUB_INCLUDE=YES
EXCLUSION_REASON=private references and weights excluded
```

## AAG-COMPOSER-ATLAS

```text
FEATURE_ID=AAG-COMPOSER-ATLAS
NAME=Native Composer, progress UI and Visual Atlas
PURPOSE=Structured image controls, progress/cancel and 28-family/493-style browsing
STATUS=ACTIVE
VERSION=Composer 1.2; Atlas 1.0.0
AUTHORITATIVE_SOURCE=patches/anythingllm and visual-atlas metadata
DEPLOYED_PATHS=AnythingLLM server replacements and read-only Atlas mount
ANYTHINGLLM_PATCHES=PromptInput,ChatContainer,Composer,Progress,Image cards,History
AGENT_SKILLS=image skills
SERVICES=composer loopback relay,compatibility service
SCRIPTS=aagComposerProxy.js,aagImageProgress.js
RUNTIME_DEPENDENCIES=AnythingLLM frontend/server
MODEL_DEPENDENCIES=none for UI
OTHER_ASSETS=493 references and thumbnails external
CONFIGURATION=visual-taxonomy.json,manifests
SECRETS_REQUIRED=compatibility proxy token at runtime
TESTS=model-neutral compatibility and Visual Atlas tests
ACCEPTANCE_EVIDENCE=product-assets.json hashes
INSTALL_METHOD=image/full plus separately licensed asset bundle
UPDATE_METHOD=governed overlay rebuild
ROLLBACK_METHOD=restore upstream files/public tree
LICENSE_NOTES=upstream UI and Atlas pixel redistribution unresolved
GITHUB_INCLUDE=PARTIAL
EXCLUSION_REASON=compiled upstream public tree and Atlas pixels excluded
```

## AAG-PDF-ARTIFACTS

```text
FEATURE_ID=AAG-PDF-ARTIFACTS
NAME=Artifact delivery, multi-image export and PDF assembly
PURPOSE=Safe downloads, MIME/path validation, bundled exports and PDFs
STATUS=ACTIVE
VERSION=integrated preview.13
AUTHORITATIVE_SOURCE=patches/anythingllm/aag{ArtifactExport,PdfAssembler}.js
DEPLOYED_PATHS=AnythingLLM endpoints and generated-file storage
ANYTHINGLLM_PATCHES=server index endpoint registration
AGENT_SKILLS=none
SERVICES=AnythingLLM server
SCRIPTS=aagArtifactExport.js,aagPdfAssembler.js
RUNTIME_DEPENDENCIES=Node.js
MODEL_DEPENDENCIES=none
OTHER_ASSETS=user artifacts excluded
CONFIGURATION=storage roots
SECRETS_REQUIRED=AnythingLLM session/auth
TESTS=syntax and reconstruction routing checks
ACCEPTANCE_EVIDENCE=live mounted source hashes
INSTALL_METHOD=pdf/full profile
UPDATE_METHOD=transactional
ROLLBACK_METHOD=restore upstream index
LICENSE_NOTES=AAG source included
GITHUB_INCLUDE=YES
EXCLUSION_REASON=
```

## AAG-CHESS

```text
FEATURE_ID=AAG-CHESS
NAME=Deterministically verified chess puzzle agent
PURPOSE=Generate, prove, diversify, render and present chess tactics
STATUS=ACTIVE
VERSION=Git 22ab439 plus deployed routing adjustment
AUTHORITATIVE_SOURCE=chess/
DEPLOYED_PATHS=AnythingLLM aag-chess-puzzle skill and local bridge
ANYTHINGLLM_PATCHES=Agent Skill only
AGENT_SKILLS=aag-chess-puzzle
SERVICES=aag-chess-anythingllm-bridge
SCRIPTS=Python package and JS handler
RUNTIME_DEPENDENCIES=Python,python-chess,optional Stockfish
MODEL_DEPENDENCIES=none
OTHER_ASSETS=generated boards excluded
CONFIGURATION=environment/service
SECRETS_REQUIRED=none
TESTS=chess/tests and JS handler test
ACCEPTANCE_EVIDENCE=upstream project evidence,git history
INSTALL_METHOD=chess/full profile
UPDATE_METHOD=transactional
ROLLBACK_METHOD=backup manifest
LICENSE_NOTES=Stockfish binary excluded; GPL obligations apply when separately installed
GITHUB_INCLUDE=YES
EXCLUSION_REASON=
```

## AAG-LOCAL-LLM

```text
FEATURE_ID=AAG-LOCAL-LLM
NAME=llama.cpp SYCL/MTP model-neutral local route
PURPOSE=Select models/mmproj, validate approved MTP pairs, cache and control server
STATUS=ACTIVE
VERSION=script snapshot 2026-09-04; llama.cpp baseline a1f96d4fc
AUTHORITATIVE_SOURCE=integrations/llamacpp/aag-llama-control
DEPLOYED_PATHS=/mnt/data/AI/Scripts/aag-llama-control
ANYTHINGLLM_PATCHES=context-window offline finder and OpenAI-compatible configuration
AGENT_SKILLS=none
SERVICES=llama-server process,model compatibility proxy
SCRIPTS=aag-llama-control
RUNTIME_DEPENDENCIES=llama.cpp built with SYCL/Intel Arc
MODEL_DEPENDENCIES=user GGUF,optional mmproj and MTP sidecar
OTHER_ASSETS=none
CONFIGURATION=environment and model directories
SECRETS_REQUIRED=runtime-generated compatibility token file
TESTS=compatibility/attestation tests
ACCEPTANCE_EVIDENCE=accepted STARTTIME fix in inspected source
INSTALL_METHOD=local-llm/full profile
UPDATE_METHOD=transactional
ROLLBACK_METHOD=restore script/service
LICENSE_NOTES=llama.cpp binary/source not vendored
GITHUB_INCLUDE=YES
EXCLUSION_REASON=weights and llama.cpp tree excluded
```

## AAG-UBUNTU-AGENT

```text
FEATURE_ID=AAG-UBUNTU-AGENT
NAME=Ubuntu diagnostics, context memory and governed maintenance
PURPOSE=Read-only audit plus policy-controlled remediation/orchestration
STATUS=OPTIONAL_ACTIVE
VERSION=local VERSION snapshot
AUTHORITATIVE_SOURCE=integrations/ubuntu-agent
DEPLOYED_PATHS=/mnt/data/AI/Agents/AAG-Ubuntu-Agent and AnythingLLM skills
ANYTHINGLLM_PATCHES=Agent Skills only
AGENT_SKILLS=context-memory,governed-orchestration,maintenance-intelligence,live-audit
SERVICES=aag-ubuntu-agent-bridge
SCRIPTS=Python tools and JS handlers
RUNTIME_DEPENDENCIES=Python,Linux system APIs
MODEL_DEPENDENCIES=none
OTHER_ASSETS=private SQLite memory excluded
CONFIGURATION=user-supplied policy config
SECRETS_REQUIRED=AnythingLLM/OpenAI keys in external secret files only
TESTS=integrations/ubuntu-agent/tests
ACCEPTANCE_EVIDENCE=release manifest in live tree (not copied because paths/private state)
INSTALL_METHOD=full or manual optional profile
UPDATE_METHOD=transactional
ROLLBACK_METHOD=backup manifest
LICENSE_NOTES=project license file absent; private-only pending owner declaration
GITHUB_INCLUDE=YES
EXCLUSION_REASON=memory,backups,runtime,secrets excluded
```

## LEGACY-IMAGE-SKILLS

```text
FEATURE_ID=LEGACY-IMAGE-SKILLS
NAME=Legacy ComfyUI generator/model inventory/reference and UpScayl skills
PURPOSE=Predecessor routes retained only for recovery/history
STATUS=LEGACY/RETIRED
VERSION=mixed
AUTHORITATIVE_SOURCE=retired live storage only
DEPLOYED_PATHS=retired-agent-skills and quarantine
ANYTHINGLLM_PATCHES=none active
AGENT_SKILLS=legacy names
SERVICES=none required
SCRIPTS=not packaged
RUNTIME_DEPENDENCIES=obsolete routes
MODEL_DEPENDENCIES=various
OTHER_ASSETS=none
CONFIGURATION=historical
SECRETS_REQUIRED=unknown
TESTS=historical only
ACCEPTANCE_EVIDENCE=canonical map
INSTALL_METHOD=none
UPDATE_METHOD=none
ROLLBACK_METHOD=release backups
LICENSE_NOTES=not reviewed
GITHUB_INCLUDE=NO
EXCLUSION_REASON=duplicate/risky/superseded implementation
```

