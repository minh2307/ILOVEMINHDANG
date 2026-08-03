"""Compatibility module delegating to the official application CLI."""

from __future__ import annotations

import sys

from app.main import main as application_main


def main() -> int:
    return application_main(["worker", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
