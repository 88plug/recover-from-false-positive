#!/usr/bin/env python3
"""
StopFailure hook: detect API output classifier trip, pre-compute classification,
write ~/.claude/.fp-state.json for UserPromptSubmit hook to consume.

Pure Python — no model generation, no retrigger risk.
Fires at trip time: transcript is live, triggering turn is fresh.
"""

import json
import os
import sys
import subprocess

# The classifier's wording drifts — match a LIST of known phrases plus a loose
# fallback, never a single hard-coded string (a stale string silently no-ops the
# whole detector). Only consulted on turns already flagged isApiErrorMessage.
SIGNATURES = (
    "appears to violate our Usage Policy",  # legacy usage-policy wording
    "flagged this message for a cybersecurity topic",  # 2026-07 cyber-safeguards wording
    "cyber-related safeguards",  # alternate cyber wording
)
_FALLBACK = ("cyber-use-case", "cybersecurity topic", "safeguards flagged")


def _sig(t):
    if not isinstance(t, str):
        return False
    low = t.lower()
    return any(s.lower() in low for s in (SIGNATURES + _FALLBACK))


def _msg_dict(o):
    """Return message dict, or {} if missing/non-dict."""
    if not isinstance(o, dict):
        return {}
    m = o.get("message")
    return m if isinstance(m, dict) else {}


def _stop_details(o):
    """Return stop_details dict from a turn, or {} if missing/non-dict."""
    sd = _msg_dict(o).get("stop_details")
    return sd if isinstance(sd, dict) else {}


def _is_refusal(o):
    """Reword-proof + all-edges: structural via message.stop_reason=='refusal' OR
    stop_details.type=='refusal' (category-agnostic — fires on cyber, weapons, or any
    future category on day zero), OR a known text wording as a legacy fallback."""
    if not (isinstance(o, dict) and o.get("isApiErrorMessage")):
        return False
    m = _msg_dict(o)
    sd = _stop_details(o)
    return (
        m.get("stop_reason") == "refusal"
        or sd.get("type") == "refusal"
        or _sig(_text(o))
    )


def _refusal_category(o):
    return _stop_details(o).get("category")


STATE_FILE = os.path.expanduser("~/.claude/.fp-state.json")
GLOBAL_CLAUDE_MD = os.path.expanduser("~/.claude/CLAUDE.md")
# Resolve the bundled repair script: the plugin install dir when running as a
# plugin, else the original standalone skill path (identical behavior off-plugin).
_PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT")
SCRUB_SCRIPT = (
    os.path.join(
        _PLUGIN_ROOT, "skills/recover-from-false-positive/scripts/scrub_refusals.py"
    )
    if _PLUGIN_ROOT
    else os.path.expanduser(
        "~/.claude/skills/recover-from-false-positive/scripts/scrub_refusals.py"
    )
)

KNOWN_TRIGGER_CLASSES = {
    "sigstore": [
        "cosign",
        "slsa",
        "oidc",
        "attestation",
        "fulcio",
        "rekor",
        "in-toto",
        "provenance",
        "keyless",
    ],
    # strip-gemm, strip phase, docker fleet deploy with SSH all trigger mining class
    "mining": [
        "coinbase",
        "dev-fee",
        "blake3",
        "jit",
        "decrypt",
        "cpuid",
        "hashrate",
        "nonce",
        "pow",
        "armor",
        "strip-gemm",
        "strip_gemm",
        "gemm",
        "strip phase",
        "pearl-fuse",
        "fleet",
        "mining vocab",
        "mining software",
        "trigger class",
    ],
    "btcpay": [
        "lnd",
        "lndinit",
        "lightning",
        "wallet",
        "bitcoin",
        "bitcoind",
        "webhook",
        "channel",
        "openssl",
    ],
    "chaos": [
        "chaos",
        "teardown",
        "failover",
        "gauntlet",
        "raft",
        "leader",
        "election",
        "never-break",
    ],
    "subagent-vocab": [
        "security vulnerabilities",
        "anti-tamper",
        "honeypot",
        "tamper detection",
        "frida",
    ],
    "credential-enum": ["ncmec", "esp_key", "token", "secret", "credential"],
    # SSH + docker fleet deployment into multiple IPs co-located with mining vocab
    "fleet-ssh": [
        "stricthostkeychecking=no",
        "for ip in",
        "docker inspect",
        "docker pull",
        "192.168",
    ],
    # compiler-internals / warm-cache build acceleration — codegen dedup, generic
    # instantiation, walking a toolchain's own source (e.g. Go cmd/compile/noder)
    "build-accel": [
        "codegen",
        "noder",
        "cmd/compile",
        "share-generics",
        "generic instantiation",
        "redundant compil",
        "instantiation",
        "warm-cache",
        "first-build",
        "build acceleration",
        "extern-template",
        "incremental compile",
        "toolchain",
    ],
}


def _text(obj):
    """Extract all readable text from a turn object, including tool_use inputs and tool_result content."""
    if not isinstance(obj, dict):
        return ""
    m = obj.get("message", {})
    if not isinstance(m, dict):
        return ""
    c = m.get("content", "")
    parts = []
    if isinstance(c, str):
        parts.append(c)
    elif isinstance(c, list):
        for b in c:
            if not isinstance(b, dict):
                continue
            t = b.get("type", "")
            if t == "text":
                parts.append(b.get("text", ""))
            elif t == "tool_use":
                # extract the bash command or any string input fields
                inp = b.get("input", {})
                if isinstance(inp, dict):
                    for v in inp.values():
                        if isinstance(v, str):
                            parts.append(v)
                elif isinstance(inp, str):
                    parts.append(inp)
            elif t == "tool_result":
                rc = b.get("content", "")
                if isinstance(rc, str):
                    parts.append(rc)
                elif isinstance(rc, list):
                    for rb in rc:
                        if isinstance(rb, dict) and rb.get("type") == "text":
                            parts.append(rb.get("text", ""))
    return " ".join(parts)


def extract_trigger_text(transcript_path):
    """Return (trigger_text, categories): the text of the turn(s) that triggered each
    refusal (its parent) and the set of server categories that fired (cyber/weapons/…)."""
    try:
        lines = (
            open(transcript_path, encoding="utf-8", errors="surrogatepass")
            .read()
            .splitlines()
        )
    except Exception:
        return "", []

    uuid_map = {}
    error_parent_uuids = []
    categories = []

    for line in lines:
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("uuid"):
            uuid_map[o["uuid"]] = o
        if _is_refusal(o) and o.get("parentUuid"):
            error_parent_uuids.append(o["parentUuid"])
            c = _refusal_category(o)
            if c and c not in categories:
                categories.append(c)

    texts = []
    for puuid in error_parent_uuids:
        parent = uuid_map.get(puuid)
        if parent:
            texts.append(_text(parent))
    return "\n".join(texts), categories


def extract_vocab_clusters(text):
    """Return {class_name: [matched_terms]} for classes with >= 2 term hits."""
    text_lower = text.lower()
    found = {}
    for cls, terms in KNOWN_TRIGGER_CLASSES.items():
        hits = [t for t in terms if t in text_lower]
        if len(hits) >= 2:
            found[cls] = hits
    return found


def terms_in_file(filepath, terms):
    """Count how many terms appear in a file."""
    if not filepath or not os.path.exists(filepath):
        return 0
    try:
        content = open(filepath, encoding="utf-8", errors="ignore").read().lower()
        return sum(1 for t in terms if t in content)
    except Exception:
        return 0


def decode_slug(slug_dir):
    """Slug dir name → real filesystem path."""
    name = os.path.basename(slug_dir)
    if not name.startswith("-"):
        return None
    parts = name[1:].split("-")
    path = "/"
    i = 0
    while i < len(parts):
        matched = False
        for j in range(len(parts), i, -1):
            candidate = "-".join(parts[i:j])
            full = os.path.join(path, candidate)
            if os.path.isdir(full):
                path = full
                i = j
                matched = True
                break
        if not matched:
            path = os.path.join(path, "-".join(parts[i:]))
            break
    return path


def find_project_claude_md(project_path):
    if not project_path:
        return None
    for candidate in [
        os.path.join(project_path, "CLAUDE.md"),
        os.path.join(os.path.dirname(project_path), "CLAUDE.md"),
    ]:
        if os.path.exists(candidate):
            return candidate
    return None


def main():
    try:
        inp = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    transcript_path = inp.get("transcript_path", "")
    if not transcript_path or not os.path.exists(transcript_path):
        sys.exit(0)

    trigger_text, refusal_categories = extract_trigger_text(transcript_path)
    if not trigger_text:
        sys.exit(0)

    vocab_clusters = extract_vocab_clusters(trigger_text)
    if not vocab_clusters:
        # Unknown trigger class — still flag it with empty clusters
        pass

    project_path = decode_slug(os.path.dirname(transcript_path))
    project_claude_md = find_project_claude_md(project_path)

    # Classify each vocab cluster
    cross_project_classes = []
    project_only_classes = []
    for cls, terms in vocab_clusters.items():
        in_global = terms_in_file(GLOBAL_CLAUDE_MD, terms) >= 2
        if not in_global:
            cross_project_classes.append(cls)
        else:
            project_only_classes.append(cls)

    if not vocab_clusters:
        # Unknown class — flag as cross-project (needs edgar-morin to decide)
        classification = "unknown-needs-edgar-morin"
    elif cross_project_classes:
        classification = "both"
    else:
        classification = "project-only"

    state = {
        "session_id": inp.get("session_id"),
        "transcript_path": transcript_path,
        "project_path": project_path,
        "project_claude_md": project_claude_md,
        "vocab_clusters": vocab_clusters,
        "cross_project_classes": cross_project_classes,
        "project_only_classes": project_only_classes,
        "classification": classification,
        "refusal_categories": refusal_categories,
        "scrub_command": (
            f'bash "{os.environ.get("CLAUDE_PLUGIN_ROOT", "")}/scripts/run-python.sh" '
            f'"{SCRUB_SCRIPT}" --fix-active --apply'
            if os.environ.get("CLAUDE_PLUGIN_ROOT")
            else f'python3 "{SCRUB_SCRIPT}" --fix-active --apply'
        ),
        "trigger_text_snippet": trigger_text[:500],
    }

    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

    # Desktop notification (best-effort; subprocess.run with list avoids shell injection)
    try:
        subprocess.run(
            [
                "notify-send",
                "Claude Code",
                "API output error detected. Continue session to auto-recover.",
            ],
            capture_output=True,
            timeout=3,
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
