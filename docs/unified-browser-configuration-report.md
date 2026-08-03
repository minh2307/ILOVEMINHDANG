# Unified Chrome Profile, Session, Cookie, and Configuration Report

Date: 2026-07-30

## 1. Root Cause

The official Worker and operator browser commands had separate configuration composition paths:

- `DependencyContainer` built the Worker from `Settings.from_env()` and resolved `CHROME_PROFILE_DIR` to `runtime/chrome_profiles/cdha_automation`.
- `facebook_browser_cli` independently called `FacebookBrowserConfig.load()`, which read `app/config/browser.yaml` and resolved a second default, `runtime/chrome_profiles/facebook`.
- The official Reel path called a legacy downloader whose package-local `cookies.txt` was an implicit fallback. The disconnected `PlaywrightReelAdapter` also searched `Cookie.txt` and `runtime/cookies.txt`.
- A legacy Facebook module loaded `.env` at import time, allowing legacy aliases to leak into the official process environment.
- Shell scripts delegated to a browser CLI that did not use the same settings factory and lock object as the Worker.

A manual browser session could therefore be healthy in one profile while the Worker used another, and Reel authentication could change based on which cookie file happened to exist.

`app.config.settings.Settings` is now the sole authoritative typed source. `FacebookBrowserConfig` is only a derived immutable browser view. No active path selects a profile or cookie based on file existence.

## 2. Configuration Inventory

| Component | Previous Profile | Previous Cookie Path | Active in Official Flow? |
| --- | --- | --- | --- |
| Root CLI / container / Worker | `runtime/chrome_profiles/cdha_automation` | None directly | Yes |
| Browser CLI and start/check/stop scripts | `runtime/chrome_profiles/facebook` from YAML | None | Yes |
| Browser manager | Caller-dependent; also used private `.manager.lock` | None | Yes |
| Facebook publisher | Settings profile through Worker | Persistent profile session, no cookie file | Yes |
| CDHA client | Settings profile through Worker | Persistent profile session, no cookie file | Yes |
| `DownloadReelAdapter` → legacy downloader | No browser profile in normal download path | Package-local `app/infrastructure/legacy/dowloadReelFB/cookies.txt` fallback | Yes |
| `PlaywrightReelAdapter` | Mixed defaults | `Cookie.txt`, then `runtime/cookies.txt` | No; compatibility-only |
| Legacy AutoFacebook Selenium project | `FB_POSTER_PROFILE`, default under `~/.cache` | Browser session data | No |
| Preflight | YAML-backed `FacebookBrowserConfig.load()` | No cookie validation | Yes |

## 3. Final Canonical Configuration

All relative paths resolve against the repository root, independently of the current working directory.

| Setting | Environment Variable | Resolved Value/Source | Used By |
| --- | --- | --- | --- |
| Environment | `APP_ENV` | `development` in inspected environment | Diagnostics |
| Chrome profile | `CHROME_PROFILE_DIR` | `runtime/chrome_profiles/cdha_automation` → absolute | Worker, browser CLI, manager, publisher, CDHA, preflight |
| Chrome executable | `CHROME_EXECUTABLE_FALLBACK` | `/opt/google/chrome/google-chrome` in inspected environment | Manager, CLI, preflight |
| Headless | `HEADLESS` | `false` | Manager |
| CDP endpoint | `FACEBOOK_CDP_HOST`, `FACEBOOK_CDP_PORT` | `127.0.0.1:9222` | Manager, CLI, Worker |
| Browser lock | `FACEBOOK_BROWSER_LOCK_PATH` | `runtime/locks/facebook_browser.lock` → absolute | Worker and browser CLI through `FileBrowserLock` |
| PID file | `FACEBOOK_BROWSER_PID_PATH` | `runtime/pids/facebook_chrome.pid` → absolute | Manager and CLI |
| Startup timeout | `FACEBOOK_STARTUP_TIMEOUT_SECONDS` | `30` seconds | Manager |
| Action timeout | `PAGE_TIMEOUT_SECONDS` | `60` seconds | Browser clients |
| Downloads | `FACEBOOK_DOWNLOAD_PATH` | `runtime/downloads/facebook` → absolute | Managed browser context |
| Reel cookie | `FACEBOOK_COOKIE_FILE` | `runtime/auth/facebook_cookies.txt` → absolute | Official Reel downloader only |
| Cookie required | `FACEBOOK_COOKIE_REQUIRED` | `false`; missing means explicit anonymous mode | Settings, preflight, downloader |
| Facebook target | `FACEBOOK_TARGET_URL` | Sanitized: `https://www.facebook.com/me` | Publisher |
| CDHA endpoint | `CDHA_URL` | Sanitized: `https://cdha.ai/dash` | CDHA client |
| Active adapters | Typed composition | `DownloadReelAdapter`, `FacebookPublisherAdapter`, `CDHAWebClient` | Container/startup diagnostics |

`app/config/browser.yaml` is retained only as a deprecation marker. It contains no active profile, executable, port, timeout, or cookie defaults.

## 4. Official Browser Dependency Flow

```text
main.py → app.main → Settings.from_env() → DependencyContainer
  → FacebookBrowserConfig.from_settings(settings)
  → one FileBrowserLock(canonical absolute profile)
  → one FacebookBrowserManager(config, same lock)
  → FacebookBrowserWorker(manager, same lock, fingerprint)
      → VerifiedWorkflowStageAdapter
          → FacebookPublisherAdapter → shared manager/profile
          → CDHAWebClient            → shared manager/profile

scripts/start|check|stop_facebook_browser.sh
  → app.browser.facebook_browser_cli
  → Settings.from_env()
  → FacebookBrowserConfig.from_settings(settings)
  → same canonical FileBrowserLock + FacebookBrowserManager
```

Worker and browser CLI produced the same fingerprint: `db4dec838e17cb569e6a2fddec32f48a2501113ec133e0574747ef71ac906906`. Construction fails clearly if profile, executable, CDP port, or lock differs from Settings.

`FileBrowserLock` is the only active inter-process profile lock. Chrome `Singleton*` markers are never automatically moved or deleted; an unverified marker fails startup without modifying profile data.

## 5. Official Cookie Dependency Flow

```text
Settings.facebook_cookie_file
  → DownloadReelAdapter
  → fb_downloader.process_and_download_reel(cookie_path=...)
  → download_manager.download_single(cookie_path=...)
  → yt-dlp --cookies <canonical Netscape file>
```

Only the official Reel downloader uses this file. Missing optional means explicitly anonymous; it never searches another path. Missing-required, unreadable, and invalid Netscape files produce clear diagnostics. Facebook publishing and CDHA use the persistent Chrome profile and do not import this file. Logs and config output contain only path/existence/status/method, never contents.

## 6. Legacy Configuration

| Legacy Path/Configuration | Retained? | Active? | Warning? | Migration and Removal |
| --- | --- | --- | --- | --- |
| `runtime/chrome_profiles/facebook` | Yes, untouched | No | Yes when present | Manually verify session, authenticate canonical profile if needed, remove only after backup/live verification |
| Root `Cookie.txt` | Yes, untouched | No | Yes when present | Export a current Netscape file to `FACEBOOK_COOKIE_FILE`; rotate exposed session; remove after verification |
| `runtime/cookies.txt` | Yes, untouched | No | Yes when present | Migrate manually to canonical cookie path |
| `app/infrastructure/legacy/dowloadReelFB/cookies.txt` | Yes, untouched | No in official calls | Yes when present | Move/export outside source package; remove after official downloader verification |
| Legacy downloader implicit default | Retained for standalone legacy omission | No in official calls | Documented | Deprecate standalone use; remove after all external callers inject Settings |
| `FB_POSTER_PROFILE`, `FACEBOOK_PROFILE_PATH` | Retained for conflict detection | No source of truth | Clear error when explicitly conflicting | Replace with `CHROME_PROFILE_DIR`, then remove alias |
| Duplicate browser aliases | Removed from active `.env.example` | No | Conflict where applicable | Use canonical Settings variables |
| Legacy import-time `load_dotenv()` | Isolated to local value lookup | No process mutation | N/A | Retain only while legacy compatibility remains |

No profile, cookie, SQLite database, download, screenshot, log, or session file was copied, merged, moved, overwritten, or deleted.

## 7. Files Changed

| File | Status | Change | Reason |
| --- | --- | --- | --- |
| `app/config/settings.py` | Modified | Canonical paths, cookie inspection, conflict checks, sanitized diagnostics, warnings, fingerprint | One typed source |
| `app/config/facebook_browser.py` | Modified | Derived view and compatibility delegation | Remove parallel defaults |
| `app/config/browser.yaml` | Deprecated | Migration marker only | Prevent duplicate active config |
| `app/bootstrap.py` | Modified | Inject one config, manager, and lock | One composition root |
| `app/browser/facebook_browser_cli.py` | Modified | Official Settings, shared lock, `config` command | CLI/Worker parity |
| `app/browser/facebook_browser_manager.py` | Modified | Shared lock, safe startup failure/marker handling | Protect profile data |
| `workers/facebook_browser_worker.py` | Modified | Sanitized startup config/fingerprint | Real adapter diagnostics |
| `app/preflight.py` | Modified | Canonical config and cookie validation | Fail early without browser |
| `app/adapters/downloadreel_adapter.py` | Modified | Inject cookie path/auth metadata | Remove implicit fallback |
| `app/infrastructure/legacy/dowloadReelFB/fb_downloader.py` | Modified | Accept explicit cookie path | Carry dependency |
| `app/infrastructure/legacy/dowloadReelFB/download_manager.py` | Modified | Separate explicit anonymous from legacy omission | No silent official fallback |
| `app/infrastructure/facebook/playwright_reel_adapter.py` | Modified | Compatibility-only, Settings-derived paths | Remove hardcoded candidates |
| `app/infrastructure/legacy/AutoFacebook-SonMinhShare/src/common/config.py` | Modified | Non-mutating dotenv lookup | Stop environment leakage |
| `app/main.py` | Modified | Official sanitized `config` command | Inspection |
| `.env.example` | Modified | Canonical placeholders; duplicate aliases removed | Documentation source |
| `README.md`, `docs/architecture.md`, `docs/operations.md`, `HUONG_DAN_CHAY_DU_AN.md` | Modified | Canonical commands/paths/auth distinction | Documentation convergence |
| `tests/test_canonical_browser_configuration.py` | Created | Config/lock/cookie/script/marker/dotenv regressions | Prevent recurrence |

## 8. Tests

- Previous baseline: **332 passed**.
- Final: **345 passed, 0 failed, 0 skipped**.
- 13 new canonical configuration regressions cover root-relative resolution, conflicting aliases, sanitized fingerprints, cookie validation/injection, no `Cookie.txt` fallback, composition-root identity, shared profile/lock/fingerprint, shell delegation, safe marker handling, startup lock release, dotenv isolation, and orphan aliases.
- Tests did not open Chrome, contact Facebook/CDHA, download a Reel, or publish content.

Exact commands:

```bash
.venv/bin/pytest -q --tb=short
.venv/bin/python -m compileall -q app workers config scripts
bash -n scripts/start_facebook_browser.sh scripts/check_facebook_browser.sh scripts/stop_facebook_browser.sh scripts/run_facebook_worker.sh scripts/run_worker.sh
.venv/bin/python main.py config
.venv/bin/python -m app.browser.facebook_browser_cli config
.venv/bin/python main.py worker --preflight-only
git diff --check
```

All exited 0. Full suite time: 7.38 seconds.

## 9. Configuration Check Output

Sanitized excerpt returned by both inspection commands:

```json
{
  "active_adapters": {
    "cdha": "CDHAWebClient",
    "facebook_publisher": "FacebookPublisherAdapter",
    "reel_downloader": "DownloadReelAdapter"
  },
  "application_environment": "development",
  "authentication": {
    "cdha": {"method": "persistent_chrome_profile"},
    "facebook_publisher": {"method": "persistent_chrome_profile"},
    "facebook_reel": {
      "cookie_exists": false,
      "cookie_file": "/media/nguyen-son-minh/p5/MinhDang/runtime/auth/facebook_cookies.txt",
      "cookie_status": "missing_optional",
      "method": "netscape_cookie_file_or_anonymous"
    }
  },
  "browser": {
    "cdp_host": "127.0.0.1",
    "cdp_port": 9222,
    "executable": "/opt/google/chrome/google-chrome",
    "headless": false,
    "lock_path": "/media/nguyen-son-minh/p5/MinhDang/runtime/locks/facebook_browser.lock",
    "profile_path": "/media/nguyen-son-minh/p5/MinhDang/runtime/chrome_profiles/cdha_automation",
    "startup_strategy": "managed_chrome_cdp"
  },
  "configuration_fingerprint": "db4dec838e17cb569e6a2fddec32f48a2501113ec133e0574747ef71ac906906",
  "errors": [],
  "valid": true
}
```

Preflight returned the same profile, lock, cookie path/status, and fingerprint. Legacy paths were only listed as retained/inactive warnings; their contents were not read or printed.

## 10. Remaining Risks

- The canonical Reel cookie is missing but optional. Restricted Reels need a valid manually exported Netscape file.
- Login state in the legacy `facebook` profile was intentionally not merged. An operator may need to authenticate once in the canonical profile.
- Live Chrome, Facebook publishing, CDHA, and Reel download were not exercised because they would use private session data or create external state. They remain manual acceptance checks.
- Legacy data remains for safe migration and should only be removed after backup and live verification.

## 11. Final Verdict

SUCCESS
