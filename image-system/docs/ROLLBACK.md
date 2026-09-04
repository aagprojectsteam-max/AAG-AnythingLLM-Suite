# Rollback — 0.9.0-preview.13

Use the guarded Preview 13 preview/packaging pre-state backup:

```bash
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/visual-atlas-preview-packaging-20260903T124302Z/ROLLBACK.sh --check
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/visual-atlas-preview-packaging-20260903T124302Z/ROLLBACK.sh --apply
```

It verifies 357 captured pre-state/evidence hashes, rechecks the immutable Atlas,
restores the exact Preview 12 canonical/deployed providers, Composer proxy,
public frontend and AnythingLLM compose preimages, then recreates only
AnythingLLM. It does not delete jobs, generated images, chat history, identity
state, models, or any of the 493 Atlas reference PNGs/thumbnails. The new sealed
product manifest and Preview 13-only code/release files are removed; the Atlas
corpus itself remains in its canonical product location.

## Earlier Preview 12 rollback

For the original Visual Atlas/Composer activation use:

```bash
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/visual-atlas-composer-integration-20260903T113547Z/ROLLBACK.sh --check
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/visual-atlas-composer-integration-20260903T113547Z/ROLLBACK.sh --apply
```

## Earlier Preview 11 rollback

For the batch/export activation use:

```bash
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/multi-image-export-20260831T173158Z/ROLLBACK.sh --check
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/multi-image-export-20260831T173158Z/ROLLBACK.sh --apply
```

## Earlier Preview 10 rollback

For the provider-neutral quality-selection contract correction, use:

```bash
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/quality-selection-semantics-20260831T154352Z/ROLLBACK.sh --check
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/quality-selection-semantics-20260831T154352Z/ROLLBACK.sh --apply
```

The guarded rollback verifies the complete pre-change payload and restores the
Preview 9 canonical source, staged/sealed release, deployed skills,
runtime-context schema bridge, AnythingLLM environment/compose definition, and
container. It preserves jobs, artifacts, history, attachments, models, and
retained acceptance evidence.

## Earlier Preview 9 rollback

For the real-browser public-contract and managed Gemma grammar correction, use
the verified combined rollback:

```bash
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/real-browser-public-contract-regression-20260831T140103Z/ROLLBACK-ALL.sh
```

It first restores the pre-change managed Gemma control path and embedded
template, then restores the complete Preview 8 canonical source, staged and
sealed releases, deployed skills, runtime-context bridge, normal port-8080
AnythingLLM endpoint, compose definition, and container. It preserves jobs,
artifacts, chat history, models, and retained acceptance evidence. Both backup
manifests are verified before mutation and the Preview 8 deployed doctor is the
final gate.

## Earlier Preview 8 rollback

For the provider-neutral native inline-image presentation correction, use:

```bash
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/provider-neutral-inline-presentation-20260831T055241Z/ROLLBACK.sh --check
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/provider-neutral-inline-presentation-20260831T055241Z/ROLLBACK.sh --apply
```

It restores the complete Preview 7 source, deployed providers, persistent
websocket/API bridges, and Scene C activation marker. It does not remove jobs,
artifacts, attachments, chat history, models, or retained evidence.

## Earlier Preview 7 rollback

For the canonical discovery and frozen-contract-release correction, use:

```bash
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/provider-neutral-discovery-contract-release-20260831T041651Z/ROLLBACK.sh --check
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/provider-neutral-discovery-contract-release-20260831T041651Z/ROLLBACK.sh --apply
```

It restores the complete Preview 6 preimage, including the prior legacy-skill
activation flags, while preserving jobs, artifacts, attachments and models.

## Earlier Preview 6 rollback

For the trusted invocation-prompt fallback correction, use:

```bash
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/provider-request-fallback-20260830T211509Z/ROLLBACK.sh --check
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/provider-request-fallback-20260830T211509Z/ROLLBACK.sh --apply
```

It restores the sealed Preview 5 providers, source/schema, activation record
and canonical map without touching jobs, attachments, artifacts or models.

## Earlier Preview 5 rollback

For Scene Identity Contract C and identity framing/defaulting, use the guarded
rollback shipped with the pre-change backup:

```bash
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/scene-identity-contract-20260830T182610Z/ROLLBACK.sh --check
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/scene-identity-contract-20260830T182610Z/ROLLBACK.sh --apply
```

It restores the complete Preview 4 provider/canonical preimages, disables the
Scene C path unit and removes Scene C from active discovery while preserving
append-only jobs, artifacts, experiment records and runtime evidence. Contract
B remains the same frozen hash before, during and after rollback.

## Earlier Preview 4 rollback

For the dynamic trusted identity-reference remediation, the authoritative exact rollback is:

```bash
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/dynamic-trusted-identity-reference-20260830T145755Z/ROLLBACK.sh --check
/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/dynamic-trusted-identity-reference-20260830T145755Z/ROLLBACK.sh --apply
```

It verifies the timestamped backup manifest and restores the complete pre-change code, configuration, deployed providers, unit files, and compose definition. It intentionally leaves append-only jobs, generated artifacts, models, databases, and runtime evidence intact.

Use the final-system-completion restore manifest and rollback program. It
atomically restores the 0.9.0-preview.3 canonical source, providers, registry
and ComfyUI launcher, removes only new 0.9.0-preview.4 release/state objects,
restarts AnythingLLM, then verifies the 0.9.0-preview.3 deployed seal and
doctor. Human Identity data is neither replaced nor rewritten because it is
byte-identical across this transaction.

## Earlier hardening rollback record

Authoritative baseline: `/mnt/data/AI/Apps/AnythingLLM/AAG-Image-System/backups/image-agent-pre-hardening2-20260826T160805Z` (142/142 hashes verified before migration).

1. Prove the ComfyUI queue is empty, no `upscayl-bin` process exists, and the shared scheduler has no live lease/waiter.
2. Stop only the on-demand image services. AnythingLLM remains independent until provider files are ready to restore.
3. Verify the baseline `SHA256SUMS` again.
4. Restore canonical Image Agent paths from `canonical/` while preserving immutable `image-agent/releases/0.9.0-rc.1`, `image-agent/hardening-pass-2/`, and every user output.
5. Restore both deployed provider directories from `deployed-skills/` and the original active/inactive flags. Restore integration files by renaming the same-directory `.pre-0.9.0-preview.2` anchors back into place; this preserves their original owner and mode without `sudo`.
6. Restart AnythingLLM once and verify health, loader output, version `0.9.0-rc.1`, active flags, runtime hashes, provider/model, and required core-patch markers.
7. Verify the image services remain disabled at boot and return them to their pre-test inactive state. Confirm ports `8188`, `18188`–`18192` are closed and AnythingLLM is still healthy.
8. Reapply `0.9.0-preview.2` only from its verified canonical staging manifest and repeat the same health/hash checks.

Rollback never deletes, moves, overwrites, or restores the contents of `/mnt/data/AI/Outputs`, models, databases, secrets, drivers, Torch, or ComfyUI.
