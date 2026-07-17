# Installation

## Marketplace (recommended)

```text
/plugin marketplace add 88plug/claude-code-plugins
/plugin install recover-from-false-positive@88plug
```

Claude Code lists the bundled hooks in the install panel. They run automatically
once the plugin is enabled.

| Hook event | Script | Role |
| --- | --- | --- |
| `StopFailure` | `hooks/detect-false-positive.py` | Detect classifier trip; write `~/.claude/.fp-state.json` |
| `UserPromptSubmit` | `hooks/inject-recovery-context.sh` | Inject recovery / handoff recap / neutral framing |
| `SubagentStart` | `hooks/inject-subagent-framing.sh` | Neutral generation guideline for every subagent |
| `SubagentStart` (`Explore` \| `Plan`) | `hooks/inject-claudemd-into-subagents.sh` | Re-inject project + global `CLAUDE.md` |

Hooks are read-only except for:

- `~/.claude/.fp-state.json` — detect writes, recovery consumes
- `~/.claude/.fp-reinject-<slug>.md` — handoff stages, recovery injects once
- `~/.claude/.fp-observed-signatures.log` — novel wordings logged by the scrubber

Python hooks run through `scripts/run-python.sh` so the plugin resolves a real
interpreter without assuming a system `python3` path.

## Local checkout (development)

```bash
git clone https://github.com/88plug/recover-from-false-positive.git
cd recover-from-false-positive
claude --plugin-dir "$PWD"
```

Smoke test (no live session required):

```bash
bash tests/smoke.sh
```

## Skill auto-trigger

The skill `recover-from-false-positive` is **proactive**. It fires when Usage
Policy / cyber-safeguards error text appears anywhere — user message, error
output, tool result, or workflow failure — or when you ask to scrub session
logs / recover from a false positive.

!!! warning "Never recover inside the stuck session"
    Run the scrubber from a **clean sibling terminal**. Invoking the skill
    inside the stuck session injects dense classifier vocabulary and can
    cascade another trip.

## Requirements

- Claude Code with plugin support
- Python 3 (via `scripts/run-python.sh` for hooks)
- Write access under `~/.claude/` for backups and state files

Optional: an `edgar-morin` / `start_complex_reasoning` tool for the
project-vs-global classification step. Without it, use the skill's manual
three-way matrix in [Scrub workflow](scrub-workflow.md#prevention-after-cleanup).
