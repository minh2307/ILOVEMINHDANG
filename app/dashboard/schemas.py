from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from datetime import datetime

class DashboardSummary(BaseModel):
    total: int
    pending: int
    running: int
    waiting: int
    retry_waiting: int
    manual_review: int
    success: int
    failed: int
    blocked: int
    unknown: int
    active_leases: int
    browser_health: str
    last_event_at: Optional[str] = None

class DashboardJob(BaseModel):
    job_id: str
    short_id: str
    status: str
    display_status: str
    stage: str
    source_url: str
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    attempt: int
    max_attempts: int
    
    last_event: Optional[str] = None
    last_event_at: Optional[str] = None
    
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    
    retryable: bool = False
    manual_review_required: bool = False
    
    queue_status: Optional[str] = None
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    
    browser_status: Optional[str] = None
    cdha_status: Optional[str] = None
    facebook_status: Optional[str] = None
    
    permalink: Optional[str] = None
    view_id: Optional[str] = None
    
    artifact_count: int = 0
    duration_seconds: Optional[int] = None

class JobEvent(BaseModel):
    timestamp: str
    event_type: str
    component: str
    level: str
    details: Dict[str, Any]
    attempt: int

class JobError(BaseModel):
    error_type: str
    stage: str
    message: str
    attempt: int
    retryable: bool
    first_seen: str
    last_seen: str

class ErrorGroup(BaseModel):
    fingerprint: str
    error_type: str
    stage: str
    message: str
    count: int
    jobs: List[str]

class SystemHealth(BaseModel):
    status: str
    database: str
    queue: str
    browser: str
    worker: str
    ai_engine: str
    last_event_at: Optional[str] = None

class ArtifactMeta(BaseModel):
    type: str
    path: str
    size: int
    created_at: Optional[str] = None
    sha256: Optional[str] = None

class TimelineStage(BaseModel):
    name: str
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration: Optional[int] = None
    attempt: int = 1
    last_event: Optional[str] = None
    error: Optional[str] = None

class CreateJobRequest(BaseModel):
    url: str
    force: bool = False

class ValidateJobResponse(BaseModel):
    url: str
    valid: bool
    is_duplicate: bool
    existing_job: Optional[DashboardJob] = None
    warnings: List[str] = []

class CreateJobResponse(BaseModel):
    success: bool
    job_id: Optional[str] = None
    status: Optional[str] = None
    reused: bool = False
    error: Optional[str] = None
