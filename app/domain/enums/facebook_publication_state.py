from __future__ import annotations

from enum import StrEnum


class FacebookPublicationState(StrEnum):
    """Durable lifecycle of the one allowed Facebook publish side effect."""

    FAILED_BEFORE_SUBMIT = "FAILED_BEFORE_SUBMIT"
    SUBMITTING = "SUBMITTING"
    SUBMITTED_UNCONFIRMED = "SUBMITTED_UNCONFIRMED"
    PUBLISHED_CONFIRMED = "PUBLISHED_CONFIRMED"
    POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW = (
        "POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW"
    )

