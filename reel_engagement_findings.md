# Reel Engagement Findings

- Requested API: `like_reel_and_comments(reel_url, like_reel=True,
  like_comments=True, like_replies=False) -> dict`.
- Required behavior includes author identity priority (ID, canonical URL,
  normalized name), idempotent Reel/comment likes, bounded lazy loading,
  top-level comments by default, duplicate suppression, sequential rate control,
  per-comment fault isolation, bilingual selectors, and a final summary.
- Candidate code exists in `app/browser/`, `app/infrastructure/facebook/`, and
  `app/infrastructure/legacy/facebook_integrations/`; active call paths must be
  established before choosing an integration point.
- `app/browser/facebook_browser_worker.py` is only a deprecated compatibility
  import of `app.infrastructure.legacy.facebook_browser.worker.FacebookBrowserWorker`.
- `app/browser/facebook_browser_manager.py` is the sole owner of Chrome startup
  and Playwright CDP connection. It exposes owned-page acquire/release methods
  and holds the canonical `FileBrowserLock` for its session.
- A second class named `workers.facebook_browser_worker.FacebookBrowserWorker`
  is the official durable job orchestrator. It owns the cross-process lock while
  dispatching work, but does not itself manipulate DOM.
- Existing unrelated worktree changes include deleted tracked planning/docs and
  modified/new scripts. Feature-specific planning files avoid overwriting them.
