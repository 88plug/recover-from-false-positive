# Changelog

## 2026.7.14

- **Fix the detector going stale (the bug that silently no-op'd a whole recovery).**
  The classifier reworded its hard-failure from *"appears to violate our Usage
  Policy"* to *"…safeguards flagged this message for a cybersecurity topic"* + a
  `claude.com/form/cyber-use-case` link. Every detection path hard-coded the old
  string, so `scrub_refusals.py` (all modes) and `detect-false-positive.py` reported
  **clean** on real refusals. Now both match a `SIGNATURES` list + loose fallback,
  gated on `isApiErrorMessage:true` + an "API Error" prefix.
- `--fix-active` now also scrubs the session's **subagent shards**
  (`<session>/subagents/**/*.jsonl`, incl. `workflows/wf_*/`), with their own
  backup — it previously touched only the main session file.
- `detect-false-positive.py` learns a `build-accel` trigger class (compiler
  internals / warm-cache build acceleration: codegen dedup, generic/share-generics
  instantiation, walking a toolchain's own source).
- `tests/smoke.sh` step 5: a signature regression test that detects + scrubs +
  re-stitches every known wording and holds a negative control — a known signature
  can no longer go stale unnoticed.
- SKILL.md documents both error wordings, the drift risk, and the subagent-shard
  coverage.

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
