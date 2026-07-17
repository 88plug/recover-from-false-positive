#!/usr/bin/env bash
# Re-inject CLAUDE.md into Explore/Plan subagents.
#
# Why: built-in Explore and Plan subagents skip the CLAUDE.md hierarchy
# (https://code.claude.com/docs/en/sub-agents — "Built-in subagents").
# This is documented and intentional but unfixable via config. Without this
# hook, any rule in CLAUDE.md (naming conventions, repo-visibility constraints,
# anti-drift framing) is silently bypassed during research/planning subagents.
#
# Wired via ~/.claude/settings.json SubagentStart hook with matcher "Explore|Plan".
# Reads no stdin; emits JSON to stdout with additionalContext to inject.
# JSON emit: prefer jq; fall back to run-python.sh (GOLD T1 — no hard jq dep).

set -euo pipefail

USER_MD="${HOME}/.claude/CLAUDE.md"
PROJECT_MD="${CLAUDE_PROJECT_DIR:-}/CLAUDE.md"

_plugin_root() {
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
    printf '%s' "$CLAUDE_PLUGIN_ROOT"
  else
    cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
  fi
}

emit() {
  local ctx="$1"
  if command -v jq >/dev/null 2>&1; then
    jq -n --arg c "$ctx" \
      '{hookSpecificOutput:{hookEventName:"SubagentStart",additionalContext:$c}}'
  else
    # stdin carries context (CLAUDE.md can be large / contain any chars)
    printf '%s' "$ctx" | bash "$(_plugin_root)/scripts/run-python.sh" -c \
      'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":sys.stdin.read()}}))'
  fi
}

content=$'# Inherited CLAUDE.md context\n\nExplore/Plan subagents skip CLAUDE.md by default. The following is re-injected so this subagent operates under the same rules as the parent session.\n\n'

if [ -f "${USER_MD}" ]; then
  content+=$'---\n\n## ~/.claude/CLAUDE.md (user-global)\n\n'
  content+="$(cat "${USER_MD}")"
  content+=$'\n\n'
fi

if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -f "${PROJECT_MD}" ]; then
  content+=$'---\n\n## '"${CLAUDE_PROJECT_DIR}"'/CLAUDE.md (project)\n\n'
  content+="$(cat "${PROJECT_MD}")"
  content+=$'\n'
fi

emit "$content"
