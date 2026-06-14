<div align="center">

# recover-from-false-positive

**Recover a Claude Code session when Anthropic's API output classifier false-positives on legitimate engineering work — and stop it happening again.**

[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat)](LICENSE.md)
[![Version](https://img.shields.io/badge/version-2026.6.1-green?style=flat)](.claude-plugin/plugin.json)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2?style=flat)](https://github.com/88plug/claude-code-plugins)

</div>

A Claude Code plugin for the hard request failure *"appears to violate our Usage
Policy / this request triggered cyber-related safeguards"* — the one that kills a
turn mid-generation on dense-but-legitimate topics (mining software,
supply-chain/sigstore tooling, payment automation, infra-ops, security infra).
It's a JSON-aware `.jsonl` log surgeon: it removes the poisoned turns, re-stitches
the session so `--resume` works, and installs a guard so the next session doesn't
re-trip.

## Install

```text
/plugin marketplace add 88plug/claude-code-plugins
/plugin install recover-from-false-positive@88plug
```

## Quickstart

The skill auto-triggers the moment the Usage-Policy / cyber-safeguards error text
appears anywhere in the conversation. To run the surgeon directly:

```bash
# Fix ONLY the session that just fired (removes the trigger message + refusal,
# re-stitches the chain, backs up first). Resume cleanly afterward.
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py --fix-active
```

> [!NOTE]
> Detect-only by default. The script makes a backup tarball before writing and
> refuses any change that would add a dangling `parentUuid`.

## What it does

- **Fix the active session** — removes only the triggering message + refusal turn
  from the session that just fired, so it resumes as if neither happened.
- **Clean old sessions** — dry-run, then `--apply` to remove accumulated refusal
  turns machine-wide. Chain-safe; old sessions stay resumable.
- **Prevent recurrence** — classifies the trigger and installs a
  `## Cyber-safeguard false positives — READ FIRST` guard in the right
  `CLAUDE.md`, naming the actual trigger files and vocabulary.

## Why not `sed -i '/violate/d'`

Claude Code `.jsonl` logs are a linked list — every turn carries a `uuid` and a
`parentUuid` pointing at the previous turn. Deleting a line with `sed` orphans the
next turn (its `parentUuid` points at a uuid that no longer exists), which breaks
`claude --resume`. This script **re-stitches** the chain — rewiring each orphan's
`parentUuid` up to the removed turn's parent — *before* dropping lines.

## Clean all old sessions

```bash
# dry-run first; confirm delta=0 for every file, then apply
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py --apply
```

## What it bundles

- `scripts/scrub_refusals.py` — the chain-re-stitching log surgeon.
- `references/prevention.md` — per-repo `CLAUDE.md` guard templates
  (supply-chain/sigstore, mining/PoW, BTCPay/Lightning, chaos/infra-ops).

No MCP, no hooks — one skill and one auditable script.

## License

[FSL-1.1-ALv2](LICENSE.md) — Functional Source License, converts to Apache-2.0.
