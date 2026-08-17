import sqlite3
import json
from pathlib import Path
import os

db_path = Path("data/jobs.sqlite3")
output_file = Path("Ollama_Recent_Analyses.txt")

if not db_path.exists():
    print("Database not found!")
    exit(1)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Get the last 20 jobs that have AI data
cursor.execute("""
    SELECT job_id, updated_at, data_json
    FROM jobs 
    WHERE json_extract(data_json, '$.ai_completed_at') IS NOT NULL
    ORDER BY updated_at DESC 
    LIMIT 20
""")

jobs = cursor.fetchall()

with open(output_file, "w", encoding="utf-8") as f:
    f.write("TỔNG HỢP PHÂN TÍCH OLLAMA GẦN ĐÂY\n")
    f.write("="*50 + "\n\n")
    
    count = 0
    for row in jobs:
        data = json.loads(row['data_json'] or '{}')
        
        # Check if there is actual analysis data
        impression = data.get("ai_impression") or []
        findings = data.get("ai_findings") or []
        diff_diag = data.get("ai_differential_diagnosis") or []
        
        if not impression and not findings and not diff_diag:
            continue
            
        count += 1
        source_url = data.get("source_url", "N/A")
        completed_at = data.get("ai_completed_at", "N/A")
        
        f.write(f"--- Tác vụ (Job ID): {row['job_id']} ---\n")
        f.write(f"🔗 Nguồn: {source_url}\n")
        f.write(f"⏰ Hoàn thành lúc: {completed_at}\n\n")
        
        if impression:
            f.write("📌 NHẬN ĐỊNH (IMPRESSION):\n")
            if isinstance(impression, list):
                for item in impression:
                    f.write(f"- {item}\n")
            else:
                f.write(f"- {impression}\n")
            f.write("\n")
            
        if findings:
            f.write("🔍 PHÁT HIỆN (FINDINGS):\n")
            if isinstance(findings, list):
                for item in findings:
                    f.write(f"- {item}\n")
            else:
                f.write(f"- {findings}\n")
            f.write("\n")
            
        if diff_diag:
            f.write("⚕️ CHẨN ĐOÁN PHÂN BIỆT (DIFFERENTIAL DIAGNOSIS):\n")
            if isinstance(diff_diag, list):
                for item in diff_diag:
                    f.write(f"- {item}\n")
            else:
                f.write(f"- {diff_diag}\n")
            f.write("\n")
            
        f.write("-" * 50 + "\n\n")
        
    f.write(f"Đã tổng hợp {count} bản phân tích.\n")

print(f"Successfully extracted {count} analyses to {output_file.absolute()}")
