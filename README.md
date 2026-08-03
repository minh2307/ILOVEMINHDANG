# MinhDang automation workflow

This repository is one resumable application for processing a Facebook Reel,
running local AI and CDHA analysis, pausing for medical review, and publishing
only after a second explicit operator confirmation.

The official path is:

```text
main.py → application use cases → domain rules/ports → infrastructure adapters
        → SQLite workflow + queue tables → one Playwright/Chrome profile
```

Historical mini-projects are retained under `app/infrastructure/legacy/`.
Compatibility imports and scripts delegate to the official path; they are not a
second production workflow.

## Setup

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/playwright install chromium
```

Set at least `FACEBOOK_TARGET_URL` for the publish phase and configure
`OLLAMA_MODEL` for local analysis. Runtime paths are resolved from the project
root. The official workflow and queue share `DATABASE_PATH` (default
`data/jobs.sqlite3`). Browser state is kept in
`runtime/chrome_profiles/cdha_automation` and is ignored by Git.

Validate without claiming a job or opening Chrome:

```bash
.venv/bin/python main.py worker --preflight-only
```

Inspect the same sanitized configuration used by the Worker and browser CLI:

```bash
.venv/bin/python main.py config
.venv/bin/python -m app.browser.facebook_browser_cli config
```

The Reel downloader optionally uses the Netscape file at `FACEBOOK_COOKIE_FILE`
(default `runtime/auth/facebook_cookies.txt`). Facebook publishing and CDHA
authenticate through the persistent Chrome profile; they do not import that file.

## Official commands

```bash
# Persist a job and idempotently queue its current state
.venv/bin/python main.py create-job --url "https://www.facebook.com/reel/..."

# Run scheduler and worker as separate processes
.venv/bin/python main.py orchestrator
.venv/bin/python main.py worker

# Bounded operational forms
.venv/bin/python main.py orchestrator --once
.venv/bin/python main.py worker --once

# Inspect or resume one job
.venv/bin/python main.py status --job-id "<job-id>"
.venv/bin/python main.py resume --job-id "<job-id>"
.venv/bin/python main.py retry --job-id "<job-id>"
.venv/bin/python main.py cancel --job-id "<job-id>"

# Manual gates
.venv/bin/python main.py review --job-id "<job-id>"
.venv/bin/python main.py confirm-publish --job-id "<job-id>"
```

`confirm-publish` requires the operator to type `PUBLISH <job-id>`. Automated
tests never invoke that gate and never publish real content. CAPTCHA, 2FA,
checkpoint, and login challenges remain manual.

## Process model

The orchestrator only observes persisted workflow states and creates idempotent
queue work items. The worker atomically claims one item, maintains a lease and
heartbeat, obtains the browser lock, invokes `ProcessJobUseCase`, persists the
outcome, and releases resources in `finally` blocks. Recovery only requeues
expired claims; a live worker's lease is never stolen.

The workflow stops at `WAITING_FOR_REVIEW` and
`FACEBOOK_WAITING_FOR_MANUAL_REVIEW`. Approval creates a new state-specific work
item, so completed queue rows are never resurrected.

Retry requests always persist `RETRY_PENDING`, the previous failure state,
failure stage, reason, requester, timestamps, and the bounded attempt count
before a new queue item is created. A post-click Facebook error is classified as
`FACEBOOK_PUBLISH_UNCERTAIN`; it is never published or retried automatically.

## Deprecated compatibility flags

`--reel-url`, `--resume-job`, `--review-job`, `--retry-job`, `--cancel-job`, and
`--continue-approved-job` print a warning and delegate to the use cases above.
Stage-only legacy flags without an exact safe equivalent fail with exit code 2.

## Tests

```bash
.venv/bin/python -m compileall -q app workers config scripts
.venv/bin/pytest -q
```

Tests use temporary SQLite databases and fake browser/stage objects. They do not
perform live downloads, CDHA submissions, or Facebook publication.

## Legacy queue migration

The old database is never modified. Inspect the plan first, then apply only if
the reported mappings are correct:

```bash
.venv/bin/python scripts/migrate_legacy_queue.py --source runtime/queue.db
.venv/bin/python scripts/migrate_legacy_queue.py --source runtime/queue.db --apply
```

Old `DOWNLOAD_REEL` rows become deduplicated workflow jobs. Old `CREATE_POST`
rows never become publish confirmations automatically; unsafe or unsupported
rows are reported for manual handling.

See [architecture](docs/architecture.md), [workflow](docs/workflow.md),
[folder structure](docs/folder-structure.md), [migration notes](docs/migration-notes.md),
[operations](docs/operations.md), the [official CLI convergence report](docs/official-cli-state-machine-report.md),
and the [unified browser configuration report](docs/unified-browser-configuration-report.md).
