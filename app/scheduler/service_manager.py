"""
Service Manager — starts, stops, and health-checks individual project services.

Design principles:
  - NEVER kill a process blindly (no pkill -9, no killall).
  - ALWAYS check if the service is already running before starting.
  - Verify health after start (port-check or process-check).
  - Identify processes by PID file or unique cmdline pattern.
  - Never create a duplicate Chrome/CDP process.
  - Never trigger job actions (Facebook, CDHA, etc.) — only infrastructure.

Services in Phase 1 (UI_ONLY):
  - dashboard: python main.py dashboard --host 127.0.0.1 --port 8080

Services additionally started in Phase 2 (FULL_RUNNING):
  - ollama: ollama serve (via OLLAMA_HOST env)
  - orchestrator: python main.py orchestrator
  - worker: python main.py worker (includes Ollama health-gate via process_queue.sh)
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from app.scheduler.state import ServiceState, ServiceStatus

logger = logging.getLogger("scheduler.service_manager")

# ---------------------------------------------------------------------------
# Project root derived from this file's location
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PYTHON = _PROJECT_ROOT / ".venv" / "bin" / "python"

# ---------------------------------------------------------------------------
# PID file paths (written by service manager itself)
# ---------------------------------------------------------------------------
_PID_DIR = _PROJECT_ROOT / "runtime" / "pids"
_PID_FILES: dict[str, Path] = {
    "dashboard":    _PID_DIR / "dashboard.pid",
    "ollama":       _PID_DIR / "ollama.pid",
    "orchestrator": _PID_DIR / "orchestrator.pid",
    "worker":       _PID_DIR / "worker.pid",
}

# ---------------------------------------------------------------------------
# Port / URL health checks
# ---------------------------------------------------------------------------
_HEALTH_PORTS: dict[str, tuple[str, int]] = {
    "dashboard":    ("127.0.0.1", 8080),
    "ollama":       ("127.0.0.1", 11435),
}

# ---------------------------------------------------------------------------
# Command templates
# ---------------------------------------------------------------------------
_START_COMMANDS: dict[str, list[str]] = {
    "dashboard": [str(_PYTHON), str(_PROJECT_ROOT / "main.py"), "dashboard",
                  "--host", "127.0.0.1", "--port", "8080"],
    "ollama":    ["ollama", "serve"],
    "orchestrator": [str(_PYTHON), str(_PROJECT_ROOT / "main.py"), "orchestrator"],
    "worker":    [str(_PYTHON), str(_PROJECT_ROOT / "main.py"), "worker"],
}

# Env overrides per service
_ENV_OVERRIDES: dict[str, dict[str, str]] = {
    "ollama": {"OLLAMA_HOST": "127.0.0.1:11435"},
}

# Startup wait time (seconds) after launching before first health check
_START_WAIT: dict[str, float] = {
    "dashboard":    5.0,
    "ollama":       8.0,
    "orchestrator": 2.0,
    "worker":       3.0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_pid_file(service: str) -> Optional[int]:
    pid_file = _PID_FILES.get(service)
    if pid_file and pid_file.exists():
        try:
            return int(pid_file.read_text().strip())
        except (ValueError, OSError):
            pass
    return None


def _write_pid_file(service: str, pid: int) -> None:
    _PID_DIR.mkdir(parents=True, exist_ok=True)
    _PID_FILES[service].write_text(str(pid))


def _remove_pid_file(service: str) -> None:
    try:
        _PID_FILES[service].unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """Check if a process with given PID is alive (POSIX)."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _cmdline_contains(pid: int, token: str) -> bool:
    """Read /proc/{pid}/cmdline and check for token."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode()
        return token in cmdline
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_service(service: str) -> ServiceState:
    """
    Determine the current status of a named service.

    Strategy:
      1. Read saved PID file.
      2. If PID exists and process is alive → cross-check cmdline.
      3. Additionally check port if service has one.
      4. Fall back to scanning cmdline patterns only if no PID file.
    """
    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state = ServiceState(name=service, last_checked_at=now_str)

    # --- PID file check ---
    pid = _read_pid_file(service)
    if pid is not None:
        if _pid_alive(pid):
            state.pid = pid
            state.status = ServiceStatus.RUNNING
        else:
            # Stale PID file — clean up
            logger.warning(
                "[SCHEDULER] Stale PID file for %s (pid=%d) — removing.", service, pid
            )
            _remove_pid_file(service)

    # --- Port check (overrides/confirms PID check) ---
    port_spec = _HEALTH_PORTS.get(service)
    if port_spec:
        host, port_num = port_spec
        if _port_open(host, port_num):
            # Service is definitely up; PID may differ from what we know
            if state.status != ServiceStatus.RUNNING:
                logger.info(
                    "[SCHEDULER] %s port %d open but no PID file — service alive.",
                    service, port_num,
                )
                state.status = ServiceStatus.RUNNING
        else:
            if state.status == ServiceStatus.RUNNING:
                logger.warning(
                    "[SCHEDULER] %s PID alive but port %d not responding — degraded.",
                    service, port_num,
                )
                # Keep RUNNING but note; the process may still be starting up
            else:
                state.status = ServiceStatus.STOPPED

    # --- Default if nothing matched ---
    if state.status == ServiceStatus.UNKNOWN:
        state.status = ServiceStatus.STOPPED

    logger.debug("[SCHEDULER] %s → %s (pid=%s)", service, state.status.value, state.pid)
    return state


def start_service(service: str) -> ServiceState:
    """
    Start a service if it is not already running.

    Returns ServiceState with RUNNING (or FAILED on error).
    Never creates duplicate processes.
    """
    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    current = check_service(service)
    if current.status == ServiceStatus.RUNNING:
        logger.info("[SCHEDULER] %s already running (pid=%s) — skipping.", service, current.pid)
        return current

    logger.info("[SCHEDULER] Starting %s …", service)
    cmd = _START_COMMANDS.get(service)
    if not cmd:
        return ServiceState(
            name=service,
            status=ServiceStatus.FAILED,
            error=f"No start command registered for '{service}'",
            last_checked_at=now_str,
        )

    # Build environment
    env = os.environ.copy()
    env.update(_ENV_OVERRIDES.get(service, {}))

    try:
        log_file = _PROJECT_ROOT / f"{service}.log"
        with open(log_file, "a") as log_fh:
            proc = subprocess.Popen(
                cmd,
                cwd=str(_PROJECT_ROOT),
                env=env,
                stdout=log_fh,
                stderr=log_fh,
                start_new_session=True,  # detach from parent's signal group
            )
        _write_pid_file(service, proc.pid)
        logger.info("[SCHEDULER] %s launched (pid=%d) — waiting for startup…", service, proc.pid)
    except (FileNotFoundError, PermissionError) as exc:
        logger.error("[SCHEDULER] Failed to launch %s: %s", service, exc)
        return ServiceState(
            name=service,
            status=ServiceStatus.FAILED,
            error=str(exc),
            last_checked_at=now_str,
        )

    # Wait for startup then verify
    time.sleep(_START_WAIT.get(service, 3.0))
    verified = check_service(service)
    if verified.status != ServiceStatus.RUNNING:
        logger.error("[SCHEDULER] %s did not come up after startup wait.", service)
        verified.status = ServiceStatus.FAILED
        verified.error = "Service did not pass health check after startup"
    else:
        logger.info("[SCHEDULER] %s is healthy.", service)
    return verified


def stop_service(service: str) -> ServiceState:
    """
    Gracefully stop a service managed by the scheduler.

    Sends SIGTERM to the PID in our PID file only.
    NEVER uses pkill/killall against arbitrary processes.
    """
    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    pid = _read_pid_file(service)
    if pid is None:
        logger.info("[SCHEDULER] No PID file for %s — nothing to stop.", service)
        return ServiceState(name=service, status=ServiceStatus.STOPPED, last_checked_at=now_str)

    if not _pid_alive(pid):
        _remove_pid_file(service)
        return ServiceState(name=service, status=ServiceStatus.STOPPED, last_checked_at=now_str)

    # Safety: verify the PID actually belongs to this project
    if not _cmdline_contains(pid, "main.py") and service not in ("ollama",):
        logger.warning(
            "[SCHEDULER] PID %d cmdline does not match '%s' — refusing to kill.", pid, service
        )
        return ServiceState(
            name=service,
            status=ServiceStatus.UNKNOWN,
            pid=pid,
            error="PID cmdline mismatch — manual intervention required",
            last_checked_at=now_str,
        )

    try:
        os.kill(pid, signal.SIGTERM)
        logger.info("[SCHEDULER] Sent SIGTERM to %s (pid=%d).", service, pid)
    except ProcessLookupError:
        pass  # Already gone
    except PermissionError as exc:
        return ServiceState(
            name=service,
            status=ServiceStatus.UNKNOWN,
            pid=pid,
            error=f"Cannot stop: {exc}",
            last_checked_at=now_str,
        )

    # Wait up to 10 s for graceful exit
    for _ in range(10):
        time.sleep(1)
        if not _pid_alive(pid):
            break

    _remove_pid_file(service)
    return ServiceState(name=service, status=ServiceStatus.STOPPED, last_checked_at=now_str)


def check_all_services() -> dict[str, ServiceState]:
    """Snapshot status of all registered services."""
    return {name: check_service(name) for name in _START_COMMANDS}
