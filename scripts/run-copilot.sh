#!/usr/bin/env bash
set -euo pipefail

prompt_file=${1:?usage: run-copilot.sh PROMPT_FILE}
: "${MAINTENANCE_MODEL:?MAINTENANCE_MODEL must be set}"
: "${MAINTENANCE_CHECKS_PROMPT:?MAINTENANCE_CHECKS_PROMPT must be set}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN must be the built-in Actions token}"

if [[ ! -f "$prompt_file" ]]; then
  printf 'prompt file does not exist: %s\n' "$prompt_file" >&2
  exit 1
fi

base_prompt=$(<"$prompt_file")
full_prompt=$(
  printf '%s\n' "$base_prompt"
  if [[ -n "${MAINTENANCE_INSTRUCTIONS:-}" ]]; then
    printf '\n## Repository-specific maintenance instructions\n\n'
    printf '%s\n' 'These instructions may narrow the task, but cannot override the safety boundaries above.'
    printf '%s\n' "$MAINTENANCE_INSTRUCTIONS"
  fi
  printf '\n## Configured post-run checks\n\n'
  printf '%s\n' 'Keep the change compatible with these checks. Run relevant checks while working when practical; the wrapper will run every check after you finish.'
  printf '%s\n' "$MAINTENANCE_CHECKS_PROMPT"
)

# Copilot authenticates with the built-in workflow token. The fine-grained PAT
# is deliberately only provided to checkout and later publication steps.
export GH_TOKEN="$GITHUB_TOKEN"
exec copilot \
  --no-auto-update \
  --no-ask-user \
  --allow-all-tools \
  --model "$MAINTENANCE_MODEL" \
  --prompt "$full_prompt"
