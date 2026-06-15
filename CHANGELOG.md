# Changelog

## 2026.6.3

- Recovery hook now surfaces the exact un-stick command (`/model claude-sonnet-4-6`
  + re-send, or new session) when a generation is re-blocked — Claude Code has no
  in-place auto-fallback for this error, so the switch is a manual user action.
- README documents the auto-fallback limitation and the two out-of-band
  auto-recovery options (local proxy at ANTHROPIC_BASE_URL; headless is_error loop).

## 2026.6.2

- Bundle the full detection + recovery + prevention hook pipeline:
  `detect-false-positive.py` (StopFailure), `inject-recovery-context.sh`
  (UserPromptSubmit), `inject-subagent-framing.sh` and
  `inject-claudemd-into-subagents.sh` (SubagentStart).
- Skill declares `allowed-tools` so the repair script and CLAUDE.md edits run
  without per-call permission prompts during recovery.
- Add `tests/smoke.sh` (run in CI).

Enabled by default (the auto-detect/recovery hooks are the whole point); the
install panel lists every hook so activation is transparent, not silent.

## 2026.6.1

- Initial plugin: the `recover-from-false-positive` skill, the
  `scrub_refusals.py` chain-re-stitching log surgeon, and per-repo `CLAUDE.md`
  prevention templates.
