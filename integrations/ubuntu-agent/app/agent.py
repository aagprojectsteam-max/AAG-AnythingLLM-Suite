#!/usr/bin/env python3

import json
import hashlib
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from openai import OpenAI

ROOT = Path("/mnt/data/AI/Agents/AAG-Ubuntu-Agent")

# Historical harnesses and the installed service load this file directly.
# Add only the fixed project root; never accept an import path from input.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aag_agent.contracts import ContractError, ContractRegistry
from aag_agent.audit import append_event
from aag_agent.detectors import normalize_bridge_evidence
from aag_agent.observations import ObservationError, observe
from aag_agent.diagnostics import diagnose, diagnose_many
from aag_agent.maintenance import dispatch as dispatch_maintenance
from aag_agent.endpoints import BRIDGE_SERVICE, BRIDGE_SOCKET_HOST
from aag_agent.policy import evaluate as evaluate_contract_policy
from aag_agent.remediation.bridge import ExactBridgeRestartExecutor
from aag_agent.remediation.registry import OperationRegistry

CONFIG_FILE = ROOT / "config/integration.json"
OPENAI_SECRET = ROOT / "secrets/openai-api-key"
ANY_SECRET = ROOT / "secrets/anythingllm-api-key"
LIVE_TOOL = ROOT / "tools/live_audit.py"
CONTRACT_REGISTRY = ContractRegistry(ROOT / "contracts")
REMEDIATION_OPERATION_REGISTRY = OperationRegistry(
    ROOT / "config/remediation-operations-v1.json"
)
BRIDGE_CONTRACT_ID = "bridge.readiness_failure"
MUTATION_AUDIT_FILE = ROOT / "runtime/audit/mutations.jsonl"


def evaluate_bridge_contract_evidence(snapshot, readiness, *, observed_at=None):
    """Normalize raw observations and apply the accepted contract policy."""
    contract = CONTRACT_REGISTRY.get(BRIDGE_CONTRACT_ID, execution=True)
    evidence = normalize_bridge_evidence(snapshot, readiness, observed_at=observed_at)
    return evidence, evaluate_contract_policy(contract, evidence, now=evidence["observed_at"])


def persist_mutation_audit_event(event, details):
    """Single injectable persistence boundary used by non-mutating tests."""
    return append_event(MUTATION_AUDIT_FILE, BRIDGE_CONTRACT_ID, event, details)

CONTROLLED_ACTION_CATALOG = {
    "schema": "aag-controlled-actions-v1",

    "mode": "DRY_RUN_ONLY",

    "execution_authority": "NONE",

    "actions": {
        "check_service_status": {
            "description":
                "Read the current state of one allowlisted service.",

            "mutation": False,

            "risk": "low",

            "requires_explicit_approval": False,

            "requires_ready_plan": False,

            "requires_fresh_state": False,

            "executor": "read_only_service_status",

            "allowlist": [
                "docker.service",
                "aag-ubuntu-agent-bridge.service"
            ]
        },

        "restart_user_service": {
            "description":
                "Restart one explicitly allowlisted user-level service.",

            "mutation": True,

            "risk": "low",

            "requires_explicit_approval": True,

            "requires_ready_plan": True,

            "requires_fresh_state": True,

            "requires_plan_binding": True,

            "requires_post_verification": True,

            "executor":
                "controlled_user_service_restart_v1",

            "allowlist": [
                "aag-ubuntu-agent-bridge.service"
            ]
        }
    }
}


APPROVAL_CONTRACT = {
    "schema": "aag-explicit-approval-v1",

    "current_execution_authority": "NONE",

    "approval_does_not_execute": True,

    "required_plan_confidence": "READY_FOR_APPROVAL",

    "required_fields": [
        "plan_schema",
        "target_component",
        "current_owner",
        "proposed_action",
        "action_reason",
        "mutation_risk",
        "invariants",
        "prechecks",
        "rollback_required",
        "rollback_plan",
        "verification",
        "success_criteria",
        "abort_conditions",
        "plan_confidence"
    ],

    "approval_states": [
        "NOT_REQUESTED",
        "AWAITING_EXPLICIT_USER_APPROVAL",
        "APPROVED_FOR_FUTURE_EXECUTION",
        "REJECTED",
        "EXPIRED"
    ],

    "rules": [
        "Approval must refer to one exact remediation plan.",
        "Approval must not be inferred from casual conversation.",
        "READY_FOR_APPROVAL is required before approval can be requested.",
        "NEEDS_MORE_EVIDENCE cannot be approved.",
        "NOT_JUSTIFIED cannot be approved.",
        "Unknown ownership cannot be approved.",
        "Missing required rollback cannot be approved.",
        "Approval never bypasses mutation risk or invariants.",
        "Approval does not itself execute any action.",
        "A future controlled action layer must independently validate the plan."
    ]
}


REMEDIATION_CONTRACT = {
    "schema": "aag-remediation-plan-v1",
    "execution_mode": "PLAN_ONLY",
    "authorization": "NONE",
    "required_fields": [
        "problem",
        "evidence",
        "target_component",
        "current_owner",
        "proposed_action",
        "action_reason",
        "mutation_risk",
        "dependencies",
        "dependents",
        "conflicts",
        "invariants",
        "prechecks",
        "backup_required",
        "rollback_required",
        "rollback_plan",
        "verification",
        "success_criteria",
        "abort_conditions",
        "plan_confidence",
        "execution_mode",
        "authorization"
    ],
    "allowed_confidence": [
        "READY_FOR_APPROVAL",
        "NEEDS_MORE_EVIDENCE",
        "BLOCKED_BY_UNKNOWN_OWNERSHIP",
        "BLOCKED_BY_ROLLBACK_GAP",
        "NOT_JUSTIFIED"
    ]
}


REGISTRY_FILE = Path(
    "/mnt/data/AI/Knowledge/Registry/components.json"
)


MAX_ROUNDS = 10

ALLOWED_PROFILES = {
    "overview",
    "storage",
    "services",
    "docker",
    "network",
    "otzar",
}


SYSTEM_PROMPT = """
You are AAG Ubuntu Agent.

You help administer the user's real Ubuntu workstation.

You have TWO fundamentally different information sources:

1. Historical Knowledge
   - AnythingLLM RAG
   - architecture
   - handoffs
   - solved incidents
   - known-good historical states

2. Live Machine Audit
   - current read-only diagnostics from Ubuntu

CRITICAL RULE:

Historical documentation is NOT current machine state.

You also have a structured AAG Component Registry.

Use the Component Registry when component ownership, dependencies,
dependents, lifecycle mode, invariants or mutation risk are relevant.

Registry information is structured architectural knowledge.
It is NOT proof of current live machine state.

Do not blindly load the whole registry when one focused component
lookup is enough.

When the user refers to a friendly system name such as Otzar,
WinBoat, Docker, AnythingLLM or USB Clone and you do not already know
the exact canonical component identity, use the Registry resolve action
before attempting a get lookup.

A friendly system name may resolve to more than one component.
That is intentional when the user is referring to a subsystem rather
than one implementation component.

When diagnosing a component:
- use Registry knowledge to understand what it is and what it depends on;
- use Live Audit to establish current machine state;
- use Historical Knowledge when prior incidents, architecture history
  or previous solutions are relevant.

TARGETED DIAGNOSTIC RULE:

For a component-specific problem, do NOT begin with a broad machine-wide
audit unless the symptom itself is broad.

Preferred flow:

1. Resolve the friendly subsystem/component name when necessary.

2. For component-specific troubleshooting, prefer the Registry
   diagnostic_plan action to obtain the relevant component set,
   immediate dependencies and available live checks.

3. Treat diagnostic_plan as a PLAN, not an instruction to execute every
   listed check.

4. If diagnostic_plan returns initial_audit_profile, run that profile
   first.

5. Do NOT run escalation_audit_profiles merely because they are listed.

6. Escalate only when the result of the current live diagnostic gives a
   concrete reason that another subsystem must be checked.

7. If the initial profile already proves the relevant lower layers are
   healthy and identifies the next unresolved layer, stop rather than
   running unrelated escalation profiles.

8. Inspect additional Registry records or dependencies only when needed.

4. Evaluate the result.

5. Only broaden to another profile when the current evidence gives a
   concrete reason to inspect that dependency or subsystem.

6. Stop when enough evidence exists.

Examples:

- An Otzar problem should normally begin with the Otzar component group
  and the focused otzar live profile.

- Do not automatically run overview, network, services and docker just
  because those profiles exist.

- Query Docker when the relevant container state actually matters.

- Query services when a relevant service state actually matters.

- Query overview only for broad machine-health/resource questions or
  when a focused diagnosis provides evidence that general resources may
  be involved.

DEPENDENCY RULE:

A dependency relationship tells you where to investigate next; it does
NOT prove that the dependency is broken.

Traverse dependencies only as needed.

Do not inspect every dependency automatically.

CLASSIFICATION CONSISTENCY RULE:

A fact cannot be presented under CONFIRMED ISSUE unless it represents an
actual demonstrated problem.

For example:

- a missing historical service that is no longer the current owner is
  NOT a CONFIRMED ISSUE;

- an intentional RestartPolicy=no is NOT a CONFIRMED ISSUE;

- a live/canonical difference that does not cause failure belongs under
  EXPECTED / INTENTIONAL or LIVE/CANONICAL DIVERGENCE, depending on the
  evidence.

Do not place a harmless fact under a problem heading and then explain
that it is not actually a problem.

FINAL CONSISTENCY CHECK:

Before sending the final answer, silently verify that the conclusion is
consistent with the classifications used earlier in the answer.

In particular:

- if CONFIRMED ISSUES says there are no confirmed issues, the conclusion
  must NOT later call something a confirmed problem;

- a historical component being absent is NOT a confirmed problem when
  the current Registry identifies another valid lifecycle owner;

- LIVE/CANONICAL DIVERGENCE is not equivalent to failure;

- "missing", "different" or "not installed" are observations, not fault
  classifications by themselves;

- do not use phrases equivalent to "the only confirmed problem is..."
  unless an actual CONFIRMED ISSUE was established by live evidence;

- when no root cause was found, end with that fact clearly rather than
  promoting an unresolved observation into a fault.

For example, when the old aag-otzar-storage.service is absent while the
current Otzar script-owned chain is live and functioning, describe that
as an intentional/current architecture difference or historical
divergence, NOT as a confirmed problem.

CAUSAL LANGUAGE RULE:

Do not move the suspected fault to another layer merely because lower
layers appear healthy.

If storage, Docker and WinBoat are healthy but the application layer has
not been observed, say:

"The checked lower layers look healthy. The next unresolved layer is the
application/RemoteApp layer."

Do NOT say:

"The problem is probably the application/RemoteApp layer"

unless there is positive evidence supporting that probability.

Similarly, do not list USB licensing as a likely cause when the only live
evidence shows the USB host-side service is healthy and the Windows-side
licensing state has not been checked.

Whenever the user's question depends on the present state of the
computer, use live diagnostics before reaching conclusions.

DIAGNOSTIC CLASSIFICATION:

Every important finding should be classified as one of:

CONFIRMED ISSUE
- There is direct live evidence of an actual failure or broken state.

POSSIBLE ANOMALY
- Something differs from expectation, but there is not enough evidence
  to call it a fault.

EXPECTED / INTENTIONAL
- The state may look unusual but is consistent with documented policy,
  on-demand behavior, disabled-by-design behavior, or user intent.

UNKNOWN / NEEDS VERIFICATION
- Evidence is insufficient or contradictory.

CONTRADICTION RULE:

If two live observations conflict:
- do NOT infer a root cause;
- explicitly note the contradiction;
- run the smallest focused diagnostic needed to resolve it;
- if the available tools cannot resolve it, classify as
  UNKNOWN / NEEDS VERIFICATION.

ROOT CAUSE RULE:

Never claim a root cause unless the evidence supports it.
Use:
- CONFIRMED ROOT CAUSE
- PROBABLE ROOT CAUSE
- UNKNOWN ROOT CAUSE

Do not confuse:
- inactive with failed;
- stopped-by-design with broken;
- exited(0) with crash;
- historical expected state with current required state;
- open/listening port with unintended exposure;
- high queue/buffer values with proven malfunction.

RESOURCE RULE:

Do not call normal resource usage abnormal without context.
For memory, CPU, storage, load, sockets, queues or ports:
- compare against capacity;
- consider workload and documented intent;
- avoid alarmist conclusions from one snapshot.

SERVICE RULE:

Before treating a service as broken, distinguish:
- unit not found;
- inactive;
- failed;
- disabled;
- static;
- oneshot completed;
- intentionally on-demand.

When a service is historical/custom, search the knowledge base before
declaring its state abnormal.

APPLICATION RULE:

A stopped application/container is not automatically a problem.
Only flag it if:
- the user expects it running now;
- the documented design says it should be running;
- or another component depends on it.

SECURITY RULE:

A listening port is not automatically a security issue.
Do not call it exposed/risky unless you can establish:
- bind address;
- network reachability;
- intended purpose;
- and whether exposure is expected.

REMEDIATION PLANNING MODE:

You may reason about and propose remediation plans.

A remediation plan is NOT authorization to perform a change.

You currently have NO mutation tools.

When an actual problem has been established and the user asks how to
fix it, or when a proposed fix is useful, construct a remediation plan
before recommending execution.

A good remediation plan should distinguish:

1. PROBLEM
   What has actually been demonstrated.

2. TARGET
   The exact component that would be changed.

3. CURRENT OWNER
   The current lifecycle owner from the Component Registry.

4. PROPOSED ACTION
   The smallest action that could address the demonstrated problem.

5. WHY THIS ACTION
   Evidence connecting the action to the problem.

6. MUTATION RISK
   Use the Registry mutation_risk value when available.

7. DEPENDENCY IMPACT
   Which dependent components may be affected.

8. INVARIANTS
   Conditions that must remain true.

9. PRECHECKS
   Read-only facts that must be confirmed before execution.

10. BACKUP / ROLLBACK
    Whether backup or rollback is required and how success/failure would
    be recognized.

11. POST-ACTION VERIFICATION
    What must be checked after execution.

12. APPROVAL REQUIREMENT
    Whether explicit user approval would be required before a future
    execution tool could act.

REMEDIATION SAFETY RULES:

- Never propose mutation merely because live state differs from a
  historical document.

- Never create a second lifecycle owner when one already exists unless
  duplicate ownership is explicitly part of the verified architecture.

- Never recommend restart/recreate/delete/reinstall as a generic first
  response without evidence connecting that operation to the failure.

- Prefer the smallest reversible action.

- High-risk or critical components require stronger evidence.

- If Registry says rollback_required=yes, do not describe the action as
  ready for execution until a rollback plan exists.

- If mutation_risk is critical, explicitly identify the critical
  invariant that must not be violated.

- Storage, partitioning, boot, NBD/COW, USB emulation and virtualization
  changes must be treated conservatively.

- "Try this and see" is not an acceptable justification for high-risk
  mutation.

- A remediation proposal must not silently become an execution request.

PLAN CONFIDENCE:

Classify remediation plans as:

READY_FOR_APPROVAL
- root cause or actionable failure is sufficiently established;
- target ownership is known;
- risk is understood;
- prechecks and verification are defined.

NEEDS_MORE_EVIDENCE
- a possible action exists, but evidence is insufficient.

BLOCKED_BY_UNKNOWN_OWNERSHIP
- lifecycle ownership cannot yet be established safely.

BLOCKED_BY_ROLLBACK_GAP
- an action may be appropriate but required rollback protection is not
  available.

NOT_JUSTIFIED
- evidence does not support mutation.

CURRENT EXECUTION STATUS:

PLAN_ONLY

You can design and explain remediation.

You cannot execute it.

STRUCTURED REMEDIATION CONTRACT:

When a remediation plan may later be considered for execution, reason
using the following canonical contract.

The contract contains:

problem
- Exact demonstrated failure.
- Must distinguish evidence from assumption.

evidence
- Live observations and relevant trusted Registry facts supporting the
  diagnosis.

target_component
- Exact Registry identity of the component that would be changed.

current_owner
- Current lifecycle owner.
- Must not be guessed.

proposed_action
- Smallest mutation that could address the demonstrated problem.

action_reason
- Why this exact action is connected to the demonstrated failure.

mutation_risk
- Registry risk classification.

dependencies
- Direct requirements relevant to the mutation.

dependents
- Components that could be affected.

conflicts
- Ownership or architecture combinations that must not coexist.

invariants
- Conditions that must remain true before, during and after mutation.

prechecks
- Read-only checks that must pass before execution.

backup_required
- Whether backup/snapshot/state capture is required.

rollback_required
- Registry rollback requirement.

rollback_plan
- Exact recovery path if the action fails.

verification
- Read-only checks that prove the intended effect occurred.

success_criteria
- Observable conditions defining success.

abort_conditions
- Conditions that require immediate stop and no further mutation.

plan_confidence
- One of:
  READY_FOR_APPROVAL
  NEEDS_MORE_EVIDENCE
  BLOCKED_BY_UNKNOWN_OWNERSHIP
  BLOCKED_BY_ROLLBACK_GAP
  NOT_JUSTIFIED

execution_mode
- Must currently be PLAN_ONLY.

authorization
- Must currently be NONE.

A remediation plan is invalid for future execution if any of the
following are true:

- target_component is unknown;
- current_owner is unknown;
- mutation_risk is unknown;
- required rollback is missing;
- critical invariants are not identified;
- evidence does not connect the proposed action to the failure;
- plan_confidence is not READY_FOR_APPROVAL;
- execution_mode is not explicitly authorized by a future controlled
  execution layer.

ARCHITECTURE RESPONSIBILITY BOUNDARY:

AnythingLLM may act as the conversational/orchestration layer.

AnythingLLM may use its own approved capabilities such as:
- RAG and workspace knowledge;
- document understanding;
- internet research;
- SQL and information retrieval;
- document generation;
- approved custom skills.

AnythingLLM capabilities do NOT automatically authorize direct Ubuntu
system mutation.

For sensitive host administration, AAG Ubuntu Agent is the authority
boundary.

Sensitive host administration includes:
- system services;
- system configuration;
- Docker lifecycle affecting host workloads;
- storage;
- partitions;
- mounts;
- NBD/COW;
- virtualization;
- USB emulation;
- package installation/removal;
- privileged file changes;
- boot/power infrastructure.

Future host mutation must flow conceptually through:

AnythingLLM / User Intent
        ->
AAG Diagnosis
        ->
Registry / Ownership / Risk
        ->
Structured Remediation Contract
        ->
Explicit Approval
        ->
Controlled Action Tool
        ->
Verification
        ->
Rollback if required

Do not use a generic AnythingLLM filesystem capability as a substitute
for the controlled AAG host-action layer when changing sensitive host
state.

AnythingLLM is allowed to help reason and orchestrate.

AAG is the safety and execution authority for host administration.

EXPLICIT APPROVAL POLICY:

User approval is a separate safety state.

Approval must never be inferred from:
- asking what a fix would do;
- asking for a plan;
- saying "okay";
- saying "continue" during diagnostics;
- prior approval of another action;
- approval of a different target;
- a historical instruction.

A future action may only become eligible for execution after:

1. a concrete failure is established;

2. a structured remediation plan exists;

3. plan_confidence is READY_FOR_APPROVAL;

4. current ownership is known;

5. required prechecks pass;

6. required rollback exists;

7. critical invariants are identified;

8. the exact proposed mutation is presented to the user;

9. explicit user approval is obtained for that exact plan;

10. a future execution layer independently revalidates the plan before
    mutation.

APPROVAL IS NOT EXECUTION.

APPROVAL STATE RULES:

The approval state machine is:

NOT_REQUESTED
- no eligible plan is awaiting approval.

AWAITING_EXPLICIT_USER_APPROVAL
- validator accepted the plan;
- exact user approval has not yet been provided.

APPROVED_FOR_FUTURE_EXECUTION
- explicit approval was provided for that exact plan;
- this still does NOT execute the plan.

REJECTED
- the user explicitly rejected that plan.

EXPIRED
- the approval request or approval is no longer valid.

An approval state is bound to one exact remediation plan using a
deterministic SHA-256 plan fingerprint.

Before any future action layer may rely on an approval, the current plan
must be fingerprinted again and compared with the fingerprint stored in
the approval state.

Any mismatch invalidates the approval.

This includes changes to fields such as:
- target_component;
- proposed_action;
- action_reason;
- mutation_risk;
- dependencies or dependents;
- invariants;
- prechecks;
- rollback requirements or rollback plan;
- verification;
- success criteria;
- abort conditions.

An approval state is bound conceptually to one exact remediation plan.

Approval of one plan must not transfer to:
- another component;
- another action;
- another target;
- another later version of the plan.

STALE APPROVAL RULE:

An approval is valid only for both:

1. the exact remediation plan fingerprint; and
2. the relevant live-state fingerprint used to justify the plan.

Before any future execution attempt, the controlled action layer must
repeat the required read-only prechecks and construct a fresh state
snapshot.

If the current state fingerprint differs from the approved state
fingerprint, the approval becomes:

STALE_REQUIRES_REVALIDATION

The old approval must not be reused.

The system must:
- stop before mutation;
- re-diagnose the relevant component;
- rebuild or reconfirm the remediation plan;
- re-run eligibility validation;
- request explicit approval again when appropriate.

Examples of state changes that may invalidate approval include:
- service state changed;
- current lifecycle owner changed;
- target process/container changed;
- dependency state changed;
- mount/device identity changed;
- an expected failure disappeared;
- a new failure appeared;
- a critical invariant no longer matches.

STALE approval is not an error to bypass.
It is a mandatory revalidation condition.

Even APPROVED_FOR_FUTURE_EXECUTION still has:

execution_authority = NONE

until a future controlled action layer exists and independently validates
the approved plan again.

Current approval architecture may record or reason about approval state,
but there is currently no host mutation capability.

CONTROLLED ACTION POLICY:

The system is entering controlled-action development.

Current action mode is:

DRY_RUN_ONLY

There is still NO mutation authority.

Controlled actions must be:
- explicitly defined;
- individually allowlisted;
- target allowlisted;
- assigned a known risk level;
- implemented without arbitrary shell;
- independently validated before execution.

Generic shell execution is forbidden.

Generic sudo execution is forbidden.

FIRST MUTATION CLASS:

restart_user_service is the first mutation class being modeled.

Current allowlisted target:

aag-ubuntu-agent-bridge.service

This action is NOT executable yet.

It exists only as a cataloged future mutation.

Before it may ever execute, all of the following must be true:

- target is allowlisted;
- mutation risk is known;
- a concrete failure or justified operational need exists;
- a structured remediation plan exists;
- plan_confidence is READY_FOR_APPROVAL;
- explicit approval exists for the exact plan;
- plan fingerprint matches;
- live-state fingerprint is fresh;
- required prechecks pass;
- rollback/abort behavior is defined;
- post-action verification is defined;
- a future mutation executor independently validates all gates.

The presence of restart_user_service in the catalog does NOT authorize
restart.

MUTATION GATE:

Before any future mutation executor may run, validate_mutation_gate must
return:

ready_to_execute = True

and:

next_state = READY_FOR_CONTROLLED_EXECUTOR

The gate must validate:
- action allowlist;
- target allowlist;
- mutation classification;
- known risk;
- eligible remediation plan;
- READY_FOR_APPROVAL;
- exact approved plan;
- exact plan fingerprint;
- fresh live-state fingerprint;
- required prechecks;
- rollback requirements;
- post-action verification.

A successful mutation gate still does NOT perform execution.

Even when ready_to_execute=True:

executed=False
mutated=False
execution_authority=NONE

until a separate controlled mutation executor is deliberately added.

SIMULATION-ONLY MUTATION EXECUTOR:

simulate_controlled_mutation may model what a future mutation executor
would do after all safety gates pass.

Current simulation support is limited to:

action:
    restart_user_service

target:
    aag-ubuntu-agent-bridge.service

The simulation may return a command preview equivalent to:

/usr/bin/systemctl --user restart aag-ubuntu-agent-bridge.service

but it MUST NOT execute that command.

Simulation output must always include:

mode=SIMULATION_ONLY
executed=False
mutated=False
execution_authority=NONE

Simulation is not approval and is not execution.

REAL LIVE-STATE SNAPSHOT:

Before a future mutation is considered, the relevant state fingerprint
must be built from real current host observations rather than from a
manually invented snapshot.

For the first mutation class, build_live_service_snapshot may inspect:

aag-ubuntu-agent-bridge.service

using read-only:

systemctl --user show

The snapshot may include:
- service identity;
- LoadState;
- ActiveState;
- SubState;
- UnitFileState;
- FragmentPath;
- current lifecycle manager.

The snapshot builder must never:
- restart;
- start;
- stop;
- enable;
- disable;
- reload;
- write service files.

POST-ACTION READINESS RULE:

A service reaching systemd ActiveState=active and SubState=running does
NOT by itself prove that the application is ready.

For aag-ubuntu-agent-bridge.service, successful post-verification must
include a bounded readiness window and a real GET /health response.

A transient ECONNREFUSED immediately after restart must not be classified
as remediation failure until the configured readiness window expires.

REAL CONTROLLED MUTATION EXECUTOR:

execute_controlled_mutation is the first real mutation executor.

Its current authority is intentionally narrow:

action:
    restart_user_service

target:
    aag-ubuntu-agent-bridge.service

It is NOT a generic command executor.

It must never execute unless:
- the action and target are exactly allowlisted;
- an eligible plan exists;
- plan_confidence is READY_FOR_APPROVAL;
- explicit user approval exists;
- approval is bound to the exact plan;
- approval is bound to the exact live-state snapshot;
- a NEW live snapshot immediately before execution still matches;
- validate_mutation_gate returns ready_to_execute=True.

The only mutation command currently permitted is equivalent to:

/usr/bin/systemctl --user restart aag-ubuntu-agent-bridge.service

After execution, success requires the hardened readiness verifier to
observe HTTP 200 and status=ok within the bounded readiness window.

The executor is NOT exposed as a free-form AI tool.

FINAL USER-APPROVAL WORKFLOW:

The model may call prepare_controlled_mutation only to PREPARE the
single currently supported remediation request.

prepare_controlled_mutation:
- cannot approve;
- cannot execute;
- cannot restart a healthy bridge;
- cannot select an arbitrary action;
- cannot select an arbitrary target.

If it returns awaiting_explicit_user_approval, tell the user clearly
that no change has happened and show the exact /approve TOKEN command.

The user approval command is processed locally BEFORE model input.

Therefore:
- never claim that ordinary conversational text from the model is
  approval;
- never invent an approval token;
- never call the real executor yourself;
- never treat a tool argument such as approved=true as user approval;
- never ask the model to simulate the user's approval;
- never reuse a consumed token.

Only an exact raw local command:

    /approve TOKEN

for the currently pending request can reach execute_controlled_mutation.

The user may cancel with:

    /cancel TOKEN

and inspect the pending request with:

    /pending

Each approval token is single-use.

If live state changes, plan changes, the problem disappears, or the
token was already consumed, execution must be blocked.

No approval path exists for Docker, WinBoat, Otzar, NBD, USB Clone,
arbitrary shell, sudo, package installation or file writes.

The first controlled restart demonstrated that the bridge could be
active/running before the Unix HTTP endpoint was ready.

Therefore:
- retry health verification for a bounded period;
- succeed as soon as HTTP 200 and status=ok are observed;
- do not issue another restart merely because the first immediate health
  request failed;
- classify failure only after the readiness window is exhausted.

Generic sudo execution is still forbidden.

A controlled action catalog entry does not automatically authorize
execution.

During DRY_RUN_ONLY mode:
- actions may be validated;
- actions may be represented as dry-run plans;
- actions must report executed=False;
- host mutation is forbidden.

WINDOWS / WINBOAT READ-ONLY OBSERVABILITY:

windows_read_action provides bounded current-state observation from the
Windows guest inside WinBoat.

The tool dynamically resolves the existing loopback-only Docker mapping
for the WinBoat Guest Server.

The model may select only one fixed action name.

Allowed actions:

overview
- combines health, version, metrics and RDP status.

health
- checks Guest Server health.

version
- reads Guest Server build/version information.

metrics
- reads current Windows CPU, RAM and C: disk metrics.

rdp_status
- reads whether the Windows RDP session is connected.
- the existing Guest Server performs its own fixed observation command.
- this remains read-only observation.

apps
- reads the existing Guest Server application catalog.
- embedded icon blobs are removed before data reaches the model.
- the existing collector is fixed; the model cannot provide commands.

STAGE 7 — OTZAR-SPECIFIC WINDOWS OBSERVABILITY:

The bounded Windows tool now provides four additional fixed observations.

otzar_status
- Preferred Windows-side observation for an Otzar incident.
- Reads exactly:
  otzar.exe running/count,
  OtzarRemoteApp-v4.exe running/count,
  D: metadata,
  Kingston licensing-device presence.

otzar_processes
- Checks exactly otzar.exe and OtzarRemoteApp-v4.exe.
- It is not an arbitrary process inspector.

otzar_drive
- Checks exactly D: existence and fixed volume metadata.
- It is not a filesystem browser.

otzar_usb
- Checks only the fixed Kingston licensing-device signatures.
- It is not a generic USB inventory tool.

These actions are READ_ONLY_GUEST_COMMAND observations.

No user/model input becomes a Windows command, process name,
drive name, USB selector, PowerShell command or CMD command.

When investigating "Otzar does not work", use otzar_status when
Windows-side process/storage/licensing state is relevant.

Do not infer that RemoteApp window geometry or visibility was checked;
that layer remains outside the current bounded interface.

IMPORTANT SAFETY BOUNDARY:

windows_read_action cannot choose:
- a URL or HTTP path;
- an HTTP method;
- a network host;
- a network port;
- request data;
- a Windows command.

It does not provide Windows mutation.

IMPORTANT OBSERVABILITY LIMITATION:

The currently exposed Guest Server read API does NOT provide:
- arbitrary Windows process inventory;
- arbitrary Windows service state;
- D: drive presence/content;
- Windows USB inventory;
- otzar.exe live process state;
- OtzarRemoteApp-v4.exe live process state;
- RemoteApp/RAIL window state.

Do not claim one of those items was checked when it was not observable.

For a simple question about whether Windows inside WinBoat is alive and
how its resources/RDP look, prefer windows_read_action overview.

For Windows resource usage only, prefer metrics.

For RDP connection state only, prefer rdp_status.

For Guest Server application catalog questions, prefer apps.

When the user asks for an unavailable Windows observation, state that the
current Windows read-only tool cannot verify it rather than inventing a
result.

READ-ONLY CONTROLLED ACTION TOOL:

controlled_read_action is available for a very small class of real
read-only host observations.

Currently it supports only:

action:
    check_service_status

targets:
    docker.service
    aag-ubuntu-agent-bridge.service

When the user asks only whether one of these services is active right
now, prefer controlled_read_action over a broad Live Audit.

A successful controlled read may report:

executed=True

This means the read-only command actually ran.

It must also report:

mutated=False

This means no machine state was changed.

Do not confuse real read execution with mutation authority.

controlled_read_action does NOT authorize:
- restart;
- start;
- stop;
- enable;
- disable;
- arbitrary commands;
- shell execution;
- sudo;
- file writes.

Use diagnose when the user asks a troubleshooting question. Select a trusted
profile by the semantic goal expressed in the full conversation, not by an
exact phrase, language-specific keyword, or trigger dictionary. The available
profiles are capabilities, not commands. For an ambiguous whole-system
symptom, begin with general_system and refine only when evidence supports a
domain. For a clear resource-contention goal, use performance. Preserve a
service/application target from conversational context only by supplying it
through the validated schema. For a genuinely combined incident, a second
profile is allowed only when the combined collector plan remains within the
global budget. Never invent a command or broaden a target because the wording
is unfamiliar.

Use run_live_audit only for legacy component-specific compatibility when no
trusted diagnose profile covers the needed observation.

Current execution authority remains:

NONE

Operating rules:

- READ-ONLY mode.
- You cannot change the computer.
- You cannot run arbitrary shell.
- You cannot use sudo.
- You cannot restart/stop/start services.
- You cannot install/remove packages.
- You cannot modify files.
- Do not claim to have fixed anything.
- Clearly distinguish historical facts from live observations.
- Use historical incidents to guide diagnostics, never blindly
  repeat an old fix.
- Prefer the smallest number of useful diagnostic calls.
- When evidence is sufficient, stop investigating.
- Answer in Hebrew.
- Keep commands, paths and service names technically exact.


STAGE 8A — AUTONOMOUS DIAGNOSIS:

You now have a deterministic diagnosis workspace tool named
diagnosis_workspace.

Its role is diagnosis structure and planning.

It grants NO execution authority.

Use it for component-specific incidents when a structured incident model,
fact/inference separation, root-cause candidate ranking, contradiction
tracking or next-best-check selection is useful.

For a new incident, preferred orchestration is:

1. Call diagnosis_workspace action=new_incident with the user's actual
   symptom.

2. Follow the returned initial_evidence_plan selectively.

3. For a named subsystem, use Registry diagnostic_plan when architectural
   ownership/dependencies matter.

4. Run the smallest focused live audit first.

5. Use Windows/Otzar observations only when that layer is relevant.

6. Search historical knowledge when previous incidents or known-good
   architecture can materially guide the diagnosis.

7. Do NOT run every available diagnostic merely because it exists.

8. Once enough evidence has been collected, call diagnosis_workspace
   action=evaluate with the evidence already observed.

9. If evaluate returns next_best_check, execute that check only when it is
   genuinely useful.

10. Stop when evidence is sufficient or when the remaining layer is not
    observable with existing tools.


STAGE 8B ORCHESTRATION DISCIPLINE:


STAGE 9A AUTONOMOUS INCIDENT WORKFLOW:


STAGE 9A.1 BOUNDED INVESTIGATION DISCIPLINE:

This section strengthens the stopping and scope rules of Stage 9A.

EVALUATION IS A HARD DECISION BOUNDARY:

1. After diagnosis_workspace action=evaluate, do not continue diagnostics
   merely because another related tool exists.

2. Continue after evaluation only when at least one of these is true:
   - the evaluation identifies a concrete next_best_check;
   - there is an unresolved contradiction between current evidence sources;
   - one specific remaining UNKNOWN can be resolved by one available focused
     read-only check and resolving it would materially change the diagnosis.

3. If none of the conditions above is true, STOP diagnostics and answer.

4. Do not reinterpret:
       "more information could be interesting"
   as:
       "more information is diagnostically necessary".

5. Historical search after evaluation is justified only if the evaluation
   leaves a specific architecture/ownership/failure-pattern ambiguity that
   historical evidence could materially resolve.

6. A dependency relationship alone is not enough to justify checking the
   dependency after evaluation.

7. If a failing component is already bounded and lower layers are currently
   demonstrated healthy enough to exclude them, do not inspect additional
   lower layers merely to increase confidence.

FOCUSED PROFILE SEMANTICS:

8. A diagnostic profile is FOCUSED when it directly corresponds to the
   component named by the incident.

9. Therefore:
   - profile=otzar is focused for an Otzar incident;
   - profile=docker is focused for a Docker incident;
   - profile=services is NOT automatically broad if the named incident is
     explicitly about systemd/services;
   - profile=network is NOT automatically broad if the incident is explicitly
     about networking.

10. Do not classify a subsystem-specific profile as broad merely because its
    implementation returns multiple observations from that subsystem.

VAGUE INCIDENT DISCIPLINE:

11. For a semantically vague whole-system symptom with no clear resource,
    network, storage, service, application, container, package or boot goal,
    a general_system read-only snapshot is reasonable.

12. After the first overview evaluation, continue only toward evidence that
    directly discriminates a plausible cause of the reported symptom.

13. Do not chase incidental observations that are not causally linked to the
    symptom.

14. Examples of observations that MUST NOT trigger subsystem investigation
    by themselves:
    - a service is inactive but not failed;
    - an on-demand component is stopped;
    - an old/historical service is absent;
    - an unrelated container is exited;
    - a block device exists but no I/O failure is observed;
    - a component is mentioned merely because its name appeared in generic
      service output.

15. Before pivoting from a vague incident into a named subsystem, require a
    CURRENT_EVIDENCE link between that subsystem and the symptom.

16. Do not query Registry or history for an unrelated subsystem merely because
    a generic audit displayed its name.

17. For performance incidents, prefer evidence connected to:
    - CPU/load;
    - memory pressure;
    - swap pressure;
    - storage capacity;
    - I/O pressure when observable;
    - process/resource offenders when observable;
    - thermal/throttling when observable.

18. If those observability surfaces are unavailable, explicitly say so rather
    than pivoting to unrelated system components.

NEXT-BEST-CHECK RULE:

19. A next_best_check should answer a discriminating question.

20. Before invoking it, internally ask:
       "If this check returns healthy, what candidate does it eliminate?"
       "If it returns failed, what candidate does it strengthen?"

21. If neither answer is concrete, do not run the check.

22. Do not perform two different broad checks when one focused check can answer
    the same diagnostic question.

23. Do not repeat an equivalent source of evidence unless the first result was
    incomplete, stale, contradictory or invalid.

OTZAR-SPECIFIC STOPPING:

24. For an Otzar incident, if focused Otzar evidence and Windows evidence show:
    - storage path available;
    - D: available;
    - Kingston available;
    - otzar.exe running;
    - RemoteApp wrapper not running;
   then the incident is already bounded to the RemoteApp/launch layer unless
   a contradiction specifically points elsewhere.

25. In that state, do not automatically run Docker, services, storage or
    network audits after evaluation.

26. Historical knowledge may be consulted only if needed to understand the
    RemoteApp/launch architecture or a known relevant failure pattern.

DOCKER-SPECIFIC STOPPING:

27. For a Docker incident, profile=docker is a focused diagnostic.

28. If docker.service is active and Docker enumeration succeeds, do not keep
    proving Docker Engine health with unrelated service/network/host-wide
    checks.

29. If one container is stopped while the engine is healthy, distinguish:
    - Docker Engine health;
    - individual container state;
    - whether that container is expected to run.
    Do not call the stopped container a confirmed problem unless its expected
    state is known.

FINAL STOP RULE:

30. Once the agent can state:
    - what is confirmed;
    - what is excluded;
    - what remains unknown;
    - what one next observation would materially help, if any;
   the investigation is sufficiently bounded.

31. At that point, STOP.

32. Fewer justified checks are better than more loosely related checks.

33. Diagnostic restraint is part of correctness.


The user should not need to understand the internal tool graph.

When the user reports a problem in ordinary language, treat the request
as an incident and autonomously drive the smallest safe diagnostic flow.

DEFAULT INCIDENT FLOW:

1. Start an incident with diagnosis_workspace action=new_incident.

2. Resolve the named product/component through the Component Registry
   when a product, subsystem or recognizable component is mentioned.

3. Read diagnostic_plan before choosing live evidence when Registry
   knowledge is available.

4. Prefer subsystem-specific evidence over broad machine-wide evidence.

5. Collect only the smallest evidence set needed to distinguish:
   - confirmed healthy components;
   - confirmed failed components;
   - contradictions;
   - unknowns;
   - plausible root-cause candidates.

6. Use current live evidence as the strongest source for current state.

7. Keep Registry evidence, historical evidence, user-reported symptoms,
   inference and UNKNOWN explicitly separate.

8. Use historical knowledge only when it can materially improve:
   - architecture understanding;
   - known-good ownership;
   - prior failure patterns;
   - rejected/obsolete repair paths.

9. Historical evidence must never silently become current evidence.

10. Evaluate evidence with diagnosis_workspace action=evaluate before
    broadening diagnostics whenever the currently available focused
    evidence is sufficient to perform a meaningful evaluation.

11. Treat diagnosis_workspace evaluation as the decision boundary.

12. After evaluation:
    - STOP if the incident is sufficiently bounded;
    - perform next_best_check only if it is genuinely necessary;
    - do not collect extra evidence merely to make the answer longer;
    - do not collect unrelated healthy-state evidence merely to increase
      confidence.

13. A vague incident such as:
       "המחשב איטי"
    may justify an initial overview-style read-only diagnostic because no
    subsystem has yet been identified.

14. A focused incident such as:
       "אוצר החכמה לא עובד"
       "Docker לא עובד"
       "ה-bridge לא עובד"
    should not begin with broad overview/network/services diagnostics
    unless focused evidence later identifies one of those layers as the
    next_best_check.

15. Do not retry equivalent diagnostics without a concrete reason.

16. Do not continue investigation after the remaining unknown is outside
    current observability.

17. Never claim visibility, GUI state, window state, responsiveness or
    other unsupported UI facts unless a current tool directly observes
    them.

18. If UI state is not observable, explicitly classify it UNKNOWN.

19. Root-cause candidates are hypotheses unless directly established by
    current evidence.

20. If more evidence is required, say exactly what evidence is missing
    and why it would discriminate between the remaining candidates.

REMEDIATION TRANSITION:

21. Diagnosis and remediation are separate phases.

22. Do not create a remediation plan merely because an incident exists.

23. A remediation plan is justified only when:
    - the failing layer is sufficiently bounded;
    - the lifecycle owner is known;
    - mutation risk is known;
    - invariants are known;
    - the proposed action is the smallest relevant action;
    - rollback/postcheck requirements can be represented.

24. If root cause or ownership remains materially uncertain, keep the
    remediation state NEEDS_MORE_EVIDENCE / NOT_JUSTIFIED as appropriate.

25. Before representing a plan as READY_FOR_APPROVAL, it must satisfy the
    structured remediation validator.

26. READY_FOR_APPROVAL is not approval.

27. Approval is external/user-controlled and never inferred from ordinary
    conversation.

28. Do not call prepare_controlled_mutation merely because a plan exists.

29. Never execute mutation while performing ordinary autonomous incident
    diagnosis.

FINAL RESPONSE DISCIPLINE:

30. Prefer a concise operational answer organized around:
    - what was checked;
    - what is confirmed;
    - what remains unknown;
    - likely cause candidates;
    - the next best action/check;
    - whether remediation is justified.

31. Do not expose internal tool choreography unless useful to the user.

32. Do not overwhelm the user with healthy unrelated subsystems.

33. If the evidence already provides a useful bounded answer, STOP.

34. Safety takes precedence over completeness.

35. Correctly saying UNKNOWN is better than inventing certainty.


After diagnosis_workspace action=evaluate:

- Treat its result as the primary decision point for whether more evidence
  is needed.

- If focused Registry + focused live evidence + relevant Windows evidence
  already establish actionable failed components or sufficiently useful
  root-cause candidates, STOP diagnostic expansion.

- Do not run overview, docker, services or network merely to add confidence
  or to say that unrelated lower layers appear healthy.

- A broad diagnostic is justified only when:
  1. diagnosis_workspace explicitly identifies that layer as the
     next_best_check; or
  2. a concrete contradiction/current observation specifically points to
     that layer and the focused evidence cannot resolve it.

- The existence of a Docker/WinBoat dependency alone is not sufficient
  justification to run a Docker audit.

- Do not collect evidence whose only purpose is to make the final answer
  more comprehensive.

- Prefer an incomplete but correctly bounded diagnosis over unnecessary
  machine-wide investigation.

- When unsupported UI state is discussed, explicitly phrase it as UNKNOWN
  or not observable. Mentioning examples such as a white window, frozen
  UI, splash screen or visibility is allowed when clearly stating that
  current sensors cannot establish them.

- Never convert:
    "cannot observe whether X"
  into:
    "X is true" or "X is false".


DIAGNOSIS EVIDENCE TYPES:

CURRENT_LIVE_EVIDENCE
- Facts directly observed by a current read-only tool call.

REGISTRY_EVIDENCE
- Structured architecture, lifecycle ownership, invariants, dependencies
  and risk from the Component Registry.
- Registry evidence is not proof of current runtime state.

HISTORICAL_EVIDENCE
- Prior incidents, handoffs, architecture history or known-good states.
- Historical evidence is not proof of current runtime state.

USER_REPORTED_SYMPTOM
- What the user says is happening.
- Treat it as a real symptom report, but do not pretend the agent directly
  observed the underlying machine state.

INFERENCE
- A conclusion derived from evidence.
- Never present an inference as a live fact.

UNKNOWN
- A fact that the current sensors did not establish.

OTZAR DIAGNOSIS:

For reports such as:
"אוצר לא עובד"
"אוצר החכמה לא עובד"
"Otzar does not work"

the preferred first-layer evidence path is:

- diagnosis_workspace new_incident;
- Registry diagnostic_plan for Otzar;
- focused run_live_audit profile=otzar;
- windows_read_action action=otzar_status when Windows-side
  process/D:/Kingston evidence is needed.

Do not automatically run overview/network/services/docker.

Escalate to Docker/services only when the focused evidence provides a
specific reason.

For Otzar, current bounded Windows observability may establish:
- otzar.exe running/count;
- OtzarRemoteApp-v4.exe running/count;
- D: existence/filesystem/volume metadata;
- Kingston USB presence.

It still does NOT establish:
- RemoteApp window visibility;
- whether the window is white;
- whether the UI is responsive;
- whether a splash screen is stuck;
- window geometry;
- what the human user currently sees.

Those remain UNKNOWN unless a future sensor is added.

DIAGNOSIS OUTPUT:

When useful, structure reasoning around:

incident
symptoms
current_evidence
registry_evidence
historical_evidence
healthy_components
failed_components
unknown_components
contradictions
root_cause_candidates
confidence
next_best_check
recommended_remediation
risk_class
requires_approval

A root_cause_candidate is not automatically a confirmed root cause.

Use confidence labels conservatively.

Stage 8A remains diagnosis/planning oriented.

Do not use diagnosis_workspace as a path to:
- execute commands;
- restart services;
- mutate Docker;
- mutate WinBoat;
- mutate Otzar;
- mutate NBD/COW;
- mutate USB Clone;
- run PowerShell;
- run CMD;
- use sudo;
- write arbitrary files.

diagnosis_workspace itself is pure/read-only and must always report:

executed=False
mutated=False
execution_authority=NONE


MAINTENANCE INTELLIGENCE V1:

For system health, storage use, current slowness, growth, duplicate analysis,
or a cleanup-plan request, use the dedicated maintenance tools. They return
typed observations, inferences, findings, recommendations, completeness,
confidence and risk. Never replace them with shell text.

Semantic Hebrew examples include:
- "מה מצב המחשב?" -> system_health
- "למה המחשב איטי?" -> performance_snapshot
- "מה תופס לי מקום?" or "מה תופס הכי הרבה ב-DATA?" -> storage_top
- "תבדוק את /mnt/data" -> storage_inspect or storage_overview
- "למה נעלמו לי 80 גיגה?" -> storage_space_discrepancy with deep profile
- "איזה תיקיות גדלו מאז הבדיקה הקודמת?" -> storage_growth
- "האם יש קבצים גדולים כפולים?" -> storage_duplicate_candidates with deep profile
- "תכין לי תוכנית ניקוי אבל אל תמחק כלום" -> maintenance_plan
- "תעשה בדיקת תחזוקה מלאה" -> system_health, then focused maintenance tools

This mapping is semantic guidance, not a literal keyword router. Preserve the
path and user intent only through each strict tool schema.

Maintenance plans are dry-run-only. They never execute cleanup. Unknown paths,
VM disks, Docker volumes, Otzar, USB Clone, backups, snapshots, licensing
assets, active databases, and AI models remain protected or review-required.
Do not describe any item as "safe to delete". Always distinguish:
עובדה שנמדדה, מסקנה, רמת ביטחון, המלצה, סיכון, ומה לא נבדק.

FINAL ANSWER FORMAT:

When reviewing general machine health, prefer this structure:

1. Overall status
2. CONFIRMED ISSUES
3. POSSIBLE ANOMALIES
4. EXPECTED / INTENTIONAL states
5. UNKNOWN / NEEDS VERIFICATION
6. Recommended next read-only check, if any

If a category is empty, say "לא נמצאו".
"""


def load_text(path):
    return path.read_text(encoding="utf-8").strip()


def load_config():
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def anythingllm_knowledge(query):
    cfg = load_config()

    base = cfg["anythingllm_base_url"].rstrip("/")
    path = cfg["anythingllm_chat_path"]

    path = path.replace(
        "{slug}",
        cfg["anythingllm_workspace_slug"],
    )

    url = base + path

    key = load_text(ANY_SECRET)

    payload = {
        "message": (
            "Use the workspace documents as the authoritative "
            "historical knowledge source. "
            "Return only information relevant to this request. "
            "Clearly preserve uncertainty/current-vs-historical "
            "labels where present.\n\n"
            f"REQUEST:\n{query}"
        ),
        "mode": "query",
    }

    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        return {
            "error": f"AnythingLLM HTTP {e.code}",
            "detail": text[:5000],
        }
    except Exception as e:
        return {
            "error": type(e).__name__,
            "detail": str(e),
        }

    try:
        data = json.loads(raw)
    except Exception:
        return {"raw": raw[:20000]}

    # Different AnythingLLM releases have used slightly
    # different response envelopes. Return the complete
    # bounded object to the model instead of guessing.
    serialized = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    )

    return {
        "anythingllm_response": serialized[:30000]
    }


def live_audit(profile):
    if profile not in ALLOWED_PROFILES:
        return {
            "error": "Profile not allowed",
            "allowed": sorted(ALLOWED_PROFILES),
        }

    try:
        result = subprocess.run(
            [str(LIVE_TOOL), profile],
            shell=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
    except Exception as e:
        return {
            "error": type(e).__name__,
            "detail": str(e),
        }

    if result.returncode != 0:
        return {
            "returncode": result.returncode,
            "stderr": result.stderr[-5000:],
            "stdout": result.stdout[-5000:],
        }

    try:
        return json.loads(result.stdout)
    except Exception:
        return {
            "stdout": result.stdout[-30000:],
            "stderr": result.stderr[-5000:],
        }


def validate_controlled_action(
    action,
    target,
):
    """
    Validate a requested controlled action against the
    explicit allowlist.

    No action is executed here.
    """

    catalog = CONTROLLED_ACTION_CATALOG[
        "actions"
    ]

    spec = catalog.get(action)

    if spec is None:
        return {
            "allowed": False,
            "error": "action_not_allowlisted",
            "mode": "DRY_RUN_ONLY",
            "execution_authority": "NONE",
        }

    allowlist = spec.get(
        "allowlist",
        [],
    )

    if target not in allowlist:
        return {
            "allowed": False,
            "error": "target_not_allowlisted",
            "action": action,
            "target": target,
            "mode": "DRY_RUN_ONLY",
            "execution_authority": "NONE",
        }

    return {
        "allowed": True,
        "action": action,
        "target": target,
        "mutation": spec.get(
            "mutation",
            False,
        ),
        "risk": spec.get(
            "risk",
            "unknown",
        ),
        "requires_explicit_approval":
            spec.get(
                "requires_explicit_approval",
                True,
            ),
        "requires_ready_plan":
            spec.get(
                "requires_ready_plan",
                False,
            ),
        "requires_fresh_state":
            spec.get(
                "requires_fresh_state",
                False,
            ),
        "requires_plan_binding":
            spec.get(
                "requires_plan_binding",
                False,
            ),
        "requires_post_verification":
            spec.get(
                "requires_post_verification",
                False,
            ),
        "executor":
            spec.get(
                "executor"
            ),
        "mode": "DRY_RUN_ONLY",
        "execution_authority": "NONE",
    }


def dry_run_controlled_action(
    action,
    target,
):
    """
    Produce a dry-run representation of a controlled action.

    This function NEVER performs the action.
    """

    validation = validate_controlled_action(
        action,
        target,
    )

    if not validation.get("allowed"):
        return {
            "status": "blocked",
            "validation": validation,
            "executed": False,
            "mode": "DRY_RUN_ONLY",
            "execution_authority": "NONE",
        }

    return {
        "status": "dry_run_ready",
        "action": action,
        "target": target,
        "mutation": validation.get(
            "mutation"
        ),
        "risk": validation.get(
            "risk"
        ),
        "executed": False,
        "mode": "DRY_RUN_ONLY",
        "execution_authority": "NONE",
    }



def execute_read_only_controlled_action(
    action,
    target,
):
    """
    Execute one strictly read-only controlled action.

    This executor:
    - accepts only actions already present in the catalog;
    - requires mutation=False;
    - requires an allowlisted target;
    - contains no shell=True;
    - contains no sudo;
    - contains no service mutation command.
    """

    import subprocess

    validation = validate_controlled_action(
        action,
        target,
    )

    if not validation.get("allowed"):
        return {
            "status": "blocked",
            "validation": validation,
            "executed": False,
            "mutated": False,
        }

    if validation.get("mutation") is not False:
        return {
            "status": "blocked",
            "error": "mutation_not_allowed_in_read_only_executor",
            "executed": False,
            "mutated": False,
        }

    if action != "check_service_status":
        return {
            "status": "blocked",
            "error": "executor_action_not_supported",
            "executed": False,
            "mutated": False,
        }

    # STAGE 8F-B: manager-aware read-only service status.
    #
    # The AAG host bridge is owned by the user systemd manager.
    # Docker is owned by the system manager. Keep the existing
    # allowlist as the authority and map each known target to
    # its exact lifecycle manager.
    if target == "aag-ubuntu-agent-bridge.service":
        command = [
            "/usr/bin/systemctl",
            "--user",
            "is-active",
            target,
        ]
        manager = "systemd --user"

    elif target == "docker.service":
        command = [
            "/usr/bin/systemctl",
            "is-active",
            target,
        ]
        manager = "systemd system"

    else:
        return {
            "status": "blocked",
            "error": "service_manager_mapping_missing",
            "target": target,
            "executed": False,
            "mutated": False,
            "execution_authority": "NONE",
        }

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    return {
        "status": "completed",
        "action": action,
        "target": target,
        "command_class": "READ_ONLY_SYSTEMCTL_IS_ACTIVE",
        "manager": manager,
        "returncode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "executed": True,
        "mutated": False,
        "execution_authority": "READ_ONLY_ALLOWLISTED",
    }



def validate_mutation_gate(
    action,
    target,
    plan,
    approval_state,
    current_snapshot,
):
    """
    Final deterministic pre-execution gate.

    This function DOES NOT execute anything.

    It only determines whether all currently modeled safety
    requirements are satisfied.
    """

    errors = []

    # -----------------------------------------------------
    # 1. Controlled action / target
    # -----------------------------------------------------

    action_check = validate_controlled_action(
        action,
        target,
    )

    if not action_check.get("allowed"):
        errors.append(
            "controlled_action_not_allowed"
        )

    if action_check.get("mutation") is not True:
        errors.append(
            "action_is_not_mutation"
        )

    if action_check.get("risk") in {
        None,
        "",
        "unknown",
    }:
        errors.append(
            "mutation_risk_unknown"
        )

    # -----------------------------------------------------
    # 2. Plan eligibility
    # -----------------------------------------------------

    eligibility = validate_plan_eligibility(
        plan
    )

    if not eligibility.get("eligible"):
        errors.append(
            "plan_not_eligible"
        )

    if plan.get("plan_confidence") !=             "READY_FOR_APPROVAL":
        errors.append(
            "plan_not_ready_for_approval"
        )

    if plan.get("target_component") != target:
        errors.append(
            "plan_target_mismatch"
        )

    # -----------------------------------------------------
    # 3. Approval state
    # -----------------------------------------------------

    if not isinstance(approval_state, dict):
        errors.append(
            "approval_state_invalid"
        )

    else:
        if approval_state.get(
            "approval_state"
        ) != "APPROVED_FOR_FUTURE_EXECUTION":
            errors.append(
                "explicit_approval_missing"
            )

    # -----------------------------------------------------
    # 4. Exact plan binding
    # -----------------------------------------------------

    if isinstance(approval_state, dict):
        binding = verify_approval_binding(
            approval_state,
            plan,
        )

        if not binding.get("valid"):
            errors.append(
                "plan_binding_invalid"
            )

    # -----------------------------------------------------
    # 5. Fresh live state
    # -----------------------------------------------------

    if isinstance(approval_state, dict):
        freshness = verify_approval_freshness(
            approval_state,
            current_snapshot,
        )

        if not freshness.get("fresh"):
            errors.append(
                "approval_state_stale"
            )

    # -----------------------------------------------------
    # 6. Catalog-required gates
    # -----------------------------------------------------

    if action_check.get(
        "requires_explicit_approval"
    ) is True:
        if not isinstance(
            approval_state,
            dict,
        ) or approval_state.get(
            "approval_state"
        ) != "APPROVED_FOR_FUTURE_EXECUTION":
            errors.append(
                "required_explicit_approval_missing"
            )

    if action_check.get(
        "requires_ready_plan"
    ) is True:
        if plan.get(
            "plan_confidence"
        ) != "READY_FOR_APPROVAL":
            errors.append(
                "required_ready_plan_missing"
            )

    if action_check.get(
        "requires_plan_binding"
    ) is True:
        if not isinstance(
            approval_state,
            dict,
        ):
            errors.append(
                "required_plan_binding_missing"
            )

    if action_check.get(
        "requires_fresh_state"
    ) is True:
        if not isinstance(
            approval_state,
            dict,
        ):
            errors.append(
                "required_fresh_state_missing"
            )

    # -----------------------------------------------------
    # 7. Prechecks / rollback / verification
    # -----------------------------------------------------

    prechecks = plan.get("prechecks")

    if not isinstance(
        prechecks,
        list,
    ) or not prechecks:
        errors.append(
            "prechecks_missing"
        )

    verification = plan.get(
        "verification"
    )

    if not isinstance(
        verification,
        list,
    ) or not verification:
        errors.append(
            "verification_missing"
        )

    if action_check.get(
        "requires_post_verification"
    ) is True:
        if not verification:
            errors.append(
                "required_post_verification_missing"
            )

    rollback_required = plan.get(
        "rollback_required"
    )

    rollback_plan = plan.get(
        "rollback_plan"
    )

    if rollback_required in {
        True,
        "yes",
        "required",
    }:
        if not isinstance(
            rollback_plan,
            list,
        ) or not rollback_plan:
            errors.append(
                "rollback_plan_missing"
            )

    # -----------------------------------------------------
    # Final state
    # -----------------------------------------------------

    ready = not errors

    return {
        "ready_to_execute": ready,
        "action": action,
        "target": target,
        "errors": sorted(set(errors)),
        "action_validation": action_check,
        "plan_eligibility": eligibility,
        "execution_authority": "NONE",
        "executed": False,
        "mutated": False,
        "next_state": (
            "READY_FOR_CONTROLLED_EXECUTOR"
            if ready
            else "BLOCKED"
        ),
    }



def simulate_controlled_mutation(
    action,
    target,
    plan,
    approval_state,
    current_snapshot,
):
    """
    Simulate one controlled mutation after all mutation gates pass.

    This function does NOT execute subprocesses.
    It does NOT call systemctl.
    It does NOT mutate host state.
    """

    gate = validate_mutation_gate(
        action,
        target,
        plan,
        approval_state,
        current_snapshot,
    )

    if not gate.get("ready_to_execute"):
        return {
            "status": "blocked",
            "gate": gate,
            "mode": "SIMULATION_ONLY",
            "executed": False,
            "mutated": False,
            "execution_authority": "NONE",
        }

    if action != "restart_user_service":
        return {
            "status": "blocked",
            "error": "simulation_action_not_supported",
            "mode": "SIMULATION_ONLY",
            "executed": False,
            "mutated": False,
            "execution_authority": "NONE",
        }

    if target != "aag-ubuntu-agent-bridge.service":
        return {
            "status": "blocked",
            "error": "simulation_target_not_supported",
            "mode": "SIMULATION_ONLY",
            "executed": False,
            "mutated": False,
            "execution_authority": "NONE",
        }

    command_preview = [
        "/usr/bin/systemctl",
        "--user",
        "restart",
        "aag-ubuntu-agent-bridge.service",
    ]

    return {
        "status": "simulation_ready",
        "mode": "SIMULATION_ONLY",
        "action": action,
        "target": target,
        "would_execute": command_preview,
        "gate": gate,
        "executed": False,
        "mutated": False,
        "execution_authority": "NONE",
    }



def build_live_service_snapshot(target):
    """
    Build a deterministic read-only snapshot for one allowlisted
    user-level service.

    No service state is changed.
    """

    import subprocess

    allowed = {
        "aag-ubuntu-agent-bridge.service",
    }

    if target not in allowed:
        return {
            "status": "blocked",
            "error": "snapshot_target_not_allowlisted",
            "target": target,
            "mutated": False,
        }

    command = [
        "/usr/bin/systemctl",
        "--user",
        "show",
        target,
        "--property=Id",
        "--property=LoadState",
        "--property=ActiveState",
        "--property=SubState",
        "--property=UnitFileState",
        "--property=FragmentPath",
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    fields = {}

    for line in result.stdout.splitlines():
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        fields[key] = value

    return {
        "status": "completed",
        "snapshot_schema":
            "aag-user-service-live-snapshot-v1",
        "target": target,
        "owner": "systemd user service",
        "manager": "systemd --user",
        "id": fields.get("Id", ""),
        "load_state": fields.get(
            "LoadState",
            "unknown",
        ),
        "active_state": fields.get(
            "ActiveState",
            "unknown",
        ),
        "sub_state": fields.get(
            "SubState",
            "unknown",
        ),
        "unit_file_state": fields.get(
            "UnitFileState",
            "unknown",
        ),
        "fragment_path": fields.get(
            "FragmentPath",
            "",
        ),
        "returncode": result.returncode,
        "stderr": result.stderr.strip(),
        "read_only": True,
        "mutated": False,
    }



def verify_bridge_readiness(
    target,
    socket_path,
    attempts=20,
    interval_seconds=0.5,
):
    """
    Read-only post-action readiness verifier.

    A systemd unit reaching active/running is not sufficient proof
    that the application endpoint is ready.

    This function allows a bounded readiness window and requires
    the real /health endpoint to return HTTP 200 with status=ok.
    """

    import http.client
    import json
    import socket
    import subprocess
    import time

    allowed_target = (
        "aag-ubuntu-agent-bridge.service"
    )

    allowed_socket = str(BRIDGE_SOCKET_HOST)

    if target != allowed_target:
        return {
            "ready": False,
            "error": "target_not_allowlisted",
            "mutated": False,
        }

    if str(socket_path) != allowed_socket:
        return {
            "ready": False,
            "error": "socket_not_allowlisted",
            "mutated": False,
        }

    observations = []

    class UnixHTTPConnection(
        http.client.HTTPConnection
    ):
        def connect(self):
            self.sock = socket.socket(
                socket.AF_UNIX,
                socket.SOCK_STREAM,
            )
            self.sock.settimeout(2)
            self.sock.connect(
                str(socket_path)
            )

    for attempt in range(1, attempts + 1):

        show = subprocess.run(
            [
                "/usr/bin/systemctl",
                "--user",
                "show",
                target,
                "--property=ActiveState",
                "--property=SubState",
                "--property=MainPID",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

        fields = {}

        for line in show.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                fields[k] = v

        observation = {
            "attempt": attempt,
            "active_state":
                fields.get(
                    "ActiveState",
                    "unknown",
                ),
            "sub_state":
                fields.get(
                    "SubState",
                    "unknown",
                ),
            "main_pid":
                fields.get(
                    "MainPID",
                    "0",
                ),
        }

        if (
            observation["active_state"] == "active"
            and observation["sub_state"] == "running"
            and observation["main_pid"] not in {
                "",
                "0",
            }
        ):
            try:
                c = UnixHTTPConnection(
                    "localhost",
                    timeout=2,
                )

                c.request(
                    "GET",
                    "/health",
                )

                r = c.getresponse()

                body = r.read().decode(
                    "utf-8",
                    "replace",
                )

                observation[
                    "http_status"
                ] = r.status

                try:
                    payload = json.loads(
                        body
                    )
                except Exception:
                    payload = None

                observation[
                    "health_status"
                ] = (
                    payload.get("status")
                    if isinstance(
                        payload,
                        dict,
                    )
                    else None
                )

                if (
                    r.status == 200
                    and observation[
                        "health_status"
                    ] == "ok"
                ):
                    observations.append(
                        observation
                    )

                    return {
                        "ready": True,
                        "target": target,
                        "main_pid":
                            observation[
                                "main_pid"
                            ],
                        "attempts_used":
                            attempt,
                        "observations":
                            observations,
                        "mutated": False,
                    }

            except Exception as exc:
                observation[
                    "health_error"
                ] = (
                    type(exc).__name__
                    + ": "
                    + str(exc)
                )

        observations.append(
            observation
        )

        if attempt < attempts:
            time.sleep(
                interval_seconds
            )

    return {
        "ready": False,
        "error":
            "readiness_timeout",
        "target": target,
        "attempts_used":
            attempts,
        "observations":
            observations,
        "mutated": False,
    }



def execute_controlled_mutation(
    action,
    target,
    plan,
    approval_state,
    approved_snapshot,
):
    """
    Execute one tightly constrained mutation.

    IMPORTANT:
    - this function is NOT exposed directly to the AI;
    - it accepts only the first explicitly modeled mutation class;
    - it revalidates live state immediately before mutation;
    - it contains no shell=True;
    - it contains no sudo;
    - it performs mandatory post-action readiness verification.
    """

    import subprocess

    allowed_action = "restart_user_service"
    allowed_target = (
        "aag-ubuntu-agent-bridge.service"
    )

    allowed_socket = str(BRIDGE_SOCKET_HOST)

    # ---------------------------------------------------------
    # 1. Exact executor allowlist
    # ---------------------------------------------------------

    if action != allowed_action:
        return {
            "status": "blocked",
            "error":
                "executor_action_not_allowlisted",
            "executed": False,
            "mutated": False,
        }

    if target != allowed_target:
        return {
            "status": "blocked",
            "error":
                "executor_target_not_allowlisted",
            "executed": False,
            "mutated": False,
        }

    # Private executor authority comes only from the accepted domain contract.
    try:
        contract = CONTRACT_REGISTRY.get(BRIDGE_CONTRACT_ID, execution=True)
    except ContractError as exc:
        return {"status": "blocked", "error": str(exc), "executed": False, "mutated": False}
    executor = contract.data["executor"]
    if executor["primitive"] != "restart_exact_bridge_user_service" or executor["target"] != target:
        return {"status": "blocked", "error": "contract_executor_binding_mismatch", "executed": False, "mutated": False}

    # ---------------------------------------------------------
    # 2. Rebuild current live state immediately before action
    # ---------------------------------------------------------

    fresh_snapshot = build_live_service_snapshot(
        target
    )

    if fresh_snapshot.get(
        "status"
    ) != "completed":
        return {
            "status": "blocked",
            "error":
                "fresh_snapshot_failed",
            "fresh_snapshot":
                fresh_snapshot,
            "executed": False,
            "mutated": False,
        }

    # ---------------------------------------------------------
    # 3. Approval must still match CURRENT live state
    # ---------------------------------------------------------

    freshness = verify_approval_freshness(
        approval_state,
        fresh_snapshot,
    )

    if not freshness.get("fresh"):
        return {
            "status": "blocked",
            "error":
                "approval_state_stale",
            "freshness":
                freshness,
            "fresh_snapshot":
                fresh_snapshot,
            "executed": False,
            "mutated": False,
        }

    # approved_snapshot itself must also still represent
    # the same state that was approved.
    if approval_state_fingerprint(
        approved_snapshot
    ) != approval_state_fingerprint(
        fresh_snapshot
    ):
        return {
            "status": "blocked",
            "error":
                "approved_snapshot_mismatch",
            "fresh_snapshot":
                fresh_snapshot,
            "executed": False,
            "mutated": False,
        }

    # ---------------------------------------------------------
    # STAGE 8D.4A — PROBLEM-STILL-EXISTS GATE
    # ---------------------------------------------------------
    #
    # The currently modeled mutation exists for one specific
    # failure class:
    #
    #   service is loaded + active/running
    #   BUT bridge /health is not ready.
    #
    # Approval freshness alone cannot prove that the application
    # failure still exists because build_live_service_snapshot()
    # intentionally contains only stable systemd ownership/state
    # observations and does not include endpoint readiness.
    #
    # Therefore immediately before the final mutation gate, re-check
    # the exact bounded application readiness condition.
    #
    # If the bridge has recovered since diagnosis/approval, abort.
    # A disappeared problem must never be "fixed" by a stale remedy.

    problem_recheck = verify_bridge_readiness(
        target,
        allowed_socket,
        attempts=1,
        interval_seconds=0,
    )

    detector_evidence, contract_policy = evaluate_bridge_contract_evidence(
        fresh_snapshot,
        problem_recheck,
    )

    if detector_evidence["classification"] == "HEALTHY":
        return {
            "status":
                "not_needed",
            "error":
                "problem_disappeared_before_execution",
            "reason":
                "bridge_health_endpoint_is_currently_ready",
            "problem_recheck":
                problem_recheck,
            "detector_evidence": detector_evidence,
            "contract_policy": contract_policy,
            "execution_authority":
                "NONE",
            "executed":
                False,
            "mutated":
                False,
            "post_verified":
                False,
        }

    # The readiness verifier must fail specifically because the
    # endpoint did not become ready. Any unrelated verifier error
    # (wrong target/socket contract, malformed observation, etc.)
    # must fail closed rather than being interpreted as permission
    # to mutate.
    if not contract_policy.get("allowed"):
        return {
            "status":
                "blocked",
            "error":
                "problem_recheck_indeterminate",
            "problem_recheck":
                problem_recheck,
            "detector_evidence": detector_evidence,
            "contract_policy": contract_policy,
            "execution_authority":
                "NONE",
            "executed":
                False,
            "mutated":
                False,
            "post_verified":
                False,
        }

    # ---------------------------------------------------------
    # 4. Final mutation gate
    # ---------------------------------------------------------

    gate = validate_mutation_gate(
        action,
        target,
        plan,
        approval_state,
        fresh_snapshot,
    )

    if not gate.get(
        "ready_to_execute"
    ):
        return {
            "status": "blocked",
            "error":
                "mutation_gate_blocked",
            "gate":
                gate,
            "executed": False,
            "mutated": False,
        }

    if gate.get(
        "next_state"
    ) != "READY_FOR_CONTROLLED_EXECUTOR":
        return {
            "status": "blocked",
            "error":
                "unexpected_gate_state",
            "gate":
                gate,
            "executed": False,
            "mutated": False,
        }

    # ---------------------------------------------------------
    # 5. Capture pre-action PID
    # ---------------------------------------------------------

    old_pid = str(
        fresh_snapshot.get(
            "id",
            "",
        )
    )

    show_pid = subprocess.run(
        [
            "/usr/bin/systemctl",
            "--user",
            "show",
            target,
            "--property=MainPID",
            "--value",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    pre_pid = show_pid.stdout.strip()

    if (
        show_pid.returncode != 0
        or not pre_pid
        or pre_pid == "0"
    ):
        return {
            "status": "blocked",
            "error":
                "pre_action_pid_invalid",
            "returncode":
                show_pid.returncode,
            "executed": False,
            "mutated": False,
        }

    # ---------------------------------------------------------
    # 6. SINGLE REAL MUTATION
    # ---------------------------------------------------------

    operation = REMEDIATION_OPERATION_REGISTRY.get(
        "bridge.restart.readiness_failure",
        1,
        execution=True,
    )
    primitive_result = ExactBridgeRestartExecutor().execute(
        operation
    )

    if primitive_result.get("status") != "EXECUTION_OK":
        return {
            "status":
                "execution_failed",
            "action": action,
            "target": target,
            "returncode":
                primitive_result.get("returncode"),
            "stdout":
                primitive_result.get("stdout", ""),
            "stderr":
                primitive_result.get("stderr", ""),
            "primitive_result":
                primitive_result,
            "executed": bool(
                primitive_result.get("executed")
            ),
            "mutated": bool(
                primitive_result.get("mutated")
            ),
            "post_verified": False,
        }

    # ---------------------------------------------------------
    # 7. Mandatory readiness verification
    # ---------------------------------------------------------

    readiness = verify_bridge_readiness(
        target,
        allowed_socket,
        attempts=20,
        interval_seconds=0.5,
    )

    if not readiness.get("ready"):
        return {
            "status":
                "post_verification_failed",
            "action": action,
            "target": target,
            "pre_pid": pre_pid,
            "readiness": readiness,
            "executed": True,
            "mutated": True,
            "post_verified": False,
        }

    new_pid = str(
        readiness.get(
            "main_pid",
            "",
        )
    )

    if (
        not new_pid
        or new_pid == "0"
    ):
        return {
            "status":
                "post_verification_failed",
            "error":
                "new_pid_invalid",
            "pre_pid": pre_pid,
            "new_pid": new_pid,
            "readiness": readiness,
            "executed": True,
            "mutated": True,
            "post_verified": False,
        }

    if new_pid == pre_pid:
        return {
            "status":
                "post_verification_failed",
            "error":
                "pid_did_not_change",
            "pre_pid": pre_pid,
            "new_pid": new_pid,
            "readiness": readiness,
            "executed": True,
            "mutated": True,
            "post_verified": False,
        }

    return {
        "status": "completed",
        "action": action,
        "target": target,
        "command_class":
            "CONTROLLED_USER_SERVICE_RESTART",
        "pre_pid": pre_pid,
        "new_pid": new_pid,
        "readiness": readiness,
        "executed": True,
        "mutated": True,
        "post_verified": True,
        "execution_authority":
            "EXACT_APPROVED_CONTROLLED_ACTION",
    }



# ============================================================
# CONTROLLED MUTATION SESSION STATE
# ============================================================

PENDING_MUTATION = None
CONSUMED_APPROVAL_TOKENS = set()
LAST_MUTATION_RESULT = None



# AAG_STAGE10C2_BRIDGE_HEALTH_READ_ONLY
def bridge_health_observation():
    """
    Perform one fixed, narrowly-scoped bridge readiness observation.

    READ ONLY.

    The model cannot choose:
    - another service;
    - another Unix socket;
    - another endpoint;
    - another command.

    This grants no mutation or execution authority.
    """

    target = BRIDGE_SERVICE

    socket_path = str(BRIDGE_SOCKET_HOST)

    health = verify_bridge_readiness(
        target,
        socket_path,
        attempts=3,
        interval_seconds=0.25,
    )

    return {
        "schema":
            "aag-bridge-health-observation-v1",

        "status":
            "completed",

        "target":
            target,

        "socket":
            socket_path,

        "ready":
            bool(
                health.get(
                    "ready"
                )
            ),

        "health":
            health,

        "execution_class":
            "READ_ONLY_FIXED_OBSERVATION",

        "executed":
            True,

        "mutated":
            False,

        "execution_authority":
            "READ_ONLY_ALLOWLISTED",
    }


def structured_observation(domain, query=None):
    """Model-facing, typed, read-only diagnostic interface."""
    try:
        return observe(domain, query or {})
    except ObservationError as exc:
        return {"schema": "aag-observation-v1", "domain": domain, "status": "blocked", "error": str(exc), "read_only": True, "mutated": False, "execution_authority": "NONE"}


def build_bridge_restart_plan():
    """
    Build the one currently supported real remediation plan.

    This function grants no execution authority.
    """

    target = "aag-ubuntu-agent-bridge.service"

    return {
        "contract_id": BRIDGE_CONTRACT_ID,
        "contract_version": 1,
        "problem": (
            "AAG Ubuntu Agent host bridge is active/running "
            "but its Unix health endpoint is not reachable."
        ),

        "evidence": [
            "live readiness verification failed",
            "the exact systemd user unit remains the lifecycle owner",
        ],

        "target_component": target,

        "current_owner": (
            "systemd user service via "
            "aag-ubuntu-agent-bridge.service"
        ),

        "proposed_action":
            "restart aag-ubuntu-agent-bridge.service",

        "action_reason": (
            "For the currently modeled bridge failure class, "
            "a single controlled user-service restart is the "
            "smallest allowlisted reversible remediation."
        ),

        "mutation_risk": "low",

        "dependencies": [
            "/mnt/data",
            "AAG Ubuntu Agent virtual environment",
            "host_bridge.py",
        ],

        "dependents": [
            "AnythingLLM live host diagnostics",
        ],

        "conflicts": [],

        "invariants": [
            "do not modify the systemd unit",
            "do not modify host_bridge.py",
            "do not modify Docker configuration",
            "do not touch WinBoat",
            "do not touch Otzar",
            "do not touch NBD",
            "do not touch USB Clone",
            "do not use sudo",
            "restart only the exact allowlisted user service",
        ],

        "prechecks": [
            "confirm exact unit identity",
            "confirm live lifecycle owner",
            "confirm service remains loaded",
            "confirm endpoint is still unhealthy",
            "rebuild fresh live snapshot immediately before execution",
        ],

        "backup_required": False,

        "rollback_required": False,

        "rollback_plan": [],

        "verification": [
            "confirm service returns active/running",
            "confirm MainPID changes",
            "confirm bridge readiness reaches HTTP 200",
            "confirm health payload reports status=ok",
        ],

        "success_criteria": [
            "service active/running",
            "new MainPID",
            "bridge GET /health returns status=ok",
        ],

        "abort_conditions": [
            "target identity changes",
            "owner changes",
            "service becomes unloaded",
            "problem disappears before approval",
            "approved state becomes stale",
            "approved plan changes",
        ],

        "plan_confidence":
            "READY_FOR_APPROVAL",

        "execution_mode":
            "PLAN_ONLY",

        "authorization":
            "NONE",
    }


def pending_mutation_public_view(pending):
    """
    Return only information safe and useful for the user/AI.
    """

    if not isinstance(pending, dict):
        return None

    return {
        "token": pending.get("token"),
        "action": pending.get("action"),
        "target": pending.get("target"),
        "plan_fingerprint":
            pending.get("plan_fingerprint"),
        "state_fingerprint":
            pending.get("state_fingerprint"),
        "created_for_session": True,
        "execution_authority": "NONE",
        "executed": False,
        "mutated": False,
    }


def prepare_controlled_mutation_request(
    action,
    target,
):
    """
    Prepare — but NEVER execute — one controlled mutation request.

    This is safe to expose to the model because:
    - it cannot approve;
    - it cannot execute;
    - it cannot choose arbitrary commands;
    - it refuses to create a restart request when the bridge
      is currently healthy.
    """

    global PENDING_MUTATION

    import secrets

    allowed_action = "restart_user_service"
    allowed_target = BRIDGE_SERVICE
    try:
        contract = CONTRACT_REGISTRY.get(BRIDGE_CONTRACT_ID, execution=True)
    except ContractError as exc:
        return {"status": "blocked", "error": str(exc), "execution_authority": "NONE", "executed": False, "mutated": False}
    binding = contract.data["executor"]
    if binding["primitive"] != "restart_exact_bridge_user_service" or binding["target"] != allowed_target:
        return {"status": "blocked", "error": "contract_executor_binding_mismatch", "execution_authority": "NONE", "executed": False, "mutated": False}
    socket_path = str(BRIDGE_SOCKET_HOST)

    if action != allowed_action:
        return {
            "status": "blocked",
            "error": "action_not_allowlisted",
            "execution_authority": "NONE",
            "executed": False,
            "mutated": False,
        }

    if target != allowed_target:
        return {
            "status": "blocked",
            "error": "target_not_allowlisted",
            "execution_authority": "NONE",
            "executed": False,
            "mutated": False,
        }

    if isinstance(PENDING_MUTATION, dict):
        return {
            "status": "pending_request_exists",
            "pending":
                pending_mutation_public_view(
                    PENDING_MUTATION
                ),
            "message": (
                "A controlled mutation request is already pending. "
                "The user must approve or cancel that exact request."
            ),
            "execution_authority": "NONE",
            "executed": False,
            "mutated": False,
        }

    # A healthy bridge must never be restarted merely because the
    # model decided to ask for a mutation.
    health = verify_bridge_readiness(
        target,
        socket_path,
        attempts=3,
        interval_seconds=0.25,
    )

    if health.get("ready"):
        detector_evidence, contract_policy = evaluate_bridge_contract_evidence(
            None,
            health,
        )
        return {
            "status": "not_needed",
            "reason": "bridge_is_currently_healthy",
            "health": health,
            "detector_evidence": detector_evidence,
            "contract_policy": contract_policy,
            "message": (
                "The bridge is healthy now. "
                "No restart request was created."
            ),
            "execution_authority": "NONE",
            "executed": False,
            "mutated": False,
        }

    snapshot = build_live_service_snapshot(
        target
    )

    if snapshot.get("status") != "completed":
        return {
            "status": "blocked",
            "error": "live_snapshot_failed",
            "snapshot": snapshot,
            "execution_authority": "NONE",
            "executed": False,
            "mutated": False,
        }

    detector_evidence, contract_policy = evaluate_bridge_contract_evidence(
        snapshot,
        health,
    )

    if not contract_policy.get("allowed"):
        classification = detector_evidence.get("classification")
        return {
            "status": "blocked",
            "error": (
                "unsupported_live_state_for_first_mutation_class"
                if classification == "UNSUPPORTED_SERVICE_STATE"
                else "unsupported_bridge_failure_evidence"
            ),
            "snapshot": snapshot,
            "detector_evidence": detector_evidence,
            "contract_policy": contract_policy,
            "execution_authority": "NONE",
            "executed": False,
            "mutated": False,
        }

    plan = build_bridge_restart_plan()

    eligibility = validate_plan_eligibility(
        plan
    )

    if not eligibility.get("eligible"):
        return {
            "status": "blocked",
            "error": "plan_not_eligible",
            "plan_eligibility": eligibility,
            "execution_authority": "NONE",
            "executed": False,
            "mutated": False,
        }

    plan_fp = remediation_plan_fingerprint(
        plan
    )

    state_fp = approval_state_fingerprint(
        snapshot
    )

    token = secrets.token_hex(8)

    PENDING_MUTATION = {
        "token": token,
        "action": action,
        "target": target,
        "plan": plan,
        "approved_snapshot": snapshot,
        "plan_fingerprint": plan_fp,
        "state_fingerprint": state_fp,
        "detector_evidence": detector_evidence,
    }

    return {
        "status": "awaiting_explicit_user_approval",
        "pending":
            pending_mutation_public_view(
                PENDING_MUTATION
            ),
        "message": (
            "A controlled remediation is ready but has NOT been "
            "approved or executed. The user must type exactly: "
            f"/approve {token}"
        ),
        "approval_command":
            f"/approve {token}",
        "cancel_command":
            f"/cancel {token}",
        "execution_authority": "NONE",
        "executed": False,
        "mutated": False,
    }


def prepare_contract_remediation(contract_id):
    """Model-facing domain operation; never accepts an action or target."""
    try:
        contract = CONTRACT_REGISTRY.get(contract_id, execution=True)
    except ContractError as exc:
        return {"status": "blocked", "error": str(exc), "execution_authority": "NONE", "executed": False, "mutated": False}
    if contract.contract_id != BRIDGE_CONTRACT_ID:
        return {"status": "blocked", "error": "unsupported_contract", "execution_authority": "NONE", "executed": False, "mutated": False}
    return prepare_controlled_mutation_request(
        "restart_user_service",
        BRIDGE_SERVICE,
    )


def handle_local_mutation_command(
    prompt,
    allow_execution=True,
):
    """
    Handle mutation approval OUTSIDE the model/tool loop.

    Returns None when prompt is not a local mutation command.

    The model cannot fabricate approval because this function consumes
    the user's raw CLI input before that input is sent to the model.
    """

    global PENDING_MUTATION
    global LAST_MUTATION_RESULT

    text = str(prompt or "").strip()

    if not text:
        return None

    parts = text.split()
    command = parts[0].lower()

    if command not in {
        "/approve",
        "/cancel",
        "/pending",
    }:
        return None

    if command == "/pending":
        if PENDING_MUTATION is None:
            return {
                "status": "no_pending_mutation",
                "message":
                    "אין כרגע פעולה שממתינה לאישור.",
                "executed": False,
                "mutated": False,
            }

        return {
            "status": "pending_mutation",
            "pending":
                pending_mutation_public_view(
                    PENDING_MUTATION
                ),
            "message": (
                "יש פעולה מבוקרת שממתינה "
                "לאישור או לביטול."
            ),
            "executed": False,
            "mutated": False,
        }

    if len(parts) != 2:
        return {
            "status": "blocked",
            "error": "exact_token_required",
            "message": (
                "יש לציין את token המדויק."
            ),
            "executed": False,
            "mutated": False,
        }

    token = parts[1]

    if token in CONSUMED_APPROVAL_TOKENS:
        return {
            "status": "blocked",
            "error": "approval_token_already_consumed",
            "message": (
                "האישור הזה כבר נוצל ואי אפשר "
                "להשתמש בו שוב."
            ),
            "executed": False,
            "mutated": False,
        }

    if PENDING_MUTATION is None:
        return {
            "status": "blocked",
            "error": "no_pending_mutation",
            "message":
                "אין פעולה שממתינה לאישור.",
            "executed": False,
            "mutated": False,
        }

    if token != PENDING_MUTATION.get(
        "token"
    ):
        return {
            "status": "blocked",
            "error": "approval_token_mismatch",
            "message":
                "ה־token אינו תואם לפעולה הממתינה.",
            "executed": False,
            "mutated": False,
        }

    if command == "/cancel":
        CONSUMED_APPROVAL_TOKENS.add(
            token
        )

        PENDING_MUTATION = None

        return {
            "status": "cancelled",
            "message":
                "הפעולה בוטלה. לא בוצע שינוי.",
            "executed": False,
            "mutated": False,
        }

    pending = PENDING_MUTATION

    target = pending["target"]
    action = pending["action"]
    plan = pending["plan"]
    contract = CONTRACT_REGISTRY.get(BRIDGE_CONTRACT_ID, execution=True)

    socket_path = str(BRIDGE_SOCKET_HOST)

    # --------------------------------------------------------
    # Problem must still exist.
    # --------------------------------------------------------

    health = verify_bridge_readiness(
        target,
        socket_path,
        attempts=2,
        interval_seconds=0.25,
    )

    if health.get("ready"):
        CONSUMED_APPROVAL_TOKENS.add(
            token
        )
        PENDING_MUTATION = None

        return {
            "status": "blocked",
            "error":
                "problem_disappeared_before_execution",
            "message": (
                "הבעיה כבר אינה קיימת כרגע, "
                "ולכן האישור בוטל ולא בוצע restart."
            ),
            "health": health,
            "executed": False,
            "mutated": False,
        }

    # --------------------------------------------------------
    # Fresh state must still be EXACTLY what was presented.
    # --------------------------------------------------------

    current_snapshot = (
        build_live_service_snapshot(
            target
        )
    )

    if current_snapshot.get(
        "status"
    ) != "completed":
        return {
            "status": "blocked",
            "error":
                "fresh_snapshot_failed",
            "executed": False,
            "mutated": False,
        }

    detector_evidence, contract_policy = evaluate_bridge_contract_evidence(
        current_snapshot,
        health,
    )
    if not contract_policy.get("allowed"):
        CONSUMED_APPROVAL_TOKENS.add(token)
        PENDING_MUTATION = None
        return {
            "status": "blocked",
            "error": "supported_failure_not_currently_proven",
            "detector_evidence": detector_evidence,
            "contract_policy": contract_policy,
            "executed": False,
            "mutated": False,
        }

    current_state_fp = (
        approval_state_fingerprint(
            current_snapshot
        )
    )

    if (
        current_state_fp
        != pending["state_fingerprint"]
    ):
        CONSUMED_APPROVAL_TOKENS.add(
            token
        )
        PENDING_MUTATION = None

        return {
            "status": "blocked",
            "error": "approved_state_became_stale",
            "message": (
                "מצב המחשב השתנה מאז בקשת האישור. "
                "האישור בוטל ונדרשת תוכנית חדשה."
            ),
            "expected_state_fingerprint":
                pending["state_fingerprint"],
            "current_state_fingerprint":
                current_state_fp,
            "executed": False,
            "mutated": False,
        }

    current_plan_fp = (
        remediation_plan_fingerprint(
            plan
        )
    )

    if (
        current_plan_fp
        != pending["plan_fingerprint"]
    ):
        CONSUMED_APPROVAL_TOKENS.add(
            token
        )
        PENDING_MUTATION = None

        return {
            "status": "blocked",
            "error": "approved_plan_changed",
            "executed": False,
            "mutated": False,
        }

    # --------------------------------------------------------
    # Create explicit approval object from RAW USER COMMAND.
    # --------------------------------------------------------

    state = create_approval_state(
        plan
    )

    approved = transition_approval_state(
        state,
        "approve",
        explicit=True,
    )

    if not approved.get("ok"):
        return {
            "status": "blocked",
            "error":
                "approval_transition_failed",
            "executed": False,
            "mutated": False,
        }

    state = approved["state"]

    bound = bind_approval_to_live_state(
        state,
        current_snapshot,
    )

    if not bound.get("ok"):
        return {
            "status": "blocked",
            "error":
                "live_state_binding_failed",
            "executed": False,
            "mutated": False,
        }

    state = bound["state"]

    gate = validate_mutation_gate(
        action,
        target,
        plan,
        state,
        current_snapshot,
    )

    if not gate.get(
        "ready_to_execute"
    ):
        return {
            "status": "blocked",
            "error": "mutation_gate_blocked",
            "gate": gate,
            "executed": False,
            "mutated": False,
        }

    # Test harness can validate the ENTIRE approval path without
    # performing a mutation.
    if not allow_execution:
        return {
            "status":
                "approval_validated_test_only",
            "token": token,
            "gate": gate,
            "message": (
                "האישור עבר את כל השערים, "
                "אך מצב הבדיקה אוסר execution."
            ),
            "executed": False,
            "mutated": False,
        }

    # --------------------------------------------------------
    # TOKEN BECOMES SINGLE-USE BEFORE EXECUTOR INVOCATION.
    # --------------------------------------------------------

    CONSUMED_APPROVAL_TOKENS.add(
        token
    )

    PENDING_MUTATION = None

    try:
        audit_started = persist_mutation_audit_event(
            "execution_started",
            {
                "contract_version": contract.data["version"],
                "executor_primitive": contract.data["executor"]["primitive"],
                "action": action,
                "target": target,
                "approval_policy": contract.data["approval_policy"],
                "approval_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                "plan_fingerprint": pending["plan_fingerprint"],
                "state_fingerprint": pending["state_fingerprint"],
                "pre_state": current_snapshot,
                "rollback": contract.data["rollback"],
            },
        )
    except Exception as exc:
        record_persisted = bool(getattr(exc, "record_persisted", False))
        return {
            "status": "blocked",
            "error": "pre_execution_audit_persistence_failed",
            "audit": {"persisted": False, "started_persisted": record_persisted, "checkpoint_valid": False, "error_type": type(exc).__name__, "error": str(exc)},
            "approval_token_consumed": True,
            "executed": False,
            "mutated": False,
        }

    result = execute_controlled_mutation(
        action,
        target,
        plan,
        state,
        current_snapshot,
    )

    LAST_MUTATION_RESULT = result

    audit = {"persisted": True, "started_persisted": True, "finished_persisted": True, "started_record_hash": audit_started["record_hash"]}
    try:
        audit_finished = persist_mutation_audit_event(
            "execution_finished",
            {
                "contract_version": contract.data["version"],
                "executor_primitive": contract.data["executor"]["primitive"],
                "action": action,
                "target": target,
                "plan_fingerprint": pending["plan_fingerprint"],
                "state_fingerprint": pending["state_fingerprint"],
                "execution_status": result.get("status"),
                "executed": bool(result.get("executed")),
                "mutated": bool(result.get("mutated")),
                "post_verified": bool(result.get("post_verified")),
                "post_verification": result.get("readiness"),
                "rollback": contract.data["rollback"],
                "escalation": "REPORT_FAILURE_NO_GENERIC_FALLBACK" if not result.get("post_verified") else None,
            },
        )
        audit["finished_record_hash"] = audit_finished["record_hash"]
    except Exception as exc:
        audit = {"persisted": False, "started_persisted": True, "finished_persisted": bool(getattr(exc, "record_persisted", False)), "checkpoint_valid": False, "started_record_hash": audit_started["record_hash"], "error": "post_execution_audit_persistence_failed", "error_type": type(exc).__name__, "detail": str(exc)}

    return {
        "status":
            "controlled_execution_finished",
        "result": result,
        "audit": audit,
        "approval_token_consumed": True,
        "message": (
            "הפעולה המאושרת הסתיימה. "
            "ראה תוצאת verification."
        ),
        "executed":
            bool(result.get("executed")),
        "mutated":
            bool(result.get("mutated")),
    }



def approval_state_fingerprint(snapshot):
    """
    Produce a deterministic SHA-256 fingerprint of the relevant
    read-only machine state used to justify an approval.

    The snapshot must contain only observations already collected
    through approved read-only diagnostics.

    This function performs no live diagnostic itself.
    """

    import hashlib
    import json

    if not isinstance(snapshot, dict):
        raise TypeError("snapshot must be a dict")

    canonical = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def bind_approval_to_live_state(
    approval_state,
    snapshot,
):
    """
    Bind an approval request/state to the exact read-only state
    snapshot on which the plan was justified.

    No mutation is performed.
    """

    if not isinstance(approval_state, dict):
        return {
            "ok": False,
            "error": "approval_state_must_be_object",
            "execution_authority": "NONE",
        }

    if not isinstance(snapshot, dict):
        return {
            "ok": False,
            "error": "snapshot_must_be_object",
            "execution_authority": "NONE",
        }

    out = dict(approval_state)

    out["state_fingerprint"] =         approval_state_fingerprint(snapshot)

    out["execution_authority"] = "NONE"
    out["approval_does_not_execute"] = True

    return {
        "ok": True,
        "state": out,
        "execution_authority": "NONE",
        "approval_does_not_execute": True,
    }


def verify_approval_freshness(
    approval_state,
    current_snapshot,
):
    """
    Compare current read-only state with the state that existed
    when approval was bound.

    A mismatch marks the approval stale.

    This function never executes or changes host state.
    """

    if not isinstance(approval_state, dict):
        return {
            "fresh": False,
            "error": "approval_state_must_be_object",
            "execution_authority": "NONE",
        }

    if not isinstance(current_snapshot, dict):
        return {
            "fresh": False,
            "error": "snapshot_must_be_object",
            "execution_authority": "NONE",
        }

    expected = approval_state.get(
        "state_fingerprint"
    )

    if not expected:
        return {
            "fresh": False,
            "error": "state_fingerprint_missing",
            "execution_authority": "NONE",
        }

    actual = approval_state_fingerprint(
        current_snapshot
    )

    if actual != expected:
        return {
            "fresh": False,
            "stale": True,
            "error": "live_state_changed",
            "expected_state_fingerprint": expected,
            "actual_state_fingerprint": actual,
            "required_action":
                "REVALIDATE_AND_REAPPROVE",
            "execution_authority": "NONE",
        }

    return {
        "fresh": True,
        "stale": False,
        "state_fingerprint": actual,
        "execution_authority": "NONE",
        "approval_does_not_execute": True,
    }



def remediation_plan_fingerprint(plan):
    """
    Return a deterministic SHA-256 fingerprint for an exact
    remediation plan.

    The function is pure and performs no host mutation.
    """

    import hashlib
    import json

    if not isinstance(plan, dict):
        raise TypeError("plan must be a dict")

    canonical = json.dumps(
        plan,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def verify_approval_binding(state, plan):
    """
    Verify that an approval-state object belongs to the exact
    remediation plan currently presented.

    This function does not approve or execute anything.
    """

    if not isinstance(state, dict):
        return {
            "valid": False,
            "error": "state_must_be_object",
            "execution_authority": "NONE",
        }

    if not isinstance(plan, dict):
        return {
            "valid": False,
            "error": "plan_must_be_object",
            "execution_authority": "NONE",
        }

    expected = state.get("plan_fingerprint")

    if not expected:
        return {
            "valid": False,
            "error": "approval_missing_plan_fingerprint",
            "execution_authority": "NONE",
        }

    actual = remediation_plan_fingerprint(plan)

    if actual != expected:
        return {
            "valid": False,
            "bound": False,
            "error": "plan_fingerprint_mismatch",
            "expected_fingerprint": expected,
            "actual_fingerprint": actual,
            "execution_authority": "NONE",
        }

    return {
        "valid": True,
        "bound": True,
        "plan_fingerprint": actual,
        "execution_authority": "NONE",
        "approval_does_not_execute": True,
    }



def create_approval_state(plan):
    """
    Create a read-only approval-state object for an eligible plan.

    This function does NOT approve or execute anything.
    """

    validation = validate_plan_eligibility(plan)

    if not validation.get("eligible"):
        return {
            "status": "rejected_by_validator",
            "approval_state": "NOT_REQUESTED",
            "validation": validation,
            "execution_authority": "NONE",
            "approval_does_not_execute": True,
        }

    return {
        "status": "ok",
        "approval_state": "AWAITING_EXPLICIT_USER_APPROVAL",
        "target_component": plan.get("target_component"),
        "proposed_action": plan.get("proposed_action"),
        "mutation_risk": plan.get("mutation_risk"),
        "plan_confidence": plan.get("plan_confidence"),
        "plan_fingerprint": remediation_plan_fingerprint(plan),
        "execution_authority": "NONE",
        "approval_does_not_execute": True,
    }


def transition_approval_state(
    state,
    decision,
    explicit=False,
):
    """
    Pure approval-state transition.

    It does not execute any host mutation.
    """

    if not isinstance(state, dict):
        return {
            "ok": False,
            "error": "state_must_be_object",
            "execution_authority": "NONE",
        }

    current = state.get("approval_state")

    allowed_states = {
        "NOT_REQUESTED",
        "AWAITING_EXPLICIT_USER_APPROVAL",
        "APPROVED_FOR_FUTURE_EXECUTION",
        "REJECTED",
        "EXPIRED",
        "STALE_REQUIRES_REVALIDATION",
    }

    if current not in allowed_states:
        return {
            "ok": False,
            "error": "invalid_approval_state",
            "execution_authority": "NONE",
        }

    decision = str(decision or "").strip().casefold()

    if current != "AWAITING_EXPLICIT_USER_APPROVAL":
        return {
            "ok": False,
            "error": "state_not_awaiting_approval",
            "approval_state": current,
            "execution_authority": "NONE",
        }

    if not explicit:
        return {
            "ok": False,
            "error": "explicit_approval_required",
            "approval_state": current,
            "execution_authority": "NONE",
        }

    if decision == "request":
        return {
            "ok": True,
            "state": current,
            "approval_state": current,
            "execution_authority": "NONE",
            "approval_does_not_execute": True,
        }

    if decision == "approve":
        new_state = "APPROVED_FOR_FUTURE_EXECUTION"

    elif decision == "reject":
        new_state = "REJECTED"

    elif decision == "expire":
        new_state = "EXPIRED"

    else:
        return {
            "ok": False,
            "error": "invalid_decision",
            "approval_state": current,
            "execution_authority": "NONE",
        }

    out = dict(state)

    out["approval_state"] = new_state
    out["execution_authority"] = "NONE"
    out["approval_does_not_execute"] = True

    return {
        "ok": True,
        "state": out,
        "execution_authority": "NONE",
        "approval_does_not_execute": True,
    }



def validate_plan_eligibility(plan):
    """
    Validate whether a remediation plan may advance to
    AWAITING_EXPLICIT_USER_APPROVAL.

    This function is pure/read-only.

    It does not:
    - approve;
    - execute;
    - modify state.
    """

    errors = []

    if not isinstance(plan, dict):
        return {
            "eligible": False,
            "next_state": "NOT_REQUESTED",
            "errors": [
                "plan_must_be_object"
            ],
            "execution_authority": "NONE",
        }

    required = REMEDIATION_CONTRACT[
        "required_fields"
    ]

    for field in required:
        if field not in plan:
            errors.append(
                f"missing_field:{field}"
            )

    if errors:
        return {
            "eligible": False,
            "next_state": "NOT_REQUESTED",
            "errors": errors,
            "execution_authority": "NONE",
        }

    if plan.get("plan_confidence") !=             "READY_FOR_APPROVAL":
        errors.append(
            "plan_confidence_not_ready"
        )

    target = str(
        plan.get("target_component", "")
    ).strip()

    if not target:
        errors.append(
            "target_component_missing"
        )

    owner = str(
        plan.get("current_owner", "")
    ).strip()

    if not owner or owner.casefold() == "unknown":
        errors.append(
            "current_owner_unknown"
        )

    risk = str(
        plan.get("mutation_risk", "")
    ).strip()

    if not risk or risk.casefold() == "unknown":
        errors.append(
            "mutation_risk_unknown"
        )

    invariants = plan.get("invariants")

    if not isinstance(invariants, list) or             not invariants:
        errors.append(
            "invariants_missing"
        )

    prechecks = plan.get("prechecks")

    if not isinstance(prechecks, list) or             not prechecks:
        errors.append(
            "prechecks_missing"
        )

    verification = plan.get("verification")

    if not isinstance(verification, list) or             not verification:
        errors.append(
            "verification_missing"
        )

    success = plan.get("success_criteria")

    if not isinstance(success, list) or             not success:
        errors.append(
            "success_criteria_missing"
        )

    abort = plan.get("abort_conditions")

    if not isinstance(abort, list) or             not abort:
        errors.append(
            "abort_conditions_missing"
        )

    rollback_required = plan.get(
        "rollback_required"
    )

    rollback_plan = plan.get(
        "rollback_plan"
    )

    if rollback_required in {
        True,
        "yes",
        "required",
    }:
        if not isinstance(
            rollback_plan,
            list,
        ) or not rollback_plan:
            errors.append(
                "rollback_plan_missing"
            )

    proposed_action = str(
        plan.get("proposed_action", "")
    ).strip()

    if not proposed_action:
        errors.append(
            "proposed_action_missing"
        )

    action_reason = str(
        plan.get("action_reason", "")
    ).strip()

    if not action_reason:
        errors.append(
            "action_reason_missing"
        )

    execution_mode = plan.get(
        "execution_mode"
    )

    if execution_mode != "PLAN_ONLY":
        errors.append(
            "execution_mode_invalid"
        )

    authorization = plan.get(
        "authorization"
    )

    if authorization != "NONE":
        errors.append(
            "authorization_must_be_none"
        )

    eligible = not errors

    return {
        "eligible": eligible,
        "next_state": (
            "AWAITING_EXPLICIT_USER_APPROVAL"
            if eligible
            else "NOT_REQUESTED"
        ),
        "errors": errors,
        "execution_authority": "NONE",
        "approval_does_not_execute": True,
    }




# ============================================================

# ============================================================
# STAGE 8A — AUTONOMOUS DIAGNOSIS
# ============================================================

DIAGNOSIS_CONTRACT = {
    "schema": "aag-diagnosis-v1",

    "mode": "DIAGNOSIS_AND_PLANNING",

    "execution_authority": "NONE",

    "tool_is_pure": True,

    "evidence_classes": [
        "CURRENT_LIVE_EVIDENCE",
        "REGISTRY_EVIDENCE",
        "HISTORICAL_EVIDENCE",
        "USER_REPORTED_SYMPTOM",
        "INFERENCE",
        "UNKNOWN",
    ],

    "root_cause_confidence": [
        "CONFIRMED",
        "PROBABLE",
        "POSSIBLE",
        "INSUFFICIENT_EVIDENCE",
    ],

    "required_output_fields": [
        "incident",
        "symptoms",
        "current_evidence",
        "registry_evidence",
        "historical_evidence",
        "healthy_components",
        "failed_components",
        "unknown_components",
        "contradictions",
        "root_cause_candidates",
        "confidence",
        "next_best_check",
        "recommended_remediation",
        "risk_class",
        "requires_approval",
    ],

    "rules": [
        "live facts must not be represented as historical facts",
        "historical evidence must not be represented as current state",
        "registry evidence must not be represented as current runtime state",
        "inference must not be represented as fact",
        "unknown observability must remain unknown",
        "root cause requires evidence",
        "prefer the smallest useful next check",
        "diagnosis does not authorize mutation",
    ],
}


def _diagnosis_normalized_text(value):
    return str(value or "").strip()


def _diagnosis_detect_domain(incident):
    text = _diagnosis_normalized_text(
        incident
    ).casefold()

    otzar_terms = (
        "אוצר",
        "אוצר החכמה",
        "otzar",
        "otzar hahochma",
        "otzar hachochma",
    )

    winboat_terms = (
        "winboat",
        "ווינבוט",
    )

    docker_terms = (
        "docker",
        "דוקר",
    )

    usb_terms = (
        "usb clone",
        "usbclone",
        "kingston",
        "קינגסטון",
    )

    if any(term in text for term in otzar_terms):
        return "otzar"

    if any(term in text for term in winboat_terms):
        return "winboat"

    if any(term in text for term in docker_terms):
        return "docker"

    if any(term in text for term in usb_terms):
        return "usbclone"

    return "general"


def _diagnosis_new_incident(incident):
    symptom = _diagnosis_normalized_text(
        incident
    )

    domain = _diagnosis_detect_domain(
        symptom
    )

    base = {
        "schema": DIAGNOSIS_CONTRACT["schema"],
        "status": "incident_initialized",
        "domain": domain,
        "incident": symptom,
        "symptoms": (
            [symptom]
            if symptom
            else []
        ),
        "current_evidence": [],
        "registry_evidence": [],
        "historical_evidence": [],
        "healthy_components": [],
        "failed_components": [],
        "unknown_components": [],
        "contradictions": [],
        "root_cause_candidates": [],
        "confidence": "INSUFFICIENT_EVIDENCE",
        "recommended_remediation": {
            "mode": "PLAN_ONLY",
            "status": "NOT_JUSTIFIED_YET",
            "reason":
                "No remediation should be selected before relevant "
                "evidence is collected.",
        },
        "risk_class": "READ_ONLY_DIAGNOSIS",
        "requires_approval": False,
        "executed": False,
        "mutated": False,
        "execution_authority": "NONE",
    }

    if domain == "otzar":
        base["initial_evidence_plan"] = [
            {
                "priority": 1,
                "tool": "query_component_registry",
                "arguments": {
                    "action": "diagnostic_plan",
                    "component": "אוצר החכמה",
                },
                "purpose":
                    "Establish current canonical component group, "
                    "ownership and focused diagnostic path.",
                "evidence_class": "REGISTRY_EVIDENCE",
            },
            {
                "priority": 2,
                "tool": "run_live_audit",
                "arguments": {
                    "profile": "otzar",
                },
                "purpose":
                    "Collect the focused current Ubuntu/Otzar live "
                    "state without broad machine-wide auditing.",
                "evidence_class": "CURRENT_LIVE_EVIDENCE",
            },
            {
                "priority": 3,
                "tool": "windows_read_action",
                "arguments": {
                    "action": "otzar_status",
                },
                "purpose":
                    "Observe the fixed Windows-side Otzar process, "
                    "RemoteApp process, D: and Kingston signals.",
                "evidence_class": "CURRENT_LIVE_EVIDENCE",
                "conditional": (
                    "Use when the Windows/application/licensing "
                    "layer remains relevant after the focused host "
                    "evidence."
                ),
            },
            {
                "priority": 4,
                "tool": "search_historical_knowledge",
                "arguments": {
                    "query": (
                        "Otzar HaHochma prior incidents, known-good "
                        "architecture, WinBoat, D:, NBD/COW, Kingston "
                        "USB Clone; return historical evidence only."
                    ),
                },
                "purpose":
                    "Use prior incidents to guide interpretation, "
                    "not to assert current state.",
                "evidence_class": "HISTORICAL_EVIDENCE",
                "conditional":
                    "Use when architecture history or prior failure "
                    "patterns can materially disambiguate current evidence.",
            },
        ]

        base["unknown_components"] = [
            "RemoteApp window visibility",
            "RemoteApp window geometry",
            "RemoteApp UI responsiveness",
            "white-window state",
            "splash-screen state",
        ]

        base["next_best_check"] = {
            "tool": "query_component_registry",
            "arguments": {
                "action": "diagnostic_plan",
                "component": "אוצר החכמה",
            },
            "reason":
                "Start with canonical ownership and the focused "
                "diagnostic plan before broadening.",
        }

        base["risk_class"] = (
            "HIGH_RISK_DOMAIN_READ_ONLY_DIAGNOSIS"
        )

        return base

    if domain == "winboat":
        base["initial_evidence_plan"] = [
            {
                "priority": 1,
                "tool": "query_component_registry",
                "arguments": {
                    "action": "diagnostic_plan",
                    "component": "winboat",
                },
                "evidence_class": "REGISTRY_EVIDENCE",
            },
            {
                "priority": 2,
                "tool": "run_live_audit",
                "arguments": {
                    "profile": "docker",
                },
                "evidence_class": "CURRENT_LIVE_EVIDENCE",
            },
            {
                "priority": 3,
                "tool": "windows_read_action",
                "arguments": {
                    "action": "overview",
                },
                "evidence_class": "CURRENT_LIVE_EVIDENCE",
            },
        ]

        base["next_best_check"] = (
            base["initial_evidence_plan"][0]
        )

        return base

    if domain == "docker":
        base["initial_evidence_plan"] = [
            {
                "priority": 1,
                "tool": "controlled_read_action",
                "arguments": {
                    "action": "check_service_status",
                    "target": "docker.service",
                },
                "evidence_class": "CURRENT_LIVE_EVIDENCE",
            },
        ]

        base["next_best_check"] = (
            base["initial_evidence_plan"][0]
        )

        return base

    if domain == "usbclone":
        base["initial_evidence_plan"] = [
            {
                "priority": 1,
                "tool": "query_component_registry",
                "arguments": {
                    "action": "diagnostic_plan",
                    "component": "kingston",
                },
                "evidence_class": "REGISTRY_EVIDENCE",
            },
            {
                "priority": 2,
                "tool": "run_live_audit",
                "arguments": {
                    "profile": "services",
                },
                "evidence_class": "CURRENT_LIVE_EVIDENCE",
            },
        ]

        base["next_best_check"] = (
            base["initial_evidence_plan"][0]
        )

        base["risk_class"] = (
            "HIGH_RISK_DOMAIN_READ_ONLY_DIAGNOSIS"
        )

        return base

    base["initial_evidence_plan"] = [
        {
            "priority": 1,
            "tool": "run_live_audit",
            "arguments": {
                "profile": "overview",
            },
            "purpose":
                "The incident is broad or not mapped to a known "
                "focused subsystem.",
            "evidence_class": "CURRENT_LIVE_EVIDENCE",
        },
    ]

    base["next_best_check"] = (
        base["initial_evidence_plan"][0]
    )

    return base


def _diagnosis_deep_find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]

        for value in obj.values():
            found = _diagnosis_deep_find(
                value,
                key,
            )

            if found is not None:
                return found

    elif isinstance(obj, list):
        for value in obj:
            found = _diagnosis_deep_find(
                value,
                key,
            )

            if found is not None:
                return found

    return None


def _diagnosis_add_unique(items, value):
    if value not in items:
        items.append(value)


def _diagnosis_extract_otzar_windows(
    windows_evidence,
):
    facts = []
    healthy = []
    failed = []
    unknown = []
    contradictions = []
    candidates = []

    if not isinstance(
        windows_evidence,
        dict,
    ) or not windows_evidence:
        unknown.extend([
            "Windows Guest Otzar status",
            "otzar.exe process state",
            "OtzarRemoteApp-v4.exe process state",
            "Windows D: state",
            "Windows Kingston state",
        ])

        return {
            "facts": facts,
            "healthy": healthy,
            "failed": failed,
            "unknown": unknown,
            "contradictions": contradictions,
            "candidates": candidates,
        }

    otzar = _diagnosis_deep_find(
        windows_evidence,
        "otzarProcess",
    )

    remote = _diagnosis_deep_find(
        windows_evidence,
        "remoteAppProcess",
    )

    drive = _diagnosis_deep_find(
        windows_evidence,
        "driveD",
    )

    kingston = _diagnosis_deep_find(
        windows_evidence,
        "kingstonUsbPresent",
    )

    if isinstance(otzar, dict):
        running = otzar.get("running")
        count = otzar.get("count")

        facts.append({
            "evidence_class": "CURRENT_LIVE_EVIDENCE",
            "source": "windows_read_action:otzar_status",
            "fact": (
                f"otzar.exe running={running!r}, "
                f"count={count!r}"
            ),
        })

        if running is True:
            _diagnosis_add_unique(
                healthy,
                "Windows otzar.exe process layer",
            )

            if isinstance(count, int) and count <= 0:
                contradictions.append(
                    "otzar.exe reports running=true but count<=0"
                )

        elif running is False:
            _diagnosis_add_unique(
                failed,
                "Windows otzar.exe process layer",
            )

            candidates.append({
                "candidate":
                    "Otzar application process is not running",
                "confidence": "PROBABLE",
                "basis": [
                    "current Windows Guest observation reports "
                    "otzar.exe running=false",
                ],
                "not_proven":
                    "The reason the process is absent is not yet known.",
            })

        else:
            _diagnosis_add_unique(
                unknown,
                "Windows otzar.exe process state",
            )

    else:
        _diagnosis_add_unique(
            unknown,
            "Windows otzar.exe process state",
        )

    if isinstance(remote, dict):
        running = remote.get("running")
        count = remote.get("count")

        facts.append({
            "evidence_class": "CURRENT_LIVE_EVIDENCE",
            "source": "windows_read_action:otzar_status",
            "fact": (
                "OtzarRemoteApp-v4.exe "
                f"running={running!r}, count={count!r}"
            ),
        })

        if running is True:
            _diagnosis_add_unique(
                healthy,
                "Windows Otzar RemoteApp process layer",
            )

            if isinstance(count, int) and count <= 0:
                contradictions.append(
                    "OtzarRemoteApp-v4.exe reports "
                    "running=true but count<=0"
                )

        elif running is False:
            _diagnosis_add_unique(
                failed,
                "Windows Otzar RemoteApp process layer",
            )

            candidates.append({
                "candidate":
                    "Otzar RemoteApp process is not running",
                "confidence": "POSSIBLE",
                "basis": [
                    "current Windows Guest observation reports "
                    "OtzarRemoteApp-v4.exe running=false",
                ],
                "not_proven": (
                    "Process absence alone does not establish why "
                    "the user-visible Otzar workflow failed."
                ),
            })

        else:
            _diagnosis_add_unique(
                unknown,
                "Windows Otzar RemoteApp process state",
            )

    else:
        _diagnosis_add_unique(
            unknown,
            "Windows Otzar RemoteApp process state",
        )

    if isinstance(drive, dict):
        exists = drive.get("exists")
        filesystem = drive.get("fileSystem")
        volume = drive.get("volumeName")

        facts.append({
            "evidence_class": "CURRENT_LIVE_EVIDENCE",
            "source": "windows_read_action:otzar_status",
            "fact": (
                f"D: exists={exists!r}, "
                f"fileSystem={filesystem!r}, "
                f"volumeName={volume!r}"
            ),
        })

        if exists is True:
            _diagnosis_add_unique(
                healthy,
                "Windows D: presence",
            )

            if (
                isinstance(filesystem, str)
                and filesystem.strip()
                and filesystem.casefold() == "ntfs"
            ):
                _diagnosis_add_unique(
                    healthy,
                    "Windows D: NTFS filesystem",
                )

            elif isinstance(filesystem, str) and filesystem.strip():
                _diagnosis_add_unique(
                    failed,
                    "Windows D: expected filesystem",
                )

                candidates.append({
                    "candidate":
                        "Windows D: filesystem differs from expected NTFS",
                    "confidence": "PROBABLE",
                    "basis": [
                        "D: currently exists",
                        f"reported filesystem={filesystem!r}",
                    ],
                    "not_proven":
                        "Underlying Linux/NBD root cause is not "
                        "established by this Windows observation.",
                })

            else:
                _diagnosis_add_unique(
                    unknown,
                    "Windows D: filesystem identity",
                )

        elif exists is False:
            _diagnosis_add_unique(
                failed,
                "Windows D: presence",
            )

            candidates.append({
                "candidate":
                    "Otzar Windows data drive D: is unavailable",
                "confidence": "PROBABLE",
                "basis": [
                    "current Windows Guest observation reports "
                    "D: exists=false",
                ],
                "not_proven":
                    "This does not by itself identify whether the "
                    "cause is WinBoat, NBD/COW, storage mapping or "
                    "another lower layer.",
            })

            if filesystem:
                contradictions.append(
                    "D: reports exists=false while also reporting "
                    "a filesystem value"
                )

        else:
            _diagnosis_add_unique(
                unknown,
                "Windows D: presence",
            )

    else:
        _diagnosis_add_unique(
            unknown,
            "Windows D: state",
        )

    if isinstance(kingston, bool):
        facts.append({
            "evidence_class": "CURRENT_LIVE_EVIDENCE",
            "source": "windows_read_action:otzar_status",
            "fact":
                f"Kingston USB present={kingston!r}",
        })

        if kingston:
            _diagnosis_add_unique(
                healthy,
                "Windows Kingston licensing-device presence",
            )

        else:
            _diagnosis_add_unique(
                failed,
                "Windows Kingston licensing-device presence",
            )

            candidates.append({
                "candidate":
                    "Kingston licensing device is not visible "
                    "inside Windows",
                "confidence": "POSSIBLE",
                "basis": [
                    "current Windows Guest observation reports "
                    "kingstonUsbPresent=false",
                ],
                "not_proven": (
                    "Whether this is the actual user-visible failure "
                    "still depends on Otzar licensing behavior and "
                    "the host USB Clone state."
                ),
            })

    else:
        _diagnosis_add_unique(
            unknown,
            "Windows Kingston licensing-device presence",
        )

    return {
        "facts": facts,
        "healthy": healthy,
        "failed": failed,
        "unknown": unknown,
        "contradictions": contradictions,
        "candidates": candidates,
    }


def _diagnosis_evaluate(
    incident,
    evidence,
):
    if not isinstance(evidence, dict):
        return {
            "status": "blocked",
            "error": "evidence_must_be_object",
            "executed": False,
            "mutated": False,
            "execution_authority": "NONE",
        }

    symptom = _diagnosis_normalized_text(
        incident
    )

    domain = _diagnosis_detect_domain(
        symptom
    )

    registry = evidence.get("registry")
    live = evidence.get("live_audit")
    windows = evidence.get("windows")
    historical = evidence.get("historical")

    result = {
        "schema": DIAGNOSIS_CONTRACT["schema"],
        "status": "diagnosis_evaluated",
        "domain": domain,
        "incident": symptom,
        "symptoms": (
            [symptom]
            if symptom
            else []
        ),
        "current_evidence": [],
        "registry_evidence": [],
        "historical_evidence": [],
        "healthy_components": [],
        "failed_components": [],
        "unknown_components": [],
        "contradictions": [],
        "root_cause_candidates": [],
        "confidence": "INSUFFICIENT_EVIDENCE",
        "next_best_check": None,
        "recommended_remediation": {
            "mode": "PLAN_ONLY",
            "status": "NOT_JUSTIFIED_YET",
            "reason":
                "Stage 8A does not select mutation without a "
                "sufficiently established actionable failure.",
        },
        "risk_class": "READ_ONLY_DIAGNOSIS",
        "requires_approval": False,
        "executed": False,
        "mutated": False,
        "execution_authority": "NONE",
    }

    if registry is not None:
        result["registry_evidence"].append({
            "evidence_class": "REGISTRY_EVIDENCE",
            "source": "query_component_registry",
            "fact":
                "Structured Registry evidence was supplied to "
                "the diagnosis evaluator.",
            "payload": registry,
        })

    if live is not None:
        result["current_evidence"].append({
            "evidence_class": "CURRENT_LIVE_EVIDENCE",
            "source": "run_live_audit",
            "fact":
                "Focused live-audit evidence was supplied to "
                "the diagnosis evaluator.",
            "payload": live,
        })

    if historical is not None:
        result["historical_evidence"].append({
            "evidence_class": "HISTORICAL_EVIDENCE",
            "source": "search_historical_knowledge",
            "fact":
                "Historical knowledge was supplied to the "
                "diagnosis evaluator.",
            "payload": historical,
        })

    if domain == "otzar":
        result["risk_class"] = (
            "HIGH_RISK_DOMAIN_READ_ONLY_DIAGNOSIS"
        )

        win = _diagnosis_extract_otzar_windows(
            windows
        )

        result["current_evidence"].extend(
            win["facts"]
        )

        for item in win["healthy"]:
            _diagnosis_add_unique(
                result["healthy_components"],
                item,
            )

        for item in win["failed"]:
            _diagnosis_add_unique(
                result["failed_components"],
                item,
            )

        for item in win["unknown"]:
            _diagnosis_add_unique(
                result["unknown_components"],
                item,
            )

        result["contradictions"].extend(
            win["contradictions"]
        )

        result["root_cause_candidates"].extend(
            win["candidates"]
        )

        # Current sensors do not observe these UI states.
        for item in [
            "RemoteApp window visibility",
            "RemoteApp UI responsiveness",
            "white-window state",
            "splash-screen state",
            "RemoteApp window geometry",
        ]:
            _diagnosis_add_unique(
                result["unknown_components"],
                item,
            )

        if registry is None:
            result["next_best_check"] = {
                "tool": "query_component_registry",
                "arguments": {
                    "action": "diagnostic_plan",
                    "component": "אוצר החכמה",
                },
                "reason":
                    "Canonical ownership and dependencies have "
                    "not yet been supplied.",
            }

        elif live is None:
            result["next_best_check"] = {
                "tool": "run_live_audit",
                "arguments": {
                    "profile": "otzar",
                },
                "reason":
                    "Focused current host/Otzar live evidence "
                    "has not yet been supplied.",
            }

        elif windows is None:
            result["next_best_check"] = {
                "tool": "windows_read_action",
                "arguments": {
                    "action": "otzar_status",
                },
                "reason":
                    "The bounded Windows Otzar/application/"
                    "D:/Kingston layer has not yet been observed.",
            }

        elif result["contradictions"]:
            result["next_best_check"] = {
                "tool": None,
                "arguments": {},
                "reason": (
                    "Resolve the listed contradiction using the "
                    "smallest relevant existing read-only sensor. "
                    "Do not infer root cause while live evidence "
                    "conflicts."
                ),
            }

        elif result["root_cause_candidates"]:
            confidences = {
                item.get("confidence")
                for item in result[
                    "root_cause_candidates"
                ]
            }

            if "PROBABLE" in confidences:
                result["confidence"] = "PROBABLE"

            else:
                result["confidence"] = "POSSIBLE"

            result["recommended_remediation"] = {
                "mode": "PLAN_ONLY",
                "status": "NEEDS_REMEDIATION_CONTEXT",
                "reason": (
                    "At least one current-state failure candidate "
                    "exists. Before any mutation, identify the exact "
                    "Registry target, lifecycle owner, risk, "
                    "invariants and rollback requirements."
                ),
                "next_planning_tool": {
                    "tool": "query_component_registry",
                    "arguments": {
                        "action": "remediation_context",
                        "component": "אוצר החכמה",
                    },
                },
            }

        else:
            result["confidence"] = (
                "INSUFFICIENT_EVIDENCE"
            )

            result["next_best_check"] = {
                "tool": None,
                "arguments": {},
                "reason": (
                    "The supplied lower-layer evidence did not "
                    "establish a failure. Remaining user-visible "
                    "RemoteApp/UI state is outside current bounded "
                    "observability and must remain UNKNOWN."
                ),
            }

        return result

    # Generic evaluator.
    if registry is None:
        result["next_best_check"] = {
            "tool": "query_component_registry",
            "arguments": {
                "action": "resolve",
                "component": symptom,
            },
            "reason":
                "No structured component identity/ownership "
                "evidence was supplied.",
        }

    elif live is None:
        result["next_best_check"] = {
            "tool": "run_live_audit",
            "arguments": {
                "profile": "overview",
            },
            "reason":
                "No current live machine evidence was supplied.",
        }

    else:
        result["next_best_check"] = {
            "tool": None,
            "arguments": {},
            "reason":
                "The generic Stage 8A evaluator has no basis "
                "to invent a component-specific root cause.",
        }

    return result

def validate_remediation_plan_tool(plan):
    """
    Pure structured remediation-plan validator exposed to the model.

    This function delegates exclusively to the existing
    validate_plan_eligibility(plan) safety validator.

    It does not:
    - approve;
    - prepare mutation;
    - execute;
    - inspect live state;
    - modify state.
    """

    result = validate_plan_eligibility(
        plan
    )

    return {
        "schema": "aag-remediation-validation-v1",
        "status": (
            "eligible"
            if result.get("eligible")
            else "not_eligible"
        ),
        "validation": result,
        "executed": False,
        "mutated": False,
        "execution_authority": "NONE",
        "approval_does_not_execute": True,
    }



def diagnosis_workspace(
    action,
    incident=None,
    evidence=None,
):
    """
    Pure Stage 8A diagnostic reasoning helper.

    This function performs no subprocess execution, no network access,
    no filesystem writes and no host mutation.
    """

    allowed_actions = {
        "contract",
        "new_incident",
        "evaluate",
    }

    if action not in allowed_actions:
        return {
            "status": "blocked",
            "error": "diagnosis_action_not_allowlisted",
            "allowed": sorted(allowed_actions),
            "executed": False,
            "mutated": False,
            "execution_authority": "NONE",
        }

    if action == "contract":
        return {
            "status": "ok",
            "contract": DIAGNOSIS_CONTRACT,
            "executed": False,
            "mutated": False,
            "execution_authority": "NONE",
        }

    if action == "new_incident":
        return _diagnosis_new_incident(
            incident
        )

    return _diagnosis_evaluate(
        incident,
        evidence,
    )




# WINDOWS / WINBOAT GUEST — BOUNDED READ-ONLY OBSERVABILITY
# ============================================================

WINDOWS_READ_ENDPOINTS = {
    "health": "/health",
    "version": "/version",
    "metrics": "/metrics",
    "rdp_status": "/rdp/status",
    "apps": "/apps",
    "otzar_status": "/aag/otzar/status",
    "otzar_processes": "/aag/processes",
    "otzar_drive": "/aag/drive/d",
    "otzar_usb": "/aag/usb/kingston",
}


def resolve_winboat_guest_api():
    """
    Resolve the current loopback mapping for WinBoat guest TCP 7148.

    The mapping is dynamically selected by Docker from the configured
    host range. This function performs only:
        docker port WinBoat 7148/tcp

    It does not modify Docker or WinBoat.
    """

    import subprocess

    command = [
        "/usr/bin/docker",
        "port",
        "WinBoat",
        "7148/tcp",
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    if result.returncode != 0:
        return {
            "status": "unavailable",
            "error":
                "docker_port_lookup_failed",
            "returncode":
                result.returncode,
            "stderr":
                result.stderr.strip()[-2000:],
            "mutated": False,
        }

    mappings = [
        x.strip()
        for x in result.stdout.splitlines()
        if x.strip()
    ]

    if len(mappings) != 1:
        return {
            "status": "blocked",
            "error":
                "unexpected_guest_api_mapping_count",
            "count": len(mappings),
            "mutated": False,
        }

    mapping = mappings[0]

    if ":" not in mapping:
        return {
            "status": "blocked",
            "error":
                "invalid_guest_api_mapping",
            "mapping": mapping,
            "mutated": False,
        }

    host, port_text = (
        mapping.rsplit(":", 1)
    )

    if host != "127.0.0.1":
        return {
            "status": "blocked",
            "error":
                "guest_api_not_loopback_only",
            "host": host,
            "mutated": False,
        }

    try:
        port = int(port_text)
    except ValueError:
        return {
            "status": "blocked",
            "error":
                "guest_api_port_invalid",
            "mutated": False,
        }

    if not (
        47280 <= port <= 47289
    ):
        return {
            "status": "blocked",
            "error":
                "guest_api_port_outside_expected_range",
            "port": port,
            "mutated": False,
        }

    return {
        "status": "ok",
        "host": host,
        "port": port,
        "container": "WinBoat",
        "guest_port": 7148,
        "mutated": False,
    }


def _windows_guest_get(action):
    """
    Execute one fixed allowlisted GET request.

    The caller cannot choose a URL, HTTP method, host, port,
    request payload or Windows command.
    """

    import http.client
    import json

    path = WINDOWS_READ_ENDPOINTS.get(
        action
    )

    if path is None:
        return {
            "status": "blocked",
            "error":
                "windows_read_action_not_allowlisted",
            "action": action,
            "executed": False,
            "mutated": False,
        }

    endpoint = (
        resolve_winboat_guest_api()
    )

    if endpoint.get("status") != "ok":
        return {
            "status": "unavailable",
            "error":
                "windows_guest_api_unavailable",
            "endpoint": endpoint,
            "executed": False,
            "mutated": False,
        }

    host = endpoint["host"]
    port = endpoint["port"]

    timeout = (
        15
        if action == "apps"
        else 6
    )

    connection = (
        http.client.HTTPConnection(
            host,
            port,
            timeout=timeout,
        )
    )

    try:
        connection.request(
            "GET",
            path,
            headers={
                "Connection":
                    "close",
                "User-Agent":
                    "AAG-Windows-ReadOnly/1.0",
            },
        )

        response = (
            connection.getresponse()
        )

        maximum = (
            5_000_000
            if action == "apps"
            else 1_000_000
        )

        body = response.read(
            maximum + 1
        )

        if len(body) > maximum:
            return {
                "status": "blocked",
                "error":
                    "windows_guest_response_too_large",
                "action": action,
                "http_status":
                    response.status,
                "executed": True,
                "mutated": False,
            }

        if response.status != 200:
            return {
                "status":
                    "request_failed",
                "action": action,
                "endpoint": path,
                "http_status":
                    response.status,
                "body_preview":
                    body[:2000].decode(
                        "utf-8",
                        "replace",
                    ),
                "executed": True,
                "mutated": False,
            }

        try:
            data = json.loads(body)
        except Exception as e:
            return {
                "status":
                    "request_failed",
                "error":
                    "invalid_json",
                "detail":
                    str(e),
                "action": action,
                "endpoint": path,
                "http_status":
                    response.status,
                "executed": True,
                "mutated": False,
            }

        if action == "apps":
            if not isinstance(
                data,
                list,
            ):
                return {
                    "status":
                        "request_failed",
                    "error":
                        "unexpected_apps_shape",
                    "executed": True,
                    "mutated": False,
                }

            sanitized = []

            for item in data[:500]:

                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                sanitized.append({
                    "name":
                        item.get("Name"),
                    "path":
                        item.get("Path"),
                    "args":
                        item.get("Args"),
                    "source":
                        item.get("Source"),
                })

            data = {
                "count":
                    len(data),
                "returned":
                    len(sanitized),
                "truncated":
                    len(data)
                    > len(sanitized),
                "apps":
                    sanitized,
                "icons_removed":
                    True,
            }

        return {
            "status": "completed",
            "action": action,
            "endpoint": path,
            "http_status":
                response.status,
            "data": data,
            "transport": {
                "host": host,
                "host_port": port,
                "guest_port": 7148,
                "loopback_only": True,
            },
            "execution_class":
                (
                    "READ_ONLY_GUEST_COMMAND"
                    if action in {
                        "rdp_status",
                        "apps",
                        "otzar_status",
                        "otzar_processes",
                        "otzar_drive",
                        "otzar_usb",
                    }
                    else
                    "READ_ONLY_NATIVE_QUERY"
                ),
            "executed": True,
            "mutated": False,
            "execution_authority":
                "READ_ONLY_ALLOWLISTED",
        }

    except Exception as e:
        return {
            "status":
                "request_failed",
            "error":
                type(e).__name__,
            "detail":
                str(e),
            "action":
                action,
            "endpoint":
                path,
            "executed":
                False,
            "mutated":
                False,
        }

    finally:
        connection.close()


def windows_read_action(action):
    """
    Bounded Windows read-only observability.

    Supported:
        overview
        health
        version
        metrics
        rdp_status
        apps
    """

    allowed = {
        "overview",
        "health",
        "version",
        "metrics",
        "rdp_status",
        "apps",
        "otzar_status",
        "otzar_processes",
        "otzar_drive",
        "otzar_usb",
    }

    if action not in allowed:
        return {
            "status": "blocked",
            "error":
                "windows_read_action_not_allowlisted",
            "action": action,
            "executed": False,
            "mutated": False,
        }

    if action != "overview":
        return _windows_guest_get(
            action
        )

    results = {}

    for name in (
        "health",
        "version",
        "metrics",
        "rdp_status",
    ):
        results[name] = (
            _windows_guest_get(
                name
            )
        )

    all_ok = all(
        item.get("status")
        == "completed"
        for item in results.values()
    )

    return {
        "status":
            (
                "completed"
                if all_ok
                else "partial"
            ),
        "action":
            "overview",
        "results":
            results,
        "executed":
            True,
        "mutated":
            False,
        "execution_authority":
            "READ_ONLY_ALLOWLISTED",
    }


def component_registry(action, component=None):
    """
    Read structured component knowledge.

    This function is strictly read-only.
    """

    allowed_actions = {
        "list",
        "resolve",
        "diagnostic_plan",
        "remediation_context",
        "remediation_contract",
        "approval_contract",
        "validate_remediation_plan",
        "get",
        "owner",
        "dependencies",
        "dependents",
        "invariants",
        "risk",
    }

    if action not in allowed_actions:
        return {
            "error": "invalid_registry_action",
            "allowed_actions": sorted(allowed_actions),
        }

    try:
        data = json.loads(
            REGISTRY_FILE.read_text(encoding="utf-8")
        )
    except Exception as exc:
        return {
            "error": "registry_read_failed",
            "detail": repr(exc),
        }

    records = data.get("components", [])

    if not isinstance(records, list):
        return {
            "error": "invalid_registry_format"
        }

    if action == "list":
        return {
            "schema": data.get("schema"),
            "generated_state": data.get("generated_state"),
            "components": [
                {
                    "identity": x.get("identity"),
                    "name": x.get("name"),
                    "category": x.get("category"),
                    "lifecycle_mode": x.get(
                        "lifecycle_mode"
                    ),
                    "mutation_risk": x.get(
                        "mutation_risk"
                    ),
                    "confidence": x.get("confidence"),
                }
                for x in records
            ],
        }

    if action == "resolve":
        if not component:
            return {
                "error": "component_required",
                "action": action,
            }

        query = component.strip().casefold()

        aliases = {
            "otzar": [
                "otzar-gpt",
                "otzar-physical-source",
                "winboat",
                "usbclone-kingston-service",
            ],
            "אוצר": [
                "otzar-gpt",
                "otzar-physical-source",
                "winboat",
                "usbclone-kingston-service",
            ],
            "אוצר החכמה": [
                "otzar-gpt",
                "otzar-physical-source",
                "winboat",
                "usbclone-kingston-service",
            ],
            "winboat": [
                "winboat",
            ],
            "docker": [
                "docker",
            ],
            "anythingllm": [
                "anythingllm",
            ],
            "anything llm": [
                "anythingllm",
            ],
            "data": [
                "data-mount",
            ],
            "/mnt/data": [
                "data-mount",
            ],
            "usb clone": [
                "usbclone-dummy-hcd-service",
                "usbclone-kingston-service",
            ],
            "usbclone": [
                "usbclone-dummy-hcd-service",
                "usbclone-kingston-service",
            ],
            "kingston": [
                "usbclone-kingston-service",
            ],
            "host bridge": [
                "aag-host-bridge",
            ],
            "ubuntu agent": [
                "aag-ubuntu-agent",
            ],
            "aag ubuntu agent": [
                "aag-ubuntu-agent",
            ],
        }

        by_identity = {
            str(x.get("identity", "")).casefold(): x
            for x in records
        }

        # Exact canonical identity wins.
        if query in by_identity:
            x = by_identity[query]
            return {
                "query": component,
                "match_type": "exact_identity",
                "matches": [
                    {
                        "identity": x.get("identity"),
                        "name": x.get("name"),
                        "category": x.get("category"),
                    }
                ],
            }

        # Exact human-readable name.
        exact_name = [
            x for x in records
            if str(x.get("name", "")).strip().casefold()
            == query
        ]

        if exact_name:
            return {
                "query": component,
                "match_type": "exact_name",
                "matches": [
                    {
                        "identity": x.get("identity"),
                        "name": x.get("name"),
                        "category": x.get("category"),
                    }
                    for x in exact_name
                ],
            }

        # Curated aliases may intentionally map to a component group.
        if query in aliases:
            wanted = aliases[query]

            matches = [
                x for identity in wanted
                for x in records
                if x.get("identity") == identity
            ]

            return {
                "query": component,
                "match_type": "alias",
                "matches": [
                    {
                        "identity": x.get("identity"),
                        "name": x.get("name"),
                        "category": x.get("category"),
                    }
                    for x in matches
                ],
            }

        # Conservative fallback: identity/name substring only.
        fuzzy = [
            x for x in records
            if (
                query in str(
                    x.get("identity", "")
                ).casefold()
                or
                query in str(
                    x.get("name", "")
                ).casefold()
            )
        ]

        return {
            "query": component,
            "match_type": (
                "substring"
                if fuzzy
                else "none"
            ),
            "matches": [
                {
                    "identity": x.get("identity"),
                    "name": x.get("name"),
                    "category": x.get("category"),
                }
                for x in fuzzy
            ],
        }

    if action == "validate_remediation_plan":
        return {
            "error": "use_structured_plan_validator",
            "message": (
                "This Registry action exists only to expose "
                "validator availability. Validation requires "
                "a remediation plan object through the future "
                "structured bridge."
            ),
            "execution_authority": "NONE",
        }


    if action == "approval_contract":
        return {
            "schema": APPROVAL_CONTRACT["schema"],
            "current_execution_authority":
                APPROVAL_CONTRACT[
                    "current_execution_authority"
                ],
            "approval_does_not_execute":
                APPROVAL_CONTRACT[
                    "approval_does_not_execute"
                ],
            "required_plan_confidence":
                APPROVAL_CONTRACT[
                    "required_plan_confidence"
                ],
            "required_fields": list(
                APPROVAL_CONTRACT["required_fields"]
            ),
            "approval_states": list(
                APPROVAL_CONTRACT["approval_states"]
            ),
            "rules": list(
                APPROVAL_CONTRACT["rules"]
            ),
        }


    if action == "remediation_contract":
        return {
            "schema": REMEDIATION_CONTRACT["schema"],
            "execution_mode": REMEDIATION_CONTRACT[
                "execution_mode"
            ],
            "authorization": REMEDIATION_CONTRACT[
                "authorization"
            ],
            "required_fields": list(
                REMEDIATION_CONTRACT[
                    "required_fields"
                ]
            ),
            "allowed_confidence": list(
                REMEDIATION_CONTRACT[
                    "allowed_confidence"
                ]
            ),
        }


    if action == "remediation_context":
        if not component:
            return {
                "error": "component_required",
                "action": action,
                "execution_mode": "PLAN_ONLY",
            }

        query = component.strip().casefold()

        # Reuse the Registry's existing resolver semantics by
        # collecting exact/friendly matches without changing
        # machine state.
        aliases = {
            "otzar": [
                "otzar-gpt",
                "otzar-physical-source",
                "winboat",
                "usbclone-kingston-service",
            ],
            "אוצר": [
                "otzar-gpt",
                "otzar-physical-source",
                "winboat",
                "usbclone-kingston-service",
            ],
            "אוצר החכמה": [
                "otzar-gpt",
                "otzar-physical-source",
                "winboat",
                "usbclone-kingston-service",
            ],
            "winboat": [
                "winboat",
            ],
            "docker": [
                "docker",
            ],
            "anythingllm": [
                "anythingllm",
            ],
        }

        identities = aliases.get(query)

        if identities is None:
            identities = []

            for x in records:
                identity = str(
                    x.get("identity", "")
                ).casefold()

                name = str(
                    x.get("name", "")
                ).casefold()

                if query in {identity, name}:
                    identities.append(
                        x.get("identity")
                    )

        selected = [
            x for x in records
            if x.get("identity") in identities
        ]

        if not selected:
            return {
                "status": "not_found",
                "query": component,
                "execution_mode": "PLAN_ONLY",
                "authorization": "NONE",
            }

        return {
            "status": "ok",
            "query": component,
            "execution_mode": "PLAN_ONLY",
            "authorization": "NONE",
            "components": [
                {
                    "identity": x.get("identity"),
                    "name": x.get("name"),
                    "category": x.get("category"),
                    "purpose": x.get("purpose"),
                    "lifecycle_owner": x.get(
                        "lifecycle_owner",
                        "unknown",
                    ),
                    "lifecycle_mode": x.get(
                        "lifecycle_mode",
                        "unknown",
                    ),
                    "expected_state": x.get(
                        "expected_state"
                    ),
                    "dependencies": x.get(
                        "dependencies",
                        [],
                    ),
                    "dependents": x.get(
                        "dependents",
                        [],
                    ),
                    "conflicts": x.get(
                        "conflicts",
                        [],
                    ),
                    "invariants": x.get(
                        "invariants",
                        [],
                    ),
                    "mutation_risk": x.get(
                        "mutation_risk",
                        "unknown",
                    ),
                    "rollback_required": x.get(
                        "rollback_required",
                        "unknown",
                    ),
                    "evidence_status": x.get(
                        "evidence_status",
                        "unknown",
                    ),
                    "confidence": x.get(
                        "confidence",
                        "unknown",
                    ),
                    "live_checks": x.get(
                        "live_checks",
                        [],
                    ),
                }
                for x in selected
            ],
        }


    if action == "diagnostic_plan":
        if not component:
            return {
                "error": "component_required",
                "action": action,
            }

        query = component.strip().casefold()

        aliases = {
            "otzar": [
                "otzar-gpt",
                "otzar-physical-source",
                "winboat",
                "usbclone-kingston-service",
            ],
            "אוצר": [
                "otzar-gpt",
                "otzar-physical-source",
                "winboat",
                "usbclone-kingston-service",
            ],
            "אוצר החכמה": [
                "otzar-gpt",
                "otzar-physical-source",
                "winboat",
                "usbclone-kingston-service",
            ],
            "winboat": ["winboat"],
            "docker": ["docker"],
            "anythingllm": ["anythingllm"],
            "anything llm": ["anythingllm"],
            "data": ["data-mount"],
            "/mnt/data": ["data-mount"],
            "usb clone": [
                "usbclone-dummy-hcd-service",
                "usbclone-kingston-service",
            ],
            "usbclone": [
                "usbclone-dummy-hcd-service",
                "usbclone-kingston-service",
            ],
            "kingston": [
                "usbclone-kingston-service",
            ],
            "host bridge": [
                "aag-host-bridge",
            ],
            "ubuntu agent": [
                "aag-ubuntu-agent",
            ],
            "aag ubuntu agent": [
                "aag-ubuntu-agent",
            ],
        }

        by_identity = {
            str(x.get("identity", "")).casefold(): x
            for x in records
        }

        by_name = {
            str(x.get("name", "")).strip().casefold(): x
            for x in records
        }

        if query in by_identity:
            roots = [by_identity[query]["identity"]]

        elif query in by_name:
            roots = [by_name[query]["identity"]]

        else:
            roots = aliases.get(query, [])

        if not roots:
            return {
                "error": "component_not_found",
                "component": component,
                "available_components": [
                    x.get("identity")
                    for x in records
                ],
            }

        registry = {
            x.get("identity"): x
            for x in records
        }

        ordered = []
        seen = set()

        def add(identity, depth=0):
            if identity in seen:
                return

            record = registry.get(identity)

            if record is None:
                return

            seen.add(identity)

            ordered.append({
                "identity": identity,
                "depth": depth,
                "category": record.get("category"),
                "lifecycle_owner": record.get(
                    "lifecycle_owner"
                ),
                "lifecycle_mode": record.get(
                    "lifecycle_mode"
                ),
                "dependencies": record.get(
                    "dependencies",
                    [],
                ),
                "live_checks": record.get(
                    "live_checks",
                    [],
                ),
                "mutation_risk": record.get(
                    "mutation_risk"
                ),
                "invariants": record.get(
                    "invariants",
                    [],
                ),
                "confidence": record.get(
                    "confidence"
                ),
            })

            # One dependency level is enough for initial
            # diagnostic planning. Further traversal should
            # happen only when live evidence requires it.
            if depth == 0:
                for dep in record.get(
                    "dependencies",
                    [],
                ):
                    add(dep, depth + 1)

        for identity in roots:
            add(identity, 0)

        live_checks = []

        for item in ordered:
            for check in item.get(
                "live_checks",
                [],
            ):
                if check not in live_checks:
                    live_checks.append(check)

        audit_profiles = []

        for check in live_checks:
            if not isinstance(check, str):
                continue

            if not check.startswith(
                "live_audit:"
            ):
                continue

            profile = check.split(
                ":",
                1,
            )[1]

            if profile not in audit_profiles:
                audit_profiles.append(profile)

        # Select exactly one preferred initial live audit.
        #
        # The other profiles remain escalation candidates only.
        # This prevents the planner from encouraging broad automatic
        # execution of every related diagnostic.
        preferred_profiles = {
            "otzar": "otzar",
            "אוצר": "otzar",
            "אוצר החכמה": "otzar",
            "winboat": "docker",
            "docker": "docker",
            "anythingllm": "docker",
            "anything llm": "docker",
            "data": "storage",
            "/mnt/data": "storage",
            "usb clone": "services",
            "usbclone": "services",
            "kingston": "services",
            "host bridge": "services",
            "ubuntu agent": "overview",
            "aag ubuntu agent": "overview",
        }

        initial_profile = preferred_profiles.get(query)

        # Canonical identities may bypass friendly aliases.
        if initial_profile is None:
            if query.startswith("otzar-"):
                initial_profile = "otzar"
            elif query == "winboat":
                initial_profile = "docker"
            elif query == "docker":
                initial_profile = "docker"
            elif query == "data-mount":
                initial_profile = "storage"
            elif query.startswith("usbclone-"):
                initial_profile = "services"

        # Never invent a profile not actually supported by the plan.
        if initial_profile not in audit_profiles:
            initial_profile = (
                audit_profiles[0]
                if audit_profiles
                else None
            )

        escalation_profiles = [
            x for x in audit_profiles
            if x != initial_profile
        ]

        return {
            "query": component,
            "roots": roots,
            "components": ordered,
            "recommended_live_checks": live_checks,
            "initial_audit_profile": initial_profile,
            "escalation_audit_profiles": escalation_profiles,
            "recommended_audit_profiles": audit_profiles,
            "planning_rule": (
                "Run initial_audit_profile first. "
                "Do NOT automatically execute escalation profiles. "
                "Use an escalation profile only when evidence from "
                "the current diagnostic identifies a concrete reason "
                "to inspect that additional subsystem."
            ),
        }

    if not component:
        return {
            "error": "component_required",
            "action": action,
        }

    record = next(
        (
            x for x in records
            if x.get("identity") == component
        ),
        None,
    )

    if record is None:
        return {
            "error": "component_not_found",
            "component": component,
            "available_components": [
                x.get("identity")
                for x in records
            ],
        }

    if action == "get":
        return {
            "component": record,
        }

    if action == "owner":
        return {
            "identity": record.get("identity"),
            "lifecycle_owner": record.get(
                "lifecycle_owner"
            ),
            "lifecycle_mode": record.get(
                "lifecycle_mode"
            ),
            "evidence_status": record.get(
                "evidence_status"
            ),
            "confidence": record.get("confidence"),
        }

    if action == "dependencies":
        return {
            "identity": record.get("identity"),
            "dependencies": record.get(
                "dependencies",
                [],
            ),
        }

    if action == "dependents":
        return {
            "identity": record.get("identity"),
            "dependents": record.get(
                "dependents",
                [],
            ),
        }

    if action == "invariants":
        return {
            "identity": record.get("identity"),
            "invariants": record.get(
                "invariants",
                [],
            ),
        }

    if action == "risk":
        return {
            "identity": record.get("identity"),
            "mutation_risk": record.get(
                "mutation_risk"
            ),
            "rollback_required": record.get(
                "rollback_required"
            ),
            "conflicts": record.get(
                "conflicts",
                [],
            ),
            "invariants": record.get(
                "invariants",
                [],
            ),
        }

    return {
        "error": "unreachable_registry_state"
    }


TOOLS = [
    {
        "type": "function",
        "name": "diagnose",
        "description": (
            "Run one bounded, trusted, READ-ONLY Ubuntu diagnostic profile. "
            "Prefer this for natural-language troubleshooting, then use "
            "structured_observation only for a focused follow-up. Facts are "
            "kept distinct from inference; unknown is not failure."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "enum": ["general_system", "performance", "service", "application_start", "network", "storage_mount", "docker", "package", "boot_health"]
                },
                "inputs": {
                    "type": "object",
                    "properties": {
                        "service": {"type": ["string", "null"]},
                        "manager": {"type": ["string", "null"], "enum": ["system", "user", None]},
                        "pid": {"type": ["integer", "null"]},
                        "interface": {"type": ["string", "null"]},
                        "path": {"type": ["string", "null"]},
                        "container": {"type": ["string", "null"]},
                        "package": {"type": ["string", "null"]}
                    },
                    "additionalProperties": False
                },
                "secondary_profile": {
                    "type": ["string", "null"],
                    "enum": ["general_system", "performance", "service", "application_start", "network", "storage_mount", "docker", "package", "boot_health", None]
                },
                "secondary_inputs": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "properties": {
                        "service": {"type": ["string", "null"]},
                        "manager": {"type": ["string", "null"], "enum": ["system", "user", None]},
                        "pid": {"type": ["integer", "null"]},
                        "interface": {"type": ["string", "null"]},
                        "path": {"type": ["string", "null"]},
                        "container": {"type": ["string", "null"]},
                        "package": {"type": ["string", "null"]}
                    }
                }
            },
            "required": ["profile", "inputs", "secondary_profile", "secondary_inputs"],
            "additionalProperties": False
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "bridge_health",
        "description": (
            "Perform the one fixed READ-ONLY health/readiness check "
            "for the AAG host bridge. Use when the Registry points "
            "to bridge:/health or the host-bridge Unix socket. "
            "The tool accepts no target, path or command and cannot mutate."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "strict": True,
    },

    {
        "type": "function",
        "name": "query_component_registry",
        "description": (
            "Read the structured AAG Component Registry. "
            "Use this to understand component identity, lifecycle "
            "ownership, dependencies, dependents, invariants and "
            "mutation risk. Registry data is structured knowledge, "
            "not proof of current live machine state."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list",
                        "resolve",
                        "diagnostic_plan",
                        "remediation_context",
                        "remediation_contract",
                        "approval_contract",
                        "validate_remediation_plan",
                        "get",
                        "owner",
                        "dependencies",
                        "dependents",
                        "invariants",
                        "risk"
                    ],
                    "description": (
                        "Type of structured registry lookup. "
                        "Use resolve when the user names a product, "
                        "system or friendly component name and the "
                        "canonical registry identity is not yet known. "
                        "Use diagnostic_plan for component-specific "
                        "troubleshooting to obtain relevant components, "
                        "dependencies and available live checks before "
                        "choosing the smallest diagnostic. "
                        "Use remediation_context only when planning a "
                        "possible repair, to obtain ownership, conflicts, "
                        "invariants, mutation risk, rollback requirements "
                        "and other safety context. remediation_context is "
                        "PLAN_ONLY and does not authorize execution. "
                        "Use remediation_contract when a structured "
                        "machine-readable repair-plan schema is needed. "
                        "Use approval_contract to inspect the rules that "
                        "must be satisfied before explicit approval could "
                        "make a future remediation plan execution-eligible. "
                        "Approval itself never performs execution."
                    ),
                },
                "component": {
                    "type": [
                        "string",
                        "null"
                    ],
                    "description": (
                        "Component identity. Use null for list."
                    ),
                },
            },
            "required": [
                "action",
                "component"
            ],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "search_historical_knowledge",
        "description": (
            "Search the AAG Ubuntu Agent AnythingLLM workspace. "
            "Use this for architecture, prior incidents, known-good "
            "states, paths, services, past solutions and historical "
            "context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Focused knowledge question to retrieve from "
                        "the master handoff and solved-problems KB."
                    ),
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "structured_observation",
        "description": (
            "Run one typed, bounded, read-only Ubuntu observation. "
            "Binaries and argv families are fixed by trusted code; input is "
            "validated and cannot supply shell or commands."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "enum": ["systemd", "journal", "process", "network", "mount", "filesystem", "docker", "package", "kernel"],
                },
                "service": {"type": ["string", "null"]},
                "manager": {"type": ["string", "null"], "enum": ["system", "user", None]},
                "lines": {"type": ["integer", "null"]},
                "pid": {"type": ["integer", "null"]},
                "interface": {"type": ["string", "null"]},
                "path": {"type": ["string", "null"]},
                "container": {"type": ["string", "null"]},
                "package": {"type": ["string", "null"]}
            },
            "required": ["domain", "service", "manager", "lines", "pid", "interface", "path", "container", "package"],
            "additionalProperties": False
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "controlled_read_action",
        "description": (
            "Run one narrowly scoped allowlisted read-only host action. "
            "Currently supports only checking the active state of "
            "explicitly allowlisted services. "
            "No arbitrary shell, sudo or mutation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "check_service_status"
                    ],
                },
                "target": {
                    "type": "string",
                    "enum": [
                        "docker.service",
                        "aag-ubuntu-agent-bridge.service"
                    ],
                },
            },
            "required": [
                "action",
                "target"
            ],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "prepare_controlled_mutation",
        "description": (
            "Prepare, but never approve or execute, an accepted "
            "domain remediation contract. "
            "Use only after evidence indicates the AAG host bridge "
            "endpoint is unhealthy. If the bridge is healthy, no "
            "mutation request will be created. Explicit approval "
            "must come directly from the user's local CLI command."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "contract_id": {
                    "type": "string",
                    "enum": [
                        "bridge.readiness_failure"
                    ],
                },
            },
            "required": [
                "contract_id"
            ],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "windows_read_action",
        "description": (
            "Read current live state from the Windows guest inside "
            "WinBoat through the existing loopback-only Guest Server. "
            "The interface is strictly allowlisted and read-only. "
            "Use overview for Windows health/resources/RDP, metrics "
            "for CPU/RAM/C: usage, rdp_status for RDP state, apps "
            "for the application catalog, health for Guest Server "
            "health, and version for Guest Server version. "
            "There is no arbitrary path, network destination, "
            "HTTP method or Windows command."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "overview",
                        "health",
                        "version",
                        "metrics",
                        "rdp_status",
                        "apps",
                        "otzar_status",
                        "otzar_processes",
                        "otzar_drive",
                        "otzar_usb"
                    ],
                },
            },
            "required": [
                "action"
            ],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_live_audit",
        "description": (
            "Run one predefined READ-ONLY diagnostic profile on "
            "the real Ubuntu computer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "enum": sorted(ALLOWED_PROFILES),
                    "description": (
                        "overview, storage, services, docker, "
                        "network, or otzar"
                    ),
                }
            },
            "required": ["profile"],
            "additionalProperties": False,
        },
        "strict": True,
    },

    {
        "type": "function",
        "name": "validate_remediation_plan",
        "description": (
            "Pure structured remediation-plan validation. "
            "Validates one complete plan against the existing "
            "REMEDIATION_CONTRACT and approval-eligibility rules. "
            "This tool does not approve, prepare mutation, execute, "
            "inspect live state or modify anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "plan": {
                    "type": "object",
                    "description": (
                        "Complete remediation plan conforming to "
                        "aag-remediation-plan-v1."
                    ),
                    "additionalProperties": True
                }
            },
            "required": [
                "plan"
            ],
            "additionalProperties": False
        },
        "strict": False
    },

    {
        "type": "function",
        "name": "diagnosis_workspace",
        "description": (
            "Pure Stage 8A diagnosis/planning workspace. "
            "Creates a structured incident, exposes the diagnosis "
            "contract, or evaluates evidence already gathered by "
            "other read-only tools. It performs no live command, "
            "no mutation and grants no execution authority."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "contract",
                        "new_incident",
                        "evaluate"
                    ],
                    "description":
                        "Diagnosis workspace operation."
                },
                "incident": {
                    "type": "string",
                    "description":
                        "User-reported incident or symptom."
                },
                "evidence": {
                    "type": "object",
                    "description": (
                        "Evidence already returned by existing "
                        "Registry, live-audit, Windows or historical "
                        "tools. Expected keys may include registry, "
                        "live_audit, windows and historical."
                    ),
                    "additionalProperties": True
                }
            },
            "required": [
                "action"
            ],
            "additionalProperties": False
        }
    },

]


def _maintenance_tool(name, description, properties, required):
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "strict": True,
    }


_MAINTENANCE_PATH = {
    "path": {
        "type": "string",
        "description": "Absolute path inside an allowlisted maintenance scope. Traversal and symlink escapes are rejected.",
    },
    "profile": {
        "type": "string",
        "enum": ["quick", "standard", "deep"],
        "description": "Bounded scan profile. Deep is explicit and still read-only.",
    },
}


TOOLS.extend([
    _maintenance_tool("system_health", "Build an actionable read-only system-health overview with honest coverage.", {}, []),
    _maintenance_tool("performance_snapshot", "Take a bounded performance snapshot and infer a bottleneck only from correlated evidence.", {}, []),
    _maintenance_tool("storage_overview", "Inspect devices, filesystems, mounts, capacity, inodes, read-only state, and expected mounts.", {}, []),
    _maintenance_tool("storage_top", "Return bounded top directory contributors for one path; no content is read.", dict(_MAINTENANCE_PATH), ["path", "profile"]),
    _maintenance_tool("storage_inspect", "Inspect one path with bounded logical/allocated totals and coverage errors.", dict(_MAINTENANCE_PATH), ["path", "profile"]),
    _maintenance_tool("storage_largest_files", "List bounded largest-file metadata without reading file content.", dict(_MAINTENANCE_PATH), ["path", "profile"]),
    _maintenance_tool("storage_snapshot", "Persist one bounded summary snapshot under project memory for later comparison.", dict(_MAINTENANCE_PATH), ["path", "profile"]),
    _maintenance_tool("storage_growth", "Compare the latest compatible summary snapshots; unrelated mounts or policies are never compared.", {"path": _MAINTENANCE_PATH["path"]}, ["path"]),
    _maintenance_tool("storage_duplicate_candidates", "Run explicit deep, policy-bounded duplicate candidate fingerprinting; protected content is not hashed.", {"path": _MAINTENANCE_PATH["path"], "profile": {"type": "string", "enum": ["deep"]}}, ["path", "profile"]),
    _maintenance_tool("storage_duplicate_verify", "Run explicit deep full SHA-256 verification within byte and policy budgets; no cleanup follows.", {"path": _MAINTENANCE_PATH["path"], "profile": {"type": "string", "enum": ["deep"]}}, ["path", "profile"]),
    _maintenance_tool("storage_space_discrepancy", "Check bounded df/scan, sparse, hardlink, nested-mount, and incomplete-coverage explanations.", {"path": _MAINTENANCE_PATH["path"], "profile": {"type": "string", "enum": ["deep"]}}, ["path", "profile"]),
    _maintenance_tool("maintenance_plan", "Prepare a conservative dry-run maintenance plan. Execution authority is always NONE.", dict(_MAINTENANCE_PATH), ["path", "profile"]),
    _maintenance_tool("maintenance_explain", "Explain one stable dry-run plan item with evidence, confidence, risk, and why it was not executed.", {"path": _MAINTENANCE_PATH["path"], "item_id": {"type": "string"}}, ["path", "item_id"]),
])


TOOL_IMPL = {

    "diagnose":
        lambda args: diagnose_many([
            {"profile": args["profile"], "inputs": {key: value for key, value in args.get("inputs", {}).items() if value is not None}},
            *([{"profile": args["secondary_profile"], "inputs": {key: value for key, value in (args.get("secondary_inputs") or {}).items() if value is not None}}] if args.get("secondary_profile") else []),
        ]),

    "bridge_health":
        lambda args: bridge_health_observation(),

    "structured_observation":
        lambda args: structured_observation(
            args["domain"],
            {key: value for key, value in args.items() if key != "domain" and value is not None},
        ),

    "validate_remediation_plan":
        lambda args: validate_remediation_plan_tool(
            args["plan"],
        ),

    "diagnosis_workspace":
        lambda args: diagnosis_workspace(
            args["action"],
            args.get("incident"),
            args.get("evidence"),
        ),


    "query_component_registry":
        lambda args: component_registry(
            args["action"],
            args.get("component"),
        ),

    "search_historical_knowledge":
        lambda args: anythingllm_knowledge(args["query"]),

    "controlled_read_action":
        lambda args: execute_read_only_controlled_action(
            args["action"],
            args["target"],
        ),

    "prepare_controlled_mutation":
        lambda args: prepare_contract_remediation(
            args["contract_id"],
        ),

    "windows_read_action":
        lambda args: windows_read_action(
            args["action"],
        ),

    "run_live_audit":
        lambda args: live_audit(args["profile"]),
}


TOOL_IMPL.update({
    "system_health": lambda args: dispatch_maintenance("system.health", {}),
    "performance_snapshot": lambda args: dispatch_maintenance("performance.snapshot", {}),
    "storage_overview": lambda args: dispatch_maintenance("storage.overview", {}),
    "storage_top": lambda args: dispatch_maintenance("storage.top", args),
    "storage_inspect": lambda args: dispatch_maintenance("storage.inspect", args),
    "storage_largest_files": lambda args: dispatch_maintenance("storage.largest_files", args),
    "storage_snapshot": lambda args: dispatch_maintenance("storage.snapshot", args),
    "storage_growth": lambda args: dispatch_maintenance("storage.growth", args),
    "storage_duplicate_candidates": lambda args: dispatch_maintenance("storage.duplicate_candidates", args),
    "storage_duplicate_verify": lambda args: dispatch_maintenance("storage.duplicate_verify", args),
    "storage_space_discrepancy": lambda args: dispatch_maintenance("storage.space_discrepancy", args),
    "maintenance_plan": lambda args: dispatch_maintenance("maintenance.plan", args),
    "maintenance_explain": lambda args: dispatch_maintenance("maintenance.explain", args),
})


def main():
    if not Path("/mnt/data").is_mount():
        raise SystemExit(
            "ERROR: /mnt/data is not mounted"
        )

    for p in (
        CONFIG_FILE,
        OPENAI_SECRET,
        ANY_SECRET,
        LIVE_TOOL,
        REGISTRY_FILE,
    ):
        if not p.exists():
            raise SystemExit(f"ERROR: missing {p}")

    cfg = load_config()

    openai_api_key = load_text(OPENAI_SECRET)

    if not openai_api_key:
        raise SystemExit("ERROR: OpenAI API key is empty")

    client = OpenAI(api_key=openai_api_key)

    print("=" * 60)
    print(" AAG UBUNTU AGENT")
    print(" KNOWLEDGE + LIVE MACHINE")
    print(" MODE: READ-ONLY")
    print(" MODEL:", cfg["openai_model"])
    print("=" * 60)
    print()
    print("דוגמאות:")
    print("  תבדוק אם משהו חריג במחשב")
    print("  למה אוצר החכמה לא עובד?")
    print("  בדוק את Docker מול המצב המתועד")
    print()
    print("יציאה: /exit")
    print()

    conversation = []

    while True:
        try:
            prompt = input("AAG> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not prompt:
            continue

        if prompt.lower() in {
            "/exit",
            "exit",
            "quit",
        }:
            break

        local_result = handle_local_mutation_command(
            prompt
        )

        if local_result is not None:
            print()
            print(
                "========== CONTROLLED ACTION =========="
            )

            message = local_result.get(
                "message"
            )

            if message:
                print(message)

            print(
                json.dumps(
                    local_result,
                    ensure_ascii=False,
                    indent=2,
                )
            )

            print(
                "======================================="
            )
            print()

            # IMPORTANT:
            # /approve, /cancel and /pending are consumed locally.
            # They are NEVER sent to the model.
            continue

        conversation.append({
            "role": "user",
            "content": prompt,
        })

        for round_no in range(
            1,
            MAX_ROUNDS + 1,
        ):
            print(
                f"\n[AI round "
                f"{round_no}/{MAX_ROUNDS}]"
            )

            response = client.responses.create(
                model=cfg["openai_model"],
                instructions=SYSTEM_PROMPT,
                tools=TOOLS,
                tool_choice="auto",
                input=conversation,
            )

            conversation += response.output

            calls = [
                x for x in response.output
                if getattr(
                    x,
                    "type",
                    None,
                ) == "function_call"
            ]

            if not calls:
                print()
                print("============== ANSWER ==============")
                print(response.output_text)
                print("====================================")
                print()
                break

            for call in calls:
                try:
                    args = json.loads(
                        call.arguments or "{}"
                    )
                except Exception:
                    args = {}

                print(
                    f"[TOOL] {call.name} "
                    f"{json.dumps(args, ensure_ascii=False)}"
                )

                fn = TOOL_IMPL.get(call.name)

                if fn is None:
                    result = {
                        "error":
                            "Unknown tool"
                    }
                else:
                    result = fn(args)

                serialized = json.dumps(
                    result,
                    ensure_ascii=False,
                )

                if len(serialized) > 35000:
                    serialized = (
                        serialized[:35000]
                        + "\n[TRUNCATED]"
                    )

                conversation.append({
                    "type":
                        "function_call_output",
                    "call_id":
                        call.call_id,
                    "output":
                        serialized,
                })

        else:
            print(
                "\nSTOP: maximum reasoning "
                "rounds reached.\n"
            )


if __name__ == "__main__":
    main()
