import pytest
from app.dashboard.repository import DashboardRepository

def test_dashboard_health():
    repo = DashboardRepository()
    health = repo.check_health()
    assert "database" in health
    assert "queue" in health
    assert health["database"] in ("HEALTHY", "UNKNOWN") or "ERROR" in health["database"]

def test_dashboard_summary():
    repo = DashboardRepository()
    summary = repo.get_dashboard_summary()
    assert summary.total >= 0
    assert summary.pending >= 0
    assert summary.running >= 0
