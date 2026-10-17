#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
project=$(pwd -P)

: "${PUSHOVER_TOKEN:?PUSHOVER_TOKEN must be exported}"
: "${PUSHOVER_USER_KEY:?PUSHOVER_USER_KEY must be exported}"
apprise_url="pover://${PUSHOVER_USER_KEY}@${PUSHOVER_TOKEN}"

# Always target the default session, not the invoking pane's session.
h() { herdr --session default "$@"; }

workspaces=$(h workspace list)
candidates=$(jq -r '
  .result.workspaces[]
  | select(.label == "maintenance")
  | .workspace_id
' <<< "$workspaces")
workspace=""

# Match both the label and this checkout; never pick an ambiguous workspace.
for candidate in $candidates; do
  panes=$(h pane list --workspace "$candidate")
  if jq -e --arg cwd "$project" '
    any(.result.panes[]; .cwd == $cwd)
  ' <<< "$panes" >/dev/null; then
    if [[ -n "$workspace" ]]; then
      echo "Multiple matching maintenance workspaces; refusing to guess." >&2
      exit 1
    fi
    workspace=$candidate
  fi
done

# Always create a fresh tab rather than interrupting an existing agent.
if [[ -n "$workspace" ]]; then
  created=$(h tab create \
    --workspace "$workspace" \
    --cwd "$project" --label review --no-focus \
    --env "APPRISE_URLS=$apprise_url")
else
  created=$(h workspace create \
    --cwd "$project" --label maintenance --no-focus \
    --env "APPRISE_URLS=$apprise_url")
  tab=$(jq -er '.result.tab.tab_id' <<< "$created")
  h tab rename "$tab" review
fi

pane=$(jq -er '.result.root_pane.pane_id' <<< "$created")
agent="review-$(date +%s)-$$"
h agent start "$agent" --kind pi --pane "$pane" -- --name reviewer

# The quoted delimiter preserves literal text, including $VARIABLE references.
prompt=$(cat <<'PROMPT'
Review all the PRs created by this project.
Prepare them for merging and merge if they're safe.
Close them if they're not good changes.

Use Tell MCP to notify the user of any problems or blockers, including the PR URL
and any action needed, only when there are issues.
PROMPT
)
h agent prompt "$agent" "$prompt"
