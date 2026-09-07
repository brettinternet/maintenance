from __future__ import annotations

import json

from maintenance.cli import main


CONFIG = """
version: 1
defaults:
  model: auto
  commit_author:
    name: Bot
    email: bot@example.com
  checks:
    - [python, -m, pytest]
repositories:
  - owner: acme
    name: service
"""


def test_matrix_command_writes_github_outputs(tmp_path):
    config_path = tmp_path / "config.yaml"
    output_path = tmp_path / "github-output"
    config_path.write_text(CONFIG, encoding="utf-8")

    assert main(
        [
            "matrix",
            "--config",
            str(config_path),
            "--dry-run",
            "true",
            "--output",
            str(output_path),
        ]
    ) == 0

    outputs = dict(line.split("=", 1) for line in output_path.read_text(encoding="utf-8").splitlines())
    assert outputs["has_targets"] == "true"
    assert json.loads(outputs["matrix"])["include"][0]["dry_run"] is True


def test_matrix_command_rejects_malformed_filter(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG, encoding="utf-8")

    assert main(["matrix", "--config", str(config_path), "--filter", "not-a-repository"]) == 2
    assert "owner/name" in capsys.readouterr().err
