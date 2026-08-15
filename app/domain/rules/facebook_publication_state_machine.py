from __future__ import annotations

from app.domain.enums.facebook_publication_state import FacebookPublicationState
from app.domain.exceptions.errors import InvalidTransitionError


class FacebookPublicationStateMachine:
    """Irreversible state machine for the external Facebook submit action."""

    _transitions = {
        FacebookPublicationState.FAILED_BEFORE_SUBMIT: frozenset(
            {
                FacebookPublicationState.FAILED_BEFORE_SUBMIT,
                FacebookPublicationState.SUBMITTING,
            }
        ),
        FacebookPublicationState.SUBMITTING: frozenset(
            {
                FacebookPublicationState.SUBMITTING,
                FacebookPublicationState.SUBMITTED_UNCONFIRMED,
                FacebookPublicationState.PUBLISHED_CONFIRMED,
                FacebookPublicationState.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW,
            }
        ),
        FacebookPublicationState.SUBMITTED_UNCONFIRMED: frozenset(
            {
                FacebookPublicationState.SUBMITTED_UNCONFIRMED,
                FacebookPublicationState.PUBLISHED_CONFIRMED,
                FacebookPublicationState.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW,
            }
        ),
        FacebookPublicationState.PUBLISHED_CONFIRMED: frozenset(
            {
                FacebookPublicationState.PUBLISHED_CONFIRMED,
                FacebookPublicationState.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW,
            }
        ),
        FacebookPublicationState.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW: frozenset(
            {FacebookPublicationState.POSSIBLE_DUPLICATE_REQUIRES_MANUAL_REVIEW}
        ),
    }

    @classmethod
    def allowed_targets(
        cls, current: FacebookPublicationState
    ) -> frozenset[FacebookPublicationState]:
        return cls._transitions[current]

    @classmethod
    def validate(
        cls,
        current: FacebookPublicationState,
        target: FacebookPublicationState,
        *,
        job_id: str | None = None,
    ) -> None:
        if target not in cls.allowed_targets(current):
            raise InvalidTransitionError(
                "Invalid Facebook publication transition: "
                f"job_id={job_id or 'unknown'}, old_state={current.value}, "
                f"requested_state={target.value}"
            )

