# AAG Chess Puzzle — AnythingLLM imported Skill

This directory is the source-of-truth package for the installed AnythingLLM
`skill-1.0.0` integration. It contains no chess engine or solver logic. The
handler validates structured intent, talks to the host bridge over the mounted
Unix socket, and publishes verifier-approved PNG/SVG files through
AnythingLLM's native inline-image and generated-file download cards. A private
scope-indexed capability permits later verified hints and solutions without a
token or marker in visible chat; the handler contains no chess-solving logic.
The model-facing result contains only concise Hebrew presentation and inline
delivery URLs—never internal hashes, IDs, versions, or bridge metadata.
Verified solutions are vertical SAN trees with Unicode direction isolation.
Automatic board density is the
zero-configuration default; Hebrew requests for `הרבה כלים`, `לוח מלא יותר`,
or `מעט כלים` map only to the validated rich/sparse preference.
When the user omits a seed, the bridge uses bounded conversation-scoped,
solution-free recent history to vary repeated requests. An explicitly supplied
seed preserves reproducible generation.

Install the whole `aag-chess-puzzle` directory under the live AnythingLLM
storage `plugins/agent-skills/` directory. Install and start the companion user
service from `integrations/anythingllm/systemd/` on the host.
