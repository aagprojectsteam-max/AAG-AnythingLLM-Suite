# Master Engineering Handoff — AAG AnythingLLM Suite

## Purpose

This repository contains unusually rich development, acceptance, distribution and publication evidence. The risk is not missing documents but losing the story among many `FINAL-*`, inventory and audit files. This master handoff is the entry point: it explains the project, the major engineering layers, how to read the evidence, and which documents are authoritative for each question.

## Goal

Package and preserve the AAG AnythingLLM customization as a reproducible, privacy-safe public suite without publishing private workspace data, secrets, machine-specific state, proprietary model assets or undocumented local assumptions. The suite includes application patches/integration, Visual Atlas/image-system material, configuration examples, install/distribution logic, compatibility checks and extensive publication verification.

## Engineering approach

The project treated the live development installation and the public distribution as separate trust domains. Development evidence could contain machine-specific/private state; the public artifact had to be reconstructed from an explicit ownership/feature inventory, audited for licensing/privacy, staged/fresh-installed, compared against the accepted behavior, and anonymously cloned after publication.

This is why the repository contains `COMPLETE-FEATURE-INVENTORY.md`, `COMPLETE-FILE-INVENTORY.json`, `FILE-OWNERSHIP-MAP.json`, `PATCH-MANIFEST.yaml`, distribution architecture notes and multiple final acceptance reports. They are evidence, not clutter to delete casually.

## Major problem classes solved

### Reproducible distribution

The public package cannot depend on the developer's AnythingLLM database, workspace IDs, absolute paths, cached models or secret environment values. `.env.example` and setup documentation define portable inputs. Ownership maps and manifests define which files belong to the suite.

### Feature completeness

The project audited the customized live system against the distributable tree so publication would not silently omit a feature. `COMPLETE-FEATURE-INVENTORY.md` is the feature-level reference; `COMPLETE-FILE-INVENTORY.json` and `FILE-OWNERSHIP-MAP.json` are the file-level references.

### Visual Atlas assets

Visual Atlas/image assets required a separate provenance and publication decision. `ATLAS-ASSET-PROVENANCE.json`, `ATLAS-ASSET-DISTRIBUTION.md`, `ATLAS-ASSET-PUBLICATION-DECISION.md` and `FINAL-ATLAS-DISTRIBUTION.md` record that boundary. Do not add generated/reference assets without provenance/licensing review.

### Clean/fresh-user acceptance

The project performed clean-install/fresh-user and remote-clone checks rather than assuming a package that works on the development machine is portable. Relevant evidence includes `CLEAN-INSTALL-ACCEPTANCE.md`, `FRESH-INSTALL.md`, `FINAL-FRESH-USER-TEST.md`, `FINAL-REMOTE-CLONE-TEST.md` and `FINAL-ANONYMOUS-CLONE.md`.

### Security/privacy/publication

Separate audits cover repository history, post-publication security, license/publication readiness and blockers. These include `FINAL-SECURITY-HISTORY-AUDIT.md`, `FINAL-POST-PUBLICATION-SECURITY.md`, `FINAL-LICENSE-PUBLICATION-AUDIT.md`, `FINAL-PUBLIC-READINESS.md`, `FINAL-PUBLICATION-BLOCKER-CLOSURE.md` and `FINAL-PUBLICATION.md`.

## AnythingLLM compatibility boundary

`ANYTHINGLLM-COMPATIBILITY.md` documents the upstream/runtime compatibility assumptions. A future AnythingLLM update must be treated as a compatibility event: reapply/inspect patches, run the suite, verify UI/API behavior and perform a fresh install. Do not update the claimed compatible version based only on successful startup.

## Model/image assets

`MODEL-ASSET-SETUP.md` and hardware-detection documentation describe external/local model setup. Large model files and private caches are not repository source. Distribution should keep model selection/configuration separate from source ownership. Generated Visual Atlas assets have their own provenance rules as described above.

## Testing and CI

GitHub Actions CI is in `.github/workflows/ci.yml`. Local acceptance documents capture tests that cannot be represented by hosted CI, including fresh-user/live non-regression and distribution checks. `LIVE-NONREGRESSION.md` and `FINAL-LIVE-NONREGRESSION.md` should be read when changing behavior that touches an existing live installation.

A PASS must name its layer: source/static, staged package, clean install, live non-regression, remote clone, anonymous clone, asset provenance, or security/license audit.

## Publication evidence hierarchy

For a quick current-state answer, start with `FINAL-PROJECT-CLOSURE.md` and `FINAL-PUBLICATION.md`. For release-specific state use `FINAL-RC-RELEASE.md` and `FINAL-V1-RELEASE.md`. For “is the public tree complete?” use the feature/file inventories plus final distribution result. For “is it safe/legal to publish?” use the security-history, post-publication security, license audit and asset-provenance documents.

## Repository map

- `README.md` — public entry point.
- `HANDOFF.md` — this master narrative/index.
- `COMPLETE-FEATURE-INVENTORY.md` — expected feature set.
- `COMPLETE-FILE-INVENTORY.json` — detailed file inventory.
- `FILE-OWNERSHIP-MAP.json` — ownership classification.
- `PATCH-MANIFEST.yaml` — patch set.
- `DISTRIBUTION-ARCHITECTURE.md` — packaging model.
- `MODEL-ASSET-SETUP.md` — external model assets.
- `DOCTOR.md` — diagnostics.
- `CHANGELOG.md` — concise version history.
- `FINAL-*` — dated/phase-specific acceptance and publication evidence.

## Maintenance procedure

Before changing the suite: checkpoint the accepted source; identify whether the change belongs to upstream patching, AAG-owned source, config, generated asset or packaging; update the ownership/feature inventories; run targeted tests; stage install; run clean/fresh-user acceptance when portability changes; run live non-regression when touching an existing installation; rescan secrets/privacy/licenses; rebuild deterministic distribution artifacts; remote/anonymous clone test when publishing; update changelog and this handoff.

## Documentation rule

Do not create another `FINAL-*` file merely to say “everything passed” unless it captures a distinct acceptance event with reproducible evidence. Prefer updating this handoff/index and the specific authoritative evidence document. Existing historical final reports should remain because they preserve the sequence that led to publication.

## Historical integrity rule

A later clean release does not erase earlier blockers. Keep blocker-closure and security-history reports. Do not move private machine artifacts into the public repository to make history more complete; describe them by sanitized evidence when necessary.

## Current handoff status

As of the documentation audit on 2026-09-15, this is one of the most thoroughly evidenced repositories in the portfolio. Its main documentation deficiency was discoverability: many strong reports lacked one master handoff tying them together. This file is now that entry point.