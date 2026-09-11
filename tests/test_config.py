from __future__ import annotations

import json

import pytest

from maintenance.config import ConfigError, load_config, matrix_json


VALID = """
version: 1
defaults:
  enabled: true
  model: auto
  commit_author:
    name: Default Bot
    email: default@example.com
  base_branch: null
  instructions: |
    Prefer small documentation fixes.
  checks:
    - [python, -m, pytest]
  draft_pr: true
  runs_on: ubuntu-latest
repositories:
  - owner: acme
    name: api
    model: gpt-5
    runs_on: macos-15
    instructions: |
      Prefer API type-safety improvements.
      Do not change public behavior.
    commit_author:
      email: api@example.com
    checks:
      - [bun, test]
  - owner: acme
    name: docs
    enabled: false
"""


def write_config(tmp_path, contents: str = VALID):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(contents, encoding="utf-8")
    return config_path


def test_defaults_are_merged_into_repository_overrides(tmp_path):
    config = load_config(write_config(tmp_path))

    api, docs = config.repositories
    assert api.repository == "acme/api"
    assert api.enabled is True
    assert api.model == "gpt-5"
    assert api.commit_author_name == "Default Bot"
    assert api.commit_author_email == "api@example.com"
    assert api.instructions == "Prefer API type-safety improvements.\nDo not change public behavior."
    assert api.checks == (("bun", "test"),)
    assert api.draft_pr is True
    assert api.runs_on == "macos-15"
    assert docs.enabled is False
    assert docs.runs_on == "ubuntu-latest"
    assert docs.model == "auto"
    assert docs.instructions == "Prefer small documentation fixes."

    matrix = config.matrix(dry_run=True)
    assert [entry["repository"] for entry in matrix["include"]] == ["acme/api"]
    assert matrix["include"][0]["dry_run"] is True


def test_filter_selects_one_repository_case_insensitively(tmp_path):
    config = load_config(write_config(tmp_path))

    matrix = config.matrix("ACME/API")
    assert [entry["repository"] for entry in matrix["include"]] == ["acme/api"]
    assert config.matrix("acme/missing")["include"] == []


@pytest.mark.parametrize(
    "contents",
    [
        VALID.replace("version: 1", "version: 1\nunknown: true"),
        VALID.replace("model: auto", "unexpected: auto\n  model: auto", 1),
        VALID.replace("    name: api", "    branch: main\n    name: api", 1),
        VALID.replace(
            "    commit_author:\n      email: api@example.com",
            "    commit_author:\n      address: api@example.com\n      email: api@example.com",
            1,
        ),
    ],
)
def test_unknown_keys_are_rejected(tmp_path, contents):
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(write_config(tmp_path, contents))


@pytest.mark.parametrize(
    "replacement",
    [
        ("owner: acme", "owner: acme/evil"),
        ("name: api", "name: "),
        ("- [python, -m, pytest]", "- python -m pytest"),
        ("draft_pr: true", "draft_pr: 1"),
        ("instructions: |\n    Prefer small documentation fixes.", "instructions: [not, text]"),
    ],
)
def test_malformed_repository_values_are_rejected(tmp_path, replacement):
    old, new = replacement
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, VALID.replace(old, new, 1)))


@pytest.mark.parametrize("escaped_control", [r"\x7f", r"\u009b"])
def test_instructions_reject_control_characters(tmp_path, escaped_control):
    contents = VALID.replace(
        "  instructions: |\n    Prefer small documentation fixes.",
        f'  instructions: "bad{escaped_control}text"',
        1,
    )
    with pytest.raises(ConfigError, match="control characters"):
        load_config(write_config(tmp_path, contents))


def test_instructions_reject_empty_and_oversized_values(tmp_path):
    empty = VALID.replace(
        "  instructions: |\n    Prefer small documentation fixes.",
        "  instructions: '   '",
        1,
    )
    with pytest.raises(ConfigError, match="must not be empty"):
        load_config(write_config(tmp_path, empty))

    oversized = VALID.replace(
        "  instructions: |\n    Prefer small documentation fixes.",
        f"  instructions: {'x' * 8_001}",
        1,
    )
    with pytest.raises(ConfigError, match="8000"):
        load_config(write_config(tmp_path, oversized))


def test_enabled_repository_requires_a_check(tmp_path):
    contents = VALID.replace("    checks:\n      - [bun, test]", "    checks: []", 1)
    with pytest.raises(ConfigError, match="at least one command"):
        load_config(write_config(tmp_path, contents))


def test_disabled_repository_can_have_no_checks(tmp_path):
    contents = VALID.replace("    - [python, -m, pytest]", "    []", 1).replace(
        "  - owner: acme\n    name: api",
        "  - owner: acme\n    name: api\n    enabled: false",
        1,
    )
    config = load_config(write_config(tmp_path, contents))
    assert not any(repository.enabled for repository in config.repositories)


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    contents = """
version: 1
defaults:
  model: auto
  model: gpt-5
repositories: []
"""
    with pytest.raises(ConfigError, match="duplicate"):
        load_config(write_config(tmp_path, contents))


def test_matrix_is_compact_json_ready_for_actions(tmp_path):
    config = load_config(write_config(tmp_path))

    matrix = json.loads(matrix_json(config))
    assert set(matrix) == {"include"}
    assert matrix["include"][0]["instructions"].startswith("Prefer API type-safety")
    assert matrix["include"][0]["checks"] == [["bun", "test"]]
    assert matrix["include"][0]["checks_prompt"] == "- `bun test`"
    assert matrix["include"][0]["runs_on"] == "macos-15"
