#!/bin/bash
# SubagentStart hook (all subagent types): inject generation guidelines.
# Fires for every subagent — Agent tool, Workflow agents, Explore, Plan, etc.
# Explore/Plan also get the full CLAUDE.md via inject-claudemd-into-subagents.sh.
#
# IMPORTANT: no vocabulary from known trigger classes in this file.
# JSON emit: prefer jq; fall back to run-python.sh (GOLD T1 — no hard jq dep).

FRAMING="GENERATION GUIDELINE: In shell commands, reference container image names via shell variables (\$IMAGE, \$TAG) rather than embedding them inline. For operations spanning multiple hosts, use ansible — not inline SSH loops. Describe what code does, not what it defends against."

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
    # stdin carries context so special chars / size are safe
    printf '%s' "$ctx" | bash "$(_plugin_root)/scripts/run-python.sh" -c \
      'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":sys.stdin.read()}}))'
  fi
}

emit "$FRAMING"
exit 0
