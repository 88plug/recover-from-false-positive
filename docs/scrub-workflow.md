# Scrub workflow

All paths use the same script:

```text
${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py
```

**Dry-run is the default.** Pass `--apply` to write. Backups land under
`~/.claude/` as timestamped `.tar.gz` files. Any write that would increase
dangling `parentUuid` counts is refused.

!!! warning "Clean sibling terminal"
    Run every command below from a clean terminal. Do not invoke recovery
    inside the stuck session.

## Primary path — fix the active session

Removes the triggering user turn + the refusal response. When the session is
**saturated** (≥2 refusals), also de-saturates the live resume window so the
next generation does not re-fire. Covers subagent shards under
`<session-stem>/subagents/**/*.jsonl`.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py" \
  --fix-active
# inspect output, then:
python3 "${CLAUDE_PLUGIN_ROOT}/skills/recover-from-false-positive/scripts/scrub_refusals.py" \
  --fix-active --apply
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--no-desaturate` | Opt out of automatic de-saturation inside `--fix-active` |
| `--stub` | Neutralize dense content in place (keep transcript length) instead of surgical drop |
| `--keep-recent N` | Leave the last N window turns untouched (default 6) |
| `--desat-min-score N` | Min vocab-hit score to treat a turn as dense (default 4) |

## De-saturate only

When refusals were already scrubbed but the session still trips on every
prompt (including trivial ones):

```bash
python3 …/scrub_refusals.py --desaturate --file <session.jsonl>            # dry run
python3 …/scrub_refusals.py --desaturate --file <session.jsonl> --apply    # write + backup
```

**Surgical (default):** drop whole offending turns; keep every other turn
byte-exact; re-stitch the chain; drop `tool_use` / `tool_result` pairs as a
unit so resume cannot orphan a tool call.

**Stub (`--stub`):** replace text and tool_result payloads with a short
placeholder while preserving structure and pairings.

De-saturation is best-effort. It lowers re-trip odds; only a successful resume
confirms it held. Guaranteed fallback: `/clear` or a new session.

## Clear then reinject (no close, no model switch)

Preferred way to keep the same session id without a model change:

```bash
python3 …/scrub_refusals.py --handoff --file <session.jsonl> --apply
```

That builds a **de-saturated recap offline** (no generation → no trip) from
the session's last compaction summary when present, and stages:

```text
~/.claude/.fp-reinject-<cwd-slug>.md
```

Then **in the stuck session**:

1. `/clear` — local empty of in-memory context; never generates → cannot trip
2. Type `continue` — the `UserPromptSubmit` hook injects the recap once and
   consumes the staged file

## Resume from disk instead

If you will restart the session:

```bash
# after --fix-active / --desaturate --apply from the clean terminal
# in the stuck terminal:
/quit
claude --resume <session-id> "continue"
```

A disk edit is invisible to a still-running process. The session must re-read
the file.

## Clean all old sessions (machine-wide)

```bash
python3 …/scrub_refusals.py                 # dry-run
python3 …/scrub_refusals.py --apply         # backup + scrub + verify
```

- Default root: `~/.claude/projects` (`--root DIR` to override)
- Recurses `**/*.jsonl` (includes subagent shards)
- Requires a real refusal (`isApiErrorMessage` + structural or signature match)
- Prose that merely *quotes* a signature string is never touched
- Pre-existing malformed lines are left byte-for-byte

Only use this for history cleanup. For the session that just fired, use
`--fix-active`.

## Discover the triggering project

```bash
python3 …/scrub_refusals.py --latest
```

Prints the decoded filesystem path of the project that owns the most recently
modified refusal file.

## CLI reference

| Flag | Purpose |
| --- | --- |
| `--apply` | Write changes (default is dry-run) |
| `--json` | Machine-readable dry-run report |
| `--root DIR` | Session tree (default `~/.claude/projects`) |
| `--latest` | Print project path of newest refusal file |
| `--fix-active` | Surgical fix of newest refusal session (+ auto de-sat when saturated) |
| `--desaturate` | Force de-saturation (use with `--file` if refusals already gone) |
| `--no-desaturate` | Disable auto de-sat inside `--fix-active` |
| `--stub` | In-place neutralize instead of surgical drop |
| `--file PATH` | Explicit session `.jsonl` |
| `--handoff` | Stage clear-then-reinject recap (with `--file`) |
| `--selfcheck` | Durability audit: structural vs signature vs novel counts |
| `--backtest` | List sessions whose resume window is dense+large enough to re-trip |
| `--keep-recent N` | De-sat protect last N turns (default 6) |
| `--desat-min-score N` | De-sat density threshold (default 4) |

## Hook-assisted recovery

When a turn dies, `detect-false-positive.py` may already have written
`~/.claude/.fp-state.json` with:

- `vocab_clusters`, `cross_project_classes`, `project_only_classes`
- `classification` (`project-only` \| `both` \| `unknown-needs-edgar-morin`)
- `project_claude_md`, `project_path`, `scrub_command`
- `session_id` (stale state from another session is discarded)

On the next prompt in the **same** session, `inject-recovery-context.sh`
injects ordered recovery steps and consumes the state file.

## Prevention after cleanup

Cleanup fixes history. It does not stop the next trip.

1. Classify project-only vs cross-project (edgar-morin when available; else the
   skill's three-way matrix).
2. Update project `CLAUDE.md` with a
   `## Cyber-safeguard false positives — READ FIRST` section that names **this
   session's** trigger files and vocabulary (templates live in
   `skills/…/references/prevention.md`).
3. Update global `~/.claude/CLAUDE.md` only when classification is `both` or
   `global-new-class`.
4. Apply never-close prevention env from
   `skills/…/references/never-close.md` so windows stay under the trip
   threshold (file-read caps, earlier microcompact).

!!! tip "Final summary hygiene"
    After recovery, summarize with facts only: turns removed, backup path,
    which `CLAUDE.md` was updated. Do **not** restate trigger class names or
    dense domain vocabulary in the closing summary — that text can re-trip.
