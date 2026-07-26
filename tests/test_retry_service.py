from __future__ import annotations

import asyncio

import pytest

from app.errors import AuthenticationRequiredError, BrowserTimeoutError, RetryExhaustedError
from app.services.retry_service import RetryAttempt, RetryPolicy, retry_async


def test_retry_success_first_attempt_has_no_sleep() -> None:
    calls = 0
    sleeps: list[float] = []

    async def operation() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    result = asyncio.run(retry_async(
        operation,
        policy=RetryPolicy(3, 1.0, 2.0, 10.0, 0.5),
        should_retry=lambda exc: True,
        sleep=fake_sleep,
        random_jitter=lambda _a, _b: 0.25,
    ))
    assert result == "ok"
    assert calls == 1
    assert sleeps == []


def test_retry_transient_then_success_records_backoff() -> None:
    calls = 0
    sleeps: list[float] = []
    attempts: list[RetryAttempt] = []

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise BrowserTimeoutError("temporary")
        return "ok"

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    result = asyncio.run(retry_async(
        operation,
        policy=RetryPolicy(3, 1.0, 2.0, 10.0, 0.5),
        should_retry=lambda exc: isinstance(exc, BrowserTimeoutError),
        sleep=fake_sleep,
        on_retry=attempts.append,
        random_jitter=lambda _a, _b: 0.25,
    ))
    assert result == "ok"
    assert calls == 3
    assert sleeps == [1.25, 2.25]
    assert [item.attempt for item in attempts] == [1, 2]


def test_retry_exhaustion_and_permanent_manual_error() -> None:
    calls = 0

    async def transient() -> None:
        nonlocal calls
        calls += 1
        raise BrowserTimeoutError("temporary", phase="CDHA", operation="resolve")

    with pytest.raises(RetryExhaustedError) as caught:
        asyncio.run(retry_async(
            transient,
            policy=RetryPolicy(2, 0.0, 2.0, 0.0, 0.0),
            should_retry=lambda exc: getattr(exc, "retryable", False),
            sleep=lambda _delay: asyncio.sleep(0),
        ))
    assert calls == 2
    assert caught.value.details["attempts"] == 2

    async def manual() -> None:
        raise AuthenticationRequiredError("login")

    with pytest.raises(AuthenticationRequiredError):
        asyncio.run(retry_async(
            manual,
            policy=RetryPolicy(3, 0.0, 2.0, 0.0, 0.0),
            should_retry=lambda exc: getattr(exc, "retryable", False),
            sleep=lambda _delay: asyncio.sleep(0),
        ))
