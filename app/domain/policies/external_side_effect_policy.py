from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.domain.enums.facebook_publication_state import FacebookPublicationState


class CDHACheckpoint(StrEnum):
    """Durable CDHA side-effect checkpoints, independent of transient UI state."""

    UPLOAD_NOT_STARTED = "UPLOAD_NOT_STARTED"
    UPLOAD_IN_PROGRESS = "UPLOAD_IN_PROGRESS"
    UPLOAD_CONFIRMED = "UPLOAD_CONFIRMED"
    ANALYSIS_REQUESTED = "ANALYSIS_REQUESTED"
    ANALYSIS_CONFIRMED = "ANALYSIS_CONFIRMED"

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "CDHACheckpoint":
        explicit = str(data.get("cdha_checkpoint") or "").upper()
        if explicit:
            try:
                return cls(explicit)
            except ValueError:
                pass
        legacy = str(data.get("cdha_submission_state") or "").upper()
        if legacy in {"SUBMITTING", "UPLOADING", "UNCERTAIN"}:
            return cls.UPLOAD_IN_PROGRESS
        if legacy in {"UPLOADED", "UPLOAD_CONFIRMED"}:
            return cls.UPLOAD_CONFIRMED
        if legacy in {"ANALYSIS_REQUESTED", "SUBMITTED_UNCONFIRMED"}:
            return cls.ANALYSIS_REQUESTED
        if legacy in {"SUBMITTED", "ANALYZED", "ANALYSIS_CONFIRMED"} or data.get(
            "cdha_external_analysis_id"
        ) or data.get("cdha_view_url"):
            return cls.ANALYSIS_CONFIRMED
        return cls.UPLOAD_NOT_STARTED

    @property
    def reconciliation_only(self) -> bool:
        return self in {
            self.UPLOAD_IN_PROGRESS,
            self.ANALYSIS_REQUESTED,
            self.ANALYSIS_CONFIRMED,
        }


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


class LargeUploadApproval:
    """One-shot approval bound to one exact local artifact."""

    DATA_KEY = "cdha_large_upload_approval"
    GRANTED = "GRANTED"
    CONSUMED = "CONSUMED"

    @staticmethod
    def expected_phrase(job_id: str, sha256: str, size_bytes: int) -> str:
        return f"APPROVE-CDHA-UPLOAD:{job_id}:{sha256.lower()}:{int(size_bytes)}"

    @classmethod
    def grant_data(cls, job_id: str, sha256: str, size_bytes: int) -> dict[str, Any]:
        return {
            "job_id": str(job_id),
            "sha256": str(sha256).lower(),
            "size_bytes": int(size_bytes),
            "state": cls.GRANTED,
            "uses_remaining": 1,
        }

    @classmethod
    def matches(
        cls,
        data: Mapping[str, Any],
        *,
        job_id: str,
        sha256: str,
        size_bytes: int,
    ) -> bool:
        approval = data.get(cls.DATA_KEY)
        return bool(
            isinstance(approval, Mapping)
            and approval.get("state") == cls.GRANTED
            and int(approval.get("uses_remaining") or 0) == 1
            and str(approval.get("job_id") or "") == str(job_id)
            and str(approval.get("sha256") or "").lower() == str(sha256).lower()
            and int(approval.get("size_bytes") or -1) == int(size_bytes)
        )

    @classmethod
    def consumed_data(cls, approval: Mapping[str, Any]) -> dict[str, Any]:
        return {
            **dict(approval),
            "state": cls.CONSUMED,
            "uses_remaining": 0,
        }


def video_metadata(data: Mapping[str, Any]) -> tuple[Path | None, int, str]:
    raw_path = str(data.get("video_path") or "").strip()
    path = Path(raw_path).expanduser().resolve() if raw_path else None
    try:
        size = int(data.get("video_size_bytes") or 0)
    except (TypeError, ValueError):
        size = 0
    return path, size, str(data.get("checksum_sha256") or "").strip().lower()


def large_upload_is_authorized(job: Any, threshold_bytes: int) -> bool:
    path, size, digest = video_metadata(getattr(job, "data", {}) or {})
    if size <= int(threshold_bytes):
        return True
    if path is None or not digest:
        return False
    return LargeUploadApproval.matches(
        job.data,
        job_id=str(job.job_id),
        sha256=digest,
        size_bytes=size,
    )


def large_upload_gate_required(job: Any, threshold_bytes: int) -> bool:
    data = getattr(job, "data", {}) or {}
    _path, size, _digest = video_metadata(data)
    return bool(
        size > int(threshold_bytes)
        and CDHACheckpoint.from_data(data) is not CDHACheckpoint.ANALYSIS_CONFIRMED
    )


FACEBOOK_COMMITTED_SUBMISSION_STATES = frozenset(
    {
        "SUBMITTING",
        "SUBMITTED_UNCONFIRMED",
        "PUBLICATION_UNCERTAIN",
        "RECONCILED_VERIFIED",
        "VERIFIED",
        "PUBLISHED_CONFIRMED",
        "POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW",
    }
)

FACEBOOK_DURABLE_SUBMIT_EVENTS = frozenset(
    {
        "FACEBOOK_SUBMITTING",
        "FACEBOOK_SUBMITTED_UNCONFIRMED",
        "publish_button_clicked",
        "posting_indicator_detected",
        "composer_closed",
        "post_confirmed",
    }
)

FACEBOOK_COMMITTED_ATTEMPT_STATES = frozenset(
    {"SUBMITTING", "SUBMITTED_UNCONFIRMED", "UNCERTAIN", "VERIFIED"}
)


@dataclass(frozen=True, slots=True)
class FacebookSubmissionEvidence:
    committed: bool
    possible_duplicate: bool
    publish_attempts: int
    submitted_at: str | None
    content_fingerprint: str
    target_url: str
    publication_state: FacebookPublicationState
    event_types: frozenset[str]
    permalink: str | None = None
    post_id: str | None = None


def _event_value(event: Any, key: str, default: Any = None) -> Any:
    if isinstance(event, Mapping):
        return event.get(key, default)
    return getattr(event, key, default)


def build_facebook_submission_evidence(
    data: Mapping[str, Any],
    *,
    events: Iterable[Any] = (),
    publication_attempts: Iterable[Mapping[str, Any]] = (),
) -> FacebookSubmissionEvidence:
    """Aggregate immutable submit evidence instead of trusting one status flag."""

    event_rows = list(events)
    attempt_rows = list(publication_attempts)
    event_types = frozenset(
        str(_event_value(event, "event_type") or "") for event in event_rows
    )
    submitting_events = sum(
        str(_event_value(event, "event_type") or "") == "FACEBOOK_SUBMITTING"
        for event in event_rows
    )
    clicked_events = sum(
        str(_event_value(event, "event_type") or "") == "publish_button_clicked"
        for event in event_rows
    )
    committed_attempts = sum(
        str(attempt.get("status") or "").upper()
        in FACEBOOK_COMMITTED_ATTEMPT_STATES
        or "publication outcome is uncertain"
        in str(attempt.get("error_message") or "").casefold()
        for attempt in attempt_rows
    )
    publish_attempts = max(submitting_events, clicked_events, committed_attempts)
    data_committed = facebook_submission_is_committed(data)
    committed = bool(
        data_committed
        or event_types.intersection(FACEBOOK_DURABLE_SUBMIT_EVENTS)
        or committed_attempts
    )

    submitted_at = None
    for event in event_rows:
        if str(_event_value(event, "event_type") or "") not in FACEBOOK_DURABLE_SUBMIT_EVENTS:
            continue
        details = _event_value(event, "details", {}) or {}
        if not isinstance(details, Mapping):
            continue
        candidate = str(
            details.get("submitted_at") or details.get("timestamp") or ""
        ).strip()
        if candidate:
            submitted_at = candidate
            break
    if submitted_at is None:
        submitted_at = str(data.get("facebook_submitted_at") or "").strip() or None
    if submitted_at is None and committed_attempts:
        submitted_at = next(
            (
                str(row.get("started_at") or "").strip()
                for row in attempt_rows
                if str(row.get("started_at") or "").strip()
            ),
            None,
        )

    fingerprints = [
        str(data.get("facebook_content_hash") or "").strip(),
        *[
            str(row.get("content_fingerprint") or "").strip()
            for row in attempt_rows
        ],
    ]
    fingerprint = next((value for value in fingerprints if value), "")
    targets = [
        str(data.get("facebook_target_url") or "").strip(),
        *[str(row.get("target_url") or "").strip() for row in attempt_rows],
    ]
    target_url = next((value for value in targets if value), "")
    permalink = str(data.get("facebook_post_url") or "").strip() or next(
        (
            str(row.get("permalink") or "").strip()
            for row in reversed(attempt_rows)
            if str(row.get("permalink") or "").strip()
        ),
        None,
    )
    post_id = str(data.get("facebook_post_id") or "").strip() or next(
        (
            str(row.get("post_id") or "").strip()
            for row in reversed(attempt_rows)
            if str(row.get("post_id") or "").strip()
        ),
        None,
    )
    possible_duplicate = publish_attempts > 1 or str(
        data.get("facebook_publication_state") or ""
    ).upper() == FacebookPublicationState.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW.value
    verified = bool(
        data.get("facebook_publication_verified")
        or "post_confirmed" in event_types
        or any(str(row.get("status") or "").upper() == "VERIFIED" for row in attempt_rows)
    )
    if possible_duplicate:
        state = FacebookPublicationState.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW
    elif verified:
        state = FacebookPublicationState.PUBLISHED_CONFIRMED
    elif committed:
        raw_state = str(data.get("facebook_submission_status") or "").upper()
        state = (
            FacebookPublicationState.SUBMITTING
            if raw_state == "SUBMITTING" and "publish_button_clicked" not in event_types
            else FacebookPublicationState.SUBMITTED_UNCONFIRMED
        )
    else:
        state = FacebookPublicationState.FAILED_BEFORE_SUBMIT
    return FacebookSubmissionEvidence(
        committed=committed,
        possible_duplicate=possible_duplicate,
        publish_attempts=publish_attempts,
        submitted_at=submitted_at,
        content_fingerprint=fingerprint,
        target_url=target_url,
        publication_state=state,
        event_types=event_types,
        permalink=permalink,
        post_id=post_id,
    )


def repository_facebook_submission_evidence(
    repository: Any,
    job_id: str,
    data: Mapping[str, Any] | None = None,
) -> FacebookSubmissionEvidence:
    """Read evidence through the production repository with a test-double fallback."""

    getter = getattr(repository, "get_facebook_submission_evidence", None)
    if callable(getter):
        return getter(job_id)
    events_getter = getattr(repository, "list_events", None)
    events = events_getter(job_id) if callable(events_getter) else []
    attempt_getter = getattr(repository, "get_latest_publication_attempt", None)
    latest = attempt_getter(job_id) if callable(attempt_getter) else None
    return build_facebook_submission_evidence(
        data or {},
        events=events,
        publication_attempts=[latest] if latest else [],
    )


def facebook_submission_is_committed(data: Mapping[str, Any]) -> bool:
    return bool(
        str(data.get("facebook_publication_state") or "").upper()
        in {"SUBMITTED_UNCONFIRMED", "PUBLISHED_CONFIRMED"}
        or str(data.get("facebook_submission_status") or "").upper()
        in FACEBOOK_COMMITTED_SUBMISSION_STATES
        or data.get("facebook_publish_button_clicked")
        or data.get("facebook_publish_clicked")
        or data.get("facebook_publication_verified")
    )


def verified_permalink(data: Mapping[str, Any]) -> str:
    if not data.get("facebook_publication_verified") and str(
        data.get("facebook_submission_status") or ""
    ).upper() != "RECONCILED_VERIFIED":
        return ""
    return str(
        data.get("facebook_post_url") or data.get("facebook_post_url_candidate") or ""
    ).strip()
