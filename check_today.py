import sqlite3
import json
from datetime import datetime, timezone

db_path = 'data/jobs.sqlite3'

def check():
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT job_id, source_url, status, updated_at FROM jobs")
        jobs = cursor.fetchall()
        
        today = datetime.now(timezone.utc).date()
        print(f"Today is {today}")
        
        for job in jobs:
            try:
                updated_at = datetime.fromisoformat(job['updated_at']).date()
                if updated_at == today:
                    print(f"Job: {job['job_id']}, URL: {job['source_url']}, Status: {job['status']}")
            except Exception as e:
                print(e)
                
    except Exception as e:
        print(e)

check()
