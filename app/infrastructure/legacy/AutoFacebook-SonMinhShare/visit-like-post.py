#!/usr/bin/env python3
"""Compatibility entry point that submits one serialized Facebook job.

The previous Selenium implementation is retained in `legacy/*.disabled` only as
migration reference and cannot launch a browser.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.browser.facebook_job import FacebookJobType
from app.browser.facebook_job_client import FacebookJobClient


def main() -> int:
    mode = os.getenv("MODE", "JOIN_BY_LIST").upper()
    mapping = {
        "JOIN_BY_LIST": FacebookJobType.JOIN_GROUP,
        "POST_ONLY": FacebookJobType.CREATE_POST,
        "POST_PLUS_INTERACT": FacebookJobType.CREATE_POST,
        "INTERACT_ONLY": FacebookJobType.CHECK_LOGIN,
        "VISIT_LIKE": FacebookJobType.CHECK_LOGIN,
    }
    job_type = mapping.get(mode)
    if job_type is None:
        raise ValueError(f"Unsupported MODE: {mode}")
    payload = {
        "group_url": os.getenv("FACEBOOK_GROUP_URL", ""),
        "target_url": os.getenv("FACEBOOK_TARGET_URL", ""),
        "text": os.getenv("FACEBOOK_POST_TEXT", ""),
    }
    job = FacebookJobClient().submit(job_type, payload)
    print(f"Submitted {job.job_id} ({job.job_type.value}); run scripts/run_facebook_worker.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
