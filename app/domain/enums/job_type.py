from __future__ import annotations

from enum import StrEnum


class JobType(StrEnum):
    PROCESS_WORKFLOW = "PROCESS_WORKFLOW"
    DOWNLOAD_REEL = "DOWNLOAD_REEL"
    EXTRACT_REEL_METADATA = "EXTRACT_REEL_METADATA"
    CREATE_POST = "CREATE_POST"
    SHARE_POST = "SHARE_POST"
    JOIN_GROUP = "JOIN_GROUP"
    CHECK_LOGIN = "CHECK_LOGIN"


FacebookJobType = JobType
