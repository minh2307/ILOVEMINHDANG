#!/usr/bin/env python3
"""Deprecated wrapper for ``python main.py create-job --url``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import main as application_main


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/add_job.py <facebook-reel-url>")
    raise SystemExit(application_main(["create-job", "--url", sys.argv[1]]))
