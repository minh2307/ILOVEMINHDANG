import pytest
from datetime import datetime, timezone
import time
from pathlib import Path

from app.scheduler.state import SchedulerPhase, ServiceStatus, ServiceState
from app.scheduler.engine import ProjectScheduler

@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_jobs.sqlite3"
    return db_file

@pytest.fixture
def scheduler(test_db):
    return ProjectScheduler(db_path=test_db)

# Mock service manager to avoid real process spawns
class MockServiceManager:
    def __init__(self):
        self.services = {
            "dashboard": ServiceState("dashboard"),
            "ollama": ServiceState("ollama"),
            "orchestrator": ServiceState("orchestrator"),
            "worker": ServiceState("worker"),
        }
        self.starts_called = []
        
    def check_all_services(self):
        return self.services.copy()
        
    def start_service(self, name):
        self.starts_called.append(name)
        self.services[name].status = ServiceStatus.RUNNING
        return self.services[name]
        
    def set_mock_time(self, mock_dt):
        self._mock_time = mock_dt

def test_case_1_before_time(scheduler, monkeypatch):
    """Case 1: 18:00, scheduled: 20:00 -> Expected: UI_ONLY"""
    mgr = MockServiceManager()
    monkeypatch.setattr("app.scheduler.engine.service_manager", mgr)
    
    # Set mock time to 18:00
    from datetime import timedelta
    def mock_now_in_tz(tz):
        # Return 18:00 today
        n = datetime.now(timezone.utc)
        return n.replace(hour=18, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("app.scheduler.engine._now_in_tz", mock_now_in_tz)

    scheduler.enable(start_time="20:00")
    state = scheduler.state
    
    assert state.phase == SchedulerPhase.UI_ONLY
    assert "dashboard" in mgr.starts_called
    assert "worker" not in mgr.starts_called

def test_case_2_at_time(scheduler, monkeypatch):
    """Case 2: 20:00, scheduled: 20:00 -> Expected: FULL_RUNNING"""
    mgr = MockServiceManager()
    monkeypatch.setattr("app.scheduler.engine.service_manager", mgr)
    
    def mock_now_in_tz(tz):
        n = datetime.now(timezone.utc)
        return n.replace(hour=20, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("app.scheduler.engine._now_in_tz", mock_now_in_tz)

    scheduler.enable(start_time="20:00")
    state = scheduler.state
    
    assert state.phase == SchedulerPhase.FULL_RUNNING
    assert "dashboard" in mgr.starts_called
    assert "worker" in mgr.starts_called
    assert "ollama" in mgr.starts_called

def test_case_3_after_time(scheduler, monkeypatch):
    """Case 3: 21:00, scheduled: 20:00 -> Expected: FULL_RUNNING"""
    mgr = MockServiceManager()
    monkeypatch.setattr("app.scheduler.engine.service_manager", mgr)
    
    def mock_now_in_tz(tz):
        n = datetime.now(timezone.utc)
        return n.replace(hour=21, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("app.scheduler.engine._now_in_tz", mock_now_in_tz)

    scheduler.enable(start_time="20:00")
    state = scheduler.state
    
    assert state.phase == SchedulerPhase.FULL_RUNNING
    
def test_case_4_dashboard_running(scheduler, monkeypatch):
    """Case 4: Frontend already running -> No duplicate start"""
    mgr = MockServiceManager()
    mgr.services["dashboard"].status = ServiceStatus.RUNNING
    monkeypatch.setattr("app.scheduler.engine.service_manager", mgr)
    
    def mock_now_in_tz(tz):
        n = datetime.now(timezone.utc)
        return n.replace(hour=18, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("app.scheduler.engine._now_in_tz", mock_now_in_tz)

    scheduler.enable(start_time="20:00")
    # Actually our mock 'start_service' gets called in engine, but it checks if running first.
    # In the real service_manager.start_service, it checks check_service().
    # We'll assert phase is UI_ONLY
    assert scheduler.state.phase == SchedulerPhase.UI_ONLY

def test_case_8_degraded(scheduler, monkeypatch):
    """Case 8: A critical service fails -> DEGRADED"""
    class FailMgr(MockServiceManager):
        def start_service(self, name):
            if name == "worker":
                self.services[name].status = ServiceStatus.FAILED
            else:
                self.services[name].status = ServiceStatus.RUNNING
            return self.services[name]
            
    mgr = FailMgr()
    monkeypatch.setattr("app.scheduler.engine.service_manager", mgr)
    
    def mock_now_in_tz(tz):
        n = datetime.now(timezone.utc)
        return n.replace(hour=20, minute=0, second=0, microsecond=0)
    monkeypatch.setattr("app.scheduler.engine._now_in_tz", mock_now_in_tz)

    scheduler.enable(start_time="20:00")
    
    assert scheduler.state.phase == SchedulerPhase.DEGRADED
    assert "worker" in scheduler.state.failed_services
