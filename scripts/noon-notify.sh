#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
: "${PUSHOVER_TOKEN:?PUSHOVER_TOKEN must be exported}"
: "${PUSHOVER_USER_KEY:?PUSHOVER_USER_KEY must be exported}"

# Keep credentials in the environment, never in YAML or command arguments.
export APPRISE_URLS="pover://${PUSHOVER_USER_KEY}@${PUSHOVER_TOKEN}"

case "${NOON_STATUS:-succeeded}" in
  succeeded)
    kind=success
    message="Pi review prompt dispatched in maintenance / review."
    ;;
  *)
    kind=failure
    message="Pi review launcher failed. Check noon logs review."
    ;;
esac

exec tell notify --type "$kind" --title maintenance "$message"
