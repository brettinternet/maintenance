#!/usr/bin/env python3
"""Run safety gates and publish a maintenance change when appropriate."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from maintenance.runner import finalize_main  # noqa: E402


raise SystemExit(finalize_main())
