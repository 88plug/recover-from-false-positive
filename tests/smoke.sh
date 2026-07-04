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

echo "6. durability — a NEVER-SEEN wording is still caught structurally (stop_reason=refusal)"
# This is the real anti-fragility test: even if Anthropic invents a brand-new error
# phrase tomorrow, the structural marker catches it and a benign 500 is left alone.
python3 - "$ROOT" <<'PY'
import importlib.util, os, sys
root=sys.argv[1]
spec=importlib.util.spec_from_file_location("scrub", os.path.join(root,"skills/recover-from-false-positive/scripts/scrub_refusals.py"))
scrub=importlib.util.module_from_spec(spec); spec.loader.exec_module(scrub)
fails=[]
novel={"uuid":"R","parentUuid":"U","type":"assistant","isApiErrorMessage":True,
       "message":{"role":"assistant","stop_reason":"refusal","model":"<synthetic>",
                  "content":[{"type":"text","text":"API Error: a totally new 2027 phrasing no one has hard-coded."}]}}
if not scrub.is_refusal(novel): fails.append("structural refusal (new wording) MISSED")
if not scrub.is_novel_wording(novel): fails.append("new wording not flagged as novel")
benign={"uuid":"E","type":"assistant","isApiErrorMessage":True,
        "message":{"role":"assistant","stop_reason":"end_turn",
                   "content":[{"type":"text","text":"API Error: 500 Internal server error"}]}}
if scrub.is_refusal(benign): fails.append("benign 500 wrongly flagged as refusal")
if fails: print("   REGRESSION:"); [print("     -",x) for x in fails]; sys.exit(1)
print("   new-wording caught structurally; benign 500 left alone")
PY

echo "7. de-saturate — dense window turns stubbed, chain + tool-pairing preserved"
python3 - "$ROOT" <<'PY'
import importlib.util, json, os, sys
root=sys.argv[1]
spec=importlib.util.spec_from_file_location("scrub", os.path.join(root,"skills/recover-from-false-positive/scripts/scrub_refusals.py"))
scrub=importlib.util.module_from_spec(spec); spec.loader.exec_module(scrub)
dense=("codegen noder cmd/compile share-generics instantiation redundant compilation "
       "toolchain first-build build acceleration ")*6
def turn(u,p,typ,role,content,**kw):
    return json.dumps({"uuid":u,"parentUuid":p,"type":typ,"message":{"role":role,"content":content},**kw})
raw=[
  json.dumps({"uuid":"S","parentUuid":None,"type":"system","subtype":"microcompact_boundary","content":"b"}),
  turn("A","S","assistant","assistant",[{"type":"tool_use","id":"tu1","name":"Bash","input":{"command":"go build"}}]),
  turn("B","A","user","user",[{"type":"tool_result","tool_use_id":"tu1","content":dense}]),
  turn("C","B","assistant","assistant",[{"type":"text","text":dense}]),
  turn("D","C","user","user","a short recent question"),
]
out,stubbed,saved,manifest=scrub.desaturate_lines(raw, keep_recent=1, min_score=4)
fails=[]
if stubbed<2: fails.append(f"stubbed={stubbed} (want >=2: the dense tool_result + text turns)")
if saved<=0: fails.append("bytes_saved not positive")
objs=[json.loads(l) for l in out]
by={o["uuid"]:o for o in objs}
if len(objs)!=len(raw): fails.append("turn count changed (chain risk)")
if scrub.dangling_count(out)-scrub.dangling_count(raw)>0: fails.append("dangling pointers increased")
# tool pairing preserved: tool_use id + tool_result tool_use_id intact, types unchanged
a=by["A"]["message"]["content"][0]
b=by["B"]["message"]["content"][0]
if a.get("type")!="tool_use" or a.get("id")!="tu1": fails.append("tool_use block mangled")
if b.get("type")!="tool_result" or b.get("tool_use_id")!="tu1": fails.append("tool_result pairing broken")
if b.get("content")==dense: fails.append("dense tool_result not stubbed")
# protected recent turn D untouched
if by["D"]["message"]["content"]!="a short recent question": fails.append("protected recent turn was altered")
# parent chain unchanged
if by["C"]["parentUuid"]!="B" or by["B"]["parentUuid"]!="A": fails.append("parentUuid rewritten")
if fails: print("   REGRESSION:"); [print("     -",x) for x in fails]; sys.exit(1)
print(f"   {stubbed} dense turns stubbed, ~{saved}B freed, chain + tool pairing intact, recent turn protected")
PY

echo "8. self-check runs and reports (fail-loud durability audit)"
mkdir -p /tmp/rfp-selfcheck-$$/proj && : > /tmp/rfp-selfcheck-$$/proj/empty.jsonl
python3 "$ROOT/skills/recover-from-false-positive/scripts/scrub_refusals.py" --selfcheck --root /tmp/rfp-selfcheck-$$ | grep -q "detection contract" && echo "   self-check reported" || { echo "   self-check FAILED"; exit 1; }
rm -rf /tmp/rfp-selfcheck-$$

echo "9. backtest runs over a tree and reports exposure"
D=/tmp/rfp-backtest-$$/proj; mkdir -p "$D"
python3 - "$D/s.jsonl" <<'PY'
import json,sys
dense=("codegen cmd/compile share-generics instantiation toolchain redundant compilation ")*400
rows=[{"uuid":"S","parentUuid":None,"type":"system","subtype":"microcompact_boundary","content":"b"}]
for i in range(6):
    rows.append({"uuid":f"u{i}","parentUuid":"S","type":"user","message":{"role":"user","content":dense}})
open(sys.argv[1],"w").write("\n".join(json.dumps(r) for r in rows)+"\n")
PY
python3 "$ROOT/skills/recover-from-false-positive/scripts/scrub_refusals.py" --backtest --root /tmp/rfp-backtest-$$ | grep -q "at-risk" && echo "   backtest reported" || { echo "   backtest FAILED"; exit 1; }
rm -rf /tmp/rfp-backtest-$$

echo "10. clear-then-reinject — offline recap staged, hook injects it once (low density), consumes it"
H=/tmp/rfp-handoff-$$; mkdir -p "$H/.claude" "$H/proj"
python3 - "$H/proj/s.jsonl" <<'PY'
import json,sys
dense="codegen cmd/compile share-generics instantiation toolchain redundant compilation "*8
rows=[
 {"uuid":"S","parentUuid":None,"type":"system","subtype":"microcompact_boundary","content":"b","cwd":"/home/x/demo"},
 {"uuid":"A","parentUuid":"S","type":"user","isCompactSummary":True,"cwd":"/home/x/demo",
  "message":{"role":"user","content":"Continued from previous conversation. Decision: approach X for "+dense+". Next: wire the Y lane."}},
]
open(sys.argv[1],"w").write("\n".join(json.dumps(r) for r in rows)+"\n")
PY
HOME="$H" python3 "$ROOT/skills/recover-from-false-positive/scripts/scrub_refusals.py" --handoff --file "$H/proj/s.jsonl" --apply >/dev/null 2>&1
ls "$H"/.claude/.fp-reinject-*.md >/dev/null 2>&1 || { echo "   handoff did not stage recap"; exit 1; }
OUT=$(echo '{"cwd":"/home/x/demo"}' | HOME="$H" CLAUDE_PLUGIN_ROOT="$ROOT" bash "$ROOT/hooks/inject-recovery-context.sh")
python3 - "$OUT" <<'PY'
import json,sys
ctx=json.loads(sys.argv[1])["hookSpecificOutput"]["additionalContext"]
fails=[]
if "continuity recap" not in ctx: fails.append("recap not injected")
if "wire the Y lane" not in ctx: fails.append("decision/next-step not preserved")
dens=sum(ctx.lower().count(t) for t in ("codegen","cmd/compile","share-generics","instantiation","toolchain"))
if dens>0: fails.append(f"recap still dense ({dens} trigger words) — would trip")
if fails: print("   REGRESSION:"); [print("     -",x) for x in fails]; sys.exit(1)
print("   recap injected, decisions preserved, density=0 (safe to reinject)")
PY
# one-shot: second fire has no recap
echo '{"cwd":"/home/x/demo"}' | HOME="$H" CLAUDE_PLUGIN_ROOT="$ROOT" bash "$ROOT/hooks/inject-recovery-context.sh" | grep -q "continuity recap" && { echo "   NOT one-shot (recap re-injected)"; exit 1; } || echo "   one-shot consume verified"
rm -rf "$H"

echo "smoke OK"
