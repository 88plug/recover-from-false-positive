# Keep a session alive forever (never close, never trip)

For an ALREADY-saturated live session there is no client-side way to keep full
context, stay open, and stop tripping — the harness owns the in-memory message
array, no hook can trim it mid-flight, `/compact` is a generation over the poison
(trips), the native refusal fallback's "bridge" lane is a model switch, and
`/clear` drops context. Confirmed against the installed binary. So the durable
answer is PREVENTION: keep the live window lean so it never reaches the threshold.

## Prevention config (apply once; takes effect on the next session launch)

The binary exposes these levers. Cap the biggest poison source (large tool/file
reads) and make the harness drop old tool output early:

```bash
# ~/.zshrc / ~/.bashrc  (env is read at launch, not mid-session)
export CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=8000   # cap file-read tool output
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=120000          # keep the window smaller
export CLAUDE_CODE_AUTO_COMPACT_WINDOW=0.6            # microcompact earlier (fraction)
```

settings.json (per-user):

```json
{ "autoCompactEnabled": true, "precomputeCompactionEnabled": true }
```

- Context editing (`context_management: clear_tool_uses_20250919`, beta
  `context-management-2025-06-27`) clears old tool results server-side without a
  generation — the cleanest live reducer. Use it where you drive the API directly.
- microcompact is local (no API call) and clears old tool_results in place; a lower
  AUTO_COMPACT_WINDOW makes it fire before the window saturates.

## If a session is already stuck (least-bad, in order)

1. Do NOT run `/recover` inside it — that injects this skill's own dense vocab and
   makes it worse (cascade). Run recovery from a CLEAN sibling terminal.
2. `--desaturate --file <session.jsonl> --apply` from the clean terminal, then in
   the stuck terminal `/quit` and `claude --resume <id> "continue"`. Keeps history.
   (A disk edit is invisible to a still-running session — it MUST be re-read.)
3. If you refuse to close it: `/clear` in the stuck session is the only in-place,
   no-generation reset — it drops context but keeps the session id and needs no
   model switch.
