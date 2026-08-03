from __future__ import annotations

import pytest

from app.models.workflow import WorkflowStatus
from app.workflows.state_machine import InvalidTransitionError, WorkflowStateMachine


def test_every_status_is_defined_in_transition_map() -> None:
    assert set(WorkflowStateMachine._transitions) == set(WorkflowStatus)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (WorkflowStatus.CREATED, WorkflowStatus.DOWNLOADREEL_RUNNING),
        (WorkflowStatus.DOWNLOADREEL_RUNNING, WorkflowStatus.DOWNLOADED),
        (WorkflowStatus.WAITING_FOR_REVIEW, WorkflowStatus.APPROVED),
        (WorkflowStatus.COMMENT_ADDED, WorkflowStatus.COMPLETED),
        (WorkflowStatus.FACEBOOK_PUBLISH_FAILED, WorkflowStatus.RETRY_PENDING),
        (WorkflowStatus.RETRY_PENDING, WorkflowStatus.FACEBOOK_PREPARING),
        (WorkflowStatus.CDHA_FAILED, WorkflowStatus.RETRY_PENDING),
        (WorkflowStatus.RETRY_PENDING, WorkflowStatus.CDHA_OPENING),
    ],
)
def test_valid_transitions(current: WorkflowStatus, target: WorkflowStatus) -> None:
    WorkflowStateMachine.validate(current, target)


def test_active_status_can_fail_terminally() -> None:
    WorkflowStateMachine.validate(WorkflowStatus.CDHA_ANALYZING, WorkflowStatus.FAILED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (WorkflowStatus.CREATED, WorkflowStatus.COMPLETED),
        (WorkflowStatus.DOWNLOADED, WorkflowStatus.FACEBOOK_PREPARING),
        (WorkflowStatus.COMPLETED, WorkflowStatus.RETRY_PENDING),
        (WorkflowStatus.REJECTED, WorkflowStatus.APPROVED),
        (WorkflowStatus.FACEBOOK_PUBLISH_FAILED, WorkflowStatus.COMPLETED),
        (WorkflowStatus.WAITING_FOR_REVIEW, WorkflowStatus.COMPLETED),
        (WorkflowStatus.FACEBOOK_PUBLISHING, WorkflowStatus.COMPLETED),
    ],
)
def test_invalid_transitions_raise(current: WorkflowStatus, target: WorkflowStatus) -> None:
    with pytest.raises(InvalidTransitionError):
        WorkflowStateMachine.validate(current, target)
