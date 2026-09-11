#!/usr/bin/env bash
set -euo pipefail

tap_name=${1:?usage: check-homebrew-tap.sh owner/tap formula}
formula=${2:?usage: check-homebrew-tap.sh owner/tap formula}

if [[ ! "$tap_name" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9-]+$ ]] || [[ ! "$formula" =~ ^[A-Za-z0-9@+._-]+$ ]]; then
  echo "invalid tap or formula name" >&2
  exit 2
fi

brew_repository=$(brew --repository)
tap_owner=${tap_name%%/*}
tap_repository=${tap_name#*/}
tap_path="$brew_repository/Library/Taps/$tap_owner/homebrew-$tap_repository"
target_path=$(pwd -P)

if [[ -e "$tap_path" || -L "$tap_path" ]]; then
  echo "Homebrew tap path already exists: $tap_path" >&2
  exit 1
fi

cleanup() {
  if [[ -L "$tap_path" && $(readlink "$tap_path") == "$target_path" ]]; then
    unlink "$tap_path"
  fi
}
trap cleanup EXIT

mkdir -p "$(dirname "$tap_path")"
ln -s "$target_path" "$tap_path"

brew style "$tap_name"
brew audit --strict --online --tap="$tap_name"
brew install --formula "$tap_name/$formula"
brew test "$tap_name/$formula"
