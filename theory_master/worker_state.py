"""Worker lifecycle labels and bounded scheduler policy constants."""
SCHEMA = "theory-master/worker-runtime/v1"
RUNNING = frozenset({"STARTING", "RUNNING", "PAUSE_REQUESTED", "STOP_REQUESTED"})
RESUMABLE = frozenset({"PAUSED", "FAILED", "NEEDS_ATTENTION", "INTERRUPTED"})
WAITING_RESEARCH_DIRECTION = "WAITING_RESEARCH_DIRECTION"
STOPPED_GOAL_COMPLETED = "STOPPED_GOAL_COMPLETED"
NON_INCIDENT_STOPS = frozenset({WAITING_RESEARCH_DIRECTION, STOPPED_GOAL_COMPLETED})
DIRECTION_FINGERPRINT_SCHEMA = "theory-master/research-direction-fingerprint/v1"
DIRECTION_FINGERPRINT_FIELDS = ("committed_outcome_count", "committed_outcome_ids_sha256",
                                "scientific_model_version", "research_plan_sha256")
RETRY_DIRECTION_SCHEMA = "theory-master/worker-retry-direction/v1"
SCHEDULER_NO_PROGRESS_GRACE_SECONDS = 120.0
MAX_SHARED_ACCOUNT_WORKERS = 2
WORKER_ACCOUNT_PREFERENCES_SCHEMA = "theory-master/worker-account-preferences/v1"
MAX_REPEATED_TECHNICAL_REVIEW_FAILURES = 2
