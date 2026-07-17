# Troubleshooting

## Cascade: recovery makes it worse

**Symptom:** invoking `/recover` or the skill *inside* the stuck session causes
another immediate trip.

**Cause:** the skill body is itself dense (signatures, domain classes,
classifier vocabulary). Near-threshold sessions re-fire on that injection.

**Fix:**

1. Open a **clean sibling terminal**.
2. Run `scrub_refusals.py --fix-active --apply` (or `--desaturate --file … --apply`).
3. Prefer `--handoff` + `/clear` + `continue` over generating long recovery prose
   in the stuck session.
4. Keep the next user prompt short and domain-neutral.

## Session still trips after scrubbing refusals

**Symptom:** refusals are gone; every new prompt (even trivial) still dies.

**Cause:** the client already filters `isApiErrorMessage` before send. Poison
is dense **work** content after the last compaction boundary.

**Fix:**

```bash
python3 …/scrub_refusals.py --desaturate --file <session.jsonl> --apply
# then either handoff + /clear + continue, or /quit and:
claude --resume <session-id> "continue"
```

If it still trips: `/clear` or start a **new session**. Long saturated context
often will not recover by resume alone.

## `No conversation found with session ID: …`

Claude Code uses `uv_fs_realpath` — it resolves symlinks in the cwd before
building the project slug. If `~/project` is a symlink to a longer path, the
slug may not match the directory you scrubbed.

**Fix:** alias the resolved slug to the original:

```bash
ln -s ~/.claude/projects/<original-slug> \
      ~/.claude/projects/<resolved-slug>
```

Find the resolved path with `readlink -f ~/project-dir`, then replace `/` with
`-` and prefix `-`.

## `Usage credits required for 1M context`

Very long sessions (hundreds of turns / large on-disk size) can require 1M
context credits to load for resume. The `--model` flag does **not** override
this — the session size itself trips the gate.

- Enable 1M credits at `claude.ai/settings/usage`
- Or start a new session / use handoff + `/clear` to drop the window

## `No deferred tool marker found`

`--resume` alone (no prompt) only works when the session was paused mid-tool-use.
After a false-positive removal the session usually ends at a normal state.

```bash
claude --resume <session-id> "continue"
```

## Disk edit had no effect

You edited the `.jsonl` while the Claude Code process was still running. The
in-memory message array is authoritative until restart.

**Fix:** `/quit` then `claude --resume <id> "continue"`, or use the
clear-then-reinject path ([Scrub workflow](scrub-workflow.md#clear-then-reinject-no-close-no-model-switch)).

## Model switch did not help

The classifier is family-wide for this failure mode. Switching models is not
the primary recovery path. Prefer de-saturation, handoff + `/clear`, or a
fresh session. Prevention (leaner windows, chunked narration, per-domain
sessions) is what stops recurrence.

Claude Code has **no in-place auto-fallback** to another model on this error.
`fallbackModel` covers availability (overload / unavailable) only — not policy
refusals. No hook can switch the active model.

Optional out-of-band automation (not shipped by this plugin):

- Local proxy at `ANTHROPIC_BASE_URL` that retries refused requests on a
  fallback model before the transcript is poisoned
- Headless loop around `claude -p … --output-format json` that relaunches with
  another model on `result.is_error` / signature match (loop-guarded)

## Detector reports clean but you still saw a block

Possible causes:

1. **New wording** not yet in `SIGNATURES` — structural detection should still
   catch `stop_reason: refusal`. Run:

   ```bash
   python3 …/scrub_refusals.py --selfcheck
   ```

   Check `~/.claude/.fp-observed-signatures.log` for novel snippets.

2. **Wrong project slug** — you scrubbed a different path than the live session
   (symlink / multi-cwd). Use `--latest` and confirm the file path.

3. **Not an API classifier refusal** — other hard failures (auth, billing,
   rate limit) are out of scope for this tool.

## Audit exposure before resuming old work

```bash
python3 …/scrub_refusals.py --backtest
```

Lists sessions whose resume window is dense **and** large enough to re-trip.
De-saturate those before `--resume`.

## Prevention config (keep sessions under the threshold)

From `references/never-close.md` — apply once; takes effect on the next launch:

```bash
# shell profile (env is read at launch, not mid-session)
export CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=8000
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=120000
export CLAUDE_CODE_AUTO_COMPACT_WINDOW=0.6
```

```json
{ "autoCompactEnabled": true, "precomputeCompactionEnabled": true }
```

Also:

- One dense domain per session (do not mix supply-chain + payment + mining
  vocab in one long context)
- After editing a trigger file: one-line confirm, no mechanism re-narration
- Chunk: one artifact per turn
- Cap large file reads; let microcompact clear old tool results early

## Manual surgical remove

When `--fix-active` cannot auto-detect the trigger user message, use a
targeted Python snippet (adjust path + trigger text). Always backup first.
Full example: skill body under
`skills/recover-from-false-positive/SKILL.md` ("Manual surgical remove").

Core rules:

1. Match refusals via structural `stop_reason` / signature list — not one string
2. Collect uuids to remove (refusal + trigger user turn)
3. Re-stitch orphans' `parentUuid` up the remove map
4. Write only if dangling count does not increase
