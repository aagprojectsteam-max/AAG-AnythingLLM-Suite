# AAG Maintenance Intelligence V1 skill

This canonical AnythingLLM skill maps semantic maintenance requests to the
Bridge's fixed `/maintenance` endpoint. It accepts one enumerated operation and
typed fields only. It never accepts executables, argv, shell fragments, URLs,
service actions, Docker actions, or cleanup authority.

## Path normalization

The trusted Maintenance core still requires a path for every path-scoped tool
and remains the final canonicalization, symlink, mount, exclusion, and protected
resource authority.

At the AnythingLLM boundary only, a pathless `maintenance_plan` is normalized
to `/mnt/data/AI`, the configured V1 snapshot root. This is deterministic and
does not depend on Hebrew or English wording. Other path-scoped operations must
provide a path. The handler rejects lexical paths outside the existing
`/mnt/data` and `/var/log` scope before Bridge dispatch; the core then performs
the authoritative policy validation.

## Grounding rule

A conversational plan may be presented only from a successful
`aag-maintenance-scan-envelope-v1` whose result is an
`aag-maintenance-plan-v1` with:

- `execution_authority: NONE`;
- `zero_mutations: true`;
- every item `execution_status: not_executed`.

The handler appends an integration-only `presentation_policy` and a filtered
`grounded_recommendations` list to the successful typed plan. The latter may
contain only `LOW_RISK_CANDIDATE` items with a positive numeric
`estimated_reclaimable_bytes`. Other logical or allocated sizes remain measured
observations and must not be converted into reclaim estimates or deletion
recommendations. If the filtered list is empty, the answer must say that no
evidence-backed cleanup candidate was found. Commands and execution steps are
never part of the conversational plan.

If validation, policy, Bridge, or response invariants fail, the handler returns
a structured error with no plan items and explicit response constraints. The
conversational layer must ask for clarification or state that no grounded plan
is available. It must not invent reclaim estimates, cleanup candidates, or
commands from an error response.

Version 1.0.2 is the focused Stage 15 grounding fix. AnythingLLM rereads the
manifest and clears the handler module cache for each fresh agent session, so
deployment does not require a container restart.
