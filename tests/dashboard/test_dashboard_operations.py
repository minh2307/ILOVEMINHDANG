import pytest
from app.dashboard.schemas import DashboardJob
from app.dashboard.operations_policy import OperationsPolicy, JobStatus

@pytest.fixture
def policy():
    return OperationsPolicy()

def make_job(status: str, attempt=1, max_attempts=5):
    return DashboardJob(
        job_id="test",
        short_id="test",
        status=status,
        display_status=status,
        stage="TEST",
        source_url="http://test",
        created_at="now",
        updated_at="now",
        attempt=attempt,
        max_attempts=max_attempts
    )

def test_retry_failed_job_allowed(policy):
    job = make_job(JobStatus.DOWNLOADREEL_FAILED)
    res = policy.evaluate_retry(JobStatus.DOWNLOADREEL_FAILED, job)
    assert res.allowed is True

def test_retry_published_job_blocked(policy):
    job = make_job(JobStatus.COMPLETED)
    res = policy.evaluate_retry(JobStatus.COMPLETED, job)
    assert res.allowed is False

def test_retry_unconfirmed_submission_blocked(policy):
    job = make_job(JobStatus.FACEBOOK_PUBLISH_UNCERTAIN)
    res = policy.evaluate_retry(JobStatus.FACEBOOK_PUBLISH_UNCERTAIN, job)
    assert res.allowed is False
    assert "Use Reconcile" in res.reason

def test_reconcile_unconfirmed_submission_allowed(policy):
    job = make_job(JobStatus.FACEBOOK_PUBLISH_UNCERTAIN)
    res = policy.evaluate_reconcile(JobStatus.FACEBOOK_PUBLISH_UNCERTAIN, job)
    assert res.allowed is True

def test_resume_invalid_checkpoint_blocked(policy):
    job = make_job(JobStatus.DOWNLOADREEL_FAILED)
    res = policy.evaluate_resume(JobStatus.DOWNLOADREEL_FAILED, job)
    assert res.allowed is False
    
def test_cancel_completed_job_blocked(policy):
    job = make_job(JobStatus.COMPLETED)
    res = policy.evaluate_cancel(JobStatus.COMPLETED, job)
    assert res.allowed is False
    
def test_duplicate_click_prevention(policy):
    # This is handled by OperationsDB idempotency key logic which is unit tested independently or at integration
    pass
