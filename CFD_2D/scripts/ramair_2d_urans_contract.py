"""Shared URANS execution vocabulary and start-mode rules.

Keeping these values in one small module avoids the historically dangerous
situation where the runner, staged orchestrator and study registry used
different spellings for the same physical state.
"""

from __future__ import annotations

from typing import Final


FRESH_FROM_CHECKPOINT: Final = "FRESH_FROM_CHECKPOINT"
CONTINUE_STAGE: Final = "CONTINUE_STAGE"
RESUME_EXISTING: Final = "RESUME_EXISTING"
START_MODES: Final[frozenset[str]] = frozenset(
    {FRESH_FROM_CHECKPOINT, CONTINUE_STAGE, RESUME_EXISTING}
)

# State values are intentionally broader than the UI labels: the registry is
# the durable audit trail for preparation, solver and post-processing paths.
RUN_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "PREPARED",
        "PREPARING",
        "RUNNING",
        "COMPLETED",
        "ANALYSIS_PENDING",
        "STOPPED_PARTIAL",
        "TIMEOUT_PARTIAL",
        "INTERRUPTED",
        "CANCELLED",
        "PREPARATION_FAILED",
        "STAGE_CHECKPOINT_MISSING",
        "TEMPORAL_HISTORY_MISSING",
        "RESUME_NOT_AVAILABLE",
        "SOLVER_FAILED",
        "SOLVER_DIVERGED",
        "QUEUED",
        "RUNNING_RANS",
        "RUNNING_URANS",
        "RANS_COMPLETED",
        "URANS_COMPLETED",
        "POSTPROCESSING",
        "POSTPROCESSED",
        "FAILED",
        "CANCELLED_BY_USER",
        "CONVERGED_STATISTICALLY",
        "STOPPED_FORCED_PARTIAL",
        "RUN_SETUP_FAILED",
        "RUN_COMMAND_FAILED",
        "RUN_DIVERGED",
    }
)

ACTIVE_STATUSES: Final[frozenset[str]] = frozenset(
    {"PREPARING", "RUNNING", "RUNNING_RANS", "RUNNING_URANS", "POSTPROCESSING"}
)
PARTIAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"STOPPED_PARTIAL", "TIMEOUT_PARTIAL", "INTERRUPTED", "CANCELLED", "STOPPED_FORCED_PARTIAL"}
)
FAILURE_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "PREPARATION_FAILED",
        "STAGE_CHECKPOINT_MISSING",
        "TEMPORAL_HISTORY_MISSING",
        "RESUME_NOT_AVAILABLE",
        "SOLVER_FAILED",
        "SOLVER_DIVERGED",
        "FAILED",
        "RUN_SETUP_FAILED",
        "RUN_COMMAND_FAILED",
        "RUN_DIVERGED",
    }
)


def normalize_start_mode(start_mode: str | None, *, legacy_resume: bool = False) -> str:
    """Return a canonical start mode while retaining ``--resume`` compatibility."""
    if start_mode is None or not str(start_mode).strip():
        return RESUME_EXISTING if legacy_resume else FRESH_FROM_CHECKPOINT
    normalized = str(start_mode).strip().upper().replace("-", "_")
    aliases = {
        "FRESH": FRESH_FROM_CHECKPOINT,
        "FROM_INITIAL_STATE": FRESH_FROM_CHECKPOINT,
        "CONTINUE": CONTINUE_STAGE,
        "CONTINUE_PREVIOUS_STAGE": CONTINUE_STAGE,
        "RESUME": RESUME_EXISTING,
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in START_MODES:
        raise ValueError(
            f"Unknown URANS start mode {start_mode!r}. Expected one of: "
            + ", ".join(sorted(START_MODES))
        )
    if legacy_resume and normalized != RESUME_EXISTING:
        raise ValueError("--resume cannot be combined with a non-resume --start-mode.")
    return normalized
