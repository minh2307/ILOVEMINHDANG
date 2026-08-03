"""Compatibility mapping for deprecated workflow flags.

This module contains no orchestration and performs no I/O. It only maps an old
surface to the authoritative subcommand syntax or explains why mapping is unsafe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LegacyCommandResolution:
    flag: str
    official_argv: tuple[str, ...] = ()
    official_syntax: str = ""
    error: str | None = None


def resolve_legacy_command(args: Any) -> LegacyCommandResolution | None:
    if args.reel_url:
        if args.dry_run or args.skip_facebook_comment or args.yes:
            return LegacyCommandResolution(
                "--reel-url",
                error=(
                    "Deprecated --reel-url modifiers cannot be mapped safely. Use "
                    "`python main.py create-job --url URL` and the official worker."
                ),
            )
        official = ["create-job", "--url", args.reel_url]
        if args.force_download:
            official.append("--force")
        return LegacyCommandResolution(
            "--reel-url",
            tuple(official),
            f"python main.py create-job --url {args.reel_url}",
        )
    direct = (
        ("--resume-job", args.resume_job, "resume"),
        ("--run-until-review", args.run_until_review, "resume"),
        ("--continue-approved-job", args.continue_approved_job, "resume"),
        ("--review-job", args.review_job, "review"),
        ("--retry-job", args.retry_job, "retry"),
        ("--cancel-job", args.cancel_job, "cancel"),
        ("--show-job", args.show_job, "status"),
        ("--prepare-facebook-post", args.prepare_facebook_post, "resume"),
        ("--extract-facebook-link", args.extract_facebook_link, "resume"),
        ("--comment-facebook-link", args.comment_facebook_link, "resume"),
        ("--complete-facebook", args.complete_facebook, "resume"),
        ("--publish-facebook", args.publish_facebook, "confirm-publish"),
    )
    for flag, job_id, command in direct:
        if job_id:
            return LegacyCommandResolution(
                flag,
                (command, "--job-id", job_id),
                f"python main.py {command} --job-id {job_id}",
            )
    unsupported = (
        ("--download-reel", args.download_reel),
        ("--generate-clinical-factors", args.generate_clinical_factors),
        ("--analyze-cdha", args.analyze_cdha),
        ("--process-cdha", args.process_cdha),
    )
    for flag, value in unsupported:
        if value:
            return LegacyCommandResolution(
                flag,
                error=(
                    "Deprecated stage-only command has no safe official equivalent. "
                    "Use `python main.py create-job --url URL`, `worker`, and `resume`."
                ),
            )
    return None
