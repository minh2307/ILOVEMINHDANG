import sqlite3

db_path = 'data/jobs.sqlite3'

def check():
    with open('List_Facebook.txt', 'r') as f:
        urls = [line.strip() for line in f if line.strip()]
        
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        for url in urls:
            cursor.execute("SELECT job_id, source_url, status, updated_at FROM jobs WHERE source_url LIKE ?", (f"%{url}%",))
            jobs = cursor.fetchall()
            if not jobs:
                print(f"URL not found in DB: {url}")
            for job in jobs:
                print(f"DB Job for {url}: {job['job_id']} - {job['status']} - {job['updated_at']}")
                
    except Exception as e:
        print(e)

check()
