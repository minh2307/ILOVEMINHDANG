# Folder structure

```text
MinhDang/
├── main.py                         # only official root entry point
├── app/
│   ├── bootstrap.py                # concrete dependency composition
│   ├── main.py                     # CLI/interface routing; legacy flags retained
│   ├── domain/
│   │   ├── enums/                  # JobStatus and JobType
│   │   ├── models/                 # Job, JobEvent, work-item/result DTOs
│   │   ├── exceptions/             # canonical error taxonomy
│   │   └── rules/                  # validated state transitions
│   ├── application/
│   │   ├── dto/                    # stage execution result
│   │   ├── ports/                  # repository, queue, browser and stage contracts
│   │   ├── services/               # typed queue dispatcher
│   │   └── use_cases/              # process, schedule, queued dispatch
│   ├── infrastructure/
│   │   ├── browser/                # CDP adapter and durable file lock
│   │   ├── persistence/            # SQLite repository and queue
│   │   ├── workflow/               # verified-stage adapter
│   │   └── legacy/                 # retained historical mini-projects/stores
│   ├── browser/                     # characterized Playwright service clients/manager
│   ├── adapters/                    # verified legacy feature boundaries
│   ├── ai/                          # local analyzer implementations
│   ├── config/                      # typed settings and selectors
│   ├── services/                    # content, privacy, review, retry services
│   └── workflows/                   # characterized stage mechanics only
├── workers/
│   ├── main.py                     # delegate to `main.py worker`
│   └── facebook_browser_worker.py  # claim/lease/lock/dispatch lifecycle
├── scripts/                         # compatibility delegates and browser operations
├── tests/                           # unit, integration, workflow, fixtures
├── docs/                            # architecture, workflow, migration, operations
├── runtime/                         # generated and Git-ignored
├── data/                            # SQLite/artifacts and Git-ignored
├── .env.example
└── requirements.txt
```

Large characterized files remain where moving them would add migration risk
without changing dependencies. In particular, browser clients and the stage
pipeline are adapters behind the application port; their further mechanical
split is not required for correctness and should be done only with additional
characterization tests.
