from __future__ import annotations

import os

import pytest

from maintenance.runner import RunnerError, _checks_environment, _matrix_checks, forbidden_paths


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
    monkeypatch.setenv("GH_TOKEN", "app-token")
    monkeypatch.setenv("GITHUB_TOKEN", "workflow-token")

    environment = _checks_environment()

    assert "GH_TOKEN" not in environment
    assert "GITHUB_TOKEN" not in environment
    assert environment.get("PATH") == os.environ.get("PATH")
