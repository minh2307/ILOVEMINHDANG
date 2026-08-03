from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from collections.abc import Sequence

from app.config.settings import Settings
from app.adapters.downloadreel_adapter import DownloadReelAdapter, DownloadReelCoordinator
from app.adapters.facebook_adapter import FacebookPublisherAdapter
from app.browser.cdha_client import CDHAWebClient
from app.browser.chrome_manager import ChromeManager, ProfileInUseError
from app.browser.gemini_client import GeminiWebClient
from app.browser.facebook_client import FacebookWebClient
from app.browser.selector_resolver import SelectorResolver
from app.logging_setup import configure_logging
from app.legacy_cli import resolve_legacy_command
from app.models.workflow import WorkflowStatus
from app.repositories.job_repository import JobRepository
from app.services.review_service import ReviewService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CDHA resumable workflow — Phase 1-5")
    group = parser.add_mutually_exclusive_group()

    # Init / info
    group.add_argument("--init-db", action="store_true", help="Initialize local workflow storage.")
    group.add_argument("--list-jobs", action="store_true", help="List persisted jobs.")
    group.add_argument("--list-resumable-jobs", action="store_true", help="List jobs that can be resumed.")
    group.add_argument("--show-job", metavar="JOB_ID", help="Show a job and its events.")

    # Phase 5 full pipeline
    group.add_argument("--reel-url", metavar="FACEBOOK_REEL_URL", help="Start full pipeline from Reel URL.")
    group.add_argument("--resume-job", metavar="JOB_ID", help="Resume job from its current state.")
    group.add_argument("--run-until-review", metavar="JOB_ID", help="Run pipeline until WAITING_FOR_REVIEW.")
    group.add_argument("--continue-approved-job", metavar="JOB_ID", help="Continue APPROVED job through Facebook.")
    group.add_argument("--retry-job", metavar="JOB_ID", help="Retry only the failed step of a job.")
    group.add_argument("--cancel-job", metavar="JOB_ID", help="Cancel a job safely.")

    # Config / maintenance
    group.add_argument("--check-config", action="store_true", help="Validate configuration without launching Chrome.")
    group.add_argument("--backup-db", action="store_true", help="Create a timestamped database backup.")

    # Phase 2
    group.add_argument("--download-reel", metavar="FACEBOOK_REEL_URL", help="Run only the DownloadReel step.")

    # Phase 3
    group.add_argument("--generate-clinical-factors", metavar="JOB_ID")
    group.add_argument("--analyze-cdha", metavar="JOB_ID")
    group.add_argument("--process-cdha", metavar="JOB_ID", help="Run Phase 3 and stop at human review.")
    group.add_argument("--login-setup", action="store_true", help="Open Gemini and CDHA for manual auth.")
    group.add_argument("--review-job", metavar="JOB_ID", help="Review a WAITING_FOR_REVIEW job.")

    # Phase 4
    group.add_argument("--prepare-facebook-post", metavar="JOB_ID")
    group.add_argument("--publish-facebook", metavar="JOB_ID")
    group.add_argument("--extract-facebook-link", metavar="JOB_ID")
    group.add_argument("--comment-facebook-link", metavar="JOB_ID")
    group.add_argument("--complete-facebook", metavar="JOB_ID")
    group.add_argument("--facebook-login-setup", action="store_true",
                       help="Open Facebook for manual auth only; publish nothing.")

    # Modifiers
    parser.add_argument("--force-facebook-publish", action="store_true",
                        help="Explicit duplicate override; still requires two manual confirmations.")
    parser.add_argument("--force-download", action="store_true",
                        help="Explicitly retry or redownload when allowed.")
    parser.add_argument("--skip-facebook-comment", action="store_true",
                        help="Skip permalink comment step.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate without performing external actions.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip non-medical prompts. Never bypasses review or Facebook gate.")

    commands = parser.add_subparsers(dest="command", title="official commands")
    commands.add_parser(
        "config", help="Inspect the authoritative runtime configuration safely."
    )
    create = commands.add_parser("create-job", help="Persist and queue a workflow job.")
    create.add_argument("--url", required=True, help="Facebook Reel URL")
    create.add_argument("--force", action="store_true", help="Create a new job despite a duplicate URL.")

    status = commands.add_parser("status", help="Show one workflow job, events, and queue items.")
    status.add_argument("--job-id", required=True)

    resume = commands.add_parser("resume", help="Queue one resumable workflow job.")
    resume.add_argument("--job-id", required=True)

    retry = commands.add_parser("retry", help="Move a failed job to RETRY_PENDING and queue it.")
    retry.add_argument("--job-id", required=True)

    cancel = commands.add_parser("cancel", help="Cancel a workflow job safely.")
    cancel.add_argument("--job-id", required=True)

    review = commands.add_parser("review", help="Run the medical review gate, then queue approved work.")
    review.add_argument("--job-id", required=True)

    publish = commands.add_parser(
        "confirm-publish", help="Explicitly confirm and queue the Facebook publish action."
    )
    publish.add_argument("--job-id", required=True)

    worker = commands.add_parser("worker", help="Run the single official durable worker.")
    worker.add_argument("--once", action="store_true", help="Process at most one queue item.")
    worker.add_argument("--preflight-only", action="store_true")

    orchestrator = commands.add_parser(
        "orchestrator", help="Schedule eligible persisted workflow jobs."
    )
    orchestrator.add_argument("--once", action="store_true")
    orchestrator.add_argument("--interval", type=float, default=5.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    settings.ensure_runtime_directories()
    configure_logging(settings)

    if args.command:
        return _run_official_command(args, settings)

    legacy = resolve_legacy_command(args)
    if legacy is not None:
        if legacy.error:
            print(legacy.error, file=sys.stderr)
            return 2
        print(
            f"Deprecated: {legacy.flag}; use `{legacy.official_syntax}`.",
            file=sys.stderr,
        )
        official_args = build_parser().parse_args(list(legacy.official_argv))
        return _run_official_command(official_args, settings)

    repository = JobRepository(settings.database_path)
    repository.initialize()

    # --- Maintenance commands (no Chrome needed) ---
    if args.init_db:
        print(f"Workflow database ready: {settings.database_path}")
        return 0

    if args.check_config:
        return _run_check_config(settings, repository)

    if args.backup_db:
        try:
            backup_path, info = repository.backup_database()
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
            return 1

    if args.list_jobs:
        print(json.dumps([j.to_dict() for j in repository.list_jobs()], ensure_ascii=False, indent=2))
        return 0

    if args.list_resumable_jobs:
        rows = repository.list_resumable_jobs()
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    if args.show_job:
        job = repository.get_job(args.show_job)
        if job is None:
            print(f"Job not found: {args.show_job}")
            return 1
        out = job.to_dict()
        out["events"] = [e.to_dict() for e in repository.list_events(job.job_id)]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.cancel_job:
        official = build_parser().parse_args(
            ["cancel", "--job-id", args.cancel_job]
        )
        return _run_official_command(official, settings)

    if args.retry_job:
        return _run_retry_job(args.retry_job, settings, repository)

    # --- Phase 5 full-pipeline commands ---
    if args.reel_url or args.resume_job or args.run_until_review or args.continue_approved_job:
        try:
            return asyncio.run(
                _run_pipeline_command(args, settings, repository)
            )
        except (LookupError, ValueError, ProfileInUseError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
            return 2

    # --- Phase 4 Facebook commands ---
    facebook_job_id = (
        args.prepare_facebook_post or args.publish_facebook
        or args.extract_facebook_link or args.comment_facebook_link
        or args.complete_facebook
    )
    if args.force_facebook_publish and not (args.prepare_facebook_post or args.complete_facebook):
        print("--force-facebook-publish requires --prepare-facebook-post or --complete-facebook")
        return 2
    if facebook_job_id or args.facebook_login_setup:
        try:
            return asyncio.run(_run_phase4_command(args, settings, repository))
        except (LookupError, ValueError, ProfileInUseError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
            return 2

    # --- Phase 2 DownloadReel ---
    if args.download_reel:
        adapter = DownloadReelAdapter(settings, repository)
        coordinator = DownloadReelCoordinator(settings, repository, adapter)
        try:
            result = asyncio.run(coordinator.run(args.download_reel, force_download=args.force_download))
        except ValueError as exc:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
            return 2
        persisted = repository.get_job(result.job_id)
        summary = {
            "job_id": result.job_id,
            "status": persisted.status.value if persisted else "UNKNOWN",
            "source_url": result.source_url,
            "video_path": str(result.video_path) if result.video_path else None,
            "video_size_bytes": result.video_size_bytes,
            "metadata_path": str(result.metadata_path) if result.metadata_path else None,
            "reused": result.reused,
            "error": result.error,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if result.success else 1

    # --- Phase 3 commands ---
    phase3_job_id = args.generate_clinical_factors or args.analyze_cdha or args.process_cdha
    if phase3_job_id or args.login_setup:
        try:
            return asyncio.run(_run_phase3_command(args, settings, repository))
        except (LookupError, ValueError, ProfileInUseError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
            return 2

    if args.review_job:
        try:
            decision = ReviewService(settings, repository).review(args.review_job)
            if decision.action in {"retry_gemini", "retry_cdha", "retry_ollama"}:
                return asyncio.run(
                    _run_review_retry(args.review_job, decision.action, settings, repository)
                )
            return decision.exit_code
        except (LookupError, ValueError, ProfileInUseError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
            return 2

    print(f"Workflow database ready: {settings.database_path}")
    return 0


def _run_official_command(
    args: argparse.Namespace, settings: Settings
) -> int:
    """Execute the supported post-convergence CLI without direct browser logic."""
    from app.bootstrap import DependencyContainer

    if args.command == "config":
        payload = settings.sanitized_runtime_configuration()
        payload["configuration_fingerprint"] = settings.configuration_fingerprint()
        errors: list[str] = []
        try:
            settings.validate()
            settings.inspect_facebook_cookie()
        except ValueError as exc:
            errors.append(str(exc))
        payload["valid"] = not errors
        payload["errors"] = errors
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if not errors else 1

    if args.command == "create-job":
        try:
            container = DependencyContainer(settings)
            result = asyncio.run(container.create_job.execute(args.url, force=args.force))
            print(json.dumps({
                "success": result.success,
                "job_id": result.job_id,
                "status": result.data.get("workflow_status"),
                "reused": result.data.get("reused", False),
                "queued": result.data.get("queued", False),
            }, ensure_ascii=False, indent=2))
            return 0 if result.success else 1
        except (LookupError, ValueError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
            return 2

    if args.command == "status":
        container = DependencyContainer(settings)
        result = asyncio.run(container.get_job_status.execute(args.job_id))
        output = result.data if result.success else {
            "job_id": result.job_id, "error": result.error,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if result.success else 1

    if args.command == "retry":
        container = DependencyContainer(settings)
        result = asyncio.run(
            container.retry_job.execute(args.job_id, requested_by="cli")
        )
        print(json.dumps({
            "success": result.success,
            "job_id": result.job_id,
            "status": result.data.get("workflow_status"),
            "queued": result.data.get("queued", False),
            "error": result.error,
        }, ensure_ascii=False))
        return 0 if result.success else 1

    if args.command == "resume":
        container = DependencyContainer(settings)
        result = asyncio.run(container.resume_job.execute(args.job_id))
        print(json.dumps({
            "success": result.success,
            "job_id": result.job_id,
            "status": result.data.get("workflow_status"),
            "queued": result.data.get("queued", False),
            "error": result.error,
        }, ensure_ascii=False))
        return 0 if result.success else 1

    if args.command == "cancel":
        container = DependencyContainer(settings)
        result = asyncio.run(container.cancel_job.execute(args.job_id))
        print(json.dumps({
            "success": result.success,
            "job_id": result.job_id,
            "status": result.data.get("workflow_status"),
            "error": result.error,
        }, ensure_ascii=False))
        return 0 if result.success else 1

    if args.command == "review":
        container = DependencyContainer(settings)
        try:
            result = asyncio.run(container.review_job.execute(args.job_id))
        except (LookupError, ValueError, EOFError) as exc:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
            return 2
        print(json.dumps({
            "success": result.success,
            "decision": result.data.get("decision"),
            "status": result.data.get("workflow_status"),
            "queued": result.data.get("queued", False),
            "error": result.error,
        }, ensure_ascii=False))
        return int(result.data.get("exit_code", 0)) if result.success else 1

    if args.command == "confirm-publish":
        container = DependencyContainer(settings)
        expected = container.confirm_publish.expected_phrase(args.job_id)
        confirmation = input(
            f"Type '{expected}' to authorize the final publish action: "
        )
        result = asyncio.run(
            container.confirm_publish.execute(args.job_id, confirmation=confirmation)
        )
        print(json.dumps({
            "success": result.success,
            "job_id": result.job_id,
            "queued": result.data.get("queued", False),
            "error": result.error,
        }, ensure_ascii=False))
        return 0 if result.success else 1

    if args.command == "worker":
        from dataclasses import asdict
        from app.config.facebook_browser import FacebookBrowserConfig
        from app.preflight import PreflightError, run_preflight

        try:
            report = run_preflight(
                settings, FacebookBrowserConfig.from_settings(settings)
            )
            print(json.dumps(asdict(report), ensure_ascii=False))
            if args.preflight_only:
                return 0
            container = DependencyContainer(settings)
            if args.once:
                asyncio.run(container.worker.start_once())
            else:
                asyncio.run(container.worker.start())
            return 0
        except PreflightError as exc:
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
            return 2

    if args.command == "orchestrator":
        container = DependencyContainer(settings)

        async def schedule() -> None:
            while True:
                count = await container.scheduler.schedule_once()
                print(json.dumps({"scheduled": count}, ensure_ascii=False))
                if args.once:
                    return
                await asyncio.sleep(max(0.1, args.interval))

        asyncio.run(schedule())
        return 0

    raise AssertionError(f"Unhandled command: {args.command}")


def _schedule_one(settings: Settings, job_id: str) -> int:
    from app.bootstrap import DependencyContainer

    try:
        container = DependencyContainer(settings)
        queued = asyncio.run(container.scheduler.schedule_job(job_id))
        job = container.job_repository.get_job(job_id)
        print(json.dumps({
            "success": True,
            "job_id": job_id,
            "status": job.status.value if job else "UNKNOWN",
            "queued": queued,
        }, ensure_ascii=False))
        return 0
    except (LookupError, ValueError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 2


# ---------------------------------------------------------------------------
# Phase 5 pipeline runner
# ---------------------------------------------------------------------------

async def _run_pipeline_command(
    args: argparse.Namespace, settings: Settings, repository: JobRepository
) -> int:
    from app.workflows.cdha_pipeline import VerifiedWorkflowStages

    resolver = SelectorResolver(settings.selectors_path, save_html=settings.save_diagnostic_html)
    async with ChromeManager(settings) as chrome:
        pipeline = VerifiedWorkflowStages(
            settings, repository,
            chrome=chrome,
            force_download=args.force_download,
            force_facebook_publish=args.force_facebook_publish,
            skip_facebook_comment=args.skip_facebook_comment,
            dry_run=args.dry_run,
            yes=args.yes,
        )
        if args.reel_url:
            result = await pipeline.start_from_reel(reel_url=args.reel_url)
        elif args.resume_job:
            result = await pipeline.resume(job_id=args.resume_job)
        elif args.run_until_review:
            result = await pipeline.run_until_review(job_id=args.run_until_review)
        else:
            result = await pipeline.continue_after_approval(job_id=args.continue_approved_job)

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.pending_manual_action:
        print(f"\nNext step: {result.pending_manual_action}")
    return 0 if result.success else 1


# ---------------------------------------------------------------------------
# Config check
# ---------------------------------------------------------------------------

def _run_check_config(settings: Settings, repository: JobRepository) -> int:
    checks: list[tuple[str, str, str]] = []  # (label, status, detail)

    def check(label: str, condition: bool, detail: str = "", warning_only: bool = False) -> None:
        if condition:
            checks.append((label, "PASS", detail))
        elif warning_only:
            checks.append((label, "WARNING", detail))
        else:
            checks.append((label, "FAIL", detail))

    # Chrome
    chrome_found = any(
        p.exists() for p in [
            settings.chrome_executable_fallback,
            Path("/usr/bin/google-chrome"),
            Path("/usr/bin/chromium-browser"),
            Path("/usr/bin/google-chrome-stable"),
        ]
    )
    check("Google Chrome available", chrome_found, str(settings.chrome_executable_fallback))

    # Profile dir
    try:
        settings.chrome_profile_dir.mkdir(parents=True, exist_ok=True)
        check("Chrome profile directory writable",
              settings.chrome_profile_dir.is_dir(), str(settings.chrome_profile_dir))
    except OSError as exc:
        check("Chrome profile directory writable", False, str(exc))

    # Lock file
    lock = settings.chrome_profile_dir / "SingletonLock"
    check("Chrome profile not locked", not lock.exists(),
          "Lock file found — another instance may be running", warning_only=True)

    # Database
    try:
        repository.initialize()
        check("SQLite database accessible", True, str(settings.database_path))
    except Exception as exc:
        check("SQLite database accessible", False, str(exc))

    # Directories
    for label, path in [
        ("Job data directory writable", settings.job_data_dir),
        ("Log directory writable", settings.log_dir),
    ]:
        try:
            path.mkdir(parents=True, exist_ok=True)
            check(label, True, str(path))
        except OSError as exc:
            check(label, False, str(exc))

    # selectors.yaml
    check("selectors.yaml exists", settings.selectors_path.is_file(), str(settings.selectors_path))

    # URLs
    check("Gemini URL configured", bool(settings.gemini_url), settings.gemini_url)
    check("CDHA URL configured", bool(settings.cdha_url), settings.cdha_url)
    check("Facebook target URL", bool(settings.facebook_target_url),
          "(not set — required for Facebook steps)" if not settings.facebook_target_url else settings.facebook_target_url,
          warning_only=True)

    # Test mode
    if settings.test_mode:
        check("Test Facebook target configured", bool(settings.facebook_test_target_url),
              settings.facebook_test_target_url or "(not set)")

    # Timeout sanity
    check("Publish timeout > 30s", settings.facebook_publish_timeout_seconds >= 30,
          str(settings.facebook_publish_timeout_seconds))
    check("Upload timeout > 30s", settings.facebook_upload_timeout_seconds >= 30,
          str(settings.facebook_upload_timeout_seconds))

    # Git .gitignore sanity (check for runtime/ in ignore)
    gitignore = settings.project_root / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        check("Chrome profile excluded from Git", "chrome_profile" in content or "runtime/" in content,
              ".gitignore missing chrome profile exclusion", warning_only=True)
        check("Videos excluded from Git", "*.mp4" in content or "data/jobs" in content,
              ".gitignore may not exclude downloaded videos", warning_only=True)
    else:
        check(".gitignore exists", False, "No .gitignore found", warning_only=True)

    # Print results
    failed = sum(1 for _, s, _ in checks if s == "FAIL")
    warned = sum(1 for _, s, _ in checks if s == "WARNING")
    for label, status, detail in checks:
        icon = {"PASS": "✓", "WARNING": "⚠", "FAIL": "✗"}[status]
        suffix = f"  [{detail}]" if detail else ""
        print(f"  {icon} [{status:7}] {label}{suffix}")
    print()
    if failed:
        print(f"Config check: {failed} FAIL(s), {warned} WARNING(s) — fix errors before running.")
        return 1
    if warned:
        print(f"Config check: PASS with {warned} WARNING(s).")
    else:
        print("Config check: PASS")
    return 0


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

def _run_retry_job(job_id: str, settings: Settings, repository: JobRepository) -> int:
    del repository
    official = build_parser().parse_args(["retry", "--job-id", job_id])
    return _run_official_command(official, settings)


async def _run_retry_async(
    job_id: str, retry_step: str, settings: Settings, repository: JobRepository
) -> int:
    from app.workflows.cdha_pipeline import VerifiedWorkflowStages

    resolver = SelectorResolver(settings.selectors_path, save_html=settings.save_diagnostic_html)
    async with ChromeManager(settings) as chrome:
        pipeline = VerifiedWorkflowStages(settings, repository, chrome=chrome)
        result = await pipeline._route_retry(job_id, retry_step)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.success else 1


# ---------------------------------------------------------------------------
# Phase 4 commands (unchanged from previous implementation)
# ---------------------------------------------------------------------------

async def _run_phase4_command(
    args: argparse.Namespace, settings: Settings, repository: JobRepository
) -> int:
    resolver = SelectorResolver(settings.selectors_path, save_html=settings.save_diagnostic_html)
    async with ChromeManager(settings) as chrome:
        client = FacebookWebClient(
            settings, repository, chrome, resolver=resolver,
            force_publish=args.force_facebook_publish,
        )
        if args.facebook_login_setup:
            page = await chrome.new_page()
            await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
            if not await client.is_authenticated(page):
                await chrome.wait_for_manual_action(
                    "Log in to Facebook manually and complete any 2FA, CAPTCHA, checkpoint, or verification.",
                    lambda: client.is_authenticated(page),
                )
            if not await client.is_authenticated(page):
                raise ValueError("Facebook authenticated page could not be verified")
            print("Facebook login setup verified. No composer was opened and nothing was published.")
            return 0
        adapter = FacebookPublisherAdapter(settings, repository, client)
        job_id = (
            args.prepare_facebook_post or args.publish_facebook
            or args.extract_facebook_link or args.comment_facebook_link
            or args.complete_facebook
        )
        if args.prepare_facebook_post:
            result = await adapter.prepare(job_id=job_id)
        elif args.publish_facebook:
            result = await adapter.publish(job_id=job_id)
        elif args.extract_facebook_link:
            result = await adapter.extract_permalink(job_id=job_id)
        elif args.comment_facebook_link:
            result = await adapter.add_permalink_comment(job_id=job_id)
        else:
            result = await adapter.complete(job_id=job_id)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.success else 1


# ---------------------------------------------------------------------------
# Phase 3 commands (unchanged)
# ---------------------------------------------------------------------------

async def _run_phase3_command(
    args: argparse.Namespace, settings: Settings, repository: JobRepository
) -> int:
    resolver = SelectorResolver(settings.selectors_path, save_html=settings.save_diagnostic_html)
    async with ChromeManager(settings) as chrome:
        gemini = GeminiWebClient(settings, repository, chrome, resolver=resolver)
        cdha = CDHAWebClient(settings, repository, chrome, resolver=resolver)
        if args.login_setup:
            return await _run_login_setup(settings, chrome, gemini, cdha)
        job_id = args.generate_clinical_factors or args.analyze_cdha or args.process_cdha
        job = repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")

        if args.generate_clinical_factors:
            result = await gemini.generate_clinical_factors(
                caption=str(job.data.get("caption") or ""),
                comments=list(job.data.get("comments") or []),
                job_id=job_id,
            )
            print(json.dumps({
                "success": result.success, "job_id": job_id,
                "status": repository.get_job(job_id).status.value,
                "missing_fields": result.missing_fields,
                "warnings": result.validation_warnings,
                "error": result.error,
            }, ensure_ascii=False, indent=2))
            return 0 if result.success else 1

        if args.process_cdha and job.status in {
            WorkflowStatus.DOWNLOADED, WorkflowStatus.GEMINI_FAILED, WorkflowStatus.AI_FAILED, WorkflowStatus.NEEDS_GEMINI_LOGIN,
        }:
            generated = await gemini.generate_clinical_factors(
                caption=str(job.data.get("caption") or ""),
                comments=list(job.data.get("comments") or []),
                job_id=job_id,
            )
            if not generated.success:
                print(json.dumps({"success": False, "error": generated.error}, ensure_ascii=False))
                return 1
            job = repository.get_job(job_id)

        if args.analyze_cdha and job.status not in {
            WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.CDHA_FAILED, WorkflowStatus.NEEDS_CDHA_LOGIN,
        }:
            raise ValueError(f"--analyze-cdha requires CLINICAL_FACTORS_GENERATED; got {job.status.value}")

        if args.process_cdha and job.status not in {
            WorkflowStatus.CLINICAL_FACTORS_GENERATED, WorkflowStatus.CDHA_FAILED, WorkflowStatus.NEEDS_CDHA_LOGIN,
        }:
            raise ValueError(f"--process-cdha cannot resume from {job.status.value}")

        video_path = job.data.get("video_path")
        factors = job.data.get("clinical_factors")
        if not video_path:
            raise ValueError("Job has no validated downloaded video path")
        if not factors:
            raise ValueError("Job has no validated masked Clinical Factors")

        analyzed = await cdha.analyze_video(
            video_path=Path(video_path), clinical_factors=str(factors), job_id=job_id
        )
        print(json.dumps({
            "success": analyzed.success, "job_id": job_id,
            "status": repository.get_job(job_id).status.value,
            "result_json_path": str(analyzed.result_json_path) if analyzed.result_json_path else None,
            "screenshot_paths": [str(p) for p in analyzed.screenshot_paths],
            "warnings": analyzed.warnings, "error": analyzed.error,
        }, ensure_ascii=False, indent=2))
        if analyzed.success:
            ReviewService(settings, repository).display(job_id)
        return 0 if analyzed.success else 1


async def _run_review_retry(
    job_id: str, action: str, settings: Settings, repository: JobRepository
) -> int:
    resolver = SelectorResolver(settings.selectors_path, save_html=settings.save_diagnostic_html)
    async with ChromeManager(settings) as chrome:
        gemini = GeminiWebClient(settings, repository, chrome, resolver=resolver)
        cdha = CDHAWebClient(settings, repository, chrome, resolver=resolver)
        job = repository.get_job(job_id)
        if job is None:
            raise LookupError(f"Job not found: {job_id}")
        if action in {"retry_gemini", "retry_ollama"}:
            # retry_ollama: re-run local Ollama AI, then fall through to CDHA
            # retry_gemini: legacy path kept for backwards compat
            if action == "retry_gemini":
                generated = await gemini.generate_clinical_factors(
                    caption=str(job.data.get("caption") or ""),
                    comments=list(job.data.get("comments") or []),
                    job_id=job_id,
                )
            else:
                # Retained compatibility helper; official CLI uses RetryJobUseCase.
                from app.workflows.cdha_pipeline import VerifiedWorkflowStages
                pipeline = VerifiedWorkflowStages(settings, repository, chrome=chrome)
                ai_result = await pipeline._step_ai(job_id)
                if not ai_result.success:
                    print(json.dumps(ai_result.to_dict(), ensure_ascii=False, indent=2))
                    return 1
                # _step_ai already chains into _step_cdha and _step_screenshots;
                # display the updated review and exit so the user can re-decide.
                ReviewService(settings, repository).display(job_id)
                return 0
            if action == "retry_gemini" and not generated.success:
                return 1
            job = repository.get_job(job_id)
        video_path = job.data.get("video_path")
        factors = job.data.get("clinical_factors")
        if not video_path or not factors:
            raise ValueError("Review retry requires an existing video and masked Clinical Factors")
        analyzed = await cdha.analyze_video(
            video_path=Path(video_path), clinical_factors=str(factors), job_id=job_id
        )
        if analyzed.success:
            ReviewService(settings, repository).display(job_id)
        return 0 if analyzed.success else 1


async def _run_login_setup(
    settings: Settings, chrome: ChromeManager,
    gemini: GeminiWebClient, cdha: CDHAWebClient,
) -> int:
    gemini_page = await chrome.new_page()
    cdha_page = await chrome.new_page()
    await gemini_page.goto(settings.gemini_url, wait_until="domcontentloaded")
    await cdha_page.goto(settings.cdha_url, wait_until="domcontentloaded")

    async def both_authenticated() -> bool:
        return await gemini.is_authenticated(gemini_page) and await cdha.is_authenticated(cdha_page)

    if not await both_authenticated():
        await chrome.wait_for_manual_action(
            "Log in to Gemini and CDHA manually. Complete any 2FA, CAPTCHA, checkpoint. No content will be submitted.",
            both_authenticated,
        )
    if not await both_authenticated():
        raise ValueError("Gemini and CDHA authenticated pages could not both be verified")
    print("Gemini and CDHA login setup verified. No content was submitted.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
