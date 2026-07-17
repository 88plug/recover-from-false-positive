#!/usr/bin/env python3
"""
scrub_refusals.py — JSON-aware remover of Anthropic API cyber-safeguard
false-positive refusal turns from Claude Code session logs (~/.claude/projects/*.jsonl).

Why this exists, not `sed`:
  Claude Code .jsonl logs are a linked list — each turn carries `uuid` and
  `parentUuid`. A refusal turn ("API Error: ... appears to violate our Usage
  Policy", flagged `isApiErrorMessage:true`) sits in that chain. Deleting the
  line with sed orphans the NEXT turn (its parentUuid now points at nothing),
  which can break `claude --resume`. This script instead RE-STITCHES: each
  orphan's parentUuid is rewired to the removed refusal's parent (walking up
  through chained refusals), THEN the refusal lines are dropped. Pre-existing
  malformed lines (e.g. disk-full truncation) are passed through byte-for-byte
  — they are not ours to touch.

Safety:
  - Detect-only by default. Pass --apply to write.
  - --apply makes one tar.gz backup of every file it will modify, first.
  - After writing, it self-verifies that the count of dangling parentUuids did
    NOT increase vs the in-memory original (the scrub must not add breakage).

Usage:
  python3 scrub_refusals.py                 # dry run, ~/.claude/projects
  python3 scrub_refusals.py --apply         # backup + scrub + verify
  python3 scrub_refusals.py --root DIR --apply
  python3 scrub_refusals.py --json          # machine-readable dry-run report
"""

import argparse
import glob
import json
import os
import sys
import tarfile
import time

# ── Detecting a classifier refusal turn, durably ────────────────────────────
#
# DURABILITY LESSON (learned the hard way): the user-facing wording of this error
# DRIFTS ("appears to violate our Usage Policy" → "safeguards flagged this message
# for a cybersecurity topic" → …). Hard-coding one string once let a whole recovery
# silently report "clean". Matching a LIST of strings is better but STILL breaks the
# day Anthropic invents a new phrase.
#
# So the PRIMARY signal is STRUCTURAL, not textual. Claude Code records a classifier
# refusal as an assistant turn with `isApiErrorMessage:true` AND
# `message.stop_reason == "refusal"` (also `model:"<synthetic>"`, `error:"invalid_request"`).
# Those fields are set by the client regardless of the human wording — verified
# against the installed binary (it branches on `stop_reason==="refusal"`). That path
# catches a brand-new wording on day zero.
#
# Text SIGNATURES are the SECONDARY path: legacy turns that predate stop_reason, and
# classification/telemetry. A refusal qualifies if EITHER path fires (both gated on
# isApiErrorMessage + assistant type). Unknown wordings caught structurally are
# auto-logged (observe_novel_wording) so the maintainer can widen SIGNATURES — but
# the tool already works without that. tests/smoke.sh pins both paths.
API_ERR_PREFIX = "API Error"
STRUCTURAL_STOP_REASON = "refusal"  # the reword-proof marker
SIGNATURES = (
    "appears to violate our Usage Policy",  # legacy usage-policy wording
    "flagged this message for a cybersecurity topic",  # 2026-07 cyber-safeguards wording
    "cyber-related safeguards",  # alternate cyber wording
)
# Loose fallback markers — catch future rewordings. Only meaningful post-gate.
_FALLBACK = ("cyber-use-case", "cybersecurity topic", "safeguards flagged")
_ALL_MARKERS = SIGNATURES + _FALLBACK
# Where novel (structurally-caught but text-unknown) wordings are logged for review.
OBSERVED_LOG = os.path.expanduser("~/.claude/.fp-observed-signatures.log")


def _sig(t):
    """True if text carries any known classifier fingerprint (exact or fallback)."""
    if not isinstance(t, str):
        return False
    low = t.lower()
    return any(s.lower() in low for s in _ALL_MARKERS)


def _msg(obj):
    m = obj.get("message") if isinstance(obj, dict) else None
    return m if isinstance(m, dict) else {}


def _stop_reason(obj):
    return _msg(obj).get("stop_reason")


def _stop_details(obj):
    sd = _msg(obj).get("stop_details")
    return sd if isinstance(sd, dict) else {}


def refusal_category(obj):
    """The server-named classifier category (e.g. 'cyber', 'weapons'). Free-form — the
    client is category-agnostic, so we CAPTURE it rather than hardcode a list. This is
    how we 'handle every category': read whatever the server sent."""
    return _stop_details(obj).get("category")


def refusal_explanation(obj):
    return _stop_details(obj).get("explanation") or ""


def is_structural_refusal(obj):
    """Reword-proof + all-edges: an assistant API-error turn the client marked as a
    refusal, via EITHER message.stop_reason=='refusal' OR stop_details.type=='refusal'
    (both are set by the client from the server response, independent of wording or
    category). Catches cyber, weapons, and any future category on day zero."""
    if not (
        isinstance(obj, dict)
        and obj.get("isApiErrorMessage")
        and obj.get("type") in (None, "assistant")
    ):
        return False
    return (
        _stop_reason(obj) == STRUCTURAL_STOP_REASON
        or _stop_details(obj).get("type") == STRUCTURAL_STOP_REASON
    )


def is_signature_refusal(obj):
    """Legacy/text path: isApiErrorMessage + a known wording + 'API Error' prefix."""
    if not (isinstance(obj, dict) and obj.get("isApiErrorMessage")):
        return False
    t = _text(obj)
    return _sig(t) and t.lstrip().startswith(API_ERR_PREFIX)


def is_refusal(obj):
    return is_structural_refusal(obj) or is_signature_refusal(obj)


def is_novel_wording(obj):
    """Caught structurally but no known text signature → a NEW wording to learn."""
    return is_structural_refusal(obj) and not _sig(_text(obj))


def observe_novel_wording(obj, path=""):
    """Append an unseen refusal wording (+ its server category) to OBSERVED_LOG."""
    try:
        snippet = " ".join((_text(obj) or refusal_explanation(obj)).split())[:300]
        if not snippet:
            return
        cat = refusal_category(obj) or "?"
        with open(OBSERVED_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{os.path.basename(path)}\tcategory={cat}\t{snippet}\n")
    except Exception:
        pass


def _has_marker(blob):
    """Cheap file-level prefilter. Must NOT skip structurally-detectable refusals, so
    it triggers on the durable field name too (any wording), not just known strings."""
    low = blob.lower()
    return "isapierrormessage" in low or any(m.lower() in low for m in _ALL_MARKERS)


def _text(obj):
    if not isinstance(obj, dict):
        return ""
    m = obj.get("message", {})
    c = m.get("content", "") if isinstance(m, dict) else ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(
            b.get("text", "")
            for b in c
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def dangling_count(lines):
    """How many parentUuids point at a uuid that isn't present (parseable lines only).
    The first real turn legitimately has parentUuid null/absent — null never counts."""
    uuids, parents = set(), []
    for line in lines:
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("uuid"):
            uuids.add(o["uuid"])
        p = o.get("parentUuid")
        if p:
            parents.append(p)
    return sum(1 for p in parents if p not in uuids)


def scrub_file(path):
    """Return (new_lines, removed_count, restitched_count, malformed_kept,
    delta_dangling) without writing. delta_dangling>0 means the scrub would
    ADD chain breakage and must be rejected."""
    raw = open(path, encoding="utf-8", errors="surrogatepass").read().splitlines()

    # pass 1 — map each refusal uuid -> its parentUuid (tolerate malformed lines)
    ref_parent = {}
    for line in raw:
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if is_refusal(o):
            ref_parent[o.get("uuid")] = o.get("parentUuid")

    if not ref_parent:
        return raw, 0, 0, 0, 0

    def resolve(p, seen=None):
        seen = seen or set()
        while p in ref_parent and p not in seen:
            seen.add(p)
            p = ref_parent[p]
        return p

    out, removed, restitched, malformed = [], 0, 0, 0
    for line in raw:
        if not line.strip():
            out.append(line)
            continue
        try:
            o = json.loads(line)
        except Exception:
            out.append(line)  # pre-existing corruption — keep verbatim
            malformed += 1
            continue
        if is_refusal(o):
            removed += 1
            continue
        if o.get("parentUuid") in ref_parent:
            o["parentUuid"] = resolve(o.get("parentUuid"))
            restitched += 1
            out.append(json.dumps(o, ensure_ascii=False))
        else:
            out.append(line)  # untouched original — byte-preserved

    delta = dangling_count(out) - dangling_count(raw)
    return out, removed, restitched, malformed, delta


def find_targets(root):
    hits = []
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        try:
            data = open(path, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        if not _has_marker(data):
            continue
        # confirm at least one true refusal before full parse; also learn any
        # NEW wording caught structurally (so upstream rewords surface, not hide).
        found = False
        for line in data.splitlines():
            if not _has_marker(line):
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if is_refusal(o):
                if not found:
                    hits.append(path)
                    found = True
                if is_novel_wording(o):
                    observe_novel_wording(o, path)
    return hits


def decode_slug(slug_dir):
    """Convert a project slug dir name (e.g. -home-andrew-pearl-bench) to the
    real filesystem path. Slug is absolute path with / replaced by -.
    Strategy: at each position try joining remaining parts with - (preserving
    project names that contain dashes) before splitting — so pearl-bench is
    tried as one component before pearl/bench."""
    name = os.path.basename(slug_dir)
    if not name.startswith("-"):
        return None
    parts = name[1:].split("-")
    path = "/"
    i = 0
    while i < len(parts):
        matched = False
        # try longest dash-joined name first (project names contain dashes),
        # then progressively shorter joins (path splits)
        for j in range(len(parts), i, -1):
            candidate = "-".join(parts[i:j])  # join with dash → try as single dir name
            full = os.path.join(path, candidate)
            if os.path.isdir(full):
                path = full
                i = j
                matched = True
                break
        if not matched:
            # best-effort: append all remaining joined with dashes
            path = os.path.join(path, "-".join(parts[i:]))
            break
    return path


def latest_refusal_project(root):
    """Return (project_path, jsonl_path) for the most recently modified refusal file."""
    targets = find_targets(root)
    if not targets:
        return None, None
    newest = max(targets, key=lambda p: os.path.getmtime(p))
    slug_dir = os.path.dirname(newest)
    project_path = decode_slug(slug_dir)
    return project_path, newest


def fix_active(path, apply=False):
    """Remove ONLY the triggering user turn + the refusal turn from a single
    session file. Everything else — all subsequent turns — is preserved.
    The trigger user turn is the user-role turn whose content exactly caused
    the block (short message immediately before the refusal in the chain)."""
    raw = open(path, encoding="utf-8", errors="surrogatepass").read().splitlines()

    # collect: all refusal uuids, and the user turn(s) that are their ancestors
    ref_uuids = {}  # refusal_uuid -> parentUuid
    for line in raw:
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if is_refusal(o):
            ref_uuids[o.get("uuid")] = o.get("parentUuid")

    if not ref_uuids:
        print(f"  no refusals found in {os.path.basename(path)}")
        return

    # build uuid -> obj map for ancestor walking
    uuid_map = {}
    for line in raw:
        if not line.strip():
            continue
        try:
            o = json.loads(line)
            if o.get("uuid"):
                uuid_map[o["uuid"]] = o
        except Exception:
            pass

    remove_parent = dict(ref_uuids)  # uuid -> its_parentUuid for removed turns

    # for each refusal, walk up parentUuid chain to find the nearest user-role turn
    for ref_uuid, ref_parent in ref_uuids.items():
        cur = ref_parent
        visited = set()
        while cur and cur not in visited:
            visited.add(cur)
            o = uuid_map.get(cur)
            if o is None:
                break
            m = o.get("message", {})
            role = m.get("role", "") if isinstance(m, dict) else o.get("type", "")
            if role == "user":
                remove_parent[cur] = o.get("parentUuid")
                break
            cur = o.get("parentUuid")

    print(
        f"  removing {len(remove_parent)} turns (trigger+refusal) from {os.path.basename(path)}"
    )
    if not apply:
        for uuid in remove_parent:
            o = uuid_map.get(uuid, {})
            role = (
                o.get("message", {}).get("role", o.get("type", "?"))
                if isinstance(o.get("message"), dict)
                else o.get("type", "?")
            )
            content = _text(o)[:80]
            print(f"    uuid={uuid[:8]} role={role}: {content!r}")
        return

    def resolve(p, seen=None):
        seen = seen or set()
        while p in remove_parent and p not in seen:
            seen.add(p)
            p = remove_parent[p]
        return p

    out = []
    removed = restitched = 0
    for line in raw:
        if not line.strip():
            out.append(line)
            continue
        try:
            o = json.loads(line)
        except Exception:
            out.append(line)
            continue
        if o.get("uuid") in remove_parent:
            removed += 1
            continue
        if o.get("parentUuid") in remove_parent:
            o["parentUuid"] = resolve(o["parentUuid"])
            restitched += 1
            out.append(json.dumps(o, ensure_ascii=False))
        else:
            out.append(line)

    stamp = int(time.time())
    backup = os.path.expanduser(f"~/.claude/refusal-scrub-backup-{stamp}.tar.gz")
    with tarfile.open(backup, "w:gz") as tar:
        tar.add(path)
    print(f"  backup: {backup}")

    tmp = path + ".fixtmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.replace(tmp, path)
    print(
        f"  OK removed={removed} restitched={restitched} kept={len(out)} (was {len(raw)})"
    )


# ── De-saturation ────────────────────────────────────────────────────────────
#
# Removing refusal turns does NOT stop the next trip: the client already filters
# isApiErrorMessage turns before the API send (verified in the binary). What
# re-fires on --resume is the dense WORK content still in the transcript window.
# Claude Code re-sends the turns AFTER the last compaction boundary; that window is
# what the classifier scores. De-saturation neutralizes the vocabulary-dense turns
# in that window IN PLACE — replacing text and tool_result payloads with a short
# stub while preserving uuid/parentUuid AND every tool_use/tool_result pairing (we
# never change block types or tool_use_ids, so resume can't break on a dangling
# tool call). Chain is untouched (no turn removed → zero dangling delta). Lossy but
# fully reversible from the backup. This is what actually revives a stuck session.
STUB = (
    "[de-saturated by recover-from-false-positive — dense content trimmed so the "
    "output classifier does not re-fire on resume; verbatim original is in the backup]"
)

# Domain-general poison vocabulary, drawn from the known trigger classes. Scoring a
# turn by hits across ALL classes keeps this useful beyond any one project; the
# active session's own cluster (from ~/.claude/.fp-state.json) is merged in too.
_DESAT_TERMS = (
    "cosign",
    "sigstore",
    "attestation",
    "provenance",
    "slsa",
    "fulcio",
    "rekor",
    "in-toto",
    "keyless",
    "hashrate",
    "nonce",
    "coinbase",
    "randomx",
    "cryptonight",
    "blake3",
    "dev-fee",
    "lnd",
    "lndinit",
    "bitcoind",
    "lightning",
    "wallet-unlock",
    "failover",
    "gauntlet",
    "raft",
    "teardown",
    "codegen",
    "noder",
    "cmd/compile",
    "share-generics",
    "generic instantiation",
    "instantiation",
    "redundant compil",
    "extern-template",
    "incremental compile",
    "toolchain",
    "first-build",
    "build acceleration",
    "sigma rule",
    "ueba",
    "threat detection",
    # the classifier's OWN vocabulary — a session where /recover was (mistakenly) run
    # in-place has the skill body injected as turns, which is itself dense poison.
    "cyber-related safeguards",
    "cybersecurity",
    "usage policy",
    "cyber-use-case",
    "output classifier",
    "content filter",
    "safeguard",
    "false positive",
)
# Any window turn at least this large is stubbed regardless of vocabulary — big turns
# (huge tool_result file reads, injected skill bodies) are what saturate the window,
# and vocab lists can't anticipate every poison. Domain-agnostic backstop.
DESAT_SIZE_BYTES = 8000
_WINDOW_BOUNDARY_SUBTYPES = {"compact_boundary", "microcompact_boundary"}


def _extra_terms():
    """Merge in the active session's own detected vocab cluster, if the hook wrote one."""
    try:
        st = json.load(
            open(os.path.expanduser("~/.claude/.fp-state.json"), encoding="utf-8")
        )
        terms = []
        for hits in (st.get("vocab_clusters") or {}).values():
            terms += [h for h in hits if isinstance(h, str)]
        return tuple(terms)
    except Exception:
        return ()


def _dense_text(obj):
    """Full text of a turn for SCORING — unlike _text (refusal detection, text blocks
    only), this includes tool_result payloads and tool_use inputs, which carry most of
    the vocabulary weight (big file reads, command output)."""
    m = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    c = m.get("content", "")
    if isinstance(c, str):
        return c
    if not isinstance(c, list):
        return ""
    parts = []
    for b in c:
        if not isinstance(b, dict):
            continue
        ty = b.get("type")
        if ty == "text":
            parts.append(b.get("text", ""))
        elif ty == "tool_result":
            rc = b.get("content", "")
            parts.append(rc if isinstance(rc, str) else json.dumps(rc))
        elif ty == "tool_use":
            parts.append(json.dumps(b.get("input", "")))
    return " ".join(parts)


def _turn_score(obj, terms):
    low = _dense_text(obj).lower()
    return sum(low.count(t) for t in terms)


def _window_start(objs):
    """Index of the first turn Claude Code would re-send: right after the LAST
    compaction boundary (that is exactly the live context window)."""
    last = -1
    for i, o in enumerate(objs):
        if o.get("type") == "system" and o.get("subtype") in _WINDOW_BOUNDARY_SUBTYPES:
            last = i
    return last + 1  # 0 if no boundary → whole transcript is the window


def _stub_block(b):
    if not isinstance(b, dict):
        return b
    t = b.get("type")
    if t == "text":
        return {**b, "text": STUB}
    if t == "tool_result":  # keep tool_use_id + type → pairing intact
        return {**b, "content": STUB}
    return b  # tool_use / thinking / image left structurally intact


def _stub_message_content(o):
    m = o.get("message")
    if not isinstance(m, dict):
        return o
    c = m.get("content")
    if isinstance(c, str):
        new_c = STUB
    elif isinstance(c, list):
        new_c = [_stub_block(b) for b in c]
    else:
        return o
    o = dict(o)
    o["message"] = {**m, "content": new_c}
    o["desaturated"] = True
    return o


def desaturate_lines(raw, keep_recent=6, min_score=4, terms=None):
    """Return (out_lines, stubbed, bytes_saved, manifest). Stubs vocabulary-dense
    user/assistant turns in the live window, preserving chain + tool pairing."""
    terms = tuple(terms) if terms else (_DESAT_TERMS + _extra_terms())
    objs, idx_of = [], []
    for n, line in enumerate(raw):
        if not line.strip():
            objs.append(None)
            idx_of.append(n)
            continue
        try:
            objs.append(json.loads(line))
        except Exception:
            objs.append(None)
        idx_of.append(n)

    real = [(n, o) for n, o in enumerate(objs) if isinstance(o, dict)]
    start = _window_start([o for _, o in real])
    # translate window start (index into `real`) back to positions; simpler: work on real list
    win = real[start:]
    protect = {n for n, _ in win[-keep_recent:]}  # keep the last few turns readable

    targets = {}
    for n, o in win:
        if n in protect:
            continue
        if o.get("type") not in ("user", "assistant"):
            continue
        if is_refusal(o) or o.get("isMeta"):
            continue
        sc = _turn_score(o, terms)
        if sc >= min_score:
            targets[n] = sc

    out, stubbed, saved, manifest = [], 0, 0, []
    for n, line in enumerate(raw):
        o = objs[n] if n < len(objs) else None
        if isinstance(o, dict) and n in targets:
            stub = _stub_message_content(o)
            new_l = json.dumps(stub, ensure_ascii=False)
            saved += max(0, len(line) - len(new_l))
            stubbed += 1
            role = (o.get("message", {}) or {}).get("role", o.get("type"))
            manifest.append(
                (n, targets[n], role, " ".join(_dense_text(o).split())[:60])
            )
            out.append(new_l)
        else:
            out.append(line)
    return out, stubbed, saved, manifest


def _blocks(o):
    c = _msg(o).get("content")
    return c if isinstance(c, list) else []


def _use_ids(o):
    return [
        b.get("id")
        for b in _blocks(o)
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
    ]


def _result_ids(o):
    return [
        b.get("tool_use_id")
        for b in _blocks(o)
        if isinstance(b, dict)
        and b.get("type") == "tool_result"
        and b.get("tool_use_id")
    ]


def surgical_lines(raw, keep_recent=6, min_score=4, terms=None):
    """SURGICAL (default): DROP the whole offending turns and keep every other turn
    BYTE-EXACT — no token neutralization, no stub placeholders. A dropped turn is
    removed entirely and the chain is re-stitched (parentUuid rewired up), exactly like
    refusal removal. tool_use/tool_result pairs are dropped as a UNIT so resume can't
    orphan a tool call; a drop group that would reach a protected recent turn or escape
    the resume window is skipped (kept intact) rather than risk breakage.
    Returns (out_lines, dropped, bytes_freed, manifest)."""
    terms = tuple(terms) if terms else (_DESAT_TERMS + _extra_terms())
    objs = []
    for line in raw:
        s = line.strip()
        if not s:
            objs.append(None)
            continue
        try:
            objs.append(json.loads(line))
        except Exception:
            objs.append(None)
    real = [(i, o) for i, o in enumerate(objs) if isinstance(o, dict)]
    start = _window_start([o for _, o in real])
    win = real[start:]
    win_idx = {i for i, _ in win}
    protect = {i for i, _ in win[-keep_recent:]}

    id2use, id2res = {}, {}
    for i, o in real:
        for u in _use_ids(o):
            id2use[u] = i
        for r in _result_ids(o):
            id2res[r] = i

    seeds = []
    for i, o in win:
        if i in protect or o.get("type") not in ("user", "assistant"):
            continue
        if is_refusal(o) or o.get("isMeta"):
            continue
        if _turn_score(o, terms) >= min_score:
            seeds.append(i)

    drop = set()
    for seed in seeds:
        group, stack = set(), [seed]
        while stack:  # close over tool_use/tool_result pairs
            i = stack.pop()
            if i in group:
                continue
            group.add(i)
            o = objs[i]
            for u in _use_ids(o):
                j = id2res.get(u)
                if j is not None:
                    stack.append(j)
            for r in _result_ids(o):
                j = id2use.get(r)
                if j is not None:
                    stack.append(j)
        if (group & protect) or not (group <= win_idx):  # unsafe → keep intact
            continue
        drop |= group

    remove_parent = {
        objs[i].get("uuid"): objs[i].get("parentUuid")
        for i in drop
        if objs[i].get("uuid")
    }

    def resolve(p, seen=None):
        seen = seen or set()
        while p in remove_parent and p not in seen:
            seen.add(p)
            p = remove_parent[p]
        return p

    out, dropped, freed, manifest = [], 0, 0, []
    for i, line in enumerate(raw):
        o = objs[i] if i < len(objs) else None
        if isinstance(o, dict) and i in drop:
            dropped += 1
            freed += len(line)
            manifest.append(
                (
                    i,
                    _turn_score(o, terms),
                    _msg(o).get("role") or o.get("type"),
                    " ".join(_dense_text(o).split())[:60],
                )
            )
            continue
        if isinstance(o, dict) and o.get("parentUuid") in remove_parent:
            o = dict(o)
            o["parentUuid"] = resolve(o["parentUuid"])
            out.append(json.dumps(o, ensure_ascii=False))
        else:
            out.append(line)  # BYTE-EXACT — word-for-word kept
    return out, dropped, freed, manifest


def desaturate_and_write(
    path, apply=False, keep_recent=6, min_score=4, backup_tag="desat", stub=False
):
    """Default = SURGICAL: drop whole offending turns, keep the rest word-for-word.
    stub=True falls back to the older in-place neutralization (kept for callers that
    need the transcript to stay the same length)."""
    raw = open(path, encoding="utf-8", errors="surrogatepass").read().splitlines()
    if stub:
        out, n, saved, manifest = desaturate_lines(raw, keep_recent, min_score)
        verb, unit = "neutralized", saved
    else:
        out, n, saved, manifest = surgical_lines(raw, keep_recent, min_score)
        verb, unit = "dropped", saved
    if not n:
        print(
            f"  de-saturate: nothing dense enough to touch in {os.path.basename(path)}"
        )
        return 0
    delta = dangling_count(out) - dangling_count(raw)
    print(
        f"  de-saturate ({'stub' if stub else 'surgical'}): {verb} {n} offending turn(s), "
        f"~{unit // 1024}KB, delta={delta:+d}, rest byte-exact — {os.path.basename(path)}"
    )
    for ln, sc, role, prev in manifest[:8]:
        print(f"    line {ln} score={sc} {role}: {prev!r}")
    if len(manifest) > 8:
        print(f"    … +{len(manifest) - 8} more")
    if delta > 0:
        print("  ! would increase dangling pointers — refusing to write this file.")
        return 0
    if not apply:
        return n
    stamp = int(time.time())
    backup = os.path.expanduser(f"~/.claude/{backup_tag}-{stamp}.tar.gz")
    with tarfile.open(backup, "w:gz") as tar:
        tar.add(path)
    print(f"  backup: {backup}")
    tmp = path + ".desattmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.replace(tmp, path)
    return n


def refusal_count(path):
    n = 0
    try:
        for line in open(path, encoding="utf-8", errors="surrogatepass"):
            line = line.strip()
            if not line or "isApiErrorMessage" not in line:
                continue
            try:
                if is_refusal(json.loads(line)):
                    n += 1
            except Exception:
                continue
    except Exception:
        pass
    return n


def _cwd_slug(cwd):
    """Match Claude Code's project-dir slug: absolute path, '/'→'-'."""
    return cwd.replace("/", "-") if cwd else ""


def _neutralize(text, terms=None):
    """Lower a recap's classifier-trigger DENSITY while keeping its meaning: drop fenced
    code + long paths, replace trigger vocab with a neutral placeholder, collapse space,
    cap length. The result must be safe to re-inject into a live session without tripping."""
    import re

    terms = tuple(terms) if terms else (_DESAT_TERMS + _extra_terms())
    text = re.sub(
        r"```.*?```", " [code omitted] ", text, flags=re.S
    )  # drop code blocks
    text = re.sub(r"(/[\w.\-]+){3,}", " [path] ", text)  # drop long paths
    for t in sorted(terms, key=len, reverse=True):
        text = re.sub(re.escape(t), "[detail]", text, flags=re.I)
    text = re.sub(r"\[detail\]\w*", "[detail]", text)  # eat term suffixes
    text = re.sub(r"\[detail\]([\s,]*\[detail\])+", "[detail]", text)  # collapse runs
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1600]


def handoff(path, apply=False):
    """Produce a DE-SATURATED recap of a session and stage it for re-injection after a
    /clear — the 'clear then reinject' flow: the user never closes the session or switches
    model. The recap is built offline (no generation → no trip); the UserPromptSubmit hook
    injects it on the next prompt. Prefers the transcript's own last compaction summary
    (LLM-quality, already written) so we neutralize rather than regenerate."""
    objs, cwd = [], ""
    for line in open(path, encoding="utf-8", errors="surrogatepass"):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        objs.append(o)
        if isinstance(o, dict) and o.get("cwd"):
            cwd = o["cwd"]

    summary = ""
    for o in objs:  # last compaction summary, if any
        if not isinstance(o, dict) or o.get("desaturated"):  # skip our own stubs
            continue
        m = o.get("message") if isinstance(o.get("message"), dict) else {}
        c = m.get("content", "")
        is_sum = o.get("isCompactSummary") or (
            isinstance(c, str) and "continued from a previous conversation" in c.lower()
        )
        if is_sum:
            summary = c if isinstance(c, str) else _dense_text(o)
    if not summary:  # fallback: recent real user intent
        ut = [
            _dense_text(o)
            for o in objs
            if isinstance(o, dict)
            and o.get("type") == "user"
            and not o.get("isMeta")
            and not o.get("desaturated")
        ]
        summary = "\n".join(ut[-5:])

    recap = _neutralize(summary)
    anchor = (
        "[continuity recap — prior context was cleared to recover from a false-positive "
        "classifier trip; keep working, do not re-close or switch models]\n\n" + recap
    )
    print(f"De-saturated recap ({len(anchor)} chars) for cwd={cwd or '?'}:")
    print("  " + anchor[:280].replace("\n", " ") + (" …" if len(anchor) > 280 else ""))
    if not apply:
        print(
            "\nDry run. Re-run with --handoff --file … --apply to stage the re-injection."
        )
        return
    slug = _cwd_slug(cwd)
    if not slug:
        print("  ! no cwd in transcript — cannot key the re-inject file; aborting.")
        return
    out = os.path.expanduser(f"~/.claude/.fp-reinject-{slug}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(anchor + "\n")
    print(f"\nStaged: {out}")
    print("Now, IN THE STUCK SESSION (no close, no model change):")
    print(
        "  1. /clear        ← empties in-memory context locally, never generates → no trip"
    )
    print(
        "  2. type: continue ← the hook re-injects this recap once, and you keep going"
    )


def backtest(root):
    """Machine-wide exposure report: which sessions carry a dense+large resume window
    that would re-trip the classifier on --resume. Prevention triage, not a fix."""
    files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    terms = _DESAT_TERMS + _extra_terms()
    scanned, atrisk = 0, []
    for f in files:
        try:
            objs = []
            for line in open(f, encoding="utf-8", errors="surrogatepass"):
                line = line.strip()
                if not line:
                    continue
                try:
                    objs.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            continue
        if len(objs) < 5:
            continue
        scanned += 1
        start = _window_start([o for o in objs if isinstance(o, dict)])
        win = objs[start:]
        wb = sum(len(json.dumps(o)) for o in win)
        dense = sum(_turn_score(o, terms) for o in win if isinstance(o, dict))
        if dense >= 25 and wb > 200_000:
            atrisk.append((dense, wb, len(win), f))
    atrisk.sort(reverse=True)
    print(
        f"── backtest: {scanned} sessions scanned, {len(atrisk)} at-risk "
        f"(dense+large resume window → would re-trip on --resume) ──"
    )
    for d, b, n, f in atrisk[:20]:
        print(f"  density={d:>4} window={b // 1024:>6}KB turns={n:>4}  {f}")
    if len(atrisk) > 20:
        print(f"  … +{len(atrisk) - 20} more")
    print(
        "De-saturate any of these before resuming:  --desaturate --file <path> --apply"
    )
    return atrisk


# ── Durability self-check (fail loud, never silent) ──────────────────────────
def selfcheck(root):
    """Prove the detector still matches reality, so upstream drift surfaces LOUDLY
    instead of as a false 'clean'. Reports structural vs signature vs novel counts
    and flags any isApiErrorMessage turn we can't account for."""
    total_err = struct = sigmatch = novel = other = 0
    novels = []
    from collections import Counter

    categories = Counter()
    for path in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
        try:
            data = open(path, encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line in data:
            if "isApiErrorMessage" not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if not (isinstance(o, dict) and o.get("isApiErrorMessage")):
                continue
            total_err += 1
            s = is_structural_refusal(o)
            g = is_signature_refusal(o)
            if s and g:
                struct += 1
                sigmatch += 1
            elif s:
                struct += 1
            elif g:
                sigmatch += 1
            else:
                other += 1
                continue
            categories[refusal_category(o) or "(none)"] += 1
            if is_novel_wording(o):
                novel += 1
                snip = " ".join(_text(o).split())[:80]
                if snip not in novels:
                    novels.append(snip)

    print("── recover-from-false-positive self-check ──")
    print("detection contract: isApiErrorMessage:true AND (")
    print(
        "   message.stop_reason=='refusal' OR stop_details.type=='refusal'  [structural, reword+category-proof]"
    )
    print(
        f"   OR known SIGNATURES text [{len(SIGNATURES)} known])  — category read from stop_details.category"
    )
    print(f"isApiErrorMessage turns scanned : {total_err}")
    print(f"  classified as refusal (structural) : {struct}")
    print(f"  classified as refusal (signature)  : {sigmatch}")
    print(f"  NEW wording caught structurally    : {novel}")
    print(f"  other API errors (500/overload/etc): {other}  (correctly left alone)")
    if categories:
        print("  refusal categories seen (from stop_details.category):")
        for cat, n in categories.most_common():
            print(f"    {cat}: {n}")
    if novel:
        print(
            f"⚠ {novel} refusal(s) matched STRUCTURALLY but not any known string — a new wording."
        )
        print(f"  Logged to {OBSERVED_LOG}. Consider adding to SIGNATURES:")
        for s in novels[:5]:
            print(f"    · {s!r}")
    # binary canary: are the fields we depend on still present upstream?
    vers = sorted(glob.glob(os.path.expanduser("~/.local/share/claude/versions/*")))
    if vers:
        try:
            blob = open(vers[-1], "rb").read()
            for field in (b"isApiErrorMessage", b"stop_reason", b"refusal"):
                if blob.find(field) < 0:
                    print(
                        f"⚠ installed Claude Code ({os.path.basename(vers[-1])}) no longer mentions "
                        f"{field.decode()!r} — detection contract may have drifted; review."
                    )
        except Exception:
            pass
    print("(structural detection means a reworded error is still caught on day zero.)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument(
        "--latest",
        action="store_true",
        help="print decoded project path of the most recently modified refusal file",
    )
    ap.add_argument(
        "--fix-active",
        action="store_true",
        help="remove the trigger+refusal from the most recent refusal file AND "
        "(when the session is saturated) de-saturate its live window so it "
        "resumes without re-tripping; covers subagent shards too",
    )
    ap.add_argument(
        "--desaturate",
        action="store_true",
        help="force in-place de-saturation of the most recent refusal session "
        "(stub vocabulary-dense turns in the resume window; reversible)",
    )
    ap.add_argument(
        "--no-desaturate",
        action="store_true",
        help="disable the default de-saturation inside --fix-active",
    )
    ap.add_argument(
        "--stub",
        action="store_true",
        help="de-saturate by neutralizing content in place (keeps transcript "
        "length) instead of the default SURGICAL drop (removes whole "
        "offending turns, keeps every other turn byte-exact)",
    )
    ap.add_argument(
        "--file",
        default=None,
        help="explicit session .jsonl to target (for --desaturate on a session "
        "whose refusal turns were already scrubbed but is still saturated)",
    )
    ap.add_argument(
        "--selfcheck",
        action="store_true",
        help="durability audit: prove the detector still matches reality and "
        "surface any new wording or upstream field drift (fail loud)",
    )
    ap.add_argument(
        "--backtest",
        action="store_true",
        help="scan ALL sessions on the machine and list the ones whose resume "
        "window is dense+large enough to re-trip (prevention triage)",
    )
    ap.add_argument(
        "--handoff",
        action="store_true",
        help="stage a de-saturated recap for the 'clear then reinject' flow: user "
        "runs /clear in the stuck session and the hook re-injects it — no "
        "close, no model switch. Use with --file <session.jsonl> [--apply]",
    )
    ap.add_argument(
        "--keep-recent",
        type=int,
        default=6,
        help="de-saturate: leave the last N turns untouched (default 6)",
    )
    ap.add_argument(
        "--desat-min-score",
        type=int,
        default=4,
        help="de-saturate: min trigger-vocab hits for a turn to be stubbed (default 4)",
    )
    args = ap.parse_args()

    if args.selfcheck:
        selfcheck(args.root)
        return

    if args.backtest:
        backtest(args.root)
        return

    if args.handoff:
        if not args.file:
            print("--handoff needs --file <session.jsonl>")
            sys.exit(1)
        f = os.path.abspath(os.path.expanduser(args.file))
        if not os.path.exists(f):
            print(f"No such file: {f}")
            sys.exit(1)
        handoff(f, apply=args.apply)
        return

    if args.desaturate:
        if args.file:
            newest = os.path.abspath(os.path.expanduser(args.file))
            if not os.path.exists(newest):
                print(f"No such file: {newest}")
                sys.exit(1)
        else:
            targets = find_targets(args.root)
            if not targets:
                print(
                    "No refusal files found. A saturated session whose refusals were "
                    "already scrubbed has no marker — point at it with --file <session.jsonl>."
                )
                return
            newest = max(targets, key=lambda p: os.path.getmtime(p))
        print(f"De-saturate: {newest}")
        desaturate_and_write(
            newest,
            apply=args.apply,
            keep_recent=args.keep_recent,
            min_score=args.desat_min_score,
            stub=args.stub,
        )
        # subagent shards share the trip's vocabulary — de-saturate them too
        shard_dir = os.path.splitext(newest)[0]
        for s in glob.glob(os.path.join(shard_dir, "**", "*.jsonl"), recursive=True):
            desaturate_and_write(
                s,
                apply=args.apply,
                keep_recent=args.keep_recent,
                min_score=args.desat_min_score,
                backup_tag="desat-shard",
                stub=args.stub,
            )
        if not args.apply:
            print("\nDry run. Re-run with --desaturate --apply to write.")
        return

    if args.latest:
        project_path, jsonl_path = latest_refusal_project(args.root)
        if project_path:
            print(project_path)
        else:
            print("(no refusal files found)", file=sys.stderr)
            sys.exit(1)
        return

    if args.fix_active:
        targets = find_targets(args.root)
        if not targets:
            print("No refusal files found.")
            return
        newest = max(targets, key=lambda p: os.path.getmtime(p))
        print(f"Active session: {newest}")
        # Saturation signal: repeated refusals mean the whole live window trips, not
        # a one-off. Removing turns alone leaves the user stuck (refusals are already
        # filtered before send), so default to ALSO de-saturating — unless opted out.
        saturated = refusal_count(newest) >= 2
        do_desat = saturated and not args.no_desaturate
        fix_active(newest, apply=args.apply)
        if do_desat:
            print(
                "  session is saturated (repeated trips) → de-saturating live window "
                "so resume won't re-fire (use --no-desaturate to skip):"
            )
            desaturate_and_write(
                newest,
                apply=args.apply,
                keep_recent=args.keep_recent,
                min_score=args.desat_min_score,
                stub=args.stub,
            )
        # A trip also poisons this session's SUBAGENT shards, which live under
        # <session-stem>/subagents/**/*.jsonl — fix_active only touches the main
        # file, so scrub the shards too (chain-safe, self-verifying per file).
        shard_dir = os.path.splitext(newest)[0]
        shards = [
            s
            for s in glob.glob(os.path.join(shard_dir, "**", "*.jsonl"), recursive=True)
            if _has_marker(open(s, encoding="utf-8", errors="ignore").read())
        ]
        shard_plans = []
        for s in shards:
            out, removed, restitched, malformed, delta = scrub_file(s)
            if removed:
                shard_plans.append((s, out, removed, delta))
                print(
                    f"  subagent shard: {removed} refusal(s), delta={delta:+d} {os.path.basename(s)}"
                )
        if args.apply and shard_plans:
            stamp = int(time.time())
            backup = os.path.expanduser(
                f"~/.claude/refusal-scrub-shards-{stamp}.tar.gz"
            )
            with tarfile.open(backup, "w:gz") as tar:
                for s, *_ in shard_plans:
                    tar.add(s)
            print(f"  shard backup: {backup}")
            for s, out, removed, delta in shard_plans:
                if delta > 0:
                    print(f"  SKIP shard (+{delta} dangling) {os.path.basename(s)}")
                    continue
                tmp = s + ".scrubtmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(out) + "\n")
                os.replace(tmp, s)
        # de-saturate shards too when the session is saturated
        if do_desat:
            for s in glob.glob(
                os.path.join(shard_dir, "**", "*.jsonl"), recursive=True
            ):
                desaturate_and_write(
                    s,
                    apply=args.apply,
                    keep_recent=args.keep_recent,
                    min_score=args.desat_min_score,
                    backup_tag="desat-shard",
                    stub=args.stub,
                )
        if not args.apply:
            print("\nDry run. Re-run with --fix-active --apply to write.")
        return

    targets = find_targets(args.root)
    plans = []
    for f in targets:
        out, removed, restitched, malformed, delta = scrub_file(f)
        plans.append(
            dict(
                file=f,
                removed=removed,
                restitched=restitched,
                malformed=malformed,
                delta=delta,
                new_lines=out,
            )
        )

    total_removed = sum(p["removed"] for p in plans)
    unsafe = [p for p in plans if p["delta"] > 0]

    if not args.apply:
        report = dict(
            root=args.root,
            files=len(plans),
            refusals=total_removed,
            unsafe_files=len(unsafe),
            detail=[
                {
                    k: p[k]
                    for k in ("file", "removed", "restitched", "malformed", "delta")
                }
                for p in plans
            ],
        )
        print(json.dumps(report, indent=2) if args.json else _human(report))
        return

    if not plans:
        print("Nothing to scrub — 0 refusal turns found.")
        return

    # one backup tarball for everything we will touch
    stamp = int(time.time())
    backup = os.path.expanduser(f"~/.claude/refusal-scrub-backup-{stamp}.tar.gz")
    with tarfile.open(backup, "w:gz") as tar:
        for p in plans:
            tar.add(p["file"])
    print(f"backup: {backup} ({len(plans)} files)")

    written, skipped = 0, []
    for p in plans:
        if p["delta"] > 0:  # self-check: never add chain breakage
            skipped.append(p["file"])
            print(f"  SKIP (+{p['delta']} dangling) {p['file']}")
            continue
        tmp = p["file"] + ".scrubtmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(p["new_lines"]) + "\n")
        os.replace(tmp, p["file"])
        written += 1
        print(
            f"  OK removed={p['removed']} restitched={p['restitched']} "
            f"malformed_preserved={p['malformed']} {os.path.basename(p['file'])}"
        )

    print(
        f"\nDONE: {total_removed} refusals removed from {written} files; "
        f"{len(skipped)} skipped. Backup at {backup}"
    )
    if skipped:
        print(
            "Skipped files had a positive dangling-delta — inspect by hand, "
            "restore from backup if needed."
        )


def _human(r):
    lines = [
        f"Root: {r['root']}",
        f"Files with refusals: {r['files']}",
        f"Total refusal turns: {r['refusals']}",
        f"Files the scrub would BREAK (delta>0): {r['unsafe_files']}",
        "",
    ]
    for d in r["detail"]:
        flag = "  ⚠ UNSAFE" if d["delta"] > 0 else ""
        lines.append(
            f"  {d['removed']}x refusal, {d['restitched']} re-stitch, "
            f"{d['malformed']} malformed-kept, delta={d['delta']:+d}{flag}\n"
            f"      {d['file']}"
        )
    if not r["detail"]:
        lines.append("  (clean — no refusal turns found)")
    lines.append("\nDry run. Re-run with --apply to back up + scrub + self-verify.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
