import sqlite3
import subprocess
import time

job_ids = [
    "62705d85a1ce44539be9bdf970ade303",
    "a8664d44e24146eba26a881a36a0334e",
    "2a73955d60014078ab6d1d323776a34a",
    "5d5b0b98fb1d43a1bfd5674a85ce329e",
    "de8e14b522264c95b71e6f603f39a786",
    "d1c8b421430a472fa51150ef276c80e8",
    "66a308c307d547dfa0df2e1cc8d4a159"
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
