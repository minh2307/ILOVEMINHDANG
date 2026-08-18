"""
MinhDang Project Scheduler — Two-phase startup state machine.

Phase 1 (UI_ONLY):   Dashboard/frontend only, before scheduled_start_time.
Phase 2 (FULL_RUNNING): All services including Worker, Ollama, Orchestrator.
"""
