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

echo "5. classifier-signature regression — every known wording is detected + scrubbed"
# Guards against the exact failure that shipped: a single hard-coded signature
# string going stale against a new classifier wording, so recovery silently
# reported "clean". Every entry in SIGNATURES (both modules) must be detected,
# removed, and its orphan re-stitched (delta must not increase).
python3 - "$ROOT" <<'PY'
import importlib.util, json, os, sys, tempfile
root = sys.argv[1]

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

scrub = load(os.path.join(root, "skills/recover-from-false-positive/scripts/scrub_refusals.py"), "scrub")
hook  = load(os.path.join(root, "hooks/detect-false-positive.py"), "dethook")

# Known wordings that MUST be caught (real strings observed in the wild).
CASES = {
    "legacy":  "API Error: This request appears to violate our Usage Policy. This request triggered cyber-related safeguards.",
    "cyber07": "API Error: Sonnet 5's safeguards flagged this message for a cybersecurity topic. If your work requires this access, you can apply for an exemption: https://claude.com/form/cyber-use-case?token=abc",
}
# Also assert each exact SIGNATURES entry in the scrub module is exercised.
for s in scrub.SIGNATURES:
    CASES.setdefault(f"sig::{s[:24]}", f"API Error: ... {s} ...")

def turn(uuid, parent, role, text, err=False):
    o = {"uuid": uuid, "parentUuid": parent,
         "type": "assistant" if role == "assistant" else role,
         "message": {"role": role, "content": text}}
    if err: o["isApiErrorMessage"] = True
    return json.dumps(o)

fails = []
for name, err_text in CASES.items():
    # 3-turn chain: user(U) -> refusal(R) -> child(C). Scrub must drop R and
    # re-point C's parent at U (delta stays 0), leaving a resumable log.
    lines = [
        turn("U", None, "user", "please continue the build work"),
        turn("R", "U", "assistant", err_text, err=True),
        turn("C", "R", "user", "next question"),
    ]
    # (a) both modules recognise the refusal turn
    refusal_obj = json.loads(lines[1])
    if not scrub.is_refusal(refusal_obj):
        fails.append(f"{name}: scrub.is_refusal MISSED"); continue
    if not hook._sig(hook._text(refusal_obj)):
        fails.append(f"{name}: hook._sig MISSED")
    # (b) scrub removes exactly the refusal, re-stitches, adds no breakage
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "s.jsonl")
        open(f, "w").write("\n".join(lines) + "\n")
        new_lines, removed, restitched, malformed, delta = scrub.scrub_file(f)
        if removed != 1:            fails.append(f"{name}: removed={removed} (want 1)")
        if delta > 0:               fails.append(f"{name}: delta={delta} (chain broken)")
        kept = [json.loads(l) for l in new_lines if l.strip()]
        child = next((o for o in kept if o["uuid"] == "C"), None)
        if child is None:           fails.append(f"{name}: child dropped")
        elif child["parentUuid"] != "U":
            fails.append(f"{name}: child not re-stitched (parent={child['parentUuid']})")

# Negative control: prose that merely QUOTES a phrase must NOT be scrubbed.
prose = json.loads(turn("P", "U", "assistant", "the docs mention 'cyber-related safeguards' as a false positive"))
if scrub.is_refusal(prose):
    fails.append("negative-control: prose quoting a phrase was flagged as a refusal")

if fails:
    print("   REGRESSION:"); [print("     -", x) for x in fails]; sys.exit(1)
print(f"   {len(CASES)} signatures detected + scrubbed; negative control held")
PY

echo "smoke OK"
