#!/usr/bin/env bash
# Smoke test: syntax-check every shipped script, validate hooks.json wiring,
# and confirm a context-injection hook emits valid JSON. No dependence on
# ~/.claude or a live session — safe to run in CI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "1. python syntax"
python3 -m py_compile "$ROOT"/hooks/*.py "$ROOT"/skills/recover-from-false-positive/scripts/*.py

echo "2. bash syntax"
for s in "$ROOT"/hooks/*.sh; do bash -n "$s"; done

echo "3. hooks.json valid + every command resolves via \${CLAUDE_PLUGIN_ROOT}"
python3 - "$ROOT/hooks/hooks.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert "hooks" in d, "missing top-level hooks key"
for event, groups in d["hooks"].items():
    for g in groups:
        for h in g["hooks"]:
            assert "${CLAUDE_PLUGIN_ROOT}" in h["command"], (event, h)
print("   hooks.json ok")
PY

echo "4. subagent-framing hook emits valid JSON"
"$ROOT"/hooks/inject-subagent-framing.sh | python3 -c 'import json,sys; json.load(sys.stdin)'

echo "smoke OK"
