#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$project_root"

config_file=${1:-config.yaml}
exec uv run maintenance validate --config "$config_file"
