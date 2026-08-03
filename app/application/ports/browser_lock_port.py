from typing import Protocol

class BrowserLockPort(Protocol):
    async def acquire(self, job_id: str | None = None) -> bool:
        ...

    async def release(self, owner_token: str | None = None) -> bool:
        ...
