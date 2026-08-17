from enum import Enum

class DashboardStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    RETRY_WAITING = "RETRY_WAITING"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"

def map_status(raw_status: str) -> DashboardStatus:
    if not raw_status:
        return DashboardStatus.UNKNOWN
        
    raw = raw_status.upper()
    
    if raw in ("CREATED", "PENDING"):
        return DashboardStatus.PENDING
        
    if "RUNNING" in raw or "ANALYZING" in raw or "UPLOADING" in raw or "CAPTURING" in raw or "PUBLISHING" in raw or "ADDING" in raw or "EXTRACTING" in raw or "PREPARING" in raw:
        return DashboardStatus.RUNNING
        
    if raw in ("WAITING", "DOWNLOADED", "CLINICAL_FACTORS_GENERATED", "CDHA_ANALYZED", "SCREENSHOTS_CAPTURED", "POST_URL_EXTRACTED"):
        return DashboardStatus.WAITING
        
    if raw in ("RETRY_PENDING", "RETRYABLE", "PLAYWRIGHT_RETRY_SCHEDULED"):
        return DashboardStatus.RETRY_WAITING
        
    if raw in ("WAITING_FOR_REVIEW", "FACEBOOK_WAITING_FOR_MANUAL_REVIEW", "FACEBOOK_PUBLISH_UNCERTAIN", "PUBLISH_RECONCILIATION_REQUIRED", "POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW", "WAITING_FOR_AUTH_REVIEW"):
        return DashboardStatus.MANUAL_REVIEW
        
    if raw in ("COMPLETED", "SUCCESS", "APPROVED", "COMMENT_ADDED", "FACEBOOK_PUBLISHED"):
        return DashboardStatus.SUCCESS
        
    if "FAILED" in raw or raw in ("NEEDS_CDHA_LOGIN", "ERROR"):
        return DashboardStatus.FAILED
        
    if raw in ("BLOCKED", "PROFILE_BUSY", "LOCKED"):
        return DashboardStatus.BLOCKED
        
    return DashboardStatus.UNKNOWN
