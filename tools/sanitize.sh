#!/usr/bin/env bash
set -euo pipefail
root=${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
bad_names=$(find "$root" -type f \( -name '.env' -o -name '*.db' -o -name '*.sqlite*' -o -name '*.pem' -o -name '*.key' -o -name '*.gguf' -o -name '*.safetensors' -o -name '*.ckpt' -o -name '*.pt' -o -name '*.pth' -o -name '*.onnx' -o -name '*.bin' -o -name '*.param' \) -print)
if [[ -n "$bad_names" ]]; then printf '%s\n' "FORBIDDEN_FILES" "$bad_names"; exit 1; fi
patterns='(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|Bearer[[:space:]]+[A-Za-z0-9._~-]{20,})'
if grep -RIE --binary-files=without-match --exclude-dir=.git --exclude='SANITIZATION-REPORT.md' --exclude='sanitize.sh' "$patterns" "$root"; then echo 'SECRET_PATTERN_SCAN=FAIL'; exit 1; fi
assignments="(api[_-]?key|access[_-]?token|client[_-]?secret|password)[[:space:]]*[:=][[:space:]]*[\"'][A-Za-z0-9_./+=-]{12,}"
if grep -RIE --binary-files=without-match --exclude-dir=.git --exclude='.env.example' --exclude='SANITIZATION-REPORT.md' --exclude='sanitize.sh' "$assignments" "$root"; then echo 'ASSIGNMENT_SCAN=FAIL'; exit 1; fi
echo 'SECRET_PATTERN_SCAN=PASS'
echo 'ASSIGNMENT_SCAN=PASS'
