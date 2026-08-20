#!/usr/bin/env python3
"""Structured classification of OpenFOAM process logs.

The normal ``FOAM_SIGFPE`` startup banner announces that exception trapping is
enabled.  It is not evidence that an exception occurred.  Keeping this parser
in one module prevents runners and staged orchestration from applying
different substring rules to the same solver output.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any


EVENT_SCHEMA_VERSION = 1
HARD_MAX_COURANT = 1.0e6

_NORMAL_SIGFPE = re.compile(
    r"(?im)^\s*sigFpe\s*:\s*Enabling floating point exception trapping"
    r"[^\r\n]*$"
)
_NORMAL_END = re.compile(r"(?im)^\s*End\s*$")
_COURANT = re.compile(
    r"Courant Number mean:\s*([0-9.eE+\-]+)\s+max:\s*([0-9.eE+\-]+)",
    flags=re.IGNORECASE,
)

_SETUP_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("FOAM_FATAL_IO_ERROR", re.compile(r"FOAM FATAL IO ERROR", re.IGNORECASE)),
    ("MISSING_FILE", re.compile(r"cannot find (?:file|.* object)", re.IGNORECASE)),
    ("FIELD_SIZE_MISMATCH", re.compile(r"size\s+\d+\s+is not equal to given value\s+\d+", re.IGNORECASE)),
    ("UNDEFINED_KEYWORD", re.compile(r"keyword .* is undefined", re.IGNORECASE)),
    ("UNKNOWN_TYPE", re.compile(r"Unknown .* type", re.IGNORECASE)),
    ("PRESSURE_REFERENCE", re.compile(r"Unable to set reference cell", re.IGNORECASE)),
)

_DIVERGENCE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "FLOATING_POINT_EXCEPTION",
        re.compile(r"Floating point exception(?! trapping)(?:\s|$)", re.IGNORECASE),
    ),
    ("SEGMENTATION_FAULT", re.compile(r"Segmentation fault|sigSegv", re.IGNORECASE)),
    ("MPI_ABORT", re.compile(r"MPI_ABORT was invoked", re.IGNORECASE)),
    ("NONFINITE", re.compile(r"\b(?:nan|inf)\b|not finite", re.IGNORECASE)),
    ("EXPLICIT_DIVERGENCE", re.compile(r"Divergence detected", re.IGNORECASE)),
    ("UNBOUNDED_FIELD", re.compile(r"bounding .* unbounded", re.IGNORECASE)),
)


@dataclass(frozen=True)
class OpenFOAMEventClassification:
    """Stable event summary shared by every OpenFOAM runner."""

    schema_version: int
    status: str
    normal_end: bool
    normal_sigfpe_banner: bool
    setup_error: bool
    numerical_divergence: bool
    solver_failure: bool
    fatal_markers: tuple[str, ...]
    warning_markers: tuple[str, ...]
    maximum_courant: float | None
    returncode: int | None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fatal_markers"] = list(self.fatal_markers)
        payload["warning_markers"] = list(self.warning_markers)
        return payload


def classify_openfoam_log(
    text: str,
    *,
    returncode: int | None = None,
    hard_max_courant: float = HARD_MAX_COURANT,
) -> OpenFOAMEventClassification:
    """Classify solver/setup output without treating the SIGFPE banner as fatal."""
    original = str(text or "")
    normal_sigfpe = bool(_NORMAL_SIGFPE.search(original))
    filtered = _NORMAL_SIGFPE.sub("", original)

    setup_markers = tuple(
        name for name, pattern in _SETUP_PATTERNS if pattern.search(filtered)
    )
    divergence_markers = [
        name for name, pattern in _DIVERGENCE_PATTERNS if pattern.search(filtered)
    ]
    courant_values: list[float] = []
    for match in _COURANT.finditer(filtered):
        try:
            value = float(match.group(2))
        except ValueError:
            continue
        if math.isfinite(value):
            courant_values.append(value)
        else:
            divergence_markers.append("NONFINITE_COURANT")
    maximum_courant = max(courant_values, default=None)
    if maximum_courant is not None and maximum_courant >= float(hard_max_courant):
        divergence_markers.append("CATASTROPHIC_COURANT_GROWTH")

    warnings: list[str] = []
    if re.search(r"bounding\s+(?:nut|nuTilda),\s*min:\s*-", filtered, re.IGNORECASE):
        warnings.append("NEGATIVE_TURBULENCE_BOUNDING")

    setup_error = bool(setup_markers)
    # Setup failures often end with MPI_ABORT after OpenFOAM reports a fatal
    # dictionary/field error.  The abort is secondary evidence, not numerical
    # divergence, so the structured setup diagnosis takes precedence.
    numerical_divergence = bool(divergence_markers) and not setup_error
    nonzero = returncode is not None and int(returncode) != 0
    if setup_error:
        status = "SETUP_FAILED"
    elif numerical_divergence:
        status = "NUMERICAL_DIVERGENCE"
    elif nonzero:
        status = "SOLVER_FAILED"
    elif _NORMAL_END.search(filtered):
        status = "RUN_COMPLETED"
    else:
        status = "NO_TERMINAL_EVENT"
    return OpenFOAMEventClassification(
        schema_version=EVENT_SCHEMA_VERSION,
        status=status,
        normal_end=bool(_NORMAL_END.search(filtered)),
        normal_sigfpe_banner=normal_sigfpe,
        setup_error=setup_error,
        numerical_divergence=numerical_divergence,
        solver_failure=bool(nonzero and not setup_error and not numerical_divergence),
        fatal_markers=tuple(dict.fromkeys((*setup_markers, *divergence_markers))),
        warning_markers=tuple(dict.fromkeys(warnings)),
        maximum_courant=maximum_courant,
        returncode=None if returncode is None else int(returncode),
    )


def solver_log_has_fatal_error(text: str) -> bool:
    event = classify_openfoam_log(text)
    return bool(event.setup_error or event.numerical_divergence)


def solver_log_indicates_divergence(text: str) -> bool:
    return classify_openfoam_log(text).numerical_divergence


def solver_log_indicates_setup_error(text: str) -> bool:
    return classify_openfoam_log(text).setup_error
