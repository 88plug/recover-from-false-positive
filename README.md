# recover-from-false-positive

A Claude Code plugin that recovers a session after Anthropic's **API output
classifier** false-positives on legitimate engineering work — the hard request
failure:

> *API Error: ... appears to violate our Usage Policy. This request triggered
> cyber-related safeguards.*

The classifier scores the assistant's tokens **at generation time**. On certain
dense topics (commercial mining software, software supply-chain / sigstore
tooling, payment automation, infra-ops testing, security infrastructure) it
fires on legitimate output and kills the turn mid-generation. That refusal then
gets written into the `.jsonl` session log — so on `--resume` the model re-reads
its own "refusal" and imitates it, and spawned subagents do the same. The log is
poisoned.

This plugin fixes both halves: it cleans the log, and it stops the next trip.

## Why this is not `sed -i '/violate/d'`

Claude Code `.jsonl` logs are a **linked list** — every turn carries a `uuid`
and a `parentUuid` pointing at the previous turn. Deleting a line with `sed`
orphans the next turn (its `parentUuid` now points at a uuid that no longer
exists), which can break `claude --resume`. This plugin's script **re-stitches**
the chain — rewiring each orphan's `parentUuid` up to the removed turn's parent —
*before* dropping lines, makes a backup tarball first, and refuses to write any
file where the scrub would increase the count of dangling pointers.

## What it does

- **Operation A — fix the active session.** Removes *only* the triggering user
  message + the refusal response from the session that just fired, re-stitches
  the chain, and the session resumes as if those two turns never happened.
- **Operation B — clean old sessions.** Dry-run, then `--apply` to remove
  accumulated refusal turns machine-wide. Chain-safe; old sessions stay
  resumable.
- **Prevention.** After cleanup, classifies the trigger (project-only vs
  cross-project) and installs a `## Cyber-safeguard false positives — READ FIRST`
  guard in the right `CLAUDE.md`, naming the actual trigger files and vocabulary
  so the next session writes less densely and doesn't re-trip.

## What it bundles

One skill (`recover-from-false-positive`) with:

- `scripts/scrub_refusals.py` — the JSON-aware, chain-re-stitching log surgeon
  (detect-only by default; `--apply` backs up first and self-verifies).
- `references/prevention.md` — per-repo CLAUDE.md guard templates
  (supply-chain/sigstore, mining/PoW, BTCPay/Lightning, chaos/infra-ops).

No MCP, no hooks — it teaches a recovery method and ships one auditable script.

## Install

```
/plugin marketplace add 88plug/recover-from-false-positive
/plugin install recover-from-false-positive@recover-from-false-positive
```

Or via the 88plug marketplace:

```
/plugin marketplace add 88plug/claude-code-plugins
/plugin install recover-from-false-positive@88plug
```

## Usage

The skill auto-triggers when the Usage-Policy / cyber-safeguards error text
appears anywhere in the conversation. To run the surgeon directly:

```bash
# fix only the session that just fired
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py --fix-active

# clean all sessions: dry-run, inspect delta=0, then apply
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py --apply
```

## License

[FSL-1.1-ALv2](LICENSE.md) — Functional Source License, converts to Apache-2.0.
