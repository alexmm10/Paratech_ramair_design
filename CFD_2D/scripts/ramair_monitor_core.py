#!/usr/bin/env python3
"""Shared, bounded parser for OpenFOAM scalar monitoring signals."""
from __future__ import annotations

from collections import deque
import math
import re
from pathlib import Path
from typing import Any, Iterable


RESIDUAL_RE = re.compile(
    r"Solving for\s+([^,]+),\s+Initial residual\s*=\s*([0-9.eE+-]+),"
    r"(?:\s*Final residual\s*=\s*([0-9.eE+-]+),)?\s*No Iterations\s+(\d+)"
)
TIME_RE = re.compile(r"^\s*Time\s*=\s*([0-9.eE+-]+)\s*s?\s*$")
DELTA_T_RE = re.compile(r"^\s*deltaT\s*=\s*([0-9.eE+-]+)")
COURANT_RE = re.compile(
    r"Courant Number mean:\s*([0-9.eE+-]+)\s+max:\s*([0-9.eE+-]+)"
)
CONTINUITY_RE = re.compile(
    r"time step continuity errors\s*:\s*sum local\s*=\s*([0-9.eE+-]+),"
    r"\s*global\s*=\s*([0-9.eE+-]+),\s*cumulative\s*=\s*([0-9.eE+-]+)"
)
EXECUTION_RE = re.compile(
    r"ExecutionTime\s*=\s*([0-9.eE+-]+)\s+s\s+ClockTime\s*=\s*([0-9.eE+-]+)"
)


def _finite(value: str | None) -> float | None:
    try:
        parsed = float(value) if value is not None else math.nan
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def normalized_field_name(value: str) -> str:
    raw = value.strip()
    return {"Ux": "U.x", "Uy": "U.y", "Uz": "U.z"}.get(raw, raw)


def parse_openfoam_lines(
    lines: Iterable[str],
    recent: dict[str, Any] | None = None,
    *,
    max_points: int = 1200,
) -> dict[str, Any]:
    """Parse all lightweight signals while retaining only a bounded tail."""
    state = dict(recent or {})
    residuals = list(state.get("residuals") or [])
    iterations = list(state.get("iterations") or [])
    courant = list(state.get("courant") or [])
    continuity = list(state.get("continuity") or [])
    execution = list(state.get("execution") or [])
    delta_t_history = list(state.get("deltaT_history") or [])
    delta_t = state.get("deltaT")
    current_iteration = state.get("current_iteration")
    steps_total = int(state.get("steps_total", len(iterations)) or 0)
    for line in lines:
        match = TIME_RE.search(line)
        if match:
            current_iteration = _finite(match.group(1))
            if current_iteration is not None:
                iterations.append(current_iteration)
                steps_total += 1
            continue
        match = DELTA_T_RE.search(line)
        if match:
            delta_t = _finite(match.group(1))
            if delta_t is not None:
                delta_t_history.append(
                    {"iteration": current_iteration, "deltaT": delta_t}
                )
            continue
        match = RESIDUAL_RE.search(line)
        if match:
            initial = _finite(match.group(2))
            final = _finite(match.group(3))
            if initial is not None:
                raw_field = match.group(1).strip()
                field = normalized_field_name(raw_field)
                residuals.append(
                    {
                        "iteration": current_iteration,
                        "equation": field,
                        "component": field.split(".", 1)[1] if "." in field else "",
                        "field": field,
                        "raw_field": raw_field,
                        "value": initial,
                        "initial_residual": initial,
                        "final_residual": final,
                        "n_iterations": int(match.group(4)),
                    }
                )
        match = COURANT_RE.search(line)
        if match:
            mean, maximum = _finite(match.group(1)), _finite(match.group(2))
            if mean is not None and maximum is not None:
                courant.append({"iteration": current_iteration, "mean": mean, "max": maximum})
        match = CONTINUITY_RE.search(line)
        if match:
            values = [_finite(match.group(index)) for index in (1, 2, 3)]
            if all(value is not None for value in values):
                continuity.append(
                    {
                        "iteration": current_iteration,
                        "local": values[0],
                        "global": values[1],
                        "cumulative": values[2],
                    }
                )
        match = EXECUTION_RE.search(line)
        if match:
            cpu, clock = _finite(match.group(1)), _finite(match.group(2))
            if cpu is not None and clock is not None:
                execution.append({"iteration": current_iteration, "cpu_s": cpu, "clock_s": clock})
    limit = max(50, int(max_points))
    return {
        "residuals": residuals[-limit:],
        "iterations": iterations[-limit:],
        "courant": courant[-limit:],
        "continuity": continuity[-limit:],
        "execution": execution[-limit:],
        "deltaT_history": delta_t_history[-limit:],
        "deltaT": delta_t,
        "current_iteration": current_iteration,
        "steps_total": steps_total,
    }


def solver_plot_series(text: str, max_points: int = 1200) -> dict[str, Any]:
    parsed = parse_openfoam_lines(text.splitlines(), max_points=max_points)
    residuals: dict[str, list[tuple[float, float]]] = {}
    linear: dict[str, list[tuple[float, float]]] = {}
    for row in parsed["residuals"]:
        field = str(row.get("raw_field") or row["field"])
        x = float(row.get("iteration") or 0.0)
        value = row.get("initial_residual")
        if value is not None and float(value) > 0.0:
            residuals.setdefault(field, []).append((x, float(value)))
        linear.setdefault(field, []).append((x, float(row.get("n_iterations") or 0)))
    return {
        "residuals": residuals,
        "linear_iterations": linear,
        "courant": parsed["courant"],
        "deltaT_history": parsed["deltaT_history"],
        "continuity": parsed["continuity"],
    }


class SolverLogAccumulator:
    """Incrementally parse a growing or rotated solver log."""

    def __init__(self, max_points: int = 1200) -> None:
        self.max_points = max(50, int(max_points))
        self.offset = 0
        self.partial_line = ""
        self._state: dict[str, Any] = {}
        self._identity: tuple[int, int] | None = None

    def reset(self) -> None:
        self.offset = 0
        self.partial_line = ""
        self._state = {}
        self._identity = None

    def update(self, path: Path) -> dict[str, Any]:
        path = Path(path)
        if not path.is_file():
            return self.snapshot()
        stat = path.stat()
        identity = (int(stat.st_dev), int(stat.st_ino))
        if self._identity not in {None, identity} or stat.st_size < self.offset:
            self.reset()
        self._identity = identity
        with path.open("rb") as handle:
            handle.seek(self.offset)
            chunk = handle.read()
            self.offset = handle.tell()
        text = self.partial_line + chunk.decode("utf-8", errors="ignore")
        lines = text.splitlines(keepends=True)
        self.partial_line = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self.partial_line = lines.pop()
        self._state = parse_openfoam_lines(
            (line.rstrip("\r\n") for line in lines),
            self._state,
            max_points=self.max_points,
        )
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        plot = solver_plot_series_from_state(self._state)
        return {**plot, "log_bytes_consumed": self.offset}


def solver_plot_series_from_state(parsed: dict[str, Any]) -> dict[str, Any]:
    residuals: dict[str, deque[tuple[float, float]]] = {}
    linear: dict[str, deque[tuple[float, float]]] = {}
    limit = max(50, len(parsed.get("residuals") or []))
    for row in parsed.get("residuals") or []:
        field = str(row.get("raw_field") or row["field"])
        x = float(row.get("iteration") or 0.0)
        residuals.setdefault(field, deque(maxlen=limit)).append((x, float(row["initial_residual"])))
        linear.setdefault(field, deque(maxlen=limit)).append((x, float(row.get("n_iterations") or 0)))
    return {
        "residuals": {key: list(value) for key, value in residuals.items()},
        "linear_iterations": {key: list(value) for key, value in linear.items()},
        "courant": list(parsed.get("courant") or []),
        "deltaT_history": list(parsed.get("deltaT_history") or []),
        "continuity": list(parsed.get("continuity") or []),
    }


def scalar_signal_inventory(case_dir: Path) -> dict[str, list[str]]:
    """Return case-relative paths that purgeWrite must never delete."""
    case = Path(case_dir).resolve()
    patterns = {
        "forces": ("postProcessing/forceCoeffs*/**/*.dat", "processor0/postProcessing/forceCoeffs*/**/*.dat"),
        "probes": ("postProcessing/probes*/**/*", "processor0/postProcessing/probes*/**/*"),
        "residuals_and_courant": ("log.*", "PyFoam*.logfile", "postProcessing/solverInfo*/**/*"),
    }
    result: dict[str, list[str]] = {}
    for role, role_patterns in patterns.items():
        paths = {
            str(path.resolve().relative_to(case)).replace("\\", "/")
            for pattern in role_patterns
            for path in case.glob(pattern)
            if path.is_file()
        }
        result[role] = sorted(paths)
    return result
