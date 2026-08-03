from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.domain.models.job_result import JobResult
from app.main import build_parser, main


class FakeSettings:
    def ensure_runtime_directories(self) -> None:
        return None


class Recorder:
    def __init__(self, result: JobResult) -> None:
        self.result = result
        self.calls: list[tuple[tuple, dict]] = []

    async def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class ConfirmRecorder(Recorder):
    @staticmethod
    def expected_phrase(job_id: str) -> str:
        return f"PUBLISH {job_id}"


class WorkerRecorder:
    def __init__(self) -> None:
        self.once_calls = 0
        self.start_calls = 0

    async def start_once(self) -> bool:
        self.once_calls += 1
        return True

    async def start(self) -> None:
        self.start_calls += 1


class SchedulerRecorder:
    def __init__(self) -> None:
        self.calls = 0

    async def schedule_once(self) -> int:
        self.calls += 1
        return 0


class FakeContainer:
    def __init__(self) -> None:
        ok = lambda job_id="job": JobResult.success_result(
            job_id,
            {"workflow_status": "CREATED", "queued": True, "exit_code": 0},
        )
        self.create_job = Recorder(ok("created"))
        self.get_job_status = Recorder(
            JobResult.success_result(
                "status", {"job": {"job_id": "status", "status": "CREATED"}}
            )
        )
        self.resume_job = Recorder(ok("resume"))
        self.retry_job = Recorder(ok("retry"))
        self.cancel_job = Recorder(ok("cancel"))
        self.review_job = Recorder(
            JobResult.success_result(
                "review",
                {
                    "workflow_status": "APPROVED",
                    "queued": True,
                    "decision": "approved",
                    "exit_code": 0,
                },
            )
        )
        self.confirm_publish = ConfirmRecorder(ok("publish"))
        self.worker = WorkerRecorder()
        self.scheduler = SchedulerRecorder()


@pytest.fixture
def cli(monkeypatch):
    containers: list[FakeContainer] = []

    def factory(_settings):
        container = FakeContainer()
        containers.append(container)
        return container

    monkeypatch.setattr("app.main.Settings.from_env", lambda: FakeSettings())
    monkeypatch.setattr("app.main.configure_logging", lambda _settings: None)
    monkeypatch.setattr("app.bootstrap.DependencyContainer", factory)
    return containers


@pytest.mark.parametrize(
    ("argv", "attribute", "expected_args", "expected_kwargs"),
    [
        (
            ["create-job", "--url", "https://facebook.com/reel/1"],
            "create_job",
            ("https://facebook.com/reel/1",),
            {"force": False},
        ),
        (["status", "--job-id", "j1"], "get_job_status", ("j1",), {}),
        (["resume", "--job-id", "j2"], "resume_job", ("j2",), {}),
        (
            ["retry", "--job-id", "j3"],
            "retry_job",
            ("j3",),
            {"requested_by": "cli"},
        ),
        (["cancel", "--job-id", "j4"], "cancel_job", ("j4",), {}),
        (["review", "--job-id", "j5"], "review_job", ("j5",), {}),
    ],
)
def test_official_commands_invoke_their_application_use_case(
    cli, argv, attribute, expected_args, expected_kwargs
):
    assert main(argv) == 0

    recorder = getattr(cli[-1], attribute)
    assert recorder.calls == [(expected_args, expected_kwargs)]


def test_confirm_publish_invokes_official_use_case(cli, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "PUBLISH publish-1")

    assert main(["confirm-publish", "--job-id", "publish-1"]) == 0

    assert cli[-1].confirm_publish.calls == [
        (("publish-1",), {"confirmation": "PUBLISH publish-1"})
    ]


@dataclass
class FakePreflightReport:
    overall_status: str = "PASS"

    def to_dict(self):
        return {"overall_status": self.overall_status}


def test_worker_command_invokes_official_worker_runner(cli, monkeypatch):
    monkeypatch.setattr(
        "app.config.facebook_browser.FacebookBrowserConfig.from_settings",
        lambda _settings: object(),
    )
    monkeypatch.setattr(
        "app.preflight.run_preflight",
        lambda _settings, _browser_config, **_kwargs: FakePreflightReport(),
    )

    assert main(["worker", "--once"]) == 0
    assert cli[-1].worker.once_calls == 1
    assert cli[-1].worker.start_calls == 0


def test_preflight_command_uses_mode_and_exit_code(cli, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        "app.config.facebook_browser.FacebookBrowserConfig.from_settings",
        lambda _settings: object(),
    )
    monkeypatch.setattr(
        "app.preflight.run_preflight",
        lambda _settings, _browser_config, **kwargs: (
            calls.append(kwargs) or FakePreflightReport("FAIL")
        ),
    )
    monkeypatch.setattr(
        "app.preflight.format_preflight_report",
        lambda report, **_kwargs: f"Overall verdict: {report.overall_status}",
    )

    assert main(["preflight", "--mode", "full", "--verbose"]) == 1
    assert calls == [{"mode": "full"}]
    assert "Overall verdict: FAIL" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("legacy_argv", "attribute", "expected_job_id"),
    [
        (["--reel-url", "https://facebook.com/reel/legacy"], "create_job", None),
        (["--resume-job", "legacy-resume"], "resume_job", "legacy-resume"),
        (["--review-job", "legacy-review"], "review_job", "legacy-review"),
        (
            ["--continue-approved-job", "legacy-approved"],
            "resume_job",
            "legacy-approved",
        ),
    ],
)
def test_legacy_flags_delegate_and_warn(
    cli, capsys, monkeypatch, legacy_argv, attribute, expected_job_id
):
    monkeypatch.setattr(
        "app.main._run_pipeline_command",
        lambda *_args, **_kwargs: pytest.fail("legacy pipeline must not run"),
    )

    assert main(legacy_argv) == 0

    warning = capsys.readouterr().err
    assert "Deprecated:" in warning
    recorder = getattr(cli[-1], attribute)
    assert len(recorder.calls) == 1
    if expected_job_id is not None:
        assert recorder.calls[0][0] == (expected_job_id,)


def test_stage_only_legacy_command_fails_without_running_pipeline(
    cli, capsys, monkeypatch
):
    monkeypatch.setattr(
        "app.main._run_phase3_command",
        lambda *_args, **_kwargs: pytest.fail("legacy phase 3 must not run"),
    )

    assert main(["--analyze-cdha", "legacy-job"]) == 2
    assert "no safe official equivalent" in capsys.readouterr().err
    assert cli == []


def test_invalid_command_returns_non_zero_and_useful_message(capsys):
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["does-not-exist"])

    assert caught.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_official_dependency_graph_has_no_legacy_orchestration_alias() -> None:
    root = Path(__file__).resolve().parents[1]
    official_sources = (
        root / "app" / "main.py",
        root / "app" / "bootstrap.py",
        root / "app" / "infrastructure" / "workflow" / "verified_workflow_stage_adapter.py",
        root / "app" / "application" / "use_cases" / "process_job_use_case.py",
        root / "workers" / "facebook_browser_worker.py",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in official_sources)

    assert "CDHAPipeline" not in combined
    assert "repository.transition(" not in (root / "app" / "main.py").read_text(
        encoding="utf-8"
    )


def test_active_source_has_one_status_enum_and_one_transition_map() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    active_files = [
        path
        for path in root.rglob("*.py")
        if "infrastructure/legacy" not in path.as_posix()
    ]
    enum_definitions: list[Path] = []
    transition_maps: list[Path] = []
    for path in active_files:
        source = path.read_text(encoding="utf-8")
        if "class JobStatus(StrEnum):" in source:
            enum_definitions.append(path)
        if "_transitions: dict[JobStatus" in source:
            transition_maps.append(path)

    assert [path.relative_to(root).as_posix() for path in enum_definitions] == [
        "domain/enums/job_status.py"
    ]
    assert [path.relative_to(root).as_posix() for path in transition_maps] == [
        "domain/rules/state_transitions.py"
    ]
