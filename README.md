<div align="center">

# recover-from-false-positive

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/88plug/recover-from-false-positive)

**Recover a Claude Code session when Anthropic's API output classifier false-positives on legitimate engineering work — and stop it happening again.**

[![plugin-validate](https://github.com/88plug/recover-from-false-positive/actions/workflows/plugin-validate.yml/badge.svg)](https://github.com/88plug/recover-from-false-positive/actions/workflows/plugin-validate.yml)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat)](LICENSE.md)
[![Version](https://img.shields.io/badge/version-2026.6.3-green?style=flat)](.claude-plugin/plugin.json)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2?style=flat)](https://github.com/88plug/claude-code-plugins)
[![Docs](https://img.shields.io/badge/docs-online-2ea44f?style=flat)](https://88plug.github.io/recover-from-false-positive/)

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

## How the automatic pipeline works

The bundled hooks make recovery and prevention hands-free:

- **`StopFailure` → detect** — when a turn dies on the classifier, a pure-Python
  hook reads the live transcript, extracts the trigger vocabulary, pre-classifies
  it (project-only vs cross-project), and writes `~/.claude/.fp-state.json`. No
  model generation, so it can't itself re-trip.
- **`UserPromptSubmit` → recover** — on your next prompt, if that state file
  exists, the recovery steps (repair the log, classify with edgar-morin, update
  the right `CLAUDE.md`) are injected and the state file is consumed. It also
  always injects a short neutral generation guideline.
- **`SubagentStart` → frame** — every subagent gets the neutral generation
  guideline so spawned prompts don't trip; `Explore`/`Plan` additionally get the
  full `CLAUDE.md` re-injected (they skip it by default).

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

- A **skill** (`recover-from-false-positive`) with `scripts/scrub_refusals.py` —
  the chain-re-stitching log surgeon — and `references/prevention.md`, per-repo
  `CLAUDE.md` guard templates (supply-chain/sigstore, mining/PoW, BTCPay/Lightning,
  chaos/infra-ops).
- Four **hooks** (`hooks/hooks.json`): `detect-false-positive.py` (StopFailure),
  `inject-recovery-context.sh` (UserPromptSubmit), `inject-subagent-framing.sh`
  and `inject-claudemd-into-subagents.sh` (SubagentStart).

> [!NOTE]
> The hooks run automatically once the plugin is enabled (Claude Code lists them
> in the install panel). They are read-only except for `~/.claude/.fp-state.json`,
> which the detect hook writes and the recovery hook consumes. The
> `edgar-morin`/`start_complex_reasoning` classification step expects that
> reasoning tool to be available; without it, fall back to the skill's manual
> classification matrix.

## Recovery & the auto-fallback limitation

> [!IMPORTANT]
> Claude Code has **no in-place auto-fallback to another model** on this error.
> Its only refusal auto-fallback is hardwired Fable 5 → Opus; `fallbackModel`
> covers availability errors (overload/unavailable) only — not policy refusals;
> and no hook can switch the active model. So a trip ends the turn, and the
> switch is a manual user action.

When a turn is blocked, the un-stick path is:

1. This plugin's detect hook repairs the poisoned log so `--resume` stays safe.
2. Run **`/model claude-sonnet-4-6`** and re-send — a same-generation, lower-risk
   model often clears it.
3. Or start a **new session** — context saturation is the usual real cause.

For fully automatic recovery (no manual step), two out-of-band options work — opt
in yourself, this plugin does not ship them by default:

- **Local proxy** at `ANTHROPIC_BASE_URL` that retries a refused request on the
  next model in a fallback chain (e.g. Opus 4.6 → Sonnet 4.6), transparent to the
  CLI and before the transcript is poisoned. Best for interactive use.
- **Headless loop** for autonomous runs: wrap `claude -p … --output-format json`,
  detect `result.is_error` / the Usage-Policy string, and relaunch with
  `--model claude-sonnet-4-6` (loop-guarded to avoid runaway relaunches).

## License

[FSL-1.1-ALv2](LICENSE.md) — Functional Source License, converts to Apache-2.0.
