# AnythingLLM AAG Diagnostic Skill v2

This is the canonical staged deployment source for the existing AnythingLLM
skill identity `aag-ubuntu-live-audit`.

Deployment target (requires explicit authorization):

```text
/mnt/data/AI/Apps/AnythingLLM/storage/plugins/agent-skills/aag-ubuntu-live-audit/
```

The handler does not contain a Bridge socket constant. It reads the endpoint
contract published by the canonical Host Bridge at startup. It performs only a
bounded POST to `/diagnose`, accepts only trusted profiles/typed fields, and
contains no command or mutation capability.

The corresponding approved deployment changes the exact user unit `ExecStart`
to `app/host_bridge_v2.py`; frozen `app/host_bridge.py` is not modified.
