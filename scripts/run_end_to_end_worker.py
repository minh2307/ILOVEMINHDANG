#!/usr/bin/env python3
"""Deprecated hybrid orchestrator; now creates work for the official queue."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import main as application_main


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python scripts/run_end_to_end_worker.py <facebook-reel-url>"
        )
    print("Deprecated: delegating to `python main.py create-job --url ...`.")
    raise SystemExit(application_main(["create-job", "--url", sys.argv[1]]))
