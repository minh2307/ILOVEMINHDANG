# Operations and verification

## Safe startup

```bash
.venv/bin/python main.py worker --preflight-only
.venv/bin/python main.py orchestrator
.venv/bin/python main.py worker
```

Run orchestrator and worker in separate terminals. The orchestrator does not
open Chrome or execute external actions. One worker is sufficient because the
browser profile is intentionally serialized.

## Manual verification checklist

1. Run `create-job --url ...`; record the returned job ID and verify `status`
   shows `CREATED` plus one queue item.
2. Run `worker --once`; verify the queue row has an owner/heartbeat while active
   and a terminal queue status afterward.
3. Start a second worker while the first owns the browser. Verify it logs the
   owning PID/job and schedules a bounded retry rather than opening another
   profile.
4. Verify the downloaded file is non-empty and its metadata/checksum paths are
   present in `status` output.
5. Verify Clinical Factors are validated, masked, and persisted before CDHA is
   opened.
6. Verify CDHA result JSON/HTML and screenshots exist before
   `WAITING_FOR_REVIEW`.
7. Run `review --job-id ...`; choose resume-later once, then approve. Confirm no
   Facebook publish occurred during medical review.
8. Run the worker until `FACEBOOK_WAITING_FOR_MANUAL_REVIEW`. Inspect the target,
   text, selected images, privacy result, and preview.
9. Run `confirm-publish --job-id ...`; type the exact requested phrase only after
   manual inspection. Verify a post ID/exact permalink is persisted before the
   comment phase.
10. Verify the comment uses the persisted post permalink, not the original Reel
    URL, and the workflow reaches `COMPLETED` only after its result is persisted.

## Crash recovery

To verify without deleting locks or database rows:

1. Start one work item and terminate its worker process.
2. Wait beyond the configured queue lease and browser-lock timeout.
3. Start `worker --once` again.
4. Verify queue events contain `PLAYWRIGHT_RETRY_SCHEDULED` with
   `expired_worker_lease`, and workflow events contain `JOB_RECOVERED` if the
   crash occurred inside a stage.
5. Verify attempt count increased once and no duplicate workflow row or external
   result was created.

Never manually delete a fresh lock. Inspect `runtime/locks/` metadata first. A
stale lock is archived automatically for audit.

## Retry and duplicate checks

Use `retry --job-id ...` only for a documented failure state. It commits
`RETRY_PENDING` and the exact retry stage before enqueueing. When attempts exceed
the queue row's limit, it becomes `BLOCKED`.

Run `create-job` twice with the same normalized URL and verify the second result
has `reused: true` and does not create a second state-specific queue row. Use
`--force` only when intentionally creating a distinct workflow job.

## Browser management

Inspect the canonical configuration and fingerprint without opening Chrome:

```bash
.venv/bin/python main.py config
.venv/bin/python -m app.browser.facebook_browser_cli config
```

Manage the verified Chrome PID through thin wrappers:

```bash
./scripts/check_facebook_browser.sh
./scripts/start_facebook_browser.sh
./scripts/stop_facebook_browser.sh
```

These scripts delegate to the centralized manager. Stop targets only the PID
recorded by this application; it does not use a broad process-name kill.

## Automated verification

```bash
.venv/bin/python -m compileall -q app workers config scripts
.venv/bin/pytest -q
git diff --check
```

Live Facebook publishing and live CDHA correctness remain manual acceptance
tests because safe automated tests must not create real external side effects.
