# Reel Engagement Task Plan

## Goal
Extend the existing CDP-backed `FacebookBrowserWorker` architecture with a safe,
sequential Reel/comment-like workflow that never toggles an existing like and
skips comments made by the Reel author.

## Current Phase
Phase 1 — discovery

## Phases

1. **Discovery (in progress):** map worker, Reel/comment code, CDP ownership,
   locking, configuration, and tests.
2. **Design (pending):** select the narrow integration point and define stable
   author/reaction/comment identity behavior.
3. **Implementation (pending):** add the workflow without changing existing
   crawl/download/publish behavior.
4. **Verification (pending):** add the eight requested deterministic tests and
   run focused plus relevant regression tests.
5. **Delivery (pending):** review diff and report files/architecture/results.

## Constraints

- Reuse the current `FacebookBrowserWorker`, Playwright page, profile, and CDP.
- Never launch a new browser and never use concurrent Facebook clicks.
- Use semantic/relative selectors with Vietnamese and English variants.
- Resolve locators again after DOM rerenders; isolate per-comment failures.
- Preserve unrelated worktree changes.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Shell startup reports `Failed to create stream fd: Operation not permitted` | 1 | Commands still execute and return valid output; treat as environment noise and avoid relying on shell streaming. |

