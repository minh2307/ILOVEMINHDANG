from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from app.browser.error_mapper import is_terminal_browser_condition, map_playwright_error
from app.error_events import safe_browser_url
from app.errors import SelectorNotFoundError
from app.services.privacy_service import PrivacyService


_PRIVACY = PrivacyService()


class SelectorResolutionError(SelectorNotFoundError, LookupError):
    pass


class SelectorResolver:
    def __init__(self, selectors_path: Path, logger: logging.Logger | None = None, *, save_html: bool = False):
        self.selectors_path = Path(selectors_path).resolve()
        self.logger = logger or logging.getLogger("cdha_pipeline.selectors")
        self.save_html = save_html
        with self.selectors_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Selector configuration must be a mapping: {self.selectors_path}")
        self._selectors: dict[str, Any] = loaded

    def candidates(self, key: str) -> tuple[Any, ...]:
        value: Any = self._selectors
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                raise KeyError(f"Unknown selector key: {key}")
            value = value[part]
        if isinstance(value, str):
            return (value,)
        if isinstance(value, dict) and self._valid_candidate(value):
            return (value,)
        if isinstance(value, list) and all(
            isinstance(item, str) or (isinstance(item, dict) and self._valid_candidate(item))
            for item in value
        ):
            return tuple(value)
        raise ValueError(f"Selector key contains an invalid selector definition: {key}")

    async def find_first(
        self,
        page: Any,
        key: str,
        *,
        timeout_ms: int = 5_000,
        visible: bool = True,
        diagnostics_dir: Path | None = None,
        context: str = "",
    ) -> Any:
        failures: list[str] = []
        candidates = self.candidates(key)
        per_selector_timeout = max(250, timeout_ms // max(1, len(candidates)))
        for candidate in candidates:
            description = self._describe(candidate)
            locator = self._locator(page, candidate).first
            try:
                await locator.wait_for(
                    state="visible" if visible else "attached",
                    timeout=per_selector_timeout,
                )
                self.logger.debug(
                    "Resolved selector",
                    extra={"selector_key": key, "selector": description, "url": safe_browser_url(str(page.url)) if hasattr(page, "url") else "(FrameLocator)"},
                )
                return locator
            except Exception as exc:
                mapped = map_playwright_error(
                    exc, phase="SELECTOR_RESOLUTION", operation=f"resolve:{key}"
                )
                if is_terminal_browser_condition(mapped):
                    raise mapped from exc
                failures.append(f"{description}: {type(exc).__name__}")
        title = _PRIVACY.mask(await page.title()) if hasattr(page, "title") else "(FrameLocator)"
        safe_url = safe_browser_url(str(page.url)) if hasattr(page, "url") else "(FrameLocator)"
        diagnostic_paths: tuple[Path, Path] | None = None
        if diagnostics_dir is not None:
            diagnostic_paths = await self._save_failure_diagnostics(
                page, diagnostics_dir, key, failures=failures, context=context
            )
        self.logger.error(
            "All selector fallbacks failed",
            extra={
                "selector_key": key,
                "url": safe_url,
                "title": title,
                "context": context,
                "failures": failures,
                "diagnostics": [str(path) for path in diagnostic_paths or ()],
            },
        )
        raise SelectorResolutionError(
            f"Unable to resolve '{key}' at {safe_url} ({title}); tried {len(candidates)} selectors",
            phase="SELECTOR_RESOLUTION",
            operation=f"resolve:{key}",
            diagnostic_paths=tuple(str(path) for path in diagnostic_paths or ()),
            details={
                "selector_key": key,
                "attempted_selectors": [self._describe(item) for item in candidates],
                "failures": failures,
            },
        )

    async def exists(self, page: Any, key: str, *, timeout_ms: int = 1_000) -> bool:
        try:
            await self.find_first(page, key, timeout_ms=timeout_ms)
            return True
        except SelectorResolutionError:
            return False

    async def click_first(
        self,
        page: Any,
        key: str,
        *,
        timeout_ms: int = 5_000,
        diagnostics_dir: Path | None = None,
        context: str = "",
    ) -> Any:
        locator = await self.find_first(
            page,
            key,
            timeout_ms=timeout_ms,
            diagnostics_dir=diagnostics_dir,
            context=context,
        )
        await locator.click()
        return locator

    async def fill_first(
        self,
        page: Any,
        key: str,
        value: str,
        *,
        timeout_ms: int = 5_000,
        diagnostics_dir: Path | None = None,
        context: str = "",
    ) -> Any:
        locator = await self.find_first(
            page,
            key,
            timeout_ms=timeout_ms,
            diagnostics_dir=diagnostics_dir,
            context=context,
        )
        await locator.fill(value)
        return locator

    @staticmethod
    def _valid_candidate(candidate: dict[str, Any]) -> bool:
        return (
            isinstance(candidate.get("css"), str)
            or isinstance(candidate.get("text"), str)
            or isinstance(candidate.get("label"), str)
            or (
                isinstance(candidate.get("role"), str)
                and ("name" not in candidate or isinstance(candidate.get("name"), str))
            )
        )

    @staticmethod
    def _locator(page: Any, candidate: Any) -> Any:
        if isinstance(candidate, str):
            return page.locator(candidate)
        if "css" in candidate:
            return page.locator(candidate["css"])
        if "text" in candidate:
            return page.get_by_text(candidate["text"], exact=candidate.get("exact", False))
        if "label" in candidate:
            return page.get_by_label(candidate["label"], exact=candidate.get("exact", False))
        return page.get_by_role(
            candidate["role"],
            name=candidate.get("name"),
            exact=candidate.get("exact", False),
        )

    @staticmethod
    def _describe(candidate: Any) -> str:
        if isinstance(candidate, str):
            return candidate
        return ",".join(f"{key}={value}" for key, value in candidate.items())

    async def _save_failure_diagnostics(
        self,
        page: Any,
        output_dir: Path,
        key: str,
        *,
        failures: list[str],
        context: str,
    ) -> tuple[Path, Path] | None:
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_key = re.sub(r"[^a-zA-Z0-9_.-]+", "-", key).replace(".", "-")
        screenshot = (output_dir / f"selector-{safe_key}.png").resolve()
        metadata = (output_dir / f"selector-{safe_key}.json").resolve()
        try:
            if hasattr(page, "screenshot"):
                await page.screenshot(path=str(screenshot), full_page=True)
            else:
                screenshot.write_text("Screenshot not available for FrameLocator")
            metadata.write_text(
                json.dumps(
                    {
                        "current_url": safe_browser_url(str(page.url)) if hasattr(page, "url") else "(FrameLocator)",
                        "page_title": _PRIVACY.mask(await page.title()) if hasattr(page, "title") else "(FrameLocator)",
                        "selector_key": key,
                        "attempted_selectors": [self._describe(item) for item in self.candidates(key)],
                        "failures": failures,
                        "workflow_context": context,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            artifact = metadata
            if self.save_html:
                artifact = (output_dir / f"selector-{safe_key}.html").resolve()
                if hasattr(page, "content"):
                    artifact.write_text(await page.content(), encoding="utf-8")
                else:
                    artifact.write_text("HTML content not available for FrameLocator", encoding="utf-8")
            for path in (screenshot, metadata, artifact):
                path.chmod(0o600)
            return screenshot, artifact
        except Exception as exc:
            self.logger.error(
                "Unable to save selector diagnostics",
                extra={"selector_key": key, "url": safe_browser_url(str(page.url)) if hasattr(page, "url") else "(FrameLocator)", "error_type": type(exc).__name__},
            )
            return None
