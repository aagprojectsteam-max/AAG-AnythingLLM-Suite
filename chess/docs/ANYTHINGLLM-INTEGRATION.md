# AnythingLLM Integration

## Installed V1.5 integration

The supported integration is the installed **AAG Chess Puzzle** imported Skill.
Its full operating contract, installation paths, defaults, artifact behavior,
and troubleshooting are documented in
[`ANYTHINGLLM-SKILL.md`](ANYTHINGLLM-SKILL.md).

AnythingLLM never contains chess-generation or verification logic. The Skill
passes a narrow validated request through a local Unix socket to the existing
`aag-chess` CLI. The CLI remains the only public application interface used by
the bridge, and `MateVerifier` remains the final acceptance authority.
Conversation continuity is stored privately by trusted AnythingLLM scope; no
context token or hash is embedded in visible chat. The Skill exposes concise
Hebrew puzzle text and native artifacts, while explicit solution follow-ups
render only freshly verified SAN proof branches.
