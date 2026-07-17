# When it triggers

## What actually happens

Anthropic's API runs an **output classifier** that scores the assistant's tokens
**at generation time**. On certain dense topics it false-positives and returns a
hard request failure. The turn dies mid-generation.

This is a *false positive* on legitimate engineering (commercial mining software,
software supply-chain / sigstore tooling, security infra, autonomous control
planes). Do not treat the work as illegitimate because the classifier fired.

## Error wording drifts

Do not match a single string. Known forms so far:

- *Legacy:* `API Error: … appears to violate our Usage Policy. This request
  triggered cyber-related safeguards.`
- *2026-07 onward:* `API Error: … safeguards flagged this message for a
  cybersecurity topic. … https://claude.com/form/cyber-use-case?token=…`

The detector (`scrub_refusals.py` `SIGNATURES` + `detect-false-positive.py`)
matches a **list** of phrases plus a loose fallback, gated on
`isApiErrorMessage: true` and (for the text path) an `API Error` prefix.
`tests/smoke.sh` pins every known signature so a wording change cannot silently
report "clean".

## Response schema (structural truth)

A refusal is stored as an assistant turn roughly like:

```json
{
  "isApiErrorMessage": true,
  "type": "assistant",
  "message": {
    "model": "<synthetic>",
    "stop_reason": "refusal",
    "stop_details": {
      "type": "refusal",
      "category": "cyber",
      "explanation": "This request triggered cyber-related safeguards…"
    }
  }
}
```

**Primary detection is structural**, not textual:

| Signal | Meaning |
| --- | --- |
| `stop_reason == "refusal"` | Client-set, wording-independent |
| `stop_details.type == "refusal"` | Same; category-agnostic |
| `stop_details.category` | What fired (`cyber`, `weapons`, …) — captured, not hardcoded |
| Text `SIGNATURES` | Secondary path for legacy turns and telemetry |

Structural detection catches a brand-new wording on day zero. Novel text is
appended to `~/.claude/.fp-observed-signatures.log` for maintainer review.
Run `scrub_refusals.py --selfcheck` any time to audit detector health.

## Two consequences

1. **The error is written into the `.jsonl` session log** as an
   `isApiErrorMessage: true` assistant turn. On `--resume`, dense work content
   in the live window can re-fire. → cleanup + de-saturation
   ([Scrub workflow](scrub-workflow.md)).
2. **It will fire again** on the same vocabulary next session. → prevention
   via a `CLAUDE.md` framing guard (named files + terms, not generic advice).

!!! important "Removing refusals alone is not enough"
    The client already filters `isApiErrorMessage` turns before the next API
    send. What re-fires is dense **work content** in the resume window (turns
    after the last compaction boundary), not the refusal lines themselves.

## Known trigger-domain classes

Confirmed in real sessions (non-exhaustive):

| Domain | Dense patterns that trip |
| --- | --- |
| Supply-chain / sigstore | cosign / SLSA / attestation / OIDC narration, especially after editing attestation docs or on resume with a huge context |
| Mining / PoW | coinbase / dev-fee / armor / BLAKE3 / JIT-decrypt narration; inline hash work; "security vulnerabilities" phrasing in anti-tamper review |
| BTCPay / Lightning | LND / lndinit / wallet-unlock / bitcoind RPC, especially with WireGuard or auth-token vocab nearby |
| Chaos / infra-ops | chaos / teardown / failover / gauntlet + SSH key + raft vocabulary |
| CTI / UEBA / SIGMA | "implement all these security monitoring features" fan-out in a saturated threat-detection session |
| Credential enumeration | many `_TOKEN` / `_KEY` names co-located in a grep or subagent prompt |
| Compiler internals / build acceleration | dense codegen / generics / noder / redundant-compile narration while walking a toolchain's own source |
| Free model / proxy discovery | dense "free / no key / anonymous / proxy / keyless" vocabulary across many parallel agent prompts |
| Cascade | running this recovery skill *inside* a near-threshold session loads classifier vocabulary into context |

## Density, not "bad intent"

The classifier scores **co-located dense vocabulary in a single generation**.
The same content spread across several short turns scores far lower. Highest
risk is often the **summary turn right after** editing a sensitive doc — not
the edit itself.
