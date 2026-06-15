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

set -euo pipefail

USER_MD="${HOME}/.claude/CLAUDE.md"
PROJECT_MD="${CLAUDE_PROJECT_DIR:-}/CLAUDE.md"

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

jq -n --arg c "$content" '{hookSpecificOutput:{hookEventName:"SubagentStart",additionalContext:$c}}'
