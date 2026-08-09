# Facebook CDHA Automation Workflow

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

A resumable, automated application for processing Facebook Reels. This workflow downloads reels, performs local AI and CDHA (Clinical Diagnostics) analysis, pauses for mandatory medical review, and publishes the summarized results back to Facebook upon explicit operator confirmation.

---

## 📖 Table of Contents

- [Features](#-features)
- [Architecture & Workflow](#-architecture--workflow)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Usage](#-usage)
  - [Preflight Checks](#preflight-checks)
  - [Initial Login Setup](#initial-login-setup-one-time)
  - [Automated Worker Pipeline](#automated-worker-pipeline-recommended)
  - [Job Management & Manual Review](#job-management--manual-review)
- [Troubleshooting & Logs](#-troubleshooting--logs)
- [Testing](#-testing)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

- **Automated Workflow**: End-to-end automation from downloading Facebook Reels to data extraction, AI analysis, and re-publishing.
- **Local AI Integration**: Leverages local **Ollama** models for privacy-preserving data extraction and clinical factor analysis.
- **Resumable Queue Architecture**: Built on a robust SQLite state machine that allows jobs to be paused, resumed, or retried gracefully.
- **Safe Execution**: Mandatory manual review gates before any interaction with Facebook or CDHA.
- **Chrome CDP Integration**: Operates via persistent browser sessions using Playwright, eliminating repetitive login challenges (CAPTCHA, 2FA).
- **Extensive Diagnostics**: Built-in preflight checks to validate configuration, credentials, browser state, and AI inference readiness before execution.

---

## 🧠 Architecture & Workflow

The core architecture follows clean, domain-driven design principles:

`main.py → Use Cases → Domain Rules/Ports → Adapters → SQLite Queue → Playwright/Chrome`

**Processing Pipeline:**
1. **Download**: Fetch Reel video and metadata.
2. **Extraction**: (Optional) Extract frames via `ffmpeg`.
3. **AI Analysis**: Process data via local Ollama models.
4. **CDHA Submission**: Submit video and extracted clinical factors to the CDHA platform.
5. **Medical Review**: Pipeline pauses for manual validation of the clinical summary.
6. **Publication**: Upon approval, compose and publish the result to a target Facebook page.

---

## 📋 Prerequisites

Ensure your system meets the following requirements:
- **OS**: Linux
- **Python**: `3.10` or higher
- **Browser**: Google Chrome installed locally
- **System Packages**: `ffmpeg` and `ffprobe` (if frame extraction is enabled)
- **AI Backend**: [Ollama](https://ollama.ai/) installed and running locally with at least one model pulled (e.g., `minicpm-v:latest`).
- **Accounts**: Valid Facebook and CDHA accounts.

---

## 🛠 Installation

1. **Clone the repository** (and navigate into the directory):
   ```bash
   cd /path/to/MinhDang
   ```

2. **Create and activate the virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Linux/macOS
   ```

3. **Install dependencies**:
   ```bash
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

4. **Install Playwright Browsers** (Optional if using system Chrome):
   ```bash
   playwright install chromium
   ```

---

## ⚙️ Configuration

Copy the example environment file:
```bash
cp .env.example .env
```

Edit the `.env` file with your specific configurations. Key variables include:

```dotenv
# Browser Paths
CHROME_EXECUTABLE_FALLBACK=/usr/bin/google-chrome
FACEBOOK_CHROME_EXECUTABLE=/usr/bin/google-chrome

# Targets
FACEBOOK_TARGET_URL=https://www.facebook.com/<target-page-or-profile>
CDHA_URL=https://cdha.ai/dash?modality=us_video&country=VN

# AI Configuration
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=<your-installed-model-name>

# Automation Safety Flags
HEADLESS=false
FACEBOOK_BROWSER_HEADLESS=false
FACEBOOK_FINAL_CONFIRMATION=false  # Set to 'true' to require manual publish confirmation
AUTO_APPROVE_REVIEW=true          # Set to 'false' to require manual medical review
```

*Note: Never commit `.env`, session cookies, or Chrome profiles to version control.*

---

## 🚀 Usage

### Preflight Checks
Validate local readiness without claiming a job, opening Chrome, or triggering AI inference:
```bash
.venv/bin/python -m app.main preflight --mode quick
```
Run bounded read-only external probes (requires authorized Chrome profile):
```bash
.venv/bin/python -m app.main preflight --mode full --verbose
```

### Initial Login Setup (One-time)
Because the app relies on persistent browser profiles to bypass 2FA/CAPTCHA, log in manually first:
```bash
.venv/bin/python main.py --login-setup
# Login to Gemini/CDHA in the opened browser, then press Enter in the terminal

.venv/bin/python main.py --facebook-login-setup
# Login to Facebook in the opened browser, then press Enter in the terminal
```

### Automated Worker Pipeline (Recommended)
This approach uses an orchestrator and a worker running in separate terminals.

**Terminal 1 (Worker):**
```bash
.venv/bin/python main.py worker
```

**Terminal 2 (Orchestrator):**
Create a job and run the orchestrator:
```bash
.venv/bin/python main.py create-job --url "https://www.facebook.com/reel/REEL_ID"
.venv/bin/python main.py orchestrator
```

### Job Management & Manual Review
If safety flags are configured for manual interaction, you can inspect and approve jobs:
```bash
# Check job status
.venv/bin/python main.py status --job-id <job-id>

# Resume a paused job
.venv/bin/python main.py resume --job-id <job-id>

# Review clinical factors
.venv/bin/python main.py review --job-id <job-id>

# Confirm Facebook publication
.venv/bin/python main.py confirm-publish --job-id <job-id>

# Retry a failed job
.venv/bin/python main.py retry --job-id <job-id>
```

---

## 🔍 Troubleshooting & Logs

- **Logs**: Application logs are stored in the `logs/` directory.
- **Database**: The job queue and states are maintained in `data/jobs.sqlite3`. Do not edit this manually.
- **Browser Lock Issues**: If the worker stalls due to browser locks, safely check or terminate the browser using provided scripts:
  ```bash
  ./scripts/check_facebook_browser.sh
  ./scripts/stop_facebook_browser.sh
  ```
- **Diagnostics**: Health reports and preflight results are written to `runtime/diagnostics/`.

---

## 🧪 Testing

The repository uses `pytest` with temporary SQLite databases and mock browser adapters. Live external requests are mocked.

```bash
.venv/bin/python -m compileall -q app workers config scripts
.venv/bin/pytest -q
```

---

## 🤝 Contributing

1. Fork the project.
2. Create your feature branch (`git checkout -b feature/AmazingFeature`).
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4. Push to the branch (`git push origin feature/AmazingFeature`).
5. Open a Pull Request.

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for more information.
