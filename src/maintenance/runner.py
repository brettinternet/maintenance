"""Post-agent safety checks and GitHub App-backed publication."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class RunnerError(RuntimeError):
    """Raised when a maintenance run cannot safely continue."""


FORBIDDEN_PATH_PREFIXES = (".github/workflows", ".github/actions")
FORBIDDEN_PATH_NAMES = (".gitmodules",)
COMMIT_MESSAGE = "chore: apply low-risk maintenance"
PR_TITLE = "chore: apply low-risk maintenance"


def forbidden_paths(paths: Sequence[str]) -> list[str]:
    """Return changed paths that the maintenance agent is not allowed to edit."""

    rejected: set[str] = set()
    for path in paths:
        normalized = path.replace("\\", "/")
        if (
            normalized in FORBIDDEN_PATH_NAMES
            or any(normalized.endswith(f"/{name}") for name in FORBIDDEN_PATH_NAMES)
            or any(
                normalized == prefix or normalized.startswith(f"{prefix}/")
                for prefix in FORBIDDEN_PATH_PREFIXES
            )
        ):
            rejected.add(path)
    return sorted(rejected)


def _run(
    command: Sequence[str],
    cwd: Path,
    *,
    env: Mapping[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            check=False,
            capture_output=capture_output,
            text=True,
        )
    except OSError as error:
        rendered = " ".join(command)
        raise RunnerError(f"could not run {rendered!r}: {error}") from error


def _checked(
    command: Sequence[str],
    cwd: Path,
    *,
    env: Mapping[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = _run(command, cwd, env=env, capture_output=capture_output)
    if result.returncode != 0:
        detail = ""
        if capture_output:
            detail = (result.stderr or result.stdout).strip()
        if detail:
            raise RunnerError(f"command failed ({result.returncode}): {' '.join(command)}\n{detail}")
        raise RunnerError(f"command failed ({result.returncode}): {' '.join(command)}")
    return result


def _git_bytes(command: Sequence[str], cwd: Path) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise RunnerError(f"could not run {' '.join(command)!r}: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RunnerError(f"command failed ({result.returncode}): {' '.join(command)}\n{detail}")
    return result.stdout


def _nul_paths(output: bytes) -> list[str]:
    return [entry.decode("utf-8", "surrogateescape") for entry in output.split(b"\0") if entry]


def _git_status(cwd: Path) -> bytes:
    return _git_bytes(["git", "status", "--porcelain=v1", "-z"], cwd)


def _git_head(cwd: Path) -> str:
    result = _checked(["git", "rev-parse", "HEAD"], cwd, capture_output=True)
    return result.stdout.strip()


def changed_paths(cwd: Path) -> list[str]:
    """Return tracked and untracked paths that would be candidates for commit."""

    tracked = _nul_paths(_git_bytes(["git", "diff", "--name-only", "-z", "HEAD"], cwd))
    untracked = _nul_paths(_git_bytes(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd))
    return sorted(set(tracked + untracked))


def _repository_identifier(value: Any) -> str:
    if not isinstance(value, str) or value.count("/") != 1 or any(character.isspace() for character in value):
        raise RunnerError("matrix repository must be an owner/name string")
    owner, name = value.split("/", 1)
    if not owner or not name or any(character in "\x00\n\r" for character in value):
        raise RunnerError("matrix repository must be an owner/name string")
    return value


def _github_environment() -> dict[str, str]:
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise RunnerError("GH_TOKEN is required for GitHub App operations")
    return os.environ.copy()


def has_open_automated_pr(repository: str, cwd: Path | None = None) -> bool:
    """Detect an existing open PR from this orchestrator's branch namespace."""

    repository = _repository_identifier(repository)
    environment = _github_environment()
    working_directory = cwd or Path.cwd()
    result = _checked(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--state",
            "open",
            "--limit",
            "1000",
            "--json",
            "headRefName",
        ],
        working_directory,
        env=environment,
        capture_output=True,
    )
    try:
        pull_requests = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as error:
        raise RunnerError("gh returned invalid JSON while checking open maintenance PRs") from error
    if not isinstance(pull_requests, list):
        raise RunnerError("gh returned an unexpected response while checking open maintenance PRs")
    return any(
        isinstance(pull_request, dict)
        and isinstance(pull_request.get("headRefName"), str)
        and pull_request["headRefName"].startswith("maintenance/")
        for pull_request in pull_requests
    )


def _load_matrix(argument: str | None) -> dict[str, Any]:
    raw = argument if argument is not None else os.environ.get("MAINTENANCE_MATRIX_JSON", "")
    if not raw:
        raise RunnerError("MAINTENANCE_MATRIX_JSON is required")
    try:
        matrix = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RunnerError("MAINTENANCE_MATRIX_JSON is not valid JSON") from error
    if not isinstance(matrix, dict):
        raise RunnerError("MAINTENANCE_MATRIX_JSON must be an object")
    return matrix


def _matrix_string(matrix: Mapping[str, Any], key: str) -> str:
    value = matrix.get(key)
    if not isinstance(value, str) or not value or any(ord(character) < 32 for character in value):
        raise RunnerError(f"matrix field {key!r} must be a non-empty string")
    return value


def _matrix_checks(matrix: Mapping[str, Any]) -> list[list[str]]:
    value = matrix.get("checks", [])
    if not isinstance(value, list):
        raise RunnerError("matrix checks must be a list of argv arrays")
    if not value:
        raise RunnerError("matrix checks must contain at least one command")
    checks: list[list[str]] = []
    for command_index, command in enumerate(value):
        if not isinstance(command, list) or not command or any(not isinstance(argument, str) for argument in command):
            raise RunnerError(f"matrix checks[{command_index}] must be a non-empty argv array")
        if not command[0]:
            raise RunnerError(f"matrix checks[{command_index}][0] must not be empty")
        checks.append(command)
    return checks


def _base_branch(matrix: Mapping[str, Any], cwd: Path, repository: str) -> str:
    configured = matrix.get("base_branch")
    if configured is not None:
        if not isinstance(configured, str) or not configured:
            raise RunnerError("matrix base_branch must be a non-empty string or null")
        return configured
    expected = os.environ.get("MAINTENANCE_BASE_BRANCH")
    if expected:
        return expected
    result = _checked(
        ["gh", "repo", "view", repository, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name"],
        cwd,
        env=_github_environment(),
        capture_output=True,
    )
    branch = result.stdout.strip()
    if not branch:
        raise RunnerError("could not determine the repository default branch")
    return branch


def _checks_environment() -> dict[str, str]:
    environment = os.environ.copy()
    # Checks are configured argv, but should not receive either workflow or App
    # credential merely because they run in the finalization process.
    environment.pop("GH_TOKEN", None)
    environment.pop("GITHUB_TOKEN", None)
    return environment


def _run_checks(checks: Sequence[Sequence[str]], cwd: Path) -> None:
    if not checks:
        print("No configured checks; continuing with the safety gates.")
        return
    environment = _checks_environment()
    for index, command in enumerate(checks, start=1):
        print(f"Running configured check {index}: {' '.join(command)}")
        result = _run(command, cwd, env=environment)
        if result.returncode != 0:
            raise RunnerError(f"configured check {index} failed with exit code {result.returncode}")


def _maintenance_branch() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"maintenance/{timestamp}-{uuid.uuid4().hex[:12]}"


def _git_with_app_token(command: Sequence[str], cwd: Path, token: str) -> None:
    encoded = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    _checked(
        ["git", "-c", f"http.extraheader=AUTHORIZATION: basic {encoded}", *command],
        cwd,
    )


def _create_pr(
    cwd: Path,
    repository: str,
    branch: str,
    base_branch: str,
    draft: bool,
    author_name: str,
    checks: Sequence[Sequence[str]],
) -> None:
    body = "\n".join(
        [
            "## Automated maintenance",
            "",
            "This PR was prepared by the central GitHub Actions maintenance orchestrator.",
            "",
            f"- Commit author: `{author_name}`",
            f"- Configured checks: `{len(checks)}`",
            "- The GitHub App installation token is the authentication and pusher identity.",
            "- This PR is intentionally limited to one low-risk maintenance change.",
        ]
    )
    environment = _github_environment()
    with tempfile.TemporaryDirectory(prefix="maintenance-pr-") as temporary_directory:
        body_path = Path(temporary_directory) / "body.md"
        body_path.write_text(body + "\n", encoding="utf-8")
        command = [
            "gh",
            "pr",
            "create",
            "--repo",
            repository,
            "--head",
            branch,
            "--base",
            base_branch,
            "--title",
            PR_TITLE,
            "--body-file",
            str(body_path),
        ]
        if draft:
            command.append("--draft")
        _checked(command, cwd, env=environment)


def finalize(matrix_argument: str | None = None, repo_root: str | Path = ".") -> int:
    """Run post-agent gates and publish one safe change when appropriate."""

    matrix = _load_matrix(matrix_argument)
    repository = _repository_identifier(_matrix_string(matrix, "repository"))
    author_name = _matrix_string(matrix, "commit_author_name")
    author_email = _matrix_string(matrix, "commit_author_email")
    model = _matrix_string(matrix, "model")
    del model  # Model selection is consumed by the preceding Copilot step.
    checks = _matrix_checks(matrix)
    base_branch = matrix.get("base_branch")
    if base_branch is not None and (not isinstance(base_branch, str) or not base_branch):
        raise RunnerError("matrix base_branch must be a non-empty string or null")
    draft = matrix.get("draft_pr", True)
    if not isinstance(draft, bool):
        raise RunnerError("matrix draft_pr must be a boolean")
    dry_run = matrix.get("dry_run", False)
    if not isinstance(dry_run, bool):
        raise RunnerError("matrix dry_run must be a boolean")

    cwd = Path(repo_root).resolve()
    if not cwd.is_dir():
        raise RunnerError(f"repository directory does not exist: {cwd}")

    expected_head = os.environ.get("MAINTENANCE_BASE_SHA")
    if expected_head:
        if len(expected_head) != 40 or any(character not in "0123456789abcdef" for character in expected_head):
            raise RunnerError("MAINTENANCE_BASE_SHA must be a 40-character commit SHA")
        if _git_head(cwd) != expected_head:
            raise RunnerError("Copilot changed HEAD; refusing to publish an agent-created commit")
    expected_branch = os.environ.get("MAINTENANCE_BASE_BRANCH")
    if expected_branch:
        current_branch = _checked(["git", "branch", "--show-current"], cwd, capture_output=True).stdout.strip()
        if current_branch != expected_branch:
            raise RunnerError("Copilot changed branches; refusing to publish")

    if not _git_status(cwd):
        print("Working tree is clean; skipping commit and pull request.")
        return 0

    paths = changed_paths(cwd)
    rejected = forbidden_paths(paths)
    if rejected:
        raise RunnerError("forbidden paths changed: " + ", ".join(rejected))

    if has_open_automated_pr(repository, cwd):
        print("An open automated maintenance PR already exists; skipping this repository.")
        return 0

    _run_checks(checks, cwd)
    paths_after_checks = changed_paths(cwd)
    rejected = forbidden_paths(paths_after_checks)
    if rejected:
        raise RunnerError("configured checks changed forbidden paths: " + ", ".join(rejected))
    if not _git_status(cwd):
        print("Configured checks left a clean working tree; skipping commit and pull request.")
        return 0

    # Re-check after the potentially long-running checks to avoid a pileup if
    # another run opened a maintenance PR while this run was working.
    if has_open_automated_pr(repository, cwd):
        print("An open automated maintenance PR appeared during checks; skipping this repository.")
        return 0

    if dry_run:
        print("Dry run: checks passed; no branch, commit, push, or pull request was created.")
        return 0

    token = os.environ.get("GH_TOKEN")
    if not token:
        raise RunnerError("GH_TOKEN is required to publish maintenance changes")
    resolved_base_branch = _base_branch(matrix, cwd, repository)
    branch = _maintenance_branch()
    _checked(["git", "switch", "--create", branch], cwd)
    _checked(["git", "config", "user.name", author_name], cwd)
    _checked(["git", "config", "user.email", author_email], cwd)
    _checked(["git", "add", "--all"], cwd)
    staged_paths = _nul_paths(_git_bytes(["git", "diff", "--cached", "--name-only", "-z"], cwd))
    rejected = forbidden_paths(staged_paths)
    if rejected:
        raise RunnerError("forbidden paths staged: " + ", ".join(rejected))
    if not staged_paths:
        print("Nothing is staged after safety checks; skipping commit and pull request.")
        return 0
    _checked(["git", "commit", "-m", COMMIT_MESSAGE], cwd)
    _git_with_app_token(["push", "--set-upstream", "origin", branch], cwd, token)
    _create_pr(cwd, repository, branch, resolved_base_branch, draft, author_name, checks)
    print(f"Created {'draft ' if draft else ''}maintenance PR from {branch}.")
    return 0


def check_open_pr_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check-open-maintenance-pr")
    parser.add_argument("repository", help="target owner/name")
    arguments = parser.parse_args(argv)
    try:
        print("true" if has_open_automated_pr(arguments.repository) else "false")
        return 0
    except RunnerError as error:
        print(f"maintenance: {error}", file=sys.stderr)
        return 1


def finalize_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finalize-maintenance")
    parser.add_argument("--matrix-json", help="matrix object; defaults to MAINTENANCE_MATRIX_JSON")
    parser.add_argument("--repo-root", default=".", type=Path)
    arguments = parser.parse_args(argv)
    try:
        return finalize(arguments.matrix_json, arguments.repo_root)
    except RunnerError as error:
        print(f"maintenance: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(finalize_main())
