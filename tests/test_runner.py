from __future__ import annotations

import base64
import json
import os

import pytest

from maintenance import runner
from maintenance.runner import RunnerError, _checks_environment, _matrix_checks, forbidden_paths


def _matrix(**overrides):
    value = {
        "repository": "acme/service",
        "commit_author_name": "Maintenance Bot",
        "commit_author_email": "bot@example.com",
        "model": "auto",
        "base_branch": "main",
        "checks": [["python", "-m", "pytest"]],
        "draft_pr": True,
        "dry_run": False,
    }
    value.update(overrides)
    return json.dumps(value)


def _prepare_changed_tree(monkeypatch, paths=None):
    monkeypatch.delenv("MAINTENANCE_BASE_SHA", raising=False)
    monkeypatch.delenv("MAINTENANCE_BASE_BRANCH", raising=False)
    monkeypatch.setattr(runner, "_git_status", lambda cwd: b" M src/app.py\0")
    monkeypatch.setattr(runner, "changed_paths", lambda cwd: paths or ["src/app.py"])
    monkeypatch.setattr(runner, "has_open_automated_pr", lambda repository, cwd: False)


def test_forbidden_paths_include_nested_gitmodules_and_sensitive_directories():
    paths = [
        "src/module.py",
        ".github/workflows/ci.yml",
        ".github/actions/release/action.yml",
        "vendor/.gitmodules",
        "docs/workflows-not-actions.md",
    ]

    assert forbidden_paths(paths) == [
        ".github/actions/release/action.yml",
        ".github/workflows/ci.yml",
        "vendor/.gitmodules",
    ]


def test_matrix_requires_at_least_one_check():
    with pytest.raises(RunnerError, match="at least one command"):
        _matrix_checks({"checks": []})


def test_configured_checks_do_not_receive_workflow_tokens(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "maintenance-pat")
    monkeypatch.setenv("GITHUB_TOKEN", "workflow-token")

    environment = _checks_environment()

    assert "GH_TOKEN" not in environment
    assert "GITHUB_TOKEN" not in environment
    assert environment.get("PATH") == os.environ.get("PATH")


@pytest.mark.parametrize(
    "repository",
    ["owner/../repo", "owner/repo/name", "-owner/repo", "owner/.repo", "owner/repo name"],
)
def test_finalizer_rejects_malformed_repository_identifiers(repository):
    with pytest.raises(RunnerError, match="matrix repository"):
        runner.finalize(_matrix(repository=repository))


def test_push_token_is_passed_outside_command_line(monkeypatch, tmp_path):
    monkeypatch.setenv("GH_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_TOKEN", "workflow-token")
    captured = {}

    def fake_checked(command, cwd, *, env=None, capture_output=False):
        captured["command"] = command
        captured["env"] = env

    monkeypatch.setattr(runner, "_checked", fake_checked)

    runner._git_with_app_token(["push", "origin", "maintenance/test"], tmp_path, "secret-token")

    rendered_command = " ".join(captured["command"])
    encoded = base64.b64encode(b"x-access-token:secret-token").decode("ascii")
    assert "secret-token" not in rendered_command
    assert encoded not in rendered_command
    assert "GH_TOKEN" not in captured["env"]
    assert "GITHUB_TOKEN" not in captured["env"]
    assert captured["env"]["GIT_CONFIG_KEY_0"] == "http.extraheader"
    assert captured["env"]["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {encoded}"


def test_finalize_dry_run_checks_without_publishing(monkeypatch, tmp_path, capsys):
    _prepare_changed_tree(monkeypatch)
    checked = []
    monkeypatch.setattr(runner, "_run_checks", lambda checks, cwd: checked.extend(checks))

    def unexpected_checked(*args, **kwargs):
        raise AssertionError("dry run attempted a publishing command")

    monkeypatch.setattr(runner, "_checked", unexpected_checked)

    assert runner.finalize(_matrix(dry_run=True), tmp_path) == 0
    assert checked == [["python", "-m", "pytest"]]
    assert "no branch, commit, push, or pull request" in capsys.readouterr().out


def test_finalize_rejects_changed_base_revision(monkeypatch, tmp_path):
    monkeypatch.setenv("MAINTENANCE_BASE_SHA", "a" * 40)
    monkeypatch.setattr(runner, "_git_head", lambda cwd: "b" * 40)

    with pytest.raises(RunnerError, match="changed HEAD"):
        runner.finalize(_matrix(), tmp_path)


def test_finalize_rejects_forbidden_changes_created_by_checks(monkeypatch, tmp_path):
    _prepare_changed_tree(monkeypatch)
    path_sets = iter([["src/app.py"], ["src/app.py", ".github/workflows/ci.yml"]])
    monkeypatch.setattr(runner, "changed_paths", lambda cwd: next(path_sets))
    monkeypatch.setattr(runner, "_run_checks", lambda checks, cwd: None)

    with pytest.raises(RunnerError, match="configured checks changed forbidden paths"):
        runner.finalize(_matrix(dry_run=True), tmp_path)


def test_finalize_stages_commits_pushes_and_creates_pr(monkeypatch, tmp_path):
    _prepare_changed_tree(monkeypatch)
    monkeypatch.setenv("GH_TOKEN", "secret-token")
    monkeypatch.setattr(runner, "_run_checks", lambda checks, cwd: None)
    monkeypatch.setattr(runner, "_maintenance_branch", lambda: "maintenance/test")
    commands = []

    class Result:
        stdout = ""

    def fake_checked(command, cwd, *, env=None, capture_output=False):
        commands.append(command)
        return Result()

    def fake_git_bytes(command, cwd):
        assert command == ["git", "diff", "--cached", "--name-only", "-z"]
        return b"src/app.py\0"

    pushed = []
    created = []
    monkeypatch.setattr(runner, "_checked", fake_checked)
    monkeypatch.setattr(runner, "_git_bytes", fake_git_bytes)
    monkeypatch.setattr(runner, "_git_with_app_token", lambda command, cwd, token: pushed.append((command, token)))
    monkeypatch.setattr(runner, "_create_pr", lambda *args: created.append(args))

    assert runner.finalize(_matrix(), tmp_path) == 0
    assert ["git", "switch", "--create", "maintenance/test"] in commands
    assert ["git", "add", "--all"] in commands
    assert ["git", "commit", "-m", runner.COMMIT_MESSAGE] in commands
    assert pushed == [(["push", "--set-upstream", "origin", "maintenance/test"], "secret-token")]
    assert created[0][1:5] == ("acme/service", "maintenance/test", "main", True)
