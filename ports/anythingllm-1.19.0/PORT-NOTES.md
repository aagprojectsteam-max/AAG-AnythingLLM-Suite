# AnythingLLM 1.19.0 port notes

Status: candidate, not released. Target image digest, tag and commit are in `PORT-FILES.json`.
On this branch the sources under `image-system/integrations/anythingllm/` and `patches/anythingllm/` are already switched to these port files. The live deployment still runs 07bd65f8.

## Resolved conflicts

- `openai.js` (agent provider): AAG `stream()` and `complete()` stay. 1.19 removed the private request helpers, so input and tools now come from `helpers/responsesTooled.js`. `reasoningConfig` is sent, and both calls go through `createWithReasoningSummaryFallback`, so an organization without reasoning summaries retries once instead of failing.
- `gemini.js`: the AAG `SerialToolAssembly` loop stays. Usage is recorded once per stream from the latest chunk, as in 1.19.
- `AiProviders/openAi/index.js`: upstream `modelSupportsTemperature` plus the AAG `gpt-6-sol` exception.
- `agents/index.js`: `#providerSetupAndCheck(prompt)` reads the current turn's prompt, falling back to `invocation.prompt`. The Expert routing closure passes the prompt and returns `reasoningEffort`.
- `toolReranker.js`: AAG `#chunkedRerank` dropped, because the 1.19 native reranker batches internally, offsets ids to the caller's array and sorts globally.
- `anthropic.js`: `watchTokenLimit` is not called on the stream path. AAG receives a raw `Response` there, and its `message_stop` check already rejects `max_tokens`.

## Decisions to review before release

- Temperature is not sent on OpenAI agent Responses calls. This keeps live behaviour; 1.19 sends `temperatureParam(provider.temperature)`. Adding it is one line per call and is safe for `gpt-6-sol`.
- `<think>` reasoning summary display from `responsesTooled.js` is not adopted in the AAG OpenAI stream path.
- Behaviour change inherited from 1.19: `o*` and `gpt-5*` ordinary chat now omit temperature; 1.15 sent 1.
- AAG stays stricter than 1.19 in two cases, unchanged from the live build: a Gemini no-argument tool with empty raw arguments fails `TOOL_ARGUMENTS_INVALID`, and parallel tool calls fail instead of keeping the first.
- First boot removes `web-browsing` from configured skills.

## Frontend

`tools/build-anythingllm-frontend.js`, `patch-composer-dock.js` and `patch-new-thread-composer.js` now target commit 9b1fcab. The composer hook's storage key is `threadSlug ?? workspaceSlug ?? null`: the 1.19 route fallback is the stale-write path AAG closes.

## Tests

- `image-system/tests/support/anythingllm-1.19-stubs.js` supplies the modules 1.19 added around the provider files.
- `image-composer-mode.test.js` now matches `<NewThreadButton workspace={workspace}` as a regex, because 1.19 changed that line's wording. The button itself is still there.
- Revision pins, the `#providerSetupAndCheck` anchors and the `gpt-5` temperature expectation now describe 1.19. They pass only with the port files overlaid on the integration sources, so these test edits ship together with the switch.

## Installer fixes found by acceptance

These predate 1.19. Package 325933a maps the same 18 targets twice.

- `install.sh` backed up a target on each visit, so the second backup held the patched file. A file mapped inside a directory created by the same run had the same effect. Rollback, uninstall and update then left patched files behind. The installer now records the whole pre-install state before its first write.
- `doctor.sh --package` reported valid frontend ES modules as broken on Node below 22, which is what the 1.19 image ships (Node 18). It now retries module syntax as `.mjs`.

## Release state

Passed, each on its own layer:

- Package layer: `verify-upstream` 37 of 37 on clean 9b1fcab; clean-install acceptance, see `CLEAN-INSTALL-ACCEPTANCE.md`.
- Test layer: 520 passed, 0 failed, in a build tree that includes the Atlas assets.
- Isolated runtime layer, with local stubs only: `--network none`, private storage copy, final server files, storage overlays and built frontend. 20 of 20 flow checks passed: ordinary chat, agent chat over the websocket, saved history, token usage, composer relay, lifecycle socket, artifact route guard, home, workspace and image workspace pages, and a clean 503 when the relay is down.

Not proven yet:

- Real provider calls. The stubs prove routing and streaming, not vendor behaviour.
- Real composer, image, identity, chess and Ubuntu Agent bridges, and real image generation.
- Hebrew and RTL by eye. The automated check only counted RTL nodes.
- The sealed production flow and `doctor.js --deployed`.
- Live non-regression.

`build.js` used copies of `DEPLOYED-ACCEPTANCE-EVIDENCE.json`, the canonical map and the systemd units from the live tree. That evidence is inherited and says nothing about 1.19.

## Still required for a release

1. Review the decisions listed above.
2. Bump the Suite and Image System versions. This branch keeps the current numbers.
3. Set `tested: true` in `config/compatibility.json` only after the items under "Not proven yet" close.
4. Seal the release. `release_gate.py` hardcodes the live tree and writes into it, so sealing needs its own approval.
5. Change the compose image to the pinned digest in the same switch. `release_mounts.py` requires the compose image to match the running image, and `latest` is not allowed.
6. Back up the storage first. There are no new Prisma migrations, but the first boot runs a data step that removes `web-browsing` from configured skills, so going back to 1.15 needs the database backup.
7. Update the server copy and refresh the launchers after the switch.
