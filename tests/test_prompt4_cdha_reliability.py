from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser.cdha_client import CDHAWebClient
from app.browser.cdha_state import (
    AuthenticationState,
    CDHAState,
    CDHAStateSnapshot,
    CDHAStateTimeoutError,
    wait_for_cdha_state,
)
from app.browser.facebook_browser_manager import FacebookBrowserManager
from app.browser.facebook_job_client import FacebookJobClient
from app.browser.selector_resolver import SelectorResolver
from app.config.facebook_browser import FacebookBrowserConfig
from app.config.settings import Settings
from app.domain.models.browser_health import BrowserHealthState
from app.errors import BrowserPageOwnershipError, QueueLeaseExpiredError
from app.errors import (
    CDHAControlDisabledError,
    CDHAControlHiddenError,
    CDHASelectorMismatchError,
)
from app.application.use_cases.inspect_runtime_use_cases import (
    InspectBrowserUseCase,
    InspectQueueUseCase,
)
from app.domain.models.browser_health import BrowserHealth
from app.infrastructure.persistence.sqlite_job_queue import SQLiteJobQueue
from app.main import build_parser
from app.domain.enums.facebook_job_type import FacebookJobType
from app.domain.models.facebook_job import FacebookJob
from workers.facebook_browser_worker import FacebookBrowserWorker


def make_settings(tmp_path: Path, **changes: Any) -> Settings:
    settings = replace(
        Settings.from_env(env_file=tmp_path / "missing.env"),
        database_path=tmp_path / "jobs.sqlite3",
        job_data_dir=tmp_path / "jobs",
        diagnostic_directory=tmp_path / "diagnostics",
        chrome_profile_dir=tmp_path / "profile",
        browser_lock_path=tmp_path / "locks/browser.lock",
        browser_pid_path=tmp_path / "pids/browser.pid",
        browser_download_dir=tmp_path / "downloads",
    )
    return replace(settings, **changes)


class FakePage:
    def __init__(self, *, closed: bool = False) -> None:
        self.closed = closed
        self.close_calls = 0
        self.url = "https://cdha.ai/dash"

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class FakeContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []

    async def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected

    def is_connected(self) -> bool:
        return self.connected


def manager_with_session(tmp_path: Path) -> FacebookBrowserManager:
    settings = make_settings(tmp_path)
    manager = FacebookBrowserManager(
        settings=settings,
        config=FacebookBrowserConfig.from_settings(settings),
    )
    manager.browser = FakeBrowser()
    manager.context = FakeContext()
    return manager


@pytest.mark.asyncio
async def test_manager_releases_only_tracked_temporary_page(tmp_path: Path) -> None:
    manager = manager_with_session(tmp_path)
    page = await manager.acquire_page("cdha:job-1")
    foreign = FakePage()

    with pytest.raises(BrowserPageOwnershipError):
        await manager.release_page(foreign)
    assert foreign.close_calls == 0

    assert await manager.release_page(page) is True
    assert page.close_calls == 1
    assert manager.browser.is_connected()


@pytest.mark.asyncio
async def test_manager_distinguishes_browser_context_and_page_health(
    tmp_path: Path,
) -> None:
    manager = manager_with_session(tmp_path)
    page = FakePage()
    assert (await manager.get_health(page)).state is BrowserHealthState.CONNECTED

    page.closed = True
    assert (await manager.get_health(page)).state is BrowserHealthState.PAGE_CLOSED

    page.closed = False
    manager._context_closed = True
    assert (await manager.get_health(page)).state is BrowserHealthState.CONTEXT_CLOSED

    manager._context_closed = False
    manager.browser.connected = False
    assert (await manager.get_health(page)).state is BrowserHealthState.DISCONNECTED


class ProbeLocator:
    def __init__(self, *, attached: bool, visible: bool, enabled: bool) -> None:
        self.attached = attached
        self.visible = visible
        self.enabled = enabled
        self.clicks = 0

    @property
    def first(self) -> "ProbeLocator":
        return self

    async def wait_for(self, *, state: str, **_: Any) -> None:
        if state == "attached" and not self.attached:
            raise TimeoutError("missing")
        if state == "visible" and not self.visible:
            raise TimeoutError("not visible")

    async def is_visible(self) -> bool:
        return self.visible

    async def is_enabled(self) -> bool:
        return self.enabled

    async def click(self, **_: Any) -> None:
        self.clicks += 1


class ProbePage:
    url = "https://cdha.ai/dash"

    def __init__(self, locators: dict[str, ProbeLocator]) -> None:
        self.locators = locators

    def locator(self, selector: str) -> ProbeLocator:
        return self.locators[selector]

    def get_by_role(
        self, role: str, *, name: str | None, exact: bool
    ) -> ProbeLocator:
        return self.locators[f"role={role}:{name}"]


@pytest.mark.asyncio
async def test_selector_probe_preserves_priority_and_hidden_disabled_state(
    tmp_path: Path,
) -> None:
    config = tmp_path / "selectors.yaml"
    config.write_text(
        "cdha:\n"
        "  complete:\n"
        "    - css: '#btnComplete'\n"
        "    - role: button\n"
        "      name: Hoàn tất\n",
        encoding="utf-8",
    )
    resolver = SelectorResolver(config)
    page = ProbePage(
        {
            "#btnComplete": ProbeLocator(
                attached=True, visible=False, enabled=False
            ),
            "role=button:Hoàn tất": ProbeLocator(
                attached=True, visible=True, enabled=False
            ),
        }
    )

    observations = await resolver.probe(page, "cdha.complete", timeout_ms=50)

    assert [item.priority for item in observations] == [0, 1]
    assert observations[0].attached is True
    assert observations[0].visible is False
    assert observations[1].visible is True
    assert observations[1].enabled is False


@pytest.mark.asyncio
async def test_semantic_wait_returns_accepted_state_and_logs_only_changes() -> None:
    states = iter(
        [
            CDHAState.ANALYSIS_QUEUED,
            CDHAState.ANALYSIS_QUEUED,
            CDHAState.ANALYSIS_RUNNING,
            CDHAState.RESULT_READY,
        ]
    )
    changes: list[CDHAState] = []

    async def detect() -> CDHAStateSnapshot:
        return CDHAStateSnapshot(next(states))

    result = await wait_for_cdha_state(
        detect,
        accepted_states={CDHAState.RESULT_READY},
        timeout_seconds=1,
        poll_interval_seconds=0,
        on_progress=lambda snapshot: changes.append(snapshot.state),
    )

    assert result.state is CDHAState.RESULT_READY
    assert changes == [
        CDHAState.ANALYSIS_QUEUED,
        CDHAState.ANALYSIS_RUNNING,
        CDHAState.RESULT_READY,
    ]


@pytest.mark.asyncio
async def test_semantic_wait_timeout_exposes_final_state() -> None:
    async def detect() -> CDHAStateSnapshot:
        return CDHAStateSnapshot(CDHAState.CONTROL_DISABLED)

    with pytest.raises(CDHAStateTimeoutError) as caught:
        await wait_for_cdha_state(
            detect,
            accepted_states={CDHAState.RESULT_READY},
            timeout_seconds=0,
            poll_interval_seconds=0,
        )

    assert caught.value.final_snapshot.state is CDHAState.CONTROL_DISABLED
    assert caught.value.details["current_cdha_state"] == "CONTROL_DISABLED"


@pytest.mark.asyncio
async def test_semantic_wait_cancellation_stops_polling() -> None:
    calls = 0

    async def detect() -> CDHAStateSnapshot:
        nonlocal calls
        calls += 1
        return CDHAStateSnapshot(CDHAState.ANALYSIS_RUNNING)

    task = asyncio.create_task(
        wait_for_cdha_state(
            detect,
            accepted_states={CDHAState.RESULT_READY},
            timeout_seconds=10,
            poll_interval_seconds=1,
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    observed = calls
    await asyncio.sleep(0)
    assert calls == observed


def test_timeout_configuration_is_independent_and_sanitized(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        browser_action_timeout_seconds=11,
        browser_navigation_timeout_seconds=22,
        cdha_upload_timeout_seconds=33,
        cdha_analysis_timeout_seconds=44,
        cdha_result_timeout_seconds=55,
        worker_stage_timeout_seconds=66,
    )
    timeouts = settings.sanitized_runtime_configuration()["timeouts"]

    assert len(set(timeouts.values())) == 8
    assert timeouts["browser_action_seconds"] == 11
    assert timeouts["cdha_analysis_seconds"] == 44
    assert timeouts["queue_lease_seconds"] == settings.job_lease_seconds


def test_legacy_job_client_uses_typed_worker_stage_timeout(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path, worker_stage_timeout_seconds=321)
    client = FacebookJobClient(store=object(), settings=settings)
    assert client.default_timeout_seconds == 321


def test_zero_cdha_result_timeout_fails_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CDHA_RESULT_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError, match="CDHA_RESULT_TIMEOUT_SECONDS"):
        Settings.from_env(tmp_path / "missing.env")


def test_cdha_submission_fingerprint_uses_stable_source_identity(
    tmp_path: Path,
) -> None:
    settings = make_settings(tmp_path)
    client = CDHAWebClient(settings, object(), object())
    job = SimpleNamespace(
        job_id="job-1",
        normalized_source_url="https://facebook.com/reel/1",
        source_url="https://facebook.com/reel/1?tracking=ignored",
        data={"checksum_sha256": "a" * 64},
    )

    first = client.submission_fingerprint(job)
    second = client.submission_fingerprint(job)
    job.data["checksum_sha256"] = "b" * 64

    assert first == second
    assert len(first) == 64
    assert client.submission_fingerprint(job) != first


def test_existing_external_analysis_id_reconstructs_result_url(
    tmp_path: Path,
) -> None:
    client = CDHAWebClient(make_settings(tmp_path), object(), object())
    job = SimpleNamespace(data={"cdha_external_analysis_id": "44069"})

    result = client.existing_analysis_url(job)

    assert "view=44069" in result
    assert "modality=" not in result


class CompletionClient(CDHAWebClient):
    def __init__(self, *args: Any, frame: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.frame = frame

    async def _resolve_upload_frame(self, page: Any, **_: Any) -> Any:
        return self.frame

    async def _view_url_value(self, page: Any) -> str:
        return ""

    async def _wait_for_upload(self, page: Any) -> str:
        return "https://storage.invalid/upload.mp4"


def completion_client(
    tmp_path: Path, locators: dict[str, ProbeLocator]
) -> tuple[CompletionClient, ProbePage]:
    config = tmp_path / "selectors.yaml"
    config.write_text(
        "cdha:\n"
        "  upload_complete_button:\n"
        "    - css: '#btnComplete'\n"
        "    - role: button\n"
        "      name: Hoàn tất\n",
        encoding="utf-8",
    )
    settings = replace(
        make_settings(tmp_path),
        selectors_path=config,
        cdha_upload_timeout_seconds=0,
    )
    frame = ProbePage(locators)
    return (
        CompletionClient(
            settings, object(), object(), resolver=SelectorResolver(config), frame=frame
        ),
        frame,
    )


@pytest.mark.parametrize(
    ("primary", "fallback", "expected"),
    [
        (
            ProbeLocator(attached=True, visible=False, enabled=False),
            ProbeLocator(attached=False, visible=False, enabled=False),
            CDHAControlHiddenError,
        ),
        (
            ProbeLocator(attached=True, visible=True, enabled=False),
            ProbeLocator(attached=False, visible=False, enabled=False),
            CDHAControlDisabledError,
        ),
        (
            ProbeLocator(attached=False, visible=False, enabled=False),
            ProbeLocator(attached=False, visible=False, enabled=False),
            CDHASelectorMismatchError,
        ),
    ],
)
def test_complete_upload_distinguishes_hidden_disabled_and_missing(
    tmp_path: Path,
    primary: ProbeLocator,
    fallback: ProbeLocator,
    expected: type[Exception],
) -> None:
    client, _frame = completion_client(
        tmp_path,
        {
            "#btnComplete": primary,
            "role=button:Hoàn tất": fallback,
        },
    )
    with pytest.raises(expected):
        asyncio.run(client._complete_upload(object()))
    assert primary.clicks == 0
    assert fallback.clicks == 0


def test_complete_upload_uses_semantic_fallback_without_broad_button(
    tmp_path: Path,
) -> None:
    primary = ProbeLocator(attached=False, visible=False, enabled=False)
    fallback = ProbeLocator(attached=True, visible=True, enabled=True)
    client, _frame = completion_client(
        tmp_path,
        {
            "#btnComplete": primary,
            "role=button:Hoàn tất": fallback,
        },
    )

    asyncio.run(client._complete_upload(object()))

    assert primary.clicks == 0
    assert fallback.clicks == 1
    candidates = client.resolver.candidates("cdha.upload_complete_button")
    assert not any(
        item == "button" or (isinstance(item, dict) and item.get("css") == "button")
        for item in candidates
    )


class AuthenticationResolver:
    def __init__(self, present: set[str]) -> None:
        self.present = present

    async def exists(self, page: Any, key: str, **_: Any) -> bool:
        return key in self.present


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("cdha.authenticated_marker", AuthenticationState.AUTHENTICATED),
        ("cdha.login_markers", AuthenticationState.LOGIN_REQUIRED),
        ("cdha.two_factor_markers", AuthenticationState.TWO_FACTOR_REQUIRED),
        ("cdha.checkpoint_markers", AuthenticationState.CHECKPOINT_REQUIRED),
        ("cdha.session_expired", AuthenticationState.SESSION_EXPIRED),
        ("cdha.permission_denied", AuthenticationState.PERMISSION_DENIED),
    ],
)
def test_cdha_authentication_states_are_not_generic_selector_errors(
    tmp_path: Path, marker: str, expected: AuthenticationState
) -> None:
    client = CDHAWebClient(
        make_settings(tmp_path),
        object(),
        object(),
        resolver=AuthenticationResolver({marker}),
    )
    page = SimpleNamespace(url="https://cdha.ai/dash")
    assert asyncio.run(client.detect_authentication_state(page)) is expected


@pytest.mark.asyncio
async def test_queue_heartbeat_persists_current_stage(tmp_path: Path) -> None:
    queue = SQLiteJobQueue(str(tmp_path / "queue.db"))
    job = FacebookJob("job-stage", FacebookJobType.CREATE_POST, {})
    await queue.enqueue(job)
    assert await queue.dequeue(worker_id="worker-a", lease_seconds=10)

    assert await queue.heartbeat(
        job.job_id,
        worker_id="worker-a",
        lease_seconds=10,
        current_stage="CDHA_ANALYZING",
    )
    record = await queue.get_record(job.job_id)
    assert record["current_stage"] == "CDHA_ANALYZING"
    assert record["last_heartbeat"]


@pytest.mark.asyncio
async def test_recovery_blocks_job_when_max_attempts_would_be_exceeded(
    tmp_path: Path,
) -> None:
    queue = SQLiteJobQueue(str(tmp_path / "queue.db"))
    job = FacebookJob(
        "job-exhausted",
        FacebookJobType.CREATE_POST,
        {},
        attempt_count=0,
        max_attempts=1,
    )
    await queue.enqueue(job)
    assert await queue.dequeue(worker_id="dead-worker", lease_seconds=10)
    with queue._connect() as connection:
        connection.execute(
            "UPDATE queue SET attempt_count=1, lease_expires_at=0 WHERE job_id=?",
            (job.job_id,),
        )

    assert await queue.recover_jobs() == 1
    record = await queue.get_record(job.job_id)
    assert record["status"] == "BLOCKED"
    assert record["attempt_count"] == 2


@pytest.mark.asyncio
async def test_queue_inspection_never_exposes_payload(tmp_path: Path) -> None:
    queue = SQLiteJobQueue(str(tmp_path / "queue.db"))
    secret = "patient@example.com token=secret"
    await queue.enqueue(
        FacebookJob(
            "job-inspect",
            FacebookJobType.CREATE_POST,
            {"clinical_factors": secret},
        )
    )

    result = await InspectQueueUseCase(queue).execute()
    rendered = json.dumps(result.data)

    assert result.success is True
    assert secret not in rendered
    assert "payload" not in rendered


@pytest.mark.asyncio
async def test_browser_inspection_is_read_only_and_does_not_start_chrome() -> None:
    class InspectManager:
        async def get_health(self) -> BrowserHealth:
            return BrowserHealth(
                BrowserHealthState.DISCONNECTED,
                browser_connected=False,
                context_available=False,
            )

        async def is_cdp_ready(self) -> bool:
            return False

        def managed_pid(self) -> None:
            return None

        async def ensure_chrome(self) -> None:
            raise AssertionError("inspection must not start Chrome")

    lock = SimpleNamespace(read_metadata=lambda: {})
    result = await InspectBrowserUseCase(InspectManager(), lock).execute()

    assert result.success is True
    assert result.data["cdp_ready"] is False
    assert result.data["browser_health_state"] == "DISCONNECTED"


class LeaseLossQueue(SQLiteJobQueue):
    async def heartbeat(self, *args: Any, **kwargs: Any) -> bool:
        return False


class OwnedLock:
    async def acquire(self, job_id: str | None = None) -> bool:
        return True

    async def release(self) -> bool:
        return True


class CancellableDispatcher:
    def __init__(self) -> None:
        self.cancelled = False

    async def dispatch(self, job: Any) -> Any:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_worker_cancels_dispatch_when_queue_lease_is_lost(
    tmp_path: Path,
) -> None:
    queue = LeaseLossQueue(str(tmp_path / "queue.db"))
    job = FacebookJob("job-lease", FacebookJobType.CREATE_POST, {})
    await queue.enqueue(job)
    dispatcher = CancellableDispatcher()
    worker = FacebookBrowserWorker(
        queue,
        OwnedLock(),
        dispatcher,
        queue_lease_seconds=0.1,
        queue_heartbeat_seconds=0.01,
        retry_base_seconds=0,
        retry_max_seconds=0,
        retry_jitter_seconds=0,
    )

    assert await asyncio.wait_for(worker.run_once(), timeout=1) is True
    assert dispatcher.cancelled is True
    record = await queue.get_record(job.job_id)
    assert record["status"] == "RETRYABLE"
    assert "lease" in record["error_message"].casefold()


class ClosedDiagnosticPage(FakePage):
    async def screenshot(self, **_: Any) -> None:
        raise AssertionError("closed page must not be screenshotted")

    async def title(self) -> str:
        raise AssertionError("closed page title must not be read")

    async def content(self) -> str:
        raise AssertionError("closed page HTML must not be read")


@pytest.mark.asyncio
async def test_closed_page_diagnostics_still_write_machine_readable_summary(
    tmp_path: Path,
) -> None:
    manager = manager_with_session(tmp_path)
    page = ClosedDiagnosticPage(closed=True)

    paths = await manager.save_diagnostics(
        page,
        tmp_path / "diagnostics",
        "closed",
        details={
            "job_id": "job-1",
            "workflow_stage": "CDHA_ANALYZING",
            "current_cdha_state": "PAGE_CLOSED",
            "access_token": "secret",
        },
    )

    metadata = next(path for path in paths if path.suffix == ".json")
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    rendered = json.dumps(payload)
    assert payload["browser_health_state"] == "PAGE_CLOSED"
    assert "secret" not in rendered
    assert not (tmp_path / "diagnostics" / "closed.png").exists()


@pytest.mark.asyncio
async def test_manager_cleanup_closes_owned_page_not_shared_context(
    tmp_path: Path,
) -> None:
    manager = manager_with_session(tmp_path)
    page = await manager.acquire_page("cdha:job-cleanup")
    context = manager.context
    browser = manager.browser

    await manager.close()

    assert page.close_calls == 1
    assert not hasattr(context, "close_calls")
    assert browser.connected is True


def test_queue_lease_error_is_structured_and_retryable() -> None:
    error = QueueLeaseExpiredError(
        "lease lost",
        job_id="job-1",
        phase="CDHA_ANALYZING",
        operation="heartbeat",
        details={"timeout_seconds": 10},
    )
    assert error.retryable is True
    assert error.error_code == "QUEUE_LEASE_EXPIRED"


@pytest.mark.parametrize("command", ["inspect-browser", "inspect-queue"])
def test_official_cli_exposes_safe_recovery_inspection_commands(
    command: str,
) -> None:
    args = build_parser().parse_args([command])
    assert args.command == command
