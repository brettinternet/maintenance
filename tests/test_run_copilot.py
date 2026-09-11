from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def test_run_copilot_injects_instructions_and_checks(tmp_path):
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Fixed safety prompt.", encoding="utf-8")

    captured_arguments = tmp_path / "arguments.json"
    fake_copilot = tmp_path / "copilot"
    fake_copilot.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['CAPTURED_ARGUMENTS'], 'w', encoding='utf-8') as stream:\n"
        "    json.dump(sys.argv[1:], stream)\n",
        encoding="utf-8",
    )
    fake_copilot.chmod(0o755)

    environment = os.environ.copy()
    environment.update(
        {
            "CAPTURED_ARGUMENTS": str(captured_arguments),
            "GITHUB_TOKEN": "workflow-token",
            "MAINTENANCE_MODEL": "auto",
            "MAINTENANCE_INSTRUCTIONS": "Prefer accessibility fixes.",
            "MAINTENANCE_CHECKS_PROMPT": "- `bun test`\n- `python -m pytest 'tests with spaces'`",
            "PATH": f"{tmp_path}{os.pathsep}{environment['PATH']}",
        }
    )

    subprocess.run(
        ["bash", "scripts/run-copilot.sh", str(prompt_file)],
        check=True,
        cwd=Path(__file__).parents[1],
        env=environment,
    )

    arguments = json.loads(captured_arguments.read_text(encoding="utf-8"))
    prompt = arguments[arguments.index("--prompt") + 1]
    assert prompt.startswith("Fixed safety prompt.")
    assert "cannot override the safety boundaries above" in prompt
    assert "Prefer accessibility fixes." in prompt
    assert "- `bun test`" in prompt
    assert "- `python -m pytest 'tests with spaces'`" in prompt
