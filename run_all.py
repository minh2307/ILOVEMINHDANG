import sqlite3
import subprocess
import time

job_ids = [
    "4a602461bd644a44887bf3512bbea012",
    "91ba90ebb03c4afe9120b8e6a135822b",
    "db17616569c74564bae3617cc70b8be4",
    "b409f248bea94e42aee8f34f3a33facd",
    "0c89bc1ea7534f7895750025be16a6aa",
    "cc01779bcdac4029b66bc8983720ea69",
    "c2a72b17283e4fdaa0b7f45e7733d4f2"
]

db_path = 'data/jobs.sqlite3'
python_exec = '.venv/bin/python'

def get_job_statuses():
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(f"SELECT job_id, status FROM jobs WHERE job_id IN ({','.join(['?']*len(job_ids))})", job_ids)
        jobs = cursor.fetchall()
        return {job['job_id']: job['status'] for job in jobs}
    except Exception as e:
        print(f"Error reading db: {e}")
        return {}

def all_done():
    statuses = get_job_statuses()
    for jid in job_ids:
        if statuses.get(jid) not in ['COMPLETED', 'FAILED', 'CANCELLED']:
            return False
    return True

print("Starting processing loop...")
while not all_done():
    subprocess.run([python_exec, "main.py", "orchestrator", "--once"], capture_output=True)
    
    res = subprocess.run([python_exec, "main.py", "worker", "--once"], capture_output=True, text=True)
    if "No queue items available" in res.stdout or "No pending queue items" in res.stdout:
        time.sleep(5)
    else:
        print("Worker ran. Checking status...")
        print(get_job_statuses())

print("All jobs finished.")
for k, v in get_job_statuses().items():
    print(f"{k}: {v}")
