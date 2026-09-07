"""Command-line interface used locally and by the planning workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ConfigError, load_config, matrix_json


def _boolean(value: str) -> bool:
    normalized = value.casefold()
    if normalized not in {"true", "false"}:
        raise argparse.ArgumentTypeError("expected true or false")
    return normalized == "true"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="maintenance")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a configuration file")
    validate.add_argument("--config", default="config.yaml", type=Path)
    validate.add_argument("--json", action="store_true", help="emit a machine-readable validation summary")

    matrix = subparsers.add_parser("matrix", help="emit a GitHub Actions include matrix")
    matrix.add_argument("--config", default="config.yaml", type=Path)
    matrix.add_argument("--filter", default="", help="limit the matrix to one owner/name repository")
    matrix.add_argument(
        "--dry-run",
        default=False,
        type=_boolean,
        metavar="true|false",
        help="include a dry-run flag in each matrix entry",
    )
    matrix.add_argument(
        "--output",
        default="-",
        help="output destination; use - for stdout or a GitHub Actions output file",
    )
    return parser


def _write_matrix_output(output: str, matrix: str) -> None:
    if output == "-":
        print(matrix)
        return
    output_path = Path(output)
    try:
        with output_path.open("a", encoding="utf-8") as stream:
            stream.write(f"matrix={matrix}\n")
            stream.write(f"has_targets={'true' if json.loads(matrix)['include'] else 'false'}\n")
    except OSError as error:
        raise ConfigError(f"could not write GitHub Actions output {output_path}: {error}") from error


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        config = load_config(arguments.config)
        if arguments.command == "validate":
            enabled_count = sum(repository.enabled for repository in config.repositories)
            summary = {
                "valid": True,
                "version": config.version,
                "repositories": len(config.repositories),
                "enabled_repositories": enabled_count,
            }
            if arguments.json:
                print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
            else:
                print(
                    f"{arguments.config}: valid ({len(config.repositories)} repositories, "
                    f"{enabled_count} enabled)"
                )
            return 0

        matrix = matrix_json(config, arguments.filter, arguments.dry_run)
        _write_matrix_output(arguments.output, matrix)
        return 0
    except ConfigError as error:
        print(f"maintenance: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
