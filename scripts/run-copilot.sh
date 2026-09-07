#!/usr/bin/env bash
set -euo pipefail

prompt_file=${1:?usage: run-copilot.sh PROMPT_FILE}
: "${MAINTENANCE_MODEL:?MAINTENANCE_MODEL must be set}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN must be the built-in Actions token}"

if [[ ! -f "$prompt_file" ]]; then
  printf 'prompt file does not exist: %s\n' "$prompt_file" >&2
  exit 1
fi

# Copilot authenticates with the built-in workflow token. The fine-grained PAT
# is deliberately only provided to checkout and later publication steps.
export GH_TOKEN="$GITHUB_TOKEN"
exec copilot \
  --no-auto-update \
  --no-ask-user \
  --allow-all-tools \
  --model "$MAINTENANCE_MODEL" \
  --prompt "$(<"$prompt_file")"
