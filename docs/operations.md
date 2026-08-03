# Operations and verification

## Safe startup

```bash
.venv/bin/python -m app.main preflight --mode quick
.venv/bin/python -m app.main preflight --mode full
.venv/bin/python -m app.main orchestrator
.venv/bin/python -m app.main worker
```

Quick mode is local-only. Full mode uses the canonical persistent Chrome
profile and contacts Ollama, Facebook, and CDHA with bounded read-only probes;
run it only when that authenticated access is authorized. Do not start the
Worker when either mode returns FAIL. Inspect the JSON report path printed by
the CLI under `runtime/diagnostics/preflight/`.

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
.venv/bin/python -m app.main config
.venv/bin/python -m app.main inspect-browser
.venv/bin/python -m app.main inspect-queue
.venv/bin/python -m app.browser.facebook_browser_cli config
```

`inspect-browser` reports manager/CDP/verified-PID/lock health without launching
Chrome. `inspect-queue` omits payloads and reports stage, owner, lease,
heartbeat, and attempt limits. Use `status`, `retry`, and `resume` for recovery;
never edit SQLite manually or delete a live lock.

The Browser Manager owns Playwright, the shared CDP connection/context, and the
profile lock. Adapters may close only pages explicitly acquired for their job.
Cleanup releases tracked temporary pages and detaches automation; it does not
close the shared browser or persistent context.

CDHA completion uses bounded semantic polling. The observed `#btnComplete` is
followed by accessible `Hoàn tất`/`Complete` fallbacks. Missing, hidden,
disabled, processing, authentication, page-closed, and disconnected states are
distinct; there is no broad `button` fallback or force-click. Persisted upload
and external-analysis identities prevent automatic duplicate submission.

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
.venv/bin/python -m compileall -q app workers
.venv/bin/python -m pytest -q
git diff --check
```

Live Facebook publishing and live CDHA correctness remain manual acceptance
tests because safe automated tests must not create real external side effects.
