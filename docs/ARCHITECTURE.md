# Architecture

AnythingLLM remains the host UI and agent runtime. Narrow AAG skills normalize requests and write owner-scoped job records. The image scheduler serializes constrained accelerator work, dispatches to ComfyUI or the upscale engine, verifies outputs through Image Hub, and returns canonical envelopes. Server overlays render trusted envelopes, expose progress/cancel and artifact/PDF endpoints, and never let the model decide filesystem paths.

Local LLM traffic is OpenAI-compatible and may pass through the model-neutral compatibility service. Attestation verifies PID, UID, executable and process start time. Chess and Ubuntu capabilities use independent loopback bridges and narrow Agent Skills.

Runtime databases, state and tokens are external to this repository.

