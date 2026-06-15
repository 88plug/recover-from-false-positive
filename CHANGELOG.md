# Changelog

## 2026.6.2

- Bundle the full detection + recovery + prevention hook pipeline:
  `detect-false-positive.py` (StopFailure), `inject-recovery-context.sh`
  (UserPromptSubmit), `inject-subagent-framing.sh` and
  `inject-claudemd-into-subagents.sh` (SubagentStart).
- Skill declares `allowed-tools` so the repair script and CLAUDE.md edits run
  without per-call permission prompts during recovery.
- Ship `defaultEnabled: false` — the hooks inject context every turn/subagent, so
  install does not silently activate them; enable the plugin to turn them on.
- Add `tests/smoke.sh` (run in CI).

## 2026.6.1

- Initial plugin: the `recover-from-false-positive` skill, the
  `scrub_refusals.py` chain-re-stitching log surgeon, and per-repo `CLAUDE.md`
  prevention templates.
