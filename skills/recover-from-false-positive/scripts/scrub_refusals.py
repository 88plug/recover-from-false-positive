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
import argparse, glob, json, os, sys, tarfile, time

# The two-part signature of a REAL refusal turn. Both must hold, so we never
# touch CLAUDE.md prose that merely quotes the phrase, memory notes, or a
# transcript where an agent was grepping for it: (1) isApiErrorMessage:true AND
# the text starts with "API Error", AND (2) it carries a classifier fingerprint.
#
# The classifier's wording DRIFTS over time — hard-coding a single string is what
# let a whole recovery silently no-op. So we match a LIST of known exact phrases
# plus a loose fallback. The fallback is safe because it is only ever consulted on
# a turn already gated by isApiErrorMessage + "API Error" prefix (see is_refusal),
# so an ordinary API error can't be mistaken for a classifier trip.
# tests/smoke.sh pins every entry below so a known signature can never go stale.
API_ERR_PREFIX = "API Error"
SIGNATURES = (
    "appears to violate our Usage Policy",             # legacy usage-policy wording
    "flagged this message for a cybersecurity topic",  # 2026-07 cyber-safeguards wording
    "cyber-related safeguards",                         # alternate cyber wording
)
# Loose fallback markers — catch future rewordings. Only meaningful post-gate.
_FALLBACK = ("cyber-use-case", "cybersecurity topic", "safeguards flagged")
# Cheap whole-file / per-line prefilter union (find_targets uses this before parse).
_ALL_MARKERS = SIGNATURES + _FALLBACK


def _sig(t):
    """True if text carries any known classifier fingerprint (exact or fallback)."""
    if not isinstance(t, str):
        return False
    low = t.lower()
    return any(s.lower() in low for s in _ALL_MARKERS)


def _has_marker(blob):
    """Cheap substring prefilter over a raw string (file body or one line)."""
    low = blob.lower()
    return any(m.lower() in low for m in _ALL_MARKERS)


def _text(obj):
    if not isinstance(obj, dict):
        return ""
    m = obj.get("message", {})
    c = m.get("content", "") if isinstance(m, dict) else ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(
            b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def is_refusal(obj):
    if not isinstance(obj, dict) or not obj.get("isApiErrorMessage"):
        return False
    t = _text(obj)
    return _sig(t) and t.lstrip().startswith(API_ERR_PREFIX)


def dangling_count(lines):
    """How many parentUuids point at a uuid that isn't present (parseable lines only).
    The first real turn legitimately has parentUuid null/absent — null never counts."""
    uuids, parents = set(), []
    for l in lines:
        if not l.strip():
            continue
        try:
            o = json.loads(l)
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
    for l in raw:
        if not l.strip():
            continue
        try:
            o = json.loads(l)
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
    for l in raw:
        if not l.strip():
            out.append(l)
            continue
        try:
            o = json.loads(l)
        except Exception:
            out.append(l)  # pre-existing corruption — keep verbatim
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
            out.append(l)  # untouched original — byte-preserved

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
        # cheap confirm at least one true refusal before full parse
        for l in data.splitlines():
            if not _has_marker(l):
                continue
            try:
                if is_refusal(json.loads(l)):
                    hits.append(path)
                    break
            except Exception:
                continue
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
    for l in raw:
        if not l.strip(): continue
        try: o = json.loads(l)
        except: continue
        if is_refusal(o):
            ref_uuids[o.get("uuid")] = o.get("parentUuid")

    if not ref_uuids:
        print(f"  no refusals found in {os.path.basename(path)}")
        return

    # build uuid -> obj map for ancestor walking
    uuid_map = {}
    for l in raw:
        if not l.strip(): continue
        try:
            o = json.loads(l)
            if o.get("uuid"):
                uuid_map[o["uuid"]] = o
        except: pass

    remove_parent = dict(ref_uuids)  # uuid -> its_parentUuid for removed turns

    # for each refusal, walk up parentUuid chain to find the nearest user-role turn
    for ref_uuid, ref_parent in ref_uuids.items():
        cur = ref_parent
        visited = set()
        while cur and cur not in visited:
            visited.add(cur)
            o = uuid_map.get(cur)
            if o is None: break
            m = o.get("message", {})
            role = m.get("role", "") if isinstance(m, dict) else o.get("type", "")
            if role == "user":
                remove_parent[cur] = o.get("parentUuid")
                break
            cur = o.get("parentUuid")

    print(f"  removing {len(remove_parent)} turns (trigger+refusal) from {os.path.basename(path)}")
    if not apply:
        for uuid in remove_parent:
            o = uuid_map.get(uuid, {})
            role = o.get("message", {}).get("role", o.get("type", "?")) if isinstance(o.get("message"), dict) else o.get("type", "?")
            content = _text(o)[:80]
            print(f"    uuid={uuid[:8]} role={role}: {content!r}")
        return

    def resolve(p, seen=None):
        seen = seen or set()
        while p in remove_parent and p not in seen:
            seen.add(p); p = remove_parent[p]
        return p

    out = []
    removed = restitched = 0
    for l in raw:
        if not l.strip(): out.append(l); continue
        try: o = json.loads(l)
        except: out.append(l); continue
        if o.get("uuid") in remove_parent:
            removed += 1
            continue
        if o.get("parentUuid") in remove_parent:
            o["parentUuid"] = resolve(o["parentUuid"])
            restitched += 1
            out.append(json.dumps(o, ensure_ascii=False))
        else:
            out.append(l)

    stamp = int(time.time())
    backup = os.path.expanduser(f"~/.claude/refusal-scrub-backup-{stamp}.tar.gz")
    with tarfile.open(backup, "w:gz") as tar:
        tar.add(path)
    print(f"  backup: {backup}")

    tmp = path + ".fixtmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.replace(tmp, path)
    print(f"  OK removed={removed} restitched={restitched} kept={len(out)} (was {len(raw)})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--latest", action="store_true",
                    help="print decoded project path of the most recently modified refusal file")
    ap.add_argument("--fix-active", action="store_true",
                    help="remove ONLY the trigger user turn + refusal from the most recent "
                         "refusal file; keeps all other session history intact")
    args = ap.parse_args()

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
        fix_active(newest, apply=args.apply)
        # A trip also poisons this session's SUBAGENT shards, which live under
        # <session-stem>/subagents/**/*.jsonl — fix_active only touches the main
        # file, so scrub the shards too (chain-safe, self-verifying per file).
        shard_dir = os.path.splitext(newest)[0]
        shards = [s for s in glob.glob(os.path.join(shard_dir, "**", "*.jsonl"), recursive=True)
                  if _has_marker(open(s, encoding="utf-8", errors="ignore").read())]
        shard_plans = []
        for s in shards:
            out, removed, restitched, malformed, delta = scrub_file(s)
            if removed:
                shard_plans.append((s, out, removed, delta))
                print(f"  subagent shard: {removed} refusal(s), delta={delta:+d} {os.path.basename(s)}")
        if args.apply and shard_plans:
            stamp = int(time.time())
            backup = os.path.expanduser(f"~/.claude/refusal-scrub-shards-{stamp}.tar.gz")
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
        if not args.apply:
            print("\nDry run. Re-run with --fix-active --apply to write.")
        return

    targets = find_targets(args.root)
    plans = []
    for f in targets:
        out, removed, restitched, malformed, delta = scrub_file(f)
        plans.append(dict(file=f, removed=removed, restitched=restitched,
                          malformed=malformed, delta=delta, new_lines=out))

    total_removed = sum(p["removed"] for p in plans)
    unsafe = [p for p in plans if p["delta"] > 0]

    if not args.apply:
        report = dict(root=args.root, files=len(plans), refusals=total_removed,
                      unsafe_files=len(unsafe),
                      detail=[{k: p[k] for k in ("file", "removed", "restitched",
                               "malformed", "delta")} for p in plans])
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
        print(f"  OK removed={p['removed']} restitched={p['restitched']} "
              f"malformed_preserved={p['malformed']} {os.path.basename(p['file'])}")

    print(f"\nDONE: {total_removed} refusals removed from {written} files; "
          f"{len(skipped)} skipped. Backup at {backup}")
    if skipped:
        print("Skipped files had a positive dangling-delta — inspect by hand, "
              "restore from backup if needed.")


def _human(r):
    lines = [f"Root: {r['root']}",
             f"Files with refusals: {r['files']}",
             f"Total refusal turns: {r['refusals']}",
             f"Files the scrub would BREAK (delta>0): {r['unsafe_files']}", ""]
    for d in r["detail"]:
        flag = "  ⚠ UNSAFE" if d["delta"] > 0 else ""
        lines.append(f"  {d['removed']}x refusal, {d['restitched']} re-stitch, "
                     f"{d['malformed']} malformed-kept, delta={d['delta']:+d}{flag}\n"
                     f"      {d['file']}")
    if not r["detail"]:
        lines.append("  (clean — no refusal turns found)")
    lines.append("\nDry run. Re-run with --apply to back up + scrub + self-verify.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
