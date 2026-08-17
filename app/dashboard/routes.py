from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Dict, Any
from app.dashboard.repository import DashboardRepository
from app.dashboard.schemas import (
    DashboardSummary, DashboardJob, JobEvent, SystemHealth,
    CreateJobRequest, ValidateJobResponse, CreateJobResponse
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

def get_repository():
    return DashboardRepository()

@router.get("/summary", response_model=DashboardSummary)
def get_summary(repo: DashboardRepository = Depends(get_repository)):
    return repo.get_dashboard_summary()

@router.get("/jobs", response_model=List[DashboardJob])
def list_jobs(
    status: str = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    repo: DashboardRepository = Depends(get_repository)
):
    return repo.list_jobs(limit=limit, offset=offset, status=status)

@router.get("/jobs/{job_id}", response_model=DashboardJob)
def get_job(job_id: str, repo: DashboardRepository = Depends(get_repository)):
    job = repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@router.get("/jobs/{job_id}/events", response_model=List[JobEvent])
def get_job_events(job_id: str, limit: int = 50, repo: DashboardRepository = Depends(get_repository)):
    return repo.get_job_events(job_id, limit=limit)

@router.post("/jobs/validate", response_model=ValidateJobResponse)
def validate_job_url(req: CreateJobRequest, repo: DashboardRepository = Depends(get_repository)):
    from app.services.reel_normalization import normalize_reel_url
    from app.config.settings import Settings
    from app.bootstrap import DependencyContainer
    
    settings = Settings.from_env()
    container = DependencyContainer(settings)
    
    try:
        normalized = normalize_reel_url(req.url)
        if not normalized or "facebook.com" not in normalized.lower():
            return ValidateJobResponse(
                url=req.url, valid=False, is_duplicate=False, 
                warnings=["URL does not appear to be a valid Facebook Reel link."]
            )
            
        existing = container.job_repository.find_latest_by_normalized_source_url(normalized)
        
        if existing:
            # We must map it to a DashboardJob so frontend can show it
            dashboard_job = repo.get_job(existing.job_id)
            return ValidateJobResponse(
                url=req.url, valid=True, is_duplicate=True,
                existing_job=dashboard_job,
                warnings=["This URL has been processed previously."]
            )
            
        return ValidateJobResponse(url=req.url, valid=True, is_duplicate=False)
        
    except Exception as e:
        return ValidateJobResponse(
            url=req.url, valid=False, is_duplicate=False, 
            warnings=[f"Validation error: {str(e)}"]
        )

@router.post("/jobs", response_model=CreateJobResponse)
async def create_job(req: CreateJobRequest):
    from app.config.settings import Settings
    from app.bootstrap import DependencyContainer
    
    try:
        settings = Settings.from_env()
        container = DependencyContainer(settings)
        
        result = await container.create_job.execute(req.url, force=req.force)
        
        return CreateJobResponse(
            success=result.success,
            job_id=result.job_id,
            status=result.data.get("workflow_status") if result.success else None,
            reused=result.data.get("reused", False) if result.success else False,
            error=result.error if not result.success else None
        )
    except Exception as e:
        return CreateJobResponse(
            success=False,
            error=str(e)
        )

@router.get("/health", response_model=SystemHealth)
def get_health(repo: DashboardRepository = Depends(get_repository)):
    import subprocess
    import urllib.request
    health_data = repo.check_health()
    
    # Check if worker is running
    worker_status = "OFFLINE"
    browser_status = "STANDBY"
    try:
        ps_output = subprocess.check_output(["ps", "aux"]).decode()
        if "main.py worker" in ps_output or "process_queue.sh" in ps_output:
            worker_status = "HEALTHY"
            
        if "chrome" in ps_output or "chromium" in ps_output:
            browser_status = "ACTIVE"
    except Exception:
        pass
        
    ai_status = "OFFLINE"
    try:
        res = urllib.request.urlopen("http://localhost:11435/", timeout=1)
        if res.getcode() == 200:
            ai_status = "HEALTHY"
    except Exception:
        pass

    status = "HEALTHY" if health_data["database"] == "HEALTHY" and health_data["queue"] == "HEALTHY" else "UNHEALTHY"
    return SystemHealth(
        status=status,
        database=health_data.get("database", "UNKNOWN"),
        queue=health_data.get("queue", "UNKNOWN"),
        browser=browser_status, 
        worker=worker_status, 
        ai_engine=ai_status,
        last_event_at=health_data.get("last_event_at")
    )

@router.post("/worker/start")
def start_worker():
    import subprocess
    import os
    
    try:
        # Check if already running
        ps_output = subprocess.check_output(["ps", "aux"]).decode()
        if "main.py worker" in ps_output:
            return {"status": "already_running"}
            
        # Start worker via subprocess
        script_path = os.path.join(os.getcwd(), "process_queue.sh")
        subprocess.Popen(["bash", script_path], cwd=os.getcwd(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"status": "started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/worker/stop")
def stop_worker():
    import subprocess
    try:
        subprocess.run(["pkill", "-f", "main.py worker"], capture_output=True)
        subprocess.run(["pkill", "-f", "process_queue.sh"], capture_output=True)
        return {"status": "stopped"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
