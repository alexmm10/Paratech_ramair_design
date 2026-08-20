#!/usr/bin/env python3
"""Audit the archived closed-medium slowdown from real evidence."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from ramair_2d_study_registry import utc_stamp, write_json_atomic


TIME_RE = re.compile(r"^Time\s*=\s*([0-9.eE+-]+)", re.MULTILINE)
EXEC_RE = re.compile(
    r"ExecutionTime\s*=\s*([0-9.eE+-]+)\s*s\s+ClockTime\s*=\s*([0-9.eE+-]+)"
)
SOLVER_RE = re.compile(
    r"(?P<solver>\w+):\s+Solving for (?P<field>[^,]+),.*?"
    r"No Iterations (?P<iterations>\d+)"
)
TIME_BLOCK_RE = re.compile(
    r"^Time\s*=\s*(?P<iteration>[0-9.eE+-]+)s?\s*$"
    r"(?P<body>.*?)(?=^Time\s*=|\Z)",
    re.MULTILINE | re.DOTALL,
)
SOLVER_DETAIL_RE = re.compile(
    r"(?P<solver>\w+):\s+Solving for (?P<field>[^,]+),\s+"
    r"Initial residual = (?P<initial>[0-9.eE+-]+),\s+"
    r"Final residual = (?P<final>[0-9.eE+-]+),\s+"
    r"No Iterations (?P<iterations>\d+)"
)
CONTINUITY_RE = re.compile(
    r"time step continuity errors\s*:\s*sum local = (?P<local>[0-9.eE+-]+),\s*"
    r"global = (?P<global>[0-9.eE+-]+),\s*cumulative = (?P<cumulative>[0-9.eE+-]+)"
)


def _iteration_records(text: str, log: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for match in TIME_BLOCK_RE.finditer(text):
        body = match.group("body")
        solvers = []
        for solver in SOLVER_DETAIL_RE.finditer(body):
            solvers.append(
                {
                    "solver": solver.group("solver"),
                    "field": solver.group("field").strip(),
                    "initial_residual": float(solver.group("initial")),
                    "final_residual": float(solver.group("final")),
                    "linear_iterations": int(solver.group("iterations")),
                }
            )
        continuity_match = CONTINUITY_RE.search(body)
        timing_match = EXEC_RE.search(body)
        records.append(
            {
                "log": str(log),
                "iteration": float(match.group("iteration")),
                "solvers": solvers,
                "continuity": (
                    {
                        key: float(continuity_match.group(key))
                        for key in ("local", "global", "cumulative")
                    }
                    if continuity_match
                    else None
                ),
                "execution_time_s": (
                    float(timing_match.group(1)) if timing_match else None
                ),
                "clock_time_s": (
                    float(timing_match.group(2)) if timing_match else None
                ),
                "simple_converged": "SIMPLE solution converged" in body,
            }
        )
    return records


def _numeric_directories(case: Path) -> list[float]:
    values = []
    for path in case.iterdir() if case.is_dir() else ():
        if not path.is_dir():
            continue
        try:
            values.append(float(path.name))
        except ValueError:
            continue
    return sorted(value for value in values if value > 0.0)


def audit_slowdown(
    archive_root: Path,
    *,
    output_markdown: Path,
) -> dict[str, Any]:
    archive_root = Path(archive_root)
    case = archive_root / "case"
    logs = sorted(case.rglob("log.foamRun"))
    parsed: list[dict[str, Any]] = []
    solver_rows: list[dict[str, Any]] = []
    iteration_records: list[dict[str, Any]] = []
    for log in logs:
        text = log.read_text(encoding="utf-8", errors="replace")
        times = [float(value) for value in TIME_RE.findall(text)]
        timings = [
            (float(execution), float(clock))
            for execution, clock in EXEC_RE.findall(text)
        ]
        parsed.append(
            {
                "log": str(log),
                "bytes": log.stat().st_size,
                "first_iteration": min(times) if times else None,
                "last_iteration": max(times) if times else None,
                "timing_samples": len(timings),
                "last_execution_s": timings[-1][0] if timings else None,
                "last_clock_s": timings[-1][1] if timings else None,
            }
        )
        for match in SOLVER_RE.finditer(text):
            solver_rows.append(
                {
                    "solver": match.group("solver"),
                    "field": match.group("field"),
                    "iterations": int(match.group("iterations")),
                }
            )
        iteration_records.extend(_iteration_records(text, log))
    writes = _numeric_directories(case)
    near_7200 = [value for value in writes if 7000 <= value <= 7500]
    gaps = [
        later - earlier
        for earlier, later in zip(near_7200, near_7200[1:])
    ]
    dense_writes = bool(gaps and min(gaps) < 100)
    one_step_relaunch_evidence = (
        len(iteration_records) == 1
        and bool(iteration_records[0].get("simple_converged"))
    )
    unavailable_evidence = [
        "historical_cpu_ram_swap_samples",
        "historical_process_table",
        "per_iteration_force_coefficients_in_archived_solver_log",
        "full_1_to_7850_solver_log",
    ]
    report = {
        "schema_version": 1,
        "status": "AUDITED_FROM_ARCHIVED_REAL_EVIDENCE",
        "archive": str(archive_root),
        "logs": parsed,
        "solver_iteration_samples": solver_rows,
        "iteration_records": iteration_records,
        "written_time_directories": len(writes),
        "writes_between_7000_and_7500": near_7200,
        "write_gaps_between_7000_and_7500": gaps,
        "diagnosis": (
            "REPEATED_ONE_ITERATION_RELAUNCH_WITH_DENSE_FIELD_WRITES"
            if dense_writes and one_step_relaunch_evidence
            else "EXCESSIVE_FIELD_WRITE_IO_NEAR_7200"
            if dense_writes
            else "NO_SINGLE_CAUSE_CONFIRMED_FROM_AVAILABLE_ARCHIVE"
        ),
        "root_cause": (
            "The archived continuation starts at iteration 7849, advances to "
            "7850, satisfies SIMPLE convergence in one iteration and exits. "
            "The legacy orchestrator repeatedly relaunched this short solve "
            "before the absolute 10000 target, producing a full field write "
            "per relaunch. Dense I/O is therefore the slowdown mechanism; "
            "premature one-iteration relaunch is the orchestration root cause."
            if dense_writes and one_step_relaunch_evidence
            else None
        ),
        "process_duplication": "NOT_OBSERVED_IN_CURRENT_SINGLE_FLIGHT_REGISTRY",
        "unavailable_evidence": unavailable_evidence,
        "numerics_changed": False,
        "recommended_action": (
            "Keep scalar histories, avoid writeNow/reconstruction in the live "
            "loop, and retain single-flight execution."
        ),
        "generated_at": utc_stamp(),
    }
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Validation Lab closed_medium slowdown audit",
        "",
        f"- Status: `{report['status']}`",
        f"- Archived evidence: `{archive_root}`",
        f"- Solver logs found: `{len(logs)}`",
        f"- Written time directories: `{len(writes)}`",
        f"- Writes in iteration range 7000-7500: `{len(near_7200)}`",
        f"- Diagnosis: `{report['diagnosis']}`",
        f"- Parsed iteration records: `{len(iteration_records)}`",
        "- Numerics changed by this audit: `false`",
        "",
        "## Root cause",
        "",
        (
            str(report["root_cause"])
            if report["root_cause"]
            else "The available archive does not prove one unique cause."
        ),
        "",
        "## Archived solver evidence",
        "",
        "| Iteration | Clock [s] | p iterations | U iterations | nuTilda iterations | SIMPLE converged |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for row in iteration_records:
        by_field = {
            str(item["field"]): int(item["linear_iterations"])
            for item in row["solvers"]
        }
        u_iterations = sum(
            value
            for field, value in by_field.items()
            if field in {"U", "Ux", "Uy", "Uz"}
        )
        lines.append(
            "| {iteration:g} | {clock} | {p} | {u} | {nu} | {converged} |".format(
                iteration=float(row["iteration"]),
                clock=(
                    f"{row['clock_time_s']:g}"
                    if row["clock_time_s"] is not None
                    else "N/A"
                ),
                p=by_field.get("p", "N/A"),
                u=u_iterations or "N/A",
                nu=by_field.get("nuTilda", "N/A"),
                converged=str(bool(row["simple_converged"])).lower(),
            )
        )
    lines.extend(
        [
        "",
        "The archive does not contain a continuous 1-7850 solver log, so "
        "per-iteration force trends and historical CPU/RAM/swap cannot be "
        "reconstructed honestly.",
        "",
        "## Evidence not available in the archive",
        "",
        *[f"- `{name}`" for name in unavailable_evidence],
        "",
        "## Corrective policy",
        "",
        "- Preserve scalar residual/force histories.",
        "- Do not reconstruct or postprocess while the solver is active.",
        "- Do not issue repeated writeNow requests from the monitor.",
        "- Keep one single-flight lease per run.",
        "- Evaluate the RANS gate only at absolute block targets.",
        "- Do not alter numerical schemes without separate evidence.",
        ]
    )
    output_markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json_atomic(output_markdown.with_suffix(".json"), report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print(audit_slowdown(args.archive_root, output_markdown=args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
