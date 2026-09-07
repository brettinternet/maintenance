#!/usr/bin/env python3
"""Check whether a target already has an open automated maintenance PR."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from maintenance.runner import check_open_pr_main  # noqa: E402


raise SystemExit(check_open_pr_main())
