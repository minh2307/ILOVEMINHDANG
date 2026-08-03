# Unified workflow

## End-to-end sequence

```text
Reel URL
  → create-job: normalize URL, reuse duplicate or persist CREATED
  → orchestrator: enqueue <job-id>:<status> once
  → worker: atomic claim + queue lease/heartbeat
  → worker: browser file lock + manager profile lock
  → ProcessJobUseCase
      → verified DownloadReel adapter (non-empty video + metadata/checksum)
      → configured local analyzer (validated Clinical Factors)
      → CDHA Playwright client (parsed result + screenshots)
      → WAITING_FOR_REVIEW
  → operator review
      → APPROVED or REJECTED/retry
  → orchestrator/worker
      → Facebook preparation (content/privacy/duplicate validation)
      → FACEBOOK_WAITING_FOR_MANUAL_REVIEW
  → operator types PUBLISH <job-id>
  → worker publishes and verifies exact post/permalink
  → worker adds exact permalink comment idempotently
  → COMPLETED
```

Every stage reloads the persisted job. `ProcessJobUseCase` stops when it reaches
a manual boundary, terminal state, failure state, no-progress result, or its
bounded step limit. Queue completion means that one state-specific work item
finished; it does not falsely mark a workflow job complete.

## Responsibility and recovery

| Stage | Responsible component | Required persisted result | Resume / retry behavior |
| --- | --- | --- | --- |
| Create/deduplicate | CLI + repository | normalized source URL, input payload | Same URL reuses latest job unless `--force` |
| Schedule | Orchestrator use case | unique `<job-id>:<status>` queue row | Duplicate insert is ignored |
| Claim | SQLite queue | owner, lease expiry, heartbeat | Only expired claims recover; attempts bounded |
| Download | verified DownloadReel adapter | non-empty video, metadata, checksum/history | Reuses validated artifact; retry from `RETRY_PENDING` |
| Analyze | configured Ollama/analyzer adapter | validated/masked Clinical Factors and artifacts | Existing valid output skips side effect |
| CDHA | CDHA client | parsed result, view URL, screenshots | Persisted successful result is reused |
| Review | `ReviewService` | explicit decision and privacy acknowledgement | Stop indefinitely; operator can approve/reject/retry |
| Facebook prepare | publisher adapter | target, content hash, selected images, preview | Duplicate fingerprint check before publishing |
| Facebook publish | publisher adapter/client | verified post identifier/permalink or uncertain state | Interrupted publish reconciles; uncertain result never republishes |
| Comment | publisher adapter/client | exact permalink/comment result | Existing successful comment is reused |
| Cleanup | worker/manager locks | release events | `finally` releases browser lock; lease expires after crash |

## State machine

`FAILED` and `CANCELLED` are globally available from non-terminal workflow
states. The table below is generated from the same authoritative transition
rules used by persistence. `ACQUIRING_BROWSER_LOCK`,
`WAITING_FOR_BROWSER_LOCK`, and `RUNNING` are queue-operation states; they do not
replace the workflow job's stage state.

| Current | Allowed next |
| --- | --- |
| `CREATED` | `CANCELLED`, `DOWNLOADREEL_RUNNING`, `FAILED` |
| `ACQUIRING_BROWSER_LOCK` | `CANCELLED`, `FAILED` |
| `WAITING_FOR_BROWSER_LOCK` | `CANCELLED`, `FAILED` |
| `RUNNING` | `CANCELLED`, `FAILED` |
| `DOWNLOADREEL_RUNNING` | `CANCELLED`, `DOWNLOADED`, `DOWNLOADREEL_FAILED`, `FAILED`, `RETRY_PENDING` |
| `DOWNLOADED` | `AI_ANALYZING`, `CANCELLED`, `FAILED`, `GEMINI_OPENING` |
| `DOWNLOADREEL_FAILED` | `CANCELLED`, `FAILED`, `RETRY_PENDING` |
| `GEMINI_OPENING` | `CANCELLED`, `FAILED`, `GEMINI_FAILED`, `GEMINI_GENERATING`, `NEEDS_GEMINI_LOGIN`, `RETRY_PENDING` |
| `NEEDS_GEMINI_LOGIN` | `CANCELLED`, `FAILED`, `GEMINI_FAILED`, `GEMINI_GENERATING`, `RETRY_PENDING` |
| `GEMINI_GENERATING` | `CANCELLED`, `CLINICAL_FACTORS_GENERATED`, `FAILED`, `GEMINI_FAILED`, `RETRY_PENDING` |
| `CLINICAL_FACTORS_GENERATED` | `CANCELLED`, `CDHA_OPENING`, `FAILED` |
| `GEMINI_FAILED` | `CANCELLED`, `FAILED`, `RETRY_PENDING` |
| `AI_ANALYZING` | `AI_FAILED`, `CANCELLED`, `CLINICAL_FACTORS_GENERATED`, `FAILED`, `RETRY_PENDING` |
| `AI_FAILED` | `CANCELLED`, `FAILED`, `RETRY_PENDING` |
| `CDHA_OPENING` | `CANCELLED`, `CDHA_FAILED`, `CDHA_UPLOADING`, `FAILED`, `NEEDS_CDHA_LOGIN`, `RETRY_PENDING` |
| `NEEDS_CDHA_LOGIN` | `CANCELLED`, `CDHA_FAILED`, `CDHA_UPLOADING`, `FAILED`, `RETRY_PENDING` |
| `CDHA_UPLOADING` | `CANCELLED`, `CDHA_ANALYZING`, `CDHA_FAILED`, `FAILED`, `RETRY_PENDING` |
| `CDHA_ANALYZING` | `CANCELLED`, `CDHA_ANALYZED`, `CDHA_FAILED`, `FAILED`, `RETRY_PENDING` |
| `CDHA_ANALYZED` | `CANCELLED`, `FAILED`, `SCREENSHOTS_CAPTURING` |
| `CDHA_FAILED` | `CANCELLED`, `FAILED`, `RETRY_PENDING` |
| `SCREENSHOTS_CAPTURING` | `CANCELLED`, `CDHA_FAILED`, `FAILED`, `RETRY_PENDING`, `SCREENSHOTS_CAPTURED` |
| `SCREENSHOTS_CAPTURED` | `CANCELLED`, `FAILED`, `WAITING_FOR_REVIEW` |
| `WAITING_FOR_REVIEW` | `AI_ANALYZING`, `APPROVED`, `CANCELLED`, `CDHA_OPENING`, `FAILED`, `GEMINI_OPENING`, `REJECTED`, `RETRY_PENDING` |
| `APPROVED` | `CANCELLED`, `FACEBOOK_PREPARING`, `FAILED` |
| `REJECTED` | terminal |
| `FACEBOOK_PREPARING` | `BLOCKED`, `CANCELLED`, `FACEBOOK_PUBLISH_FAILED`, `FACEBOOK_WAITING_FOR_MANUAL_REVIEW`, `FAILED`, `RETRYABLE`, `RETRY_PENDING`, `WAITING_FOR_AUTH_REVIEW` |
| `WAITING_FOR_AUTH_REVIEW` | `BLOCKED`, `CANCELLED`, `FACEBOOK_PREPARING`, `FAILED`, `RETRYABLE` |
| `FACEBOOK_WAITING_FOR_MANUAL_REVIEW` | `APPROVED`, `CANCELLED`, `FACEBOOK_PUBLISHING`, `FACEBOOK_PUBLISH_FAILED`, `FAILED` |
| `FACEBOOK_PUBLISHING` | `CANCELLED`, `FACEBOOK_PUBLISHED`, `FACEBOOK_PUBLISH_FAILED`, `FACEBOOK_PUBLISH_UNCERTAIN`, `FAILED` |
| `FACEBOOK_PUBLISHED` | `CANCELLED`, `FAILED`, `POST_URL_EXTRACTING` |
| `FACEBOOK_PUBLISH_FAILED` | `CANCELLED`, `FAILED`, `RETRY_PENDING` |
| `FACEBOOK_PUBLISH_UNCERTAIN` | `CANCELLED`, `FAILED` |
| `POST_URL_EXTRACTING` | `CANCELLED`, `FAILED`, `POST_URL_EXTRACTED`, `POST_URL_EXTRACTION_FAILED`, `RETRY_PENDING` |
| `POST_URL_EXTRACTED` | `CANCELLED`, `COMMENT_ADDING`, `FAILED` |
| `POST_URL_EXTRACTION_FAILED` | `CANCELLED`, `FAILED`, `RETRY_PENDING` |
| `COMMENT_ADDING` | `CANCELLED`, `COMMENT_ADDED`, `COMMENT_FAILED`, `FAILED`, `RETRY_PENDING` |
| `COMMENT_ADDED` | `CANCELLED`, `COMPLETED`, `FAILED` |
| `COMMENT_FAILED` | `CANCELLED`, `FAILED`, `RETRY_PENDING` |
| `COMPLETED` | terminal |
| `FAILED` | terminal |
| `RETRY_PENDING` | `AI_ANALYZING`, `CANCELLED`, `CDHA_OPENING`, `COMMENT_ADDING`, `DOWNLOADREEL_RUNNING`, `FACEBOOK_PREPARING`, `FAILED`, `GEMINI_OPENING`, `POST_URL_EXTRACTING`, `SCREENSHOTS_CAPTURING` |
| `RETRYABLE` | `AI_ANALYZING`, `CANCELLED`, `CDHA_OPENING`, `COMMENT_ADDING`, `DOWNLOADREEL_RUNNING`, `FACEBOOK_PREPARING`, `FAILED`, `POST_URL_EXTRACTING` |
| `BLOCKED` | `CANCELLED`, `FAILED`, `RETRYABLE` |
| `CANCELLED` | terminal |

An interrupted in-flight stage is first committed to `RETRY_PENDING` with a
`JOB_RECOVERED` event and an explicit `retry_step`; only then is the external
operation attempted again. `FACEBOOK_PUBLISHING` is special: it runs
reconciliation and may become `FACEBOOK_PUBLISH_UNCERTAIN`, never blind retry.
