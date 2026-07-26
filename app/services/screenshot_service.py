from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.browser.selector_resolver import SelectorResolutionError, SelectorResolver


class ScreenshotService:
    """
    Captures two canonical 390×844 px screenshots for each CDHA analysis result.

    01-detailed-analysis.png  (scroll_top mode)
        Scroll to "Chi tiết phân tích" section header, take a full-viewport shot.
        → top of image: navbar + "Chi tiết phân tích"
        → bottom of image: "Vùng được đánh dấu" list

    02-final-result.png  (scroll_impression_bottom mode)
        Scroll so the bottom edge of the Impression container aligns near the
        bottom of the viewport, then take a full-viewport shot.
        → bottom of image: end of green Impression box
        → content above: Key findings + Triage + whatever fits in 844px
    """

    SECTIONS: list[dict] = [
        {
            "filename": "01-detailed-analysis.png",
            "mode":     "scroll_top",
            "anchor":   "cdha.detailed_analysis",
            # px to subtract from element top (clears the fixed navbar)
            "offset":   80,
        },
        {
            "filename": "02-final-result.png",
            "mode":     "scroll_impression_bottom",
            "anchor":   "cdha.impression",
            # px gap between the Impression box bottom and the viewport bottom edge
            "bottom_gap": 24,
        },
    ]

    def __init__(
        self,
        resolver: SelectorResolver,
        logger: logging.Logger | None = None,
    ) -> None:
        self.resolver = resolver
        self.logger = logger or logging.getLogger("cdha_pipeline.screenshots")

    # ── public API ─────────────────────────────────────────────────────────

    async def capture_required(
        self, page: Any, job_dir: Path
    ) -> tuple[list[Path], list[str]]:
        output_dir = (Path(job_dir) / "screenshots").resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Both screenshots use the standard iPhone 13 viewport.
        await page.set_viewport_size({"width": 390, "height": 844})

        # Hide scrollbars so they don't appear in screenshots.
        await page.add_style_tag(content="""
            ::-webkit-scrollbar { display: none !important; }
            * { -ms-overflow-style: none !important; scrollbar-width: none !important; }
        """)

        await self._wait_for_visual_assets(page)

        paths: list[Path] = []
        warnings: list[str] = []

        for section in self.SECTIONS:
            path = output_dir / section["filename"]
            try:
                if section["mode"] == "scroll_top":
                    await self._capture_scroll_top(
                        page, path,
                        anchor_sel=section["anchor"],
                        offset=section.get("offset", 80),
                    )

                elif section["mode"] == "scroll_impression_bottom":
                    await self._capture_scroll_impression_bottom(
                        page, path,
                        anchor_sel=section["anchor"],
                        bottom_gap=section.get("bottom_gap", 24),
                    )

                else:
                    raise ValueError(f"Unknown screenshot mode: {section['mode']!r}")

            except Exception as exc:
                key = section.get("anchor", "?")
                warning = f"{key}: screenshot failed, full-page fallback used ({exc})"
                warnings.append(warning)
                self.logger.warning(warning)
                await self._capture_fallback(page, path)
                metadata = output_dir / f"{path.stem}-fallback.json"
                metadata.write_text(
                    json.dumps(
                        {"section": path.stem, "fallback": True, "error_type": type(exc).__name__},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                metadata.chmod(0o600)

            paths.append(path.resolve())

        return paths, warnings

    # ── capture modes ──────────────────────────────────────────────────────

    async def _capture_scroll_top(
        self, page: Any, path: Path, anchor_sel: str, offset: int = 80
    ) -> None:
        """Scroll anchor element to viewport top (minus offset), then screenshot."""
        loc    = await self.resolver.find_first(page, anchor_sel, timeout_ms=4_000)
        handle = await loc.element_handle()
        await page.evaluate(
            """([el, offset]) => {
                const y = el.getBoundingClientRect().top + window.scrollY - offset;
                window.scrollTo({top: Math.max(0, y), behavior: 'instant'});
            }""",
            [handle, offset],
        )
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(path))

    async def _capture_scroll_impression_bottom(
        self,
        page: Any,
        path: Path,
        anchor_sel: str,
        bottom_gap: int = 24,
    ) -> None:
        """
        Scroll so the bottom of the Impression container sits near the bottom
        of the 390×844 viewport, then take a standard viewport screenshot.

        The viewport height is 844 px.  We want:
            impression_container_bottom (viewport coords) = 844 - bottom_gap
        So:
            scrollY = impression_container_bottom (page coords) - (844 - bottom_gap)
        """
        loc    = await self.resolver.find_first(page, anchor_sel, timeout_ms=4_000)
        handle = await loc.element_handle()

        await page.evaluate(
            """([el, bottomGap]) => {
                // Walk up from the Impression heading to find its styled container
                // (the div with a visible background / border wrapping heading + text).
                let container = el;
                for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
                    const cs = window.getComputedStyle(p);
                    const bg = cs.backgroundColor;
                    const bw = parseInt(cs.borderTopWidth, 10) || 0;
                    const opaque = bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent';
                    if (opaque || bw > 0) { container = p; break; }
                }

                // Page-coordinate bottom of the container.
                const pageBottom = container.getBoundingClientRect().bottom + window.scrollY;

                // We want pageBottom to appear at (viewportHeight - bottomGap) in viewport coords.
                const viewportHeight = window.innerHeight;  // 844
                const targetScrollY  = pageBottom - (viewportHeight - bottomGap);

                window.scrollTo({top: Math.max(0, targetScrollY), behavior: 'instant'});
            }""",
            [handle, bottom_gap],
        )
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(path))

    # ── fallback ───────────────────────────────────────────────────────────

    async def _capture_fallback(self, page: Any, path: Path) -> None:
        masks: list[Any] = []
        try:
            masks.append(
                await self.resolver.find_first(page, "cdha.private_ui", timeout_ms=800)
            )
        except (SelectorResolutionError, KeyError):
            pass
        kwargs: dict[str, Any] = {"path": str(path), "full_page": True}
        if masks:
            kwargs["mask"] = masks
        await page.screenshot(**kwargs)

    # ── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    async def _wait_for_visual_assets(page: Any) -> None:
        try:
            await page.evaluate(
                "() => document.fonts ? document.fonts.ready : Promise.resolve()"
            )
            await page.wait_for_function(
                "() => Array.from(document.images).every(img => img.complete)",
                timeout=10_000,
            )
        except Exception:
            return


# Backwards-compatible module-level import without duplicating the canonical
# section definitions. New code should prefer ScreenshotService.SECTIONS.
SCREENSHOT_SECTIONS = ScreenshotService.SECTIONS
