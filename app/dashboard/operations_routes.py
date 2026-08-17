from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import uuid
import json
from datetime import datetime, UTC

from app.dashboard.repository import DashboardRepository
from app.dashboard.operations_db import OperationsDB, OperationRecord
from app.dashboard.operations_policy import OperationsPolicy

from app.application.use_cases.retry_job_use_case import RetryJobUseCase
from app.application.use_cases.resume_job_use_case import ResumeJobUseCase
from app.application.use_cases.reconcile_publish_use_case import ReconcilePublishUseCase
from app.application.use_cases.cancel_job_use_case import CancelJobUseCase
from app.application.use_cases.schedule_workflow_jobs_use_case import ScheduleWorkflowJobsUseCase
from app.adapters.facebook_adapter import FacebookPublisherAdapter
from app.browser.facebook_client import FacebookWebClient
from app.browser.chrome_manager import ChromeManager
from app.browser.selector_resolver import SelectorResolver

from app.bootstrap import DependencyContainer
from app.config.settings import Settings
from app.domain.enums.job_status import JobStatus

router = APIRouter(prefix="/api/operations/jobs", tags=["operations"])

class OperationRequest(BaseModel):
    operation_id: str
    idempotency_key: str
    reason: str
    operator: str = "operator"

def get_db():
    return OperationsDB()

def get_repo():
    return DashboardRepository()

def get_policy():
    return OperationsPolicy()

@router.get("/{job_id}/allowed")
async def get_allowed_operations(job_id: str, repo: DashboardRepository = Depends(get_repo), policy: OperationsPolicy = Depends(get_policy)):
    job = repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return {"allowed_operations": policy.get_allowed_operations(job)}

@router.post("/{job_id}/preview")
async def preview_operation(job_id: str, payload: dict, repo: DashboardRepository = Depends(get_repo), policy: OperationsPolicy = Depends(get_policy)):
    job = repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    op_type = payload.get("operation", "")
    allowed_ops = policy.get_allowed_operations(job)
    op_policy = next((op for op in allowed_ops if op.operation == op_type), None)
    
    if not op_policy:
        raise HTTPException(status_code=400, detail=f"Operation {op_type} not defined")
        
    return {
        "operation": op_type,
        "allowed": op_policy.allowed,
        "risk": op_policy.risk,
        "from_state": job.status,
        "attempt": job.attempt,
        "next_attempt": job.attempt + 1 if op_type == "RETRY" else job.attempt,
        "reason": op_policy.reason
    }

async def _process_operation(job_id: str, req: OperationRequest, op_type: str, policy: OperationsPolicy, repo: DashboardRepository, ops_db: OperationsDB):
    # Idempotency check
    existing = ops_db.get_by_idempotency_key(req.idempotency_key)
    if existing:
        return existing
        
    # Transactional acceptance check
    job = repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    allowed_ops = policy.get_allowed_operations(job)
    op_policy = next((op for op in allowed_ops if op.operation == op_type), None)
    
    if not op_policy or not op_policy.allowed:
        # Save as rejected
        record = OperationRecord(
            operation_id=req.operation_id,
            job_id=job_id,
            operation_type=op_type,
            idempotency_key=req.idempotency_key,
            requested_at=datetime.now(UTC).isoformat(),
            requested_by=req.operator,
            reason=req.reason,
            previous_state=job.status,
            requested_state="",
            safety_check=json.dumps({"allowed": False, "reason": op_policy.reason if op_policy else "Unknown"}),
            result="REJECTED",
            status="REJECTED",
            completed_at=datetime.now(UTC).isoformat()
        )
        ops_db.insert_operation(record)
        raise HTTPException(status_code=409, detail={
            "code": "OPERATION_NOT_ALLOWED",
            "message": op_policy.reason if op_policy else "Not allowed",
            "recommended_action": "RECONCILE" if op_type == "RETRY" and op_policy and op_policy.risk == "HIGH_RISK" else "Check job state"
        })
        
    # Accepted -> Creating record
    record = OperationRecord(
        operation_id=req.operation_id,
        job_id=job_id,
        operation_type=op_type,
        idempotency_key=req.idempotency_key,
        requested_at=datetime.now(UTC).isoformat(),
        requested_by=req.operator,
        reason=req.reason,
        previous_state=job.status,
        requested_state="",
        safety_check=json.dumps({"allowed": True, "risk": op_policy.risk}),
        result="ACCEPTED",
        status="ACCEPTED"
    )
    ops_db.insert_operation(record)
    
    # We load standard repository
    settings = Settings.from_env()
    container = DependencyContainer(settings)
    
    try:
        if op_type == "RETRY":
            res = await container.retry_job.execute(job_id, reason=req.reason, requested_by=req.operator)
        elif op_type == "RESUME":
            res = await container.resume_job.execute(job_id)
        elif op_type == "CANCEL":
            res = await container.cancel_job.execute(job_id, reason=req.reason)
        elif op_type == "RECONCILE":
            # Direct execution, safe
            from app.browser.chrome_manager import ChromeManager
            from app.browser.selector_resolver import SelectorResolver
            from app.browser.facebook_client import FacebookWebClient
            from app.adapters.facebook_adapter import FacebookPublisherAdapter
            from app.application.use_cases.reconcile_publish_use_case import ReconcilePublishUseCase
            
            resolver = SelectorResolver(settings.selectors_path, save_html=settings.save_diagnostic_html)
            async with ChromeManager(settings) as chrome:
                client = FacebookWebClient(settings, container.job_repository, chrome, resolver=resolver)
                adapter = FacebookPublisherAdapter(settings, container.job_repository, client)
                use_case = ReconcilePublishUseCase(container.job_repository, adapter)
                res = await use_case.execute(job_id)
        elif op_type == "MARK_MANUAL_REVIEW":
            container.job_repository.transition(
                job_id,
                JobStatus.WAITING_FOR_REVIEW,
                details={"reason": req.reason, "requested_by": req.operator},
                event_type="MANUAL_REVIEW_REQUESTED"
            )
            class MockRes:
                success = True
                error = None
            res = MockRes()
            
        if res.success:
            ops_db.update_operation_status(req.operation_id, "SUCCEEDED", "Operation executed successfully")
            record.status = "SUCCEEDED"
        else:
            ops_db.update_operation_status(req.operation_id, "FAILED", str(res.error))
            record.status = "FAILED"
            record.result = str(res.error)
            
    except Exception as e:
        ops_db.update_operation_status(req.operation_id, "FAILED", str(e))
        record.status = "FAILED"
        record.result = str(e)
        
    return record


@router.post("/{job_id}/retry")
async def retry_operation(job_id: str, req: OperationRequest, repo: DashboardRepository = Depends(get_repo), ops_db: OperationsDB = Depends(get_db), policy: OperationsPolicy = Depends(get_policy)):
    return await _process_operation(job_id, req, "RETRY", policy, repo, ops_db)

@router.post("/{job_id}/resume")
async def resume_operation(job_id: str, req: OperationRequest, repo: DashboardRepository = Depends(get_repo), ops_db: OperationsDB = Depends(get_db), policy: OperationsPolicy = Depends(get_policy)):
    return await _process_operation(job_id, req, "RESUME", policy, repo, ops_db)

@router.post("/{job_id}/reconcile")
async def reconcile_operation(job_id: str, req: OperationRequest, repo: DashboardRepository = Depends(get_repo), ops_db: OperationsDB = Depends(get_db), policy: OperationsPolicy = Depends(get_policy)):
    return await _process_operation(job_id, req, "RECONCILE", policy, repo, ops_db)

@router.post("/{job_id}/cancel")
async def cancel_operation(job_id: str, req: OperationRequest, repo: DashboardRepository = Depends(get_repo), ops_db: OperationsDB = Depends(get_db), policy: OperationsPolicy = Depends(get_policy)):
    return await _process_operation(job_id, req, "CANCEL", policy, repo, ops_db)

@router.post("/{job_id}/manual-review")
async def manual_review_operation(job_id: str, req: OperationRequest, repo: DashboardRepository = Depends(get_repo), ops_db: OperationsDB = Depends(get_db), policy: OperationsPolicy = Depends(get_policy)):
    return await _process_operation(job_id, req, "MARK_MANUAL_REVIEW", policy, repo, ops_db)

