#!/bin/bash
# SubagentStart hook (all subagent types): inject generation guidelines.
# Fires for every subagent — Agent tool, Workflow agents, Explore, Plan, etc.
# Explore/Plan also get the full CLAUDE.md via inject-claudemd-into-subagents.sh.
#
# IMPORTANT: no vocabulary from known trigger classes in this file.

FRAMING="GENERATION GUIDELINE: In shell commands, reference container image names via shell variables (\$IMAGE, \$TAG) rather than embedding them inline. For operations spanning multiple hosts, use ansible — not inline SSH loops. Describe what code does, not what it defends against."

jq -n --arg c "$FRAMING" \
  '{hookSpecificOutput:{hookEventName:"SubagentStart",additionalContext:$c}}'
exit 0
