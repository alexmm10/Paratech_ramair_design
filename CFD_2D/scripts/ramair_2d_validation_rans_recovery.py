#!/usr/bin/env python3
"""Return an explicitly selected short validation URANS attempt to its last RANS state."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from ramair_2d_openfoam_staged_runner import reactivate_steady_templates


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def alpha_directory(alpha: float) -> str:
    return f"alpha_{float(alpha):+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def numeric_directories(parent: Path) -> list[tuple[float, Path]]:
    rows: list[tuple[float, Path]] = []
    for path in parent.iterdir() if parent.is_dir() else ():
        if not path.is_dir():
            continue
        try:
            rows.append((float(path.name), path))
        except ValueError:
            continue
    return sorted(rows)


def latest_rans_archive(case: Path) -> tuple[Path, float, Path]:
    candidates: list[tuple[float, float, Path, Path]] = []
    history = case / "steadyInitialization/history"
    for archive in history.glob("run_*") if history.is_dir() else ():
        times = numeric_directories(archive / "time_directories")
        if times:
            iteration, checkpoint = times[-1]
            candidates.append((iteration, archive.stat().st_mtime, archive, checkpoint))
    if not candidates:
        raise FileNotFoundError(f"No archived RANS checkpoint exists below {history}")
    iteration, _, archive, checkpoint = max(candidates, key=lambda row: (row[0], row[1]))
    return archive, iteration, checkpoint


def _replace_entry(text: str, key: str, value: str) -> str:
    updated, count = re.subn(
        rf"(?m)^(\s*{re.escape(key)}\s+)[^;]+;",
        rf"\g<1>{value};",
        text,
        count=1,
    )
    if count != 1:
        raise ValueError(f"Missing OpenFOAM entry {key}")
    return updated


def synchronize_steady_settings(case: Path, solver_config: Path | None) -> dict[str, float]:
    config = read_json(solver_config) if solver_config else {}
    steady = dict(config.get("steady_numerics") or {})
    values = {
        "p": float(steady.get("p_relaxation", 0.3)),
        "U": float(steady.get("U_relaxation", 0.7)),
        "nuTilda": float(steady.get("nuTilda_relaxation", 0.7)),
    }
    solution = case / "system/steadyInitialization/fvSolution"
    text = solution.read_text(encoding="utf-8", errors="ignore")
    text, fields_count = re.subn(
        r"fields\s*\{\s*p\s+[^;]+;\s*\}",
        f"fields {{ p {values['p']:.10g}; }}",
        text,
        count=1,
    )
    text, equations_count = re.subn(
        r"equations\s*\{\s*U\s+[^;]+;\s*nuTilda\s+[^;]+;\s*\}",
        f"equations {{ U {values['U']:.10g}; nuTilda {values['nuTilda']:.10g}; }}",
        text,
        count=1,
    )
    if fields_count != 1 or equations_count != 1:
        raise ValueError(f"Cannot synchronize RANS relaxation factors in {solution}")
    solution.write_text(text, encoding="utf-8")
    stage_config_path = case / "system/steadyInitialization/stage_config.json"
    stage_config = read_json(stage_config_path)
    stage_config["relaxation"] = values
    stage_config["settings_synchronized_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json(stage_config_path, stage_config)
    return values


def recover_short_urans_to_rans(
    root: Path,
    *,
    variant: str,
    alpha: float,
    solver_config: Path | None = None,
    maximum_iterations: int = 15000,
) -> dict[str, Any]:
    root = Path(root).resolve()
    case = root / "CFD_2D/openfoam_cases" / variant / alpha_directory(alpha)
    if not case.is_dir():
        raise FileNotFoundError(case)
    archive, latest_iteration, checkpoint = latest_rans_archive(case)
    if latest_iteration >= float(maximum_iterations):
        raise ValueError(
            f"Archived RANS iteration {latest_iteration:g} already reaches {maximum_iterations}"
        )
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = case / "steadyInitialization/short_urans_backups" / stamp
    backup.mkdir(parents=True, exist_ok=False)

    moved: list[str] = []
    for value, path in numeric_directories(case):
        if value <= 0.0:
            continue
        destination = backup / "time_directories" / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination))
        moved.append(str(destination))
    for path in (case / "postProcessing", case / "run_status.json", case / "staged_run_status.json"):
        if path.exists():
            destination = backup / path.name
            shutil.move(str(path), str(destination))
            moved.append(str(destination))
    for processor in case.glob("processor[0-9]*"):
        if processor.is_dir():
            destination = backup / processor.name
            shutil.move(str(processor), str(destination))
            moved.append(str(destination))

    active_checkpoint = case / checkpoint.name
    shutil.move(str(checkpoint), str(active_checkpoint))
    if (archive / "postProcessing").is_dir():
        shutil.copytree(archive / "postProcessing", case / "postProcessing")

    reactivate_steady_templates(case)
    settings = synchronize_steady_settings(case, solver_config)
    shutil.copy2(
        case / "system/steadyInitialization/fvSolution",
        case / "system/fvSolution",
    )
    control_path = case / "system/controlDict"
    control = control_path.read_text(encoding="utf-8", errors="ignore")
    control = _replace_entry(control, "startFrom", "latestTime")
    control = _replace_entry(control, "startTime", f"{latest_iteration:.12g}")
    control = _replace_entry(control, "stopAt", "endTime")
    control = _replace_entry(control, "endTime", f"{int(maximum_iterations)}")
    control_path.write_text(control, encoding="utf-8")

    pending = {
        "status": "STOPPED_PARTIAL",
        "archive": str(archive.resolve()),
        "latest_iteration": latest_iteration,
        "transition": {
            "status": "RANS_CONTINUATION_REQUIRED",
            "latest_iteration": latest_iteration,
            "maximum_iterations": int(maximum_iterations),
        },
        "recovery_backup": str(backup.resolve()),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "available_actions": ["extend", "start-transient", "finish"],
    }
    write_json(case / "steadyInitialization/pending_stage.json", pending)
    write_json(case / "run_status.json", {
        "status": "STOPPED_PARTIAL",
        "run_outcome": "rans_restart_prepared",
        "mode": "RANS",
        "case_dir": str(case),
        "latest_iteration": latest_iteration,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    write_json(case / "staged_run_status.json", {
        "status": "STEADY_AWAITING_USER_DECISION_STOPPED",
        "production_complete": False,
        "steady_stage_will_run": True,
        "latest_iteration": latest_iteration,
        "pending_steady_stage_exists": True,
        "data_preserved_for_resume": True,
        "available_actions": ["extend", "start-transient", "finish"],
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    (case / ".ramair_stop_request.json").unlink(missing_ok=True)

    results = root / "CFD_2D/results" / variant / alpha_directory(alpha)
    result_backup = None
    if results.is_dir():
        result_backup = results / "short_urans_backups" / stamp
        for path in list(results.iterdir()):
            if path.name in {"RANS", "short_urans_backups"}:
                continue
            if path.name == "URANS" or path.is_file():
                result_backup.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(result_backup / path.name))

    report = {
        "status": "RANS_RESTART_PREPARED",
        "variant": variant,
        "alpha_deg": float(alpha),
        "latest_rans_iteration": latest_iteration,
        "remaining_iterations": int(maximum_iterations - latest_iteration),
        "case": str(case),
        "steady_archive": str(archive),
        "abandoned_urans_backup": str(backup),
        "result_backup": str(result_backup) if result_backup else None,
        "relaxation": settings,
        "moved_items": moved,
    }
    write_json(backup / "recovery_report.json", report)
    return report


def synchronize_pending_rans(
    root: Path,
    *,
    variant: str,
    alpha: float,
    solver_config: Path | None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    case = root / "CFD_2D/openfoam_cases" / variant / alpha_directory(alpha)
    pending = read_json(case / "steadyInitialization/pending_stage.json")
    if not pending:
        raise FileNotFoundError(f"No pending RANS state exists in {case}")
    settings = synchronize_steady_settings(case, solver_config)
    staged_path = case / "staged_run_status.json"
    staged = read_json(staged_path)
    staged.update({
        "status": "STEADY_AWAITING_USER_DECISION_STOPPED",
        "production_complete": False,
        "pending_steady_stage_exists": True,
        "data_preserved_for_resume": True,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    write_json(staged_path, staged)
    return {
        "status": "PENDING_RANS_SYNCHRONIZED",
        "variant": variant,
        "alpha_deg": float(alpha),
        "latest_rans_iteration": float(pending.get("latest_iteration") or 0.0),
        "relaxation": settings,
        "case": str(case),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--variant", default="reference_uncut_validation_1m")
    parser.add_argument("--alpha", type=float, action="append", default=[])
    parser.add_argument("--synchronize-pending-alpha", type=float, action="append", default=[])
    parser.add_argument("--solver-config", type=Path)
    parser.add_argument("--maximum-iterations", type=int, default=15000)
    args = parser.parse_args()
    rows = [
        recover_short_urans_to_rans(
            args.project_root,
            variant=args.variant,
            alpha=alpha,
            solver_config=args.solver_config,
            maximum_iterations=args.maximum_iterations,
        )
        for alpha in args.alpha
    ]
    rows.extend(
        synchronize_pending_rans(
            args.project_root,
            variant=args.variant,
            alpha=alpha,
            solver_config=args.solver_config,
        )
        for alpha in args.synchronize_pending_alpha
    )
    if not rows:
        parser.error("At least one --alpha or --synchronize-pending-alpha is required")
    print(json.dumps({"status": "FINISHED", "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
