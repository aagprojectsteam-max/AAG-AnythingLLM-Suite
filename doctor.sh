#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
mode=${1:---preflight}
fail=0
for cmd in bash find sha256sum python3 node git; do
  command -v "$cmd" >/dev/null || { echo "MISSING_COMMAND=$cmd"; fail=1; }
done
python3 - <<'PY' "$ROOT/config/models.yaml" || fail=1
import pathlib, sys
p=pathlib.Path(sys.argv[1])
assert p.is_file() and "policy: detect-and-report-never-auto-download" in p.read_text()
print("MODEL_POLICY=PASS")
PY
find "$ROOT" -type f -name '*.json' -print0 | xargs -0 -r -n1 python3 -m json.tool >/dev/null || fail=1
find "$ROOT" -type f -name '*.py' -print0 | xargs -0 -r python3 -m py_compile || fail=1
find "$ROOT" -type f -name '*.js' -print0 | xargs -0 -r -n1 node --check || fail=1
"$ROOT/tools/sanitize.sh" "$ROOT" || fail=1
if [[ "$mode" == "--deployed" ]]; then
  : "${ANYTHINGLLM_ROOT:?Set ANYTHINGLLM_ROOT}"
  [[ -d "$ANYTHINGLLM_ROOT/server" ]] || { echo "ANYTHINGLLM_ROOT=INVALID"; fail=1; }
fi
(( fail == 0 )) || { echo "DOCTOR=FAIL"; exit 1; }
echo "DOCTOR=PASS"

