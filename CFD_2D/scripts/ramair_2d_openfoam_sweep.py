#!/usr/bin/env python3
"""Run existing OpenFOAM angle cases sequentially with bounded resources."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def safe_alpha_dir(alpha: float) -> str:
    return f"alpha_{alpha:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def effective_case_status(
    staged_status: dict[str, Any],
    run_status: dict[str, Any],
    returncode: int,
) -> str:
    """Expose the aerodynamic run outcome instead of only the wrapper stage."""
    staged = str(staged_status.get("status") or "").upper()
    solver = str(run_status.get("status") or "").upper()
    if staged.startswith("STEADY_"):
        return staged
    if solver:
        return solver
    if staged:
        return staged
    return "RUN_FAILED" if returncode else "UNKNOWN"


def positive_times(case_dir: Path) -> list[float]:
    values: list[float] = []
    for path in case_dir.iterdir() if case_dir.is_dir() else []:
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if value > 0.0:
            values.append(value)
    return sorted(values)


def staged_command(
    args: argparse.Namespace,
    case_dir: Path,
    resume: bool,
    pending_steady: dict[str, Any] | None = None,
) -> list[str]:
    script = Path(__file__).with_name("ramair_2d_openfoam_staged_runner.py")
    command = [
        sys.executable,
        str(script),
        "--case", str(case_dir),
        "--solver", args.solver,
        "--execution-backend", args.execution_backend,
        "--n-cores", str(max(1, int(args.n_cores))),
        "--timeout-min", str(float(args.timeout_min_per_alpha)),
        "--stop-grace-min", str(float(args.stop_grace_min)),
        "--convergence-minimum-time-star", str(float(args.convergence_minimum_time_star)),
        "--convergence-window-time-star", str(float(args.convergence_window_time_star)),
        "--convergence-mean-tolerance", str(float(args.convergence_mean_tolerance)),
        "--convergence-oscillation-tolerance", str(float(args.convergence_oscillation_tolerance)),
    ]
    command.append(
        "--automatic-core-selection" if args.automatic_core_selection
        else "--no-automatic-core-selection"
    )
    command.append(
        "--renumber-before-decompose" if args.renumber_before_decompose
        else "--no-renumber-before-decompose"
    )
    if args.run:
        command.append("--run")
    if args.steady_initialization:
        command += [
            "--steady-initialization",
            "--steady-timeout-min", str(float(args.steady_timeout_min)),
            "--steady-force-window-samples", str(int(args.steady_force_window_samples)),
            "--steady-force-mean-tolerance-percent", str(float(args.steady_force_mean_tolerance_percent)),
            "--steady-force-fluctuation-tolerance-percent", str(float(args.steady_force_fluctuation_tolerance_percent)),
        ]
        if not args.steady_pyfoam_live_monitor:
            command.append("--no-steady-pyfoam-live-monitor")
    if args.continue_transient_after_steady_timeout:
        command.append("--continue-transient-after-steady-timeout")
    if args.stop_when_force_stable:
        command.append("--stop-when-force-stable")
    if args.pyfoam_live_monitor:
        command.append("--pyfoam-live-monitor")
    if not args.stop_if_checkmesh_fails:
        command.append("--no-stop-if-checkMesh-fails")
    if not args.cleanup_processor_directories:
        command.append("--no-cleanup-processor-directories")
    if pending_steady is not None:
        transition = dict(pending_steady.get("transition") or {})
        latest_iteration = float(
            transition.get("latest_iteration")
            or pending_steady.get("latest_iteration")
            or max(positive_times(case_dir), default=0.0)
        )
        maximum_iterations = float(
            transition.get("maximum_iterations") or max(latest_iteration + 500.0, 15000.0)
        )
        command += [
            "--steady-decision", "extend",
            "--steady-additional-iterations",
            str(max(1, int(round(maximum_iterations - latest_iteration)))),
        ]
    elif resume:
        command.append("--resume")
        if args.resume_additional_time_star is not None:
            command += ["--resume-additional-time-star", str(float(args.resume_additional_time_star))]
    if args.transient_phase_plan is not None:
        command += ["--transient-phase-plan", str(args.transient_phase_plan)]
    return command


def regenerate_case_from_approved_mesh(args: argparse.Namespace, alpha: float) -> dict[str, Any]:
    """Rebuild a clean case instead of trying to sanitize transferred RANS fields."""
    script = Path(__file__).with_name("ramair_2d_openfoam_case_writer.py")
    command = [
        sys.executable,
        str(script),
        "--case-root", str(args.case_root),
        "--variant", str(args.variant),
        "--alpha", str(float(alpha)),
        "--write-case",
        "--mesh-approved-required",
        "--require-converted-polymesh",
        "--overwrite",
        "--existing-case-action", "delete",
    ]
    if args.solver_config is not None:
        command += ["--solver-config", str(args.solver_config)]
    completed = subprocess.run(command, cwd=str(args.case_root), text=True)
    return {"command": command, "returncode": int(completed.returncode)}


def postprocess_command(args: argparse.Namespace, alpha: float) -> list[str]:
    script = Path(__file__).with_name("ramair_2d_postprocess.py")
    command = [
        sys.executable,
        str(script),
        "--case-root", str(args.case_root),
        "--variant", args.variant,
        "--alpha", str(float(alpha)),
        "--average-from-fraction", str(float(args.average_from_fraction)),
        "--run-openfoam-postprocess",
    ]
    return command


def run_with_case_timeout(
    command: list[str],
    *,
    cwd: Path,
    timeout_min: float,
    stop_grace_min: float,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run one staged angle with a real wall-clock bound and recoverable stop."""
    kwargs: dict[str, Any] = {"cwd": str(cwd), "text": True}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    timed_out = False
    try:
        returncode = process.wait(timeout=max(60.0, float(timeout_min) * 60.0))
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGINT)
        else:
            process.send_signal(signal.SIGTERM)
        try:
            returncode = process.wait(
                timeout=max(10.0, float(stop_grace_min) * 60.0)
            )
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                returncode = process.wait(timeout=30.0)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                returncode = process.wait()
    return subprocess.CompletedProcess(command, int(returncode)), timed_out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute prepared alpha cases sequentially. Dry-run by default.")
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--alphas", type=float, nargs="+", required=True)
    parser.add_argument("--solver", default="auto")
    parser.add_argument("--execution-backend", choices=["native", "pyfoam"], default="pyfoam")
    parser.add_argument("--n-cores", type=int, default=4)
    parser.add_argument(
        "--automatic-core-selection", action=argparse.BooleanOptionalAction, default=True,
    )
    parser.add_argument(
        "--renumber-before-decompose", action=argparse.BooleanOptionalAction, default=True,
    )
    parser.add_argument("--timeout-min-per-alpha", type=float, default=120.0)
    parser.add_argument("--steady-initialization", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--steady-timeout-min", type=float, default=30.0)
    parser.add_argument("--steady-force-window-samples", "--steady-force-window-iterations", dest="steady_force_window_samples", type=int, default=500)
    parser.add_argument("--steady-force-mean-tolerance-percent", type=float, default=1.0)
    parser.add_argument("--steady-force-fluctuation-tolerance-percent", type=float, default=2.0)
    parser.add_argument(
        "--continue-transient-after-steady-timeout",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="For unattended sweeps, transfer the latest finite SIMPLE fields and start transient even if the steady plateau test times out.",
    )
    parser.add_argument(
        "--steady-pyfoam-live-monitor",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable only for an attended diagnostic angle; unattended sweeps avoid plot-process overhead.",
    )
    parser.add_argument("--resume-existing", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--restart-existing", action=argparse.BooleanOptionalAction, default=False,
        help="Regenerate every queued angle from the approved mesh and solver configuration before running it.",
    )
    parser.add_argument("--solver-config", type=Path, default=None)
    parser.add_argument("--resume-additional-time-star", type=float, default=None)
    parser.add_argument("--skip-completed", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--continue-after-timeout", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--continue-after-error",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Record a failed angle and continue. The failed case can be inspected or resumed after the sweep.",
    )
    parser.add_argument("--stop-when-force-stable", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--convergence-minimum-time-star", type=float, default=8.0)
    parser.add_argument("--convergence-window-time-star", type=float, default=2.0)
    parser.add_argument("--convergence-mean-tolerance", type=float, default=0.02)
    parser.add_argument("--convergence-oscillation-tolerance", type=float, default=0.10)
    parser.add_argument(
        "--stop-if-checkMesh-fails",
        "--stop-if-checkmesh-fails",
        dest="stop_if_checkmesh_fails",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--pyfoam-live-monitor", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--cleanup-processor-directories", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--stop-grace-min", type=float, default=5.0)
    parser.add_argument("--postprocess-after-each", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--average-from-fraction", type=float, default=0.6)
    parser.add_argument("--transient-phase-plan", type=Path, default=None)
    parser.add_argument("--run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.case_root = args.case_root.resolve()
    if args.restart_existing and args.resume_existing:
        raise ValueError("--restart-existing and --resume-existing are mutually exclusive")
    base = args.case_root / "CFD_2D" / "openfoam_cases" / args.variant
    base.mkdir(parents=True, exist_ok=True)
    status_path = base / "alpha_sweep_status.json"
    stop_marker = base / ".ramair_sweep_stop_request.json"
    stop_marker.unlink(missing_ok=True)
    rows: list[dict[str, Any]] = []
    hard_failure = False
    stopped_by_user = False
    issues = 0
    report: dict[str, Any] = {
        "status": "DRY_RUN" if not args.run else "RUNNING",
        "variant": args.variant,
        "alphas_deg": args.alphas,
        "sequential": True,
        "per_angle_timeout_min": args.timeout_min_per_alpha,
        "per_case_timeout_enforced": True,
        "active_alpha_deg": None,
        "active_case": None,
        "active_phase": "planning",
        "rows": rows,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_json_atomic(status_path, report)
    for alpha in args.alphas:
        if stop_marker.exists():
            stopped_by_user = True
            break
        case_dir = base / safe_alpha_dir(alpha)
        fresh_case: dict[str, Any] | None = None
        if args.restart_existing and args.run:
            fresh_case = regenerate_case_from_approved_mesh(args, alpha)
            if int(fresh_case["returncode"]) != 0:
                rows.append({
                    "alpha_deg": alpha,
                    "status": "CASE_REGENERATION_FAILED",
                    "case_dir": str(case_dir),
                    "case_regeneration": fresh_case,
                })
                issues += 1
                report.update(rows=rows, active_alpha_deg=None, active_case=None, active_phase="case_regeneration_failed", updated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
                write_json_atomic(status_path, report)
                if args.continue_after_error:
                    continue
                hard_failure = True
                break
        if not (case_dir / "system" / "controlDict").is_file():
            rows.append({"alpha_deg": alpha, "status": "MISSING_CASE", "case_dir": str(case_dir)})
            issues += 1
            report.update(rows=rows, active_alpha_deg=None, active_case=None, active_phase="missing_case", updated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            write_json_atomic(status_path, report)
            if args.continue_after_error:
                continue
            hard_failure = True
            break
        prior = read_json(case_dir / "run_status.json", {}) or {}
        prior_status = str(prior.get("status", "")).upper()
        if args.skip_completed and prior_status in {"RUN_COMPLETED", "CONVERGED_STATISTICALLY"}:
            rows.append({"alpha_deg": alpha, "status": "SKIPPED_ALREADY_COMPLETE", "case_dir": str(case_dir)})
            report.update(rows=rows, active_alpha_deg=None, active_case=None, active_phase="skipped_complete", updated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            write_json_atomic(status_path, report)
            continue
        pending_path = case_dir / "steadyInitialization" / "pending_stage.json"
        pending_steady = (
            read_json(pending_path, {})
            if args.resume_existing and pending_path.is_file()
            else None
        )
        resume = bool(
            args.resume_existing and positive_times(case_dir) and pending_steady is None
        )
        command = staged_command(args, case_dir, resume, pending_steady)
        row: dict[str, Any] = {
            "alpha_deg": alpha, "case_dir": str(case_dir), "resume": resume,
            "continuation_kind": (
                "steady_pending_then_transient"
                if pending_steady is not None
                else "transient_resume" if resume else "fresh"
            ),
            "restart_existing": bool(args.restart_existing), "case_regeneration": fresh_case,
            "command": command,
        }
        if not args.run:
            row["status"] = "DRY_RUN"
            rows.append(row)
            report.update(rows=rows, active_alpha_deg=None, active_case=None, active_phase="dry_run", updated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            write_json_atomic(status_path, report)
            continue
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        wall_started = time.perf_counter()
        report.update(
            active_alpha_deg=float(alpha),
            active_case=str(case_dir.resolve()),
            active_phase=(
                "steady_resume_then_transient"
                if pending_steady is not None
                else "steady_then_transient"
                if args.steady_initialization and not resume
                else "transient_resume" if resume else "transient"
            ),
            updated_at=started,
        )
        write_json_atomic(status_path, report)
        completed, case_timed_out = run_with_case_timeout(
            command,
            cwd=case_dir,
            timeout_min=float(args.timeout_min_per_alpha),
            stop_grace_min=float(args.stop_grace_min),
        )
        run_status = read_json(case_dir / "run_status.json", {}) or {}
        staged_status = read_json(case_dir / "staged_run_status.json", {}) or {}
        status = (
            "CASE_TIMEOUT_PARTIAL"
            if case_timed_out
            else effective_case_status(staged_status, run_status, completed.returncode)
        )
        row.update(
            started_at=started,
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            wall_time_s=float(time.perf_counter() - wall_started),
            returncode=completed.returncode,
            case_timeout_reached=bool(case_timed_out),
            status=status,
            run_status=run_status,
            staged_run_status=staged_status,
        )
        if args.postprocess_after_each and positive_times(case_dir):
            post = subprocess.run(postprocess_command(args, alpha), cwd=str(args.case_root), text=True)
            row["postprocess_returncode"] = post.returncode
        rows.append(row)
        is_timeout = status in {"TIMEOUT", "TIMEOUT_PARTIAL", "CASE_TIMEOUT_PARTIAL"}
        is_error = completed.returncode != 0 or status in {
            "RUN_FAILED", "TRANSIENT_STAGE_FAILED", "STEADY_STAGE_FAILED",
            "STEADY_STAGE_DIVERGED", "STEADY_AWAITING_USER_DECISION",
        }
        if is_timeout or is_error:
            issues += 1
        report.update(
            rows=rows,
            active_alpha_deg=None,
            active_case=None,
            active_phase="angle_finished",
            updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        write_json_atomic(status_path, report)
        if stop_marker.exists():
            stopped_by_user = True
            report.update(
                active_alpha_deg=None,
                active_case=None,
                active_phase="stopped_after_active_angle",
                updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            write_json_atomic(status_path, report)
            break
        if (is_timeout and not args.continue_after_timeout) or (is_error and not args.continue_after_error):
            hard_failure = True
            break
    report.update(
        status=(
            "DRY_RUN" if not args.run else
            "STOPPED_BY_USER" if stopped_by_user else
            "STOPPED_ON_FAILURE" if hard_failure else
            "FINISHED_WITH_ISSUES" if issues else
            "FINISHED"
        ),
        active_alpha_deg=None,
        active_case=None,
        active_phase="finished",
        issue_count=int(issues),
        stopped_by_user=bool(stopped_by_user),
        finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    write_json_atomic(status_path, report)
    stop_marker.unlink(missing_ok=True)
    print(json.dumps(report, indent=2))
    return 1 if hard_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
