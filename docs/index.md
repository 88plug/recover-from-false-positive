# Recover From False Positive

**Recover a Claude Code session when Anthropic's API output classifier
false-positives on legitimate engineering work — and stop it happening again.**

[![plugin-validate](https://github.com/88plug/recover-from-false-positive/actions/workflows/plugin-validate.yml/badge.svg)](https://github.com/88plug/recover-from-false-positive/actions/workflows/plugin-validate.yml)
[![License: FSL-1.1-ALv2](https://img.shields.io/badge/license-FSL--1.1--ALv2-blue?style=flat)](https://github.com/88plug/recover-from-false-positive/blob/main/LICENSE)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-8A2BE2?style=flat)](https://github.com/88plug/claude-code-plugins)
[![Docs](https://img.shields.io/badge/docs-online-2ea44f?style=flat)](https://88plug.github.io/recover-from-false-positive/)

A Claude Code plugin for the hard request failure that kills a turn
mid-generation on dense-but-legitimate topics. It is a JSON-aware `.jsonl` log
surgeon: it removes poisoned turns, re-stitches the session so `--resume` works,
de-saturates the live window when needed, and installs a framing guard so the
next session does not re-trip.

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


Full details: [Installation](https://github.com/88plug/recover-from-false-positive/blob/main/installation.md).

## Quickstart

Run recovery from a **clean sibling terminal**, never inside the stuck session
(invoking the skill there injects dense vocab and can cascade).

```bash
# Fix the session that just fired (dry-run first; add --apply to write).
# Removes trigger + refusal; de-saturates when the session is saturated.
python3 "${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py" \
  --fix-active --apply
```

Then keep going **in place** (no model switch):

```bash
# Stage a de-saturated continuity recap for the stuck session.
python3 "${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py" \
  --handoff --file ~/.claude/projects/<slug>/<session-id>.jsonl --apply
```

In the stuck session: `/clear`, then type `continue`. The `UserPromptSubmit`
hook re-injects the recap once.

!!! note
    Detect-only by default. The script makes a backup tarball before writing
    and refuses any change that would add a dangling `parentUuid`.

## What it does

| Capability | What you get |
| --- | --- |
| Fix the active session | Removes only the triggering message + refusal turn (and de-saturates when saturated) so the session can resume |
| De-saturate | Surgical drop of vocabulary-dense turns in the resume window (default); optional `--stub` keeps length |
| Clear then reinject | `--handoff` stages a recap; `/clear` + `continue` keeps the same session without closing |
| Clean old sessions | Dry-run, then `--apply` machine-wide; chain-safe so old sessions stay resumable |
| Prevent recurrence | Classifies the trigger and installs a `CLAUDE.md` framing guard with real files and vocab |

## Why not `sed -i '/violate/d'`

Claude Code `.jsonl` logs are a linked list. Every turn carries a `uuid` and a
`parentUuid`. Deleting a line with `sed` orphans the next turn and breaks
`claude --resume`. This script **re-stitches** the chain — rewiring each orphan's
`parentUuid` up to the removed turn's parent — *before* dropping lines.

## Docs map

- [Installation](https://github.com/88plug/recover-from-false-positive/blob/main/installation.md) — marketplace install, hooks, local checkout
- [When it triggers](https://github.com/88plug/recover-from-false-positive/blob/main/when-triggers.md) — mechanism, signatures, known domains
- [Scrub workflow](https://github.com/88plug/recover-from-false-positive/blob/main/scrub-workflow.md) — fix-active, desaturate, handoff, CLI flags
- [Troubleshooting](https://github.com/88plug/recover-from-false-positive/blob/main/troubleshooting.md) — resume failures, cascade, audits

## What it bundles

- **Skill** `recover-from-false-positive` with `scripts/scrub_refusals.py` (the
  chain-re-stitching log surgeon) and `references/` (prevention templates +
  never-close config).
- **Four hooks** (`hooks/hooks.json`):
  - `detect-false-positive.py` — `StopFailure`
  - `inject-recovery-context.sh` — `UserPromptSubmit`
  - `inject-subagent-framing.sh` — `SubagentStart` (all agents)
  - `inject-claudemd-into-subagents.sh` — `SubagentStart` (`Explore` / `Plan`)

## Automatic pipeline

Once the plugin is enabled:

1. **`StopFailure` → detect** — pure Python reads the live transcript, extracts
   trigger vocabulary, pre-classifies project-only vs cross-project, writes
   `~/.claude/.fp-state.json`. No model generation, so it cannot re-trip.
2. **`UserPromptSubmit` → recover** — if that state file exists for this
   session, recovery steps are injected and the state is consumed. Always
   injects a short neutral generation guideline. Also serves the
   clear-then-reinject path when a recap was staged.
3. **`SubagentStart` → frame** — every subagent gets the neutral guideline;
   `Explore` / `Plan` also get full `CLAUDE.md` re-injected (they skip it by
   default).

## License

[FSL-1.1-ALv2](https://github.com/88plug/recover-from-false-positive/blob/main/LICENSE)
— Functional Source License, converts to Apache-2.0.

## Features

| Feature | What you get |
| --- | --- |
| Active session recovery | Remove only the trigger message + refusal; resume as if neither happened |
| Machine-wide cleanup | Dry-run then `--apply` to scrub accumulated refusal turns; chain-safe |
| Recurrence guard | Classify the trigger; install a `CLAUDE.md` framing section naming real files and vocab |
| Hands-free hooks | Detect on `StopFailure`, inject recovery on next prompt, frame every subagent |
| JSON-aware repair | Re-stitch `uuid` / `parentUuid` — never a blind `sed` delete |
