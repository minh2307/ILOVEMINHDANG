"""Bounded asynchronous retry for safe, idempotent operations."""
from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.errors import PipelineError, RetryExhaustedError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    initial_delay_seconds: float
    multiplier: float
    max_delay_seconds: float
    jitter_seconds: float

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays cannot be negative")
        if self.multiplier < 1:
            raise ValueError("retry multiplier must be at least 1")
        if self.jitter_seconds < 0:
            raise ValueError("retry jitter cannot be negative")

    def delay_for(self, failed_attempt: int, jitter: float = 0.0) -> float:
        base = self.initial_delay_seconds * self.multiplier ** max(0, failed_attempt - 1)
        return min(base, self.max_delay_seconds) + max(0.0, jitter)


@dataclass(frozen=True, slots=True)
class RetryAttempt:
    attempt: int
    max_attempts: int
    delay_seconds: float
    error: Exception


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    should_retry: Callable[[Exception], bool],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_retry: Callable[[RetryAttempt], Awaitable[None] | None] | None = None,
    random_jitter: Callable[[float, float], float] = random.uniform,
) -> T:
    """Retry only when the caller proves the operation is safe to repeat."""
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            if not should_retry(exc):
                raise
            if attempt >= policy.max_attempts:
                phase = exc.phase if isinstance(exc, PipelineError) else None
                operation_name = exc.operation if isinstance(exc, PipelineError) else None
                raise RetryExhaustedError(
                    f"Retry attempts exhausted after {attempt}: {exc}",
                    phase=phase,
                    operation=operation_name,
                    details={"attempts": attempt, "cause_type": type(exc).__name__},
                ) from exc
            jitter = random_jitter(0.0, policy.jitter_seconds)
            delay = policy.delay_for(attempt, jitter)
            retry = RetryAttempt(attempt, policy.max_attempts, delay, exc)
            if on_retry is not None:
                callback_result = on_retry(retry)
                if inspect.isawaitable(callback_result):
                    await callback_result
            await sleep(delay)
    raise AssertionError("retry loop exited unexpectedly")
