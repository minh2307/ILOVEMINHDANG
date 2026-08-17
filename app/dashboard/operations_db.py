import sqlite3
from typing import Dict, Any, Optional
from datetime import datetime, UTC
from pydantic import BaseModel
from app.config.settings import Settings

class OperationRecord(BaseModel):
    operation_id: str
    job_id: str
    operation_type: str
    idempotency_key: str
    requested_at: str
    requested_by: str
    reason: str
    previous_state: str
    requested_state: str
    safety_check: str
    result: str
    completed_at: Optional[str] = None
    status: str

class OperationsDB:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or Settings.from_env().database_path
        
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def migrate(self):
        """Create operations table if not exists."""
        with self._get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS dashboard_operations (
                    operation_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    operation_type TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    requested_at TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    reason TEXT,
                    previous_state TEXT NOT NULL,
                    requested_state TEXT,
                    safety_check TEXT,
                    result TEXT,
                    completed_at TEXT,
                    status TEXT NOT NULL
                )
            ''')
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ops_job ON dashboard_operations(job_id)")

    def get_by_idempotency_key(self, key: str) -> Optional[OperationRecord]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM dashboard_operations WHERE idempotency_key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return OperationRecord(**dict(row))
        return None

    def insert_operation(self, op: OperationRecord) -> None:
        with self._get_connection() as conn:
            conn.execute('''
                INSERT INTO dashboard_operations (
                    operation_id, job_id, operation_type, idempotency_key, requested_at, 
                    requested_by, reason, previous_state, requested_state, safety_check, 
                    result, completed_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                op.operation_id, op.job_id, op.operation_type, op.idempotency_key, op.requested_at,
                op.requested_by, op.reason, op.previous_state, op.requested_state, op.safety_check,
                op.result, op.completed_at, op.status
            ))

    def update_operation_status(self, operation_id: str, status: str, result: str) -> None:
        completed_at = datetime.now(UTC).isoformat() if status in ("SUCCEEDED", "FAILED", "REJECTED", "CANCELLED") else None
        with self._get_connection() as conn:
            conn.execute('''
                UPDATE dashboard_operations
                SET status = ?, result = ?, completed_at = COALESCE(completed_at, ?)
                WHERE operation_id = ?
            ''', (status, result, completed_at, operation_id))
