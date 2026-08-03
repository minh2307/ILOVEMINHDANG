from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.browser.facebook_browser_manager import FacebookBrowserManager
from app.config.facebook_browser import FacebookBrowserConfig
from app.config.settings import Settings
from app.infrastructure.browser.file_browser_lock import (
    BrowserLockUnavailable,
    FileBrowserLock,
)


def load_official_browser_configuration(
    *, env_file: Path | None = None
) -> tuple[Settings, FacebookBrowserConfig]:
    settings = Settings.from_env(env_file=env_file)
    config = FacebookBrowserConfig.from_settings(settings)
    settings.assert_browser_config_matches(config, "browser CLI")
    return settings, config


def _build_lock(settings: Settings, config: FacebookBrowserConfig) -> FileBrowserLock:
    return FileBrowserLock(
        str(config.lock_path),
        process_name="cdha-browser-cli",
        browser_profile=str(config.profile_path),
        browser_port=config.cdp_port,
        timeout_seconds=settings.browser_lock_timeout_seconds,
        heartbeat_seconds=settings.browser_lock_heartbeat_seconds,
    )


async def _run(command: str, force: bool) -> int:
    settings, config = load_official_browser_configuration()
    if command == "endpoint":
        print(config.cdp_url)
        return 0
    if command == "config":
        payload = settings.sanitized_runtime_configuration()
        payload["configuration_fingerprint"] = settings.configuration_fingerprint()
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    browser_lock = _build_lock(settings, config)
    manager = FacebookBrowserManager(
        settings=settings,
        config=config,
        browser_lock=browser_lock,
    )
    if command == "check":
        ready = await manager.is_cdp_ready()
        print(json.dumps({
            "ready": ready,
            "cdp_url": config.cdp_url,
            "profile_path": str(config.profile_path),
            "lock_path": str(config.lock_path),
            "configuration_fingerprint": settings.configuration_fingerprint(),
        }, ensure_ascii=False, sort_keys=True))
        return 0 if ready else 1
    try:
        async with browser_lock.hold(f"browser-cli:{command}"):
            if command == "start":
                await manager.ensure_chrome()
                print(json.dumps({
                    "ready": True,
                    "pid": manager.browser_process_id,
                    "profile_path": str(config.profile_path),
                    "configuration_fingerprint": settings.configuration_fingerprint(),
                }, ensure_ascii=False, sort_keys=True))
                return 0
            if command == "stop":
                stopped = await manager.shutdown_browser(force=force)
                print(json.dumps({"stopped": stopped, "profile_path": str(config.profile_path)}))
                return 0
    except BrowserLockUnavailable as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    raise ValueError(f"Unsupported browser command: {command}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Operate the shared official Chrome session")
    parser.add_argument(
        "command", choices=("start", "check", "stop", "worker", "endpoint", "config")
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.command == "worker":
        print("Deprecated browser worker command; delegating to the official worker.")
        from app.main import main as application_main

        return application_main(["worker"])
    return asyncio.run(_run(args.command, args.force))


if __name__ == "__main__":
    raise SystemExit(main())
