# MinhDang Automation & Operations Dashboard

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A robust, resumable automated workflow for processing Facebook Reels with integrated AI analysis, CDHA platform integration, and a modern Web Operations Dashboard.

---

## ✨ Key Features

- **End-to-End Automation**: Fetch Facebook Reels, extract frames, analyze clinical factors, and publish summaries back to Facebook.
- **MinhDang Operations Dashboard**: A web-based control center to manage jobs, view real-time system health, and control background worker processes.
- **Local AI Privacy**: Utilizes local **Ollama** models (e.g., `minicpm-v:latest`) for offline, privacy-first data extraction.
- **Resumable State Machine**: Built on SQLite, allowing jobs to be safely paused, resumed, or retried.
- **Chrome CDP Integration**: Operates via persistent browser sessions using Playwright, easily bypassing CAPTCHAs and 2FA.
- **Preflight Diagnostics**: Strict system checks to validate browser locks, credentials, and AI inference readiness before execution.

---

## 🛠 Installation

1. **Clone and setup virtual environment**:
   ```bash
   git clone <repository_url> MinhDang
   cd MinhDang
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   playwright install chromium
   ```

3. **Configure Environment**:
   Copy the example environment file and customize it.
   ```bash
   cp .env.example .env
   ```
   *Ensure you configure `FACEBOOK_TARGET_URL`, `CDHA_URL`, and Ollama settings.*

---

## 🚀 Usage

### 1. Operations Dashboard (Recommended)

The easiest way to interact with the system is via the Web Dashboard.

```bash
# Run database migrations for the dashboard
.venv/bin/python main.py dashboard-migrate --apply

# Start the dashboard
.venv/bin/python main.py dashboard --host 127.0.0.1 --port 8080
```
Open `http://127.0.0.1:8080` in your browser. From here, you can:
- **Monitor System Health**: Check Database, Queue, Browser, Worker, and AI statuses.
- **Control Worker**: Start or stop the continuous background worker directly from the UI.
- **Create & Manage Jobs**: Submit new Facebook Reel URLs for processing and track their progress.

### 2. Manual CLI Operations

If you prefer the command line:

**Setup Logins (One-time):**
```bash
.venv/bin/python main.py --login-setup
.venv/bin/python main.py --facebook-login-setup
```

**Worker & Orchestrator:**
```bash
# Terminal 1: Run the continuous worker
.venv/bin/python main.py worker

# Terminal 2: Create a job and trigger the orchestrator
.venv/bin/python main.py create-job --url "https://www.facebook.com/reel/REEL_ID"
.venv/bin/python main.py orchestrator
```

**Preflight Checks (Diagnostics):**
```bash
.venv/bin/python main.py preflight --mode full --verbose
```

---

## 🔍 Troubleshooting

- **Worker Offline / Browser Lock**: If the dashboard shows the worker as offline or failing to start, there might be a stale browser lock. Run `./scripts/stop_facebook_browser.sh` or check the `runtime/locks/` and `runtime/chrome_profiles/cdha_automation/` directories.
- **Logs**: Detailed execution logs are available in the `logs/` directory.
- **Database**: The job queue is maintained in `data/jobs.sqlite3`.

---

## 📄 License
Distributed under the MIT License.
