# Ubuntu Agent Public Boundary

The historical Ubuntu Agent capture is not part of any install profile. It includes useful portable Python modules and tests, but the complete test/runtime contract also expects machine-specific diagnostic/remediation registries, local knowledge, environment dependencies, and host-service assumptions that are not safely reconstructable from the public package.

Classification:

- `PORTABLE_CODE`: `integrations/ubuntu-agent/aag_agent`, schemas under `contracts`, and generic tools.
- `PORTABLE_DEFAULT_CONFIG`: none asserted; a minimal non-secret environment example is provided at `config/ubuntu-agent.example.env`.
- `USER_SPECIFIC_CONFIG`: allow-listed diagnostic/remediation registries, service names, mount policy, knowledge sources, and API/provider selection.
- `PRIVATE_STATE`: SQLite state, observations, audit logs, memory, conversations, credentials, tokens, and host inventory; never packaged.
- `OPTIONAL_DEPENDENCY`: Python OpenAI client and any provider/runtime selected by a future standalone package.
- `NONPORTABLE_HOST_ASSUMPTION`: local paths, service topology, bridges, ports, mounts, and private operational policy.

`full` now installs only the five public-ready profiles and omits Ubuntu Agent. Doctor reports the omission as optional. This closes the suite blocker without claiming the historical capture is a generic product. A future Ubuntu Agent release needs its own owner license, portable config schemas/defaults, dependency lock, isolated tests, and threat-model review.

`UBUNTU_AGENT_OPTIONALIZED=PASS`
