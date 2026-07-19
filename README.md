<div align="center">

# Recover From False Positive

**Claude Code session recovery for Anthropic API output-classifier false positives — JSON-aware `.jsonl` log repair, parentUuid re-stitch, and CLAUDE.md framing guards.**

[![plugin-validate](https://github.com/88plug/recover-from-false-positive/actions/workflows/plugin-validate.yml/badge.svg)](https://github.com/88plug/recover-from-false-positive/actions/workflows/plugin-validate.yml)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-online-2ea44f?style=flat)](https://88plug.github.io/recover-from-false-positive/)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2?style=flat)](https://github.com/88plug/claude-code-plugins)
[![DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/88plug/recover-from-false-positive)

</div>

Recover Claude Code sessions after an Anthropic API content-filter false positive — the hard failure *"appears to violate our Usage Policy"* / *"cyber-related safeguards"* that kills a turn mid-generation on dense-but-legitimate engineering. This Claude Code plugin is a JSON-aware `.jsonl` log surgeon for session management: it removes the triggering message and refusal turn, re-stitches the `parentUuid` chain so `claude --resume` works, cleans accumulated refusals across old sessions, and installs a per-repo `CLAUDE.md` framing guard so coding agents and LLM workflows stop re-tripping.

Built for developers whose AI agents hit the classifier on legitimate work — mining/PoW software, supply-chain/sigstore tooling, payment automation, chaos/infra-ops, security infra — not for bypassing policy on real abuse.

## Install

### Claude Code

```text
/plugin marketplace add 88plug/claude-code-plugins
/plugin install recover-from-false-positive@88plug
```

### Grok Build

```text
grok plugin marketplace add 88plug/claude-code-plugins
grok plugin install recover-from-false-positive@88plug --trust
```


## Quickstart

The skill auto-triggers when Usage-Policy / cyber-safeguards error text appears in the conversation. To run the log surgeon yourself:

```bash
# Fix ONLY the session that just fired (removes trigger + refusal,
# re-stitches the chain, backs up first). Resume cleanly afterward.
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py --fix-active
```

> [!NOTE]
> Detect-only by default. The script makes a backup tarball before writing and
> refuses any change that would add a dangling `parentUuid`.

## Features

| Feature | What you get |
| --- | --- |
| Active session recovery | Remove only the trigger message + refusal; resume as if neither happened |
| Machine-wide cleanup | Dry-run then `--apply` to scrub accumulated refusal turns; chain-safe |
| Recurrence guard | Classify the trigger; install a `CLAUDE.md` framing section naming real files and vocab |
| Hands-free hooks | Detect on `StopFailure`, inject recovery on next prompt, frame every subagent |
| JSON-aware repair | Re-stitch `uuid` / `parentUuid` — never a blind `sed` delete |

## What it does

- **Fix the active session** — removes only the triggering message + refusal turn from the session that just fired, so it resumes cleanly.
- **Clean old sessions** — dry-run, then `--apply` to remove accumulated refusal turns machine-wide. Chain-safe; old sessions stay resumable.
- **Prevent recurrence** — classifies the trigger and installs a `## Cyber-safeguard false positives — READ FIRST` guard in the right `CLAUDE.md`, naming the actual trigger files and vocabulary.

## How the automatic pipeline works

Bundled hooks make recovery and prevention hands-free:

- **`StopFailure` → detect** — when a turn dies on the classifier, a pure-Python hook reads the live transcript, extracts trigger vocabulary, pre-classifies it (project-only vs cross-project), and writes `~/.claude/.fp-state.json`. No model generation, so it cannot re-trip.
- **`UserPromptSubmit` → recover** — on your next prompt, if that state file exists, recovery steps (repair the log, classify with edgar-morin, update the right `CLAUDE.md`) are injected and the state is consumed. Always injects a short neutral generation guideline.
- **`SubagentStart` → frame** — every subagent gets the neutral guideline so spawned prompts do not trip; `Explore`/`Plan` also get full `CLAUDE.md` re-injected (they skip it by default).

## Why not `sed -i '/violate/d'`

Claude Code `.jsonl` logs are a linked list — every turn carries a `uuid` and a `parentUuid` pointing at the previous turn. Deleting a line with `sed` orphans the next turn (its `parentUuid` points at a uuid that no longer exists), which breaks `claude --resume`. This script **re-stitches** the chain — rewiring each orphan's `parentUuid` up to the removed turn's parent — *before* dropping lines. That is real json-repair / data-recovery for session debugging, not a text filter.

## Clean all old sessions

```bash
# dry-run first; confirm delta=0 for every file, then apply
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py
python3 ${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py --apply
```

## What it bundles

- A **skill** (`recover-from-false-positive`) with `scripts/scrub_refusals.py` — the chain-re-stitching log surgeon — and `references/prevention.md`, per-repo `CLAUDE.md` guard templates (supply-chain/sigstore, mining/PoW, BTCPay/Lightning, chaos/infra-ops).
- Four **hooks** (`hooks/hooks.json`): `detect-false-positive.py` (StopFailure), `inject-recovery-context.sh` (UserPromptSubmit), `inject-subagent-framing.sh` and `inject-claudemd-into-subagents.sh` (SubagentStart).

> [!NOTE]
> Hooks run automatically once the plugin is enabled (Claude Code lists them in the install panel). They are read-only except for `~/.claude/.fp-state.json`, which the detect hook writes and the recovery hook consumes. The `edgar-morin`/`start_complex_reasoning` classification step expects that reasoning tool; without it, fall back to the skill's manual classification matrix.

## Recovery and the auto-fallback limitation

> [!IMPORTANT]
> Claude Code has **no in-place auto-fallback to another model** on this error.
> Its only refusal auto-fallback is hardwired Fable 5 → Opus; `fallbackModel`
> covers availability errors (overload/unavailable) only — not policy refusals;
> and no hook can switch the active model. A trip ends the turn; the switch is
> a manual user action.

When a turn is blocked, the un-stick path is:

1. This plugin's detect hook repairs the poisoned log so `--resume` stays safe.
2. Run **`/model claude-sonnet-4-6`** and re-send — a same-generation, lower-risk model often clears it.
3. Or start a **new session** — context saturation is the usual real cause.

For fully automatic recovery (no manual step), two out-of-band options work — opt in yourself; this plugin does not ship them by default:

- **Local proxy** at `ANTHROPIC_BASE_URL` that retries a refused request on the next model in a fallback chain (e.g. Opus 4.6 → Sonnet 4.6), transparent to the CLI and before the transcript is poisoned. Best for interactive use.
- **Headless loop** for autonomous runs: wrap `claude -p … --output-format json`, detect `result.is_error` / the Usage-Policy string, and relaunch with `--model claude-sonnet-4-6` (loop-guarded to avoid runaway relaunches).

## Docs

Full workflow, trigger domains, and troubleshooting: [recover-from-false-positive docs](https://88plug.github.io/recover-from-false-positive/).

## License

[FSL-1.1-ALv2](LICENSE) — Functional Source License, converts to Apache-2.0.
