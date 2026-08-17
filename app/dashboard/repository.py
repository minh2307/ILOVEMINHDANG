import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.config.settings import Settings
from app.dashboard.schemas import DashboardJob, JobEvent, JobError, DashboardSummary, TimelineStage
from app.dashboard.status_mapping import map_status

class DashboardRepository:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or Settings.from_env().database_path

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_dashboard_summary(self) -> DashboardSummary:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Count jobs by status
            cursor.execute("SELECT status, count(*) as cnt FROM jobs GROUP BY status")
            status_counts = {row["status"]: row["cnt"] for row in cursor.fetchall()}
            
            # Map raw statuses to dashboard statuses
            grouped_counts = {
                "PENDING": 0, "RUNNING": 0, "WAITING": 0, "RETRY_WAITING": 0,
                "MANUAL_REVIEW": 0, "SUCCESS": 0, "FAILED": 0, "BLOCKED": 0, "UNKNOWN": 0
            }
            
            total = 0
            for raw_status, count in status_counts.items():
                mapped = map_status(raw_status).value
                grouped_counts[mapped] += count
                total += count
                
            # Get active leases
            cursor.execute("SELECT count(*) FROM queue WHERE lease_expires_at > strftime('%s', 'now')")
            active_leases = cursor.fetchone()[0] or 0
            
            # Get last event
            cursor.execute("SELECT created_at FROM job_events ORDER BY created_at DESC LIMIT 1")
            row = cursor.fetchone()
            last_event_at = row[0] if row else None
            
            return DashboardSummary(
                total=total,
                pending=grouped_counts["PENDING"],
                running=grouped_counts["RUNNING"],
                waiting=grouped_counts["WAITING"],
                retry_waiting=grouped_counts["RETRY_WAITING"],
                manual_review=grouped_counts["MANUAL_REVIEW"],
                success=grouped_counts["SUCCESS"],
                failed=grouped_counts["FAILED"],
                blocked=grouped_counts["BLOCKED"],
                unknown=grouped_counts["UNKNOWN"],
                active_leases=active_leases,
                browser_health="UNKNOWN", # Will be updated by service
                last_event_at=last_event_at
            )

    def list_jobs(self, limit: int = 50, offset: int = 0, status: str = None) -> List[DashboardJob]:
        query = '''
            SELECT 
                j.job_id, j.source_url, j.status, j.created_at, j.updated_at, j.error_message, j.attempt_count, j.max_attempts, j.data_json,
                q.status as queue_status, q.claimed_by as lease_owner, q.lease_expires_at, q.current_stage
            FROM jobs j
            LEFT JOIN queue q ON j.job_id = q.job_id
        '''
        params = []
        if status:
            # Simple exact match for now, could be improved to map back from DashboardStatus
            query += " WHERE j.status = ?"
            params.append(status)
            
        query += " ORDER BY j.updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        jobs = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            for row in cursor.fetchall():
                jobs.append(self._row_to_dashboard_job(row))
        return jobs

    def get_job(self, job_id: str) -> Optional[DashboardJob]:
        query = '''
            SELECT 
                j.job_id, j.source_url, j.status, j.created_at, j.updated_at, j.error_message, j.attempt_count, j.max_attempts, j.data_json,
                q.status as queue_status, q.claimed_by as lease_owner, q.lease_expires_at, q.current_stage
            FROM jobs j
            LEFT JOIN queue q ON j.job_id = q.job_id
            WHERE j.job_id = ?
        '''
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (job_id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_dashboard_job(row)
        return None

    def _row_to_dashboard_job(self, row) -> DashboardJob:
        data = {}
        try:
            if row.keys() and "data_json" in row.keys() and row["data_json"]:
                data = json.loads(row["data_json"])
        except Exception:
            pass

        return DashboardJob(
            job_id=row["job_id"],
            short_id=row["job_id"][:8] if row["job_id"] else "",
            status=row["status"],
            display_status=map_status(row["status"]).value,
            stage=row["current_stage"] or "UNKNOWN",
            source_url=row["source_url"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            attempt=row["attempt_count"],
            max_attempts=row["max_attempts"],
            error_message=row["error_message"],
            queue_status=row["queue_status"],
            lease_owner=row["lease_owner"],
            lease_expires_at=str(row["lease_expires_at"]) if row["lease_expires_at"] else None,
            permalink=data.get("facebook_post_url") or data.get("facebook_target_url"),
            view_id=data.get("cdha_view_url") or data.get("cdha_url")
        )

    def get_job_events(self, job_id: str, limit: int = 50) -> List[JobEvent]:
        query = '''
            SELECT event_type, created_at as timestamp, details_json, attempt, to_status as level
            FROM job_events
            WHERE job_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        '''
        events = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (job_id, limit))
            for row in cursor.fetchall():
                details = {}
                try:
                    if row["details_json"]:
                        details = json.loads(row["details_json"])
                except:
                    pass
                events.append(JobEvent(
                    timestamp=row["timestamp"],
                    event_type=row["event_type"],
                    component="Workflow",
                    level=row["level"],
                    details=details,
                    attempt=row["attempt"]
                ))
        return events
        
    def get_recent_failures(self, limit: int = 50):
        # Implementation for failures
        pass

    def check_health(self) -> Dict[str, str]:
        health = {
            "database": "UNKNOWN",
            "queue": "UNKNOWN",
            "last_event_at": None
        }
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM jobs LIMIT 1")
                health["database"] = "HEALTHY"
                
                cursor.execute("SELECT 1 FROM queue LIMIT 1")
                health["queue"] = "HEALTHY"
                
                cursor.execute("SELECT created_at FROM job_events ORDER BY created_at DESC LIMIT 1")
                row = cursor.fetchone()
                if row:
                    health["last_event_at"] = row[0]
        except Exception as e:
            health["database"] = f"ERROR: {str(e)}"
            health["queue"] = f"ERROR: {str(e)}"
            
        return health
