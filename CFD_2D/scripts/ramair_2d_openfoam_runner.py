#!/usr/bin/env python3
"""Safe OpenFOAM runner for ram-air 2D cases.

Default behaviour is dry-run: print/write commands only.  Nothing is executed unless
--run is explicitly given.  Only one case is run by default.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import signal
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from openfoam_environment import activate_openfoam_environment
from openfoam_history import read_force_coefficient_history
from ramair_execution_control import publish_solver_process
from ramair_2d_urans_contract import (
    CONTINUE_STAGE,
    FRESH_FROM_CHECKPOINT,
    RESUME_EXISTING,
    normalize_start_mode,
)
from ramair_2d_openfoam_log_events import (
    classify_openfoam_log,
    solver_log_has_fatal_error as _shared_solver_log_has_fatal_error,
    solver_log_indicates_divergence as _shared_solver_log_indicates_divergence,
    solver_log_indicates_setup_error as _shared_solver_log_indicates_setup_error,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

CATIA_INPUTS_DIR_NAME = "CATIA/Inputs"
CFD_ROOT_DIR_NAME = "CFD_2D"


def read_json(path: Path, default: Any = None) -> Any:
    try: return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception: return default


def write_json_atomic(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def project_root_from_case_root(case_root: Path) -> Path:
    case_root = Path(case_root)
    if case_root.name == CATIA_INPUTS_DIR_NAME:
        return case_root.parent
    return case_root


def cfd_root(case_root: Path) -> Path:
    return project_root_from_case_root(case_root) / CFD_ROOT_DIR_NAME


def safe_alpha_dir(alpha: float) -> str:
    return f"alpha_{alpha:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def case_dir(case_root: Path, variant: str, alpha: float) -> Path:
    return cfd_root(case_root) / "openfoam_cases" / variant / safe_alpha_dir(alpha)


def write_status(cdir: Path, status: dict[str, Any]) -> None:
    (cdir / "run_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")


def boundary_text(cdir: Path) -> str:
    p = cdir / "constant" / "polyMesh" / "boundary"
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""


def validate_openfoam_case_path(cdir: Path) -> Path:
    """Reject paths that OpenFOAM's fileName parser cannot represent."""
    resolved = cdir.expanduser().resolve()
    if any(character.isspace() for character in str(resolved)):
        raise RuntimeError(
            "OpenFOAM cannot execute a case from a path containing whitespace: "
            f"{resolved}. Restart the application with START_RAMAIR_CFD2D_APP.bat; "
            "the launcher migrates the native WSL runtime to ~/ramair_cfd/DESIGN_APP "
            "and keeps compatibility links for the former DESIGN APP/INPUT_FILES paths. "
            "Disabling the checkMesh gate would not fix this path error."
        )
    return resolved


def resolve_solver(solver: str) -> str:
    if solver != "auto":
        return solver
    if shutil.which("foamRun"):
        return "foamRun"
    if shutil.which("pimpleFoam"):
        return "pimpleFoam"
    return "pimpleFoam"


def solver_command(solver: str, solver_module: str, parallel: bool = False) -> str:
    if solver == "foamRun":
        cmd = f"foamRun -solver {solver_module}"
    else:
        cmd = solver
    if parallel:
        cmd += " -parallel"
    return cmd


def tail_text(path: Path, max_lines: int = 80) -> str:
    if not path.exists():
        return f"{path} does not exist."
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[-max_lines:])


def solver_log_has_fatal_error(log: str) -> bool:
    return _shared_solver_log_has_fatal_error(log)


def solver_log_indicates_divergence(log: str) -> bool:
    return _shared_solver_log_indicates_divergence(log)


def solver_log_indicates_setup_error(log: str) -> bool:
    return _shared_solver_log_indicates_setup_error(log)


def initial_field_preflight(cdir: Path, case_cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate fields required by the configured turbulence model before MPI."""
    initial_dir = cdir / "0"
    turbulence_model = str(case_cfg.get("turbulence_model", "SpalartAllmaras"))
    required = ["U", "p"]
    if turbulence_model == "SpalartAllmaras":
        required.append("nuTilda")
    present = {
        field: (initial_dir / field).is_file() or (initial_dir / f"{field}.gz").is_file()
        for field in required
    }
    missing = [field for field, exists in present.items() if not exists]
    return {
        "status": "OK" if not missing else "MISSING",
        "initial_directory": str(initial_dir),
        "turbulence_model": turbulence_model,
        "required_fields": required,
        "present": present,
        "missing_fields": missing,
    }


def numeric_time_dirs(cdir: Path) -> list[str]:
    values: list[tuple[float, str]] = []
    for p in cdir.iterdir() if cdir.exists() else []:
        if not p.is_dir():
            continue
        try:
            values.append((float(p.name), p.name))
        except ValueError:
            continue
    return [name for _, name in sorted(values)]


def prepare_resume(cdir: Path, additional_time_star: float | None = None) -> dict[str, Any]:
    """Restart from the latest reconstructed time and optionally extend endTime."""
    positive = [(float(name), name) for name in numeric_time_dirs(cdir) if float(name) > 0.0]
    reconstruction: dict[str, Any] = {"status": "NOT_REQUIRED"}
    if not positive:
        processor_times: list[set[float]] = []
        for processor in sorted(cdir.glob("processor*")):
            if not processor.is_dir():
                continue
            processor_times.append({
                float(name)
                for name in numeric_time_dirs(processor)
                if float(name) > 0.0
            })
        common = set.intersection(*processor_times) if processor_times else set()
        if common:
            latest_processor = max(common)
            executable = shutil.which("reconstructPar")
            if executable is None:
                raise RuntimeError(
                    "RESUME_NOT_AVAILABLE: processor-only positive time exists "
                    "but reconstructPar is not available"
                )
            command = [executable, "-time", f"{latest_processor:g}"]
            completed = subprocess.run(
                command,
                cwd=str(cdir),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=900,
                check=False,
            )
            reconstruction = {
                "status": "OK" if completed.returncode == 0 else "FAILED",
                "command": command,
                "returncode": int(completed.returncode),
                "processor_time": latest_processor,
                "log_tail": (completed.stdout or "")[-5000:],
            }
            if completed.returncode != 0:
                raise RuntimeError(
                    "RESUME_NOT_AVAILABLE: reconstructPar failed for the "
                    "latest common processor time"
                )
            positive = [
                (float(name), name)
                for name in numeric_time_dirs(cdir)
                if float(name) > 0.0
            ]
    if not positive:
        raise RuntimeError(
            "RESUME_NOT_AVAILABLE: the case has no positive reconstructed "
            "or common processor time directory"
        )
    latest_time, latest_name = max(positive)
    path = cdir / "system" / "controlDict"
    text = path.read_text(encoding="utf-8", errors="ignore")
    end_match = re.search(r"\bendTime\s+([0-9.eE+-]+)\s*;", text)
    configured_end = float(end_match.group(1)) if end_match else latest_time
    requested_extension = float(additional_time_star or 0.0)
    new_end = configured_end
    if requested_extension > 0.0:
        case_cfg = read_json(cdir / "case_config.json", {}) or {}
        chord = float(case_cfg.get("chord_m") or 0.0)
        velocity = float(case_cfg.get("velocity_m_s") or 0.0)
        if chord <= 0.0 or velocity <= 0.0:
            raise RuntimeError("Cannot convert resume t* extension because chord_m/velocity_m_s are invalid.")
        new_end = latest_time + requested_extension * chord / velocity
    if new_end <= latest_time + 1.0e-14:
        raise RuntimeError(
            f"Latest time {latest_time:g} has reached endTime {configured_end:g}. "
            "Provide --resume-additional-time-star with a positive convective-time extension."
        )
    backup = path.with_name(f"controlDict.before_resume_{time.strftime('%Y%m%d_%H%M%S')}")
    backup.write_text(text, encoding="utf-8")
    text = re.sub(r"\bstartFrom\s+\w+\s*;", "startFrom       latestTime;", text, count=1)
    text = re.sub(r"\bstopAt\s+\w+\s*;", "stopAt          endTime;", text, count=1)
    if end_match:
        text = re.sub(r"\bendTime\s+[0-9.eE+-]+\s*;", f"endTime         {new_end:.12g};", text, count=1)
    else:
        text += f"\nendTime         {new_end:.12g};\n"
    path.write_text(text, encoding="utf-8")
    report = {
        "status": "RESUME_PREPARED",
        "latest_time": latest_time,
        "latest_time_directory": latest_name,
        "previous_end_time": configured_end,
        "new_end_time": new_end,
        "additional_time_star": requested_extension,
        "controlDict_backup": str(backup),
        "reconstruction": reconstruction,
    }
    (cdir / "resume_plan.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def force_coeffs_available(cdir: Path) -> bool:
    history, _ = read_force_coefficient_history(cdir, include_processor0=True)
    return bool(history)


def force_coeff_stability_report(
    cdir: Path,
    *,
    minimum_time_star: float,
    window_time_star: float,
    mean_tolerance: float,
    oscillation_tolerance: float,
    minimum_samples_per_window: int = 20,
) -> dict[str, Any]:
    """Compare force-coefficient statistics in two adjacent convective windows.

    This accepts a statistically stationary oscillation: it compares both the
    mean and standard deviation between windows instead of requiring the
    instantaneous coefficients or solver residuals to become constant.
    """
    history, sources = read_force_coefficient_history(cdir, include_processor0=True)
    if not history:
        return {"status": "WAITING", "reason": "force_coefficients_not_available"}
    selected = [label for label in ("Cl", "Cd", "Cm") if label in history[-1]]
    case_cfg = read_json(cdir / "case_config.json", {}) or {}
    chord = float(case_cfg.get("chord_m") or 0.0)
    velocity = float(case_cfg.get("velocity_m_s") or 0.0)
    if not math.isfinite(chord) or not math.isfinite(velocity) or chord <= 0.0 or velocity <= 0.0:
        return {
            "status": "WAITING",
            "reason": "invalid_chord_or_velocity_for_convective_time",
            "chord_m": chord,
            "velocity_m_s": velocity,
        }
    rows: list[tuple[float, dict[str, float]]] = []
    for record in history:
        time_star = record["Time"] * velocity / chord
        values = {label: record[label] for label in selected}
        if math.isfinite(time_star) and all(math.isfinite(value) for value in values.values()):
            rows.append((time_star, values))
    if not rows:
        return {"status": "WAITING", "reason": "no_finite_force_coefficient_rows", "sources": sources}
    latest_time_star = rows[-1][0]
    if latest_time_star < max(minimum_time_star, 2.0 * window_time_star):
        return {
            "status": "WAITING",
            "reason": "minimum_convective_time_not_reached",
            "latest_time_star": latest_time_star,
            "required_time_star": max(minimum_time_star, 2.0 * window_time_star),
            "sources": sources,
        }
    current_start = latest_time_star - window_time_star
    previous_start = current_start - window_time_star
    previous = [values for time_star, values in rows if previous_start <= time_star < current_start]
    current = [values for time_star, values in rows if current_start <= time_star <= latest_time_star]
    if min(len(previous), len(current)) < minimum_samples_per_window:
        return {
            "status": "WAITING",
            "reason": "insufficient_samples_in_convective_windows",
            "samples_previous": len(previous),
            "samples_current": len(current),
            "minimum_samples_per_window": minimum_samples_per_window,
            "sources": sources,
        }

    def statistics(values: list[float]) -> tuple[float, float]:
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / max(1, len(values) - 1)
        return mean, math.sqrt(max(0.0, variance))

    coefficient_scales = {"Cl": 0.1, "Cd": 0.02, "Cm": 0.02}
    metrics: dict[str, Any] = {}
    stable = True
    for label in selected:
        previous_mean, previous_std = statistics([row[label] for row in previous])
        current_mean, current_std = statistics([row[label] for row in current])
        scale = max(abs(previous_mean), abs(current_mean), coefficient_scales[label])
        std_scale = max(previous_std, current_std, coefficient_scales[label] * 0.05)
        mean_change = abs(current_mean - previous_mean) / scale
        oscillation_change = abs(current_std - previous_std) / std_scale
        coefficient_stable = mean_change <= mean_tolerance and oscillation_change <= oscillation_tolerance
        stable = stable and coefficient_stable
        metrics[label] = {
            "previous_mean": previous_mean,
            "current_mean": current_mean,
            "previous_std": previous_std,
            "current_std": current_std,
            "normalized_mean_change": mean_change,
            "normalized_std_change": oscillation_change,
            "stable": coefficient_stable,
        }
    return {
        "status": "STABLE" if stable else "UNSTABLE",
        "reason": "adjacent_convective_window_statistics",
        "latest_time_star": latest_time_star,
        "window_time_star": window_time_star,
        "mean_tolerance": mean_tolerance,
        "oscillation_tolerance": oscillation_tolerance,
        "samples_previous": len(previous),
        "samples_current": len(current),
        "metrics": metrics,
        "sources": sources,
    }


def monitor_force_coefficient_stability(
    cdir: Path,
    stop_event: threading.Event,
    converged_event: threading.Event,
    settings: dict[str, Any],
) -> None:
    report_path = cdir / "convergence_monitor.json"
    poll_s = max(1.0, float(settings.get("poll_s", 10.0)))
    required_stable_checks = max(1, int(settings.get("required_stable_checks", 3)))
    consecutive_stable_checks = 0
    while not stop_event.wait(poll_s):
        try:
            report = force_coeff_stability_report(
                cdir,
                minimum_time_star=float(settings["minimum_time_star"]),
                window_time_star=float(settings["window_time_star"]),
                mean_tolerance=float(settings["mean_tolerance"]),
                oscillation_tolerance=float(settings["oscillation_tolerance"]),
                minimum_samples_per_window=int(settings.get("minimum_samples_per_window", 20)),
            )
            if report.get("status") == "STABLE":
                consecutive_stable_checks += 1
            else:
                consecutive_stable_checks = 0
            report["consecutive_stable_checks"] = consecutive_stable_checks
            report["required_stable_checks"] = required_stable_checks
            report["monitor_decision"] = (
                "STOP_ACCEPTED"
                if consecutive_stable_checks >= required_stable_checks
                else "CONTINUE_CONFIRMING_STABILITY"
                if report.get("status") == "STABLE"
                else "CONTINUE"
            )
            report["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            if consecutive_stable_checks >= required_stable_checks:
                backup = update_control_dict_stop_at(cdir, "nextWrite")
                report["stop_requested"] = "nextWrite"
                report["controlDict_backup"] = str(backup)
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                converged_event.set()
                return
        except Exception as exc:
            report_path.write_text(
                json.dumps({"status": "ERROR", "reason": str(exc), "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=2),
                encoding="utf-8",
            )


def cleanup_reconstructed_processor_directories(cdir: Path) -> dict[str, Any]:
    """Remove redundant processorN folders only after reconstruction is proven."""
    case_dir = cdir.resolve()
    processor_dirs = sorted(
        path for path in case_dir.iterdir()
        if path.is_dir() and re.fullmatch(r"processor\d+", path.name)
    )
    if not processor_dirs:
        return {"status": "SKIPPED", "reason": "no_processor_directories", "removed": []}
    root_times = [float(name) for name in numeric_time_dirs(case_dir) if float(name) > 0.0]
    processor_times: list[float] = []
    for processor in processor_dirs:
        processor_times.extend(float(name) for name in numeric_time_dirs(processor) if float(name) > 0.0)
    if not processor_times:
        return {"status": "SKIPPED", "reason": "no_positive_processor_times", "removed": []}
    newest_processor_time = max(processor_times)
    if not root_times or max(root_times) + 1.0e-12 < newest_processor_time:
        return {
            "status": "SKIPPED",
            "reason": "reconstructed_root_time_is_older",
            "newest_processor_time": newest_processor_time,
            "newest_root_time": max(root_times) if root_times else None,
            "removed": [],
        }
    removed: list[str] = []
    for processor in processor_dirs:
        resolved = processor.resolve()
        if resolved.parent != case_dir or not re.fullmatch(r"processor\d+", resolved.name):
            raise RuntimeError(f"Refusing unsafe processor-directory cleanup: {resolved}")
        shutil.rmtree(resolved)
        removed.append(resolved.name)
    return {
        "status": "OK",
        "newest_processor_time": newest_processor_time,
        "newest_root_time": max(root_times),
        "removed": removed,
    }


def configure_decompose_subdomains(cdir: Path, n_cores: int) -> None:
    """Keep decomposeParDict consistent with the MPI process count."""
    path = cdir / "system" / "decomposeParDict"
    if not path.is_file():
        raise FileNotFoundError(f"Missing decomposition dictionary: {path}")
    text = path.read_text(encoding="utf-8", errors="ignore")
    replacement = f"numberOfSubdomains {max(1, int(n_cores))};"
    if re.search(r"\bnumberOfSubdomains\s+\d+\s*;", text):
        text = re.sub(r"\bnumberOfSubdomains\s+\d+\s*;", replacement, text, count=1)
    else:
        text += "\n" + replacement + "\n"
    path.write_text(text, encoding="utf-8")


def update_control_dict_stop_at(cdir: Path, mode: str = "writeNow") -> Path:
    """Request a clean OpenFOAM stop through system/controlDict."""
    if mode not in {"writeNow", "nextWrite", "noWriteNow"}:
        raise ValueError(f"Unsupported stopAt mode: {mode}")
    path = cdir / "system" / "controlDict"
    if not path.exists():
        raise FileNotFoundError(f"Cannot request clean stop; missing {path}")
    text = path.read_text(encoding="utf-8", errors="ignore")
    backup = path.with_suffix(path.suffix + f".before_stop_{time.strftime('%Y%m%d_%H%M%S')}")
    backup.write_text(text, encoding="utf-8")
    if re.search(r"\bstopAt\s+\w+\s*;", text):
        text = re.sub(r"\bstopAt\s+\w+\s*;", f"stopAt          {mode};", text, count=1)
    else:
        text += f"\nstopAt          {mode};\n"
    if re.search(r"\brunTimeModifiable\s+\w+\s*;", text):
        text = re.sub(r"\brunTimeModifiable\s+\w+\s*;", "runTimeModifiable true;", text, count=1)
    else:
        text += "runTimeModifiable true;\n"
    path.write_text(text, encoding="utf-8")
    return backup


def terminate_process_group(proc: subprocess.Popen[str], sig: int = signal.SIGTERM) -> None:
    if os.name != "nt":
        try:
            os.killpg(proc.pid, sig)
            return
        except Exception:
            pass
    if sig == signal.SIGKILL:
        proc.kill()
    else:
        proc.terminate()


def run_script_with_timeout(
    script: Path,
    cdir: Path,
    timeout_s: int,
    *,
    stop_after_s: int | None = None,
    stop_grace_s: int = 120,
    stop_mode: str = "writeNow",
    convergence_settings: dict[str, Any] | None = None,
) -> tuple[int, str, str]:
    external_stop_path = cdir / ".ramair_stop_request.json"
    external_stop_path.unlink(missing_ok=True)
    kwargs: dict[str, Any] = {
        "cwd": str(cdir),
        "text": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    # The process runs with cwd=cdir.  Resolve the script first so a relative
    # --case path cannot be appended to cdir a second time.
    solver_command = ["bash", str(script.resolve())]
    proc = subprocess.Popen(solver_command, **kwargs)
    publish_solver_process(
        cdir,
        status="RUNNING",
        pid=proc.pid,
        command=solver_command,
    )
    monitor_stop = threading.Event()
    converged = threading.Event()
    external_stop = threading.Event()
    external_stop_forced = threading.Event()
    monitor: threading.Thread | None = None
    if convergence_settings is not None:
        monitor = threading.Thread(
            target=monitor_force_coefficient_stability,
            args=(cdir, monitor_stop, converged, convergence_settings),
            name="force-coefficient-stability-monitor",
            daemon=True,
        )
        monitor.start()

    def monitor_external_stop_request() -> None:
        poll = getattr(proc, "poll", None)
        if not callable(poll):
            return
        while poll() is None:
            if not external_stop_path.is_file():
                time.sleep(0.5)
                continue
            external_stop.set()
            try:
                request = json.loads(external_stop_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                request = {}
            requested_mode = str(request.get("mode", stop_mode))
            if requested_mode not in {"writeNow", "nextWrite", "noWriteNow"}:
                requested_mode = "writeNow"
            try:
                update_control_dict_stop_at(cdir, requested_mode)
            except Exception:
                pass
            publish_solver_process(
                cdir,
                status="STOP_REQUESTED",
                pid=proc.pid,
                command=solver_command,
                outcome=f"controlDict_stopAt_{requested_mode}",
            )
            deadline = time.monotonic() + max(1, stop_grace_s)
            while poll() is None and time.monotonic() < deadline:
                time.sleep(0.5)
            if poll() is None:
                external_stop_forced.set()
                publish_solver_process(
                    cdir,
                    status="STOPPING",
                    pid=proc.pid,
                    command=solver_command,
                    outcome="clean_write_grace_elapsed_sigint",
                )
                terminate_process_group(proc, signal.SIGINT)
                interrupt_deadline = time.monotonic() + 60.0
                while poll() is None and time.monotonic() < interrupt_deadline:
                    time.sleep(0.5)
            if poll() is None:
                terminate_process_group(proc, signal.SIGTERM)
                terminate_deadline = time.monotonic() + 30.0
                while poll() is None and time.monotonic() < terminate_deadline:
                    time.sleep(0.5)
            if poll() is None:
                terminate_process_group(proc, signal.SIGKILL)
            return

    external_stop_thread = threading.Thread(
        target=monitor_external_stop_request,
        name="external-openfoam-stop-monitor",
        daemon=True,
    )
    external_stop_thread.start()

    def finish_monitor(outcome: str) -> None:
        monitor_stop.set()
        if monitor is not None:
            monitor.join(timeout=2.0)
        external_stop_thread.join(timeout=2.0)
        external_stop_path.unlink(missing_ok=True)
        if outcome in {"stopped_partial", "stopped_forced", "timeout_partial"}:
            status = "PAUSED_RESTARTABLE"
        else:
            status = "COMPLETED" if int(proc.returncode or 0) == 0 else "FAILED"
        publish_solver_process(
            cdir,
            status=status,
            pid=proc.pid,
            command=solver_command,
            outcome=outcome,
            returncode=proc.returncode,
        )

    def completed_outcome(default: str) -> str:
        if external_stop_forced.is_set():
            return "stopped_forced"
        if external_stop.is_set():
            return "stopped_partial"
        return default

    def restore_control_dict(backup: Path | None) -> None:
        if backup is not None and backup.is_file():
            shutil.copy2(backup, cdir / "system" / "controlDict")

    if stop_after_s is not None and stop_after_s > 0 and stop_after_s < timeout_s:
        try:
            out, _ = proc.communicate(timeout=stop_after_s)
            outcome = completed_outcome("converged_partial" if converged.is_set() else "completed")
            finish_monitor(outcome)
            return int(proc.returncode or 0), out or "", outcome
        except subprocess.TimeoutExpired:
            stop_note = ""
            backup: Path | None = None
            try:
                backup = update_control_dict_stop_at(cdir, stop_mode)
                stop_note = f"\nRequested clean OpenFOAM stopAt {stop_mode}; backup: {backup}\n"
            except Exception as exc:
                stop_note = f"\nCould not edit controlDict for clean stop: {exc}\n"
            try:
                out, _ = proc.communicate(timeout=max(1, stop_grace_s))
                finish_monitor("stopped_partial")
                restore_control_dict(backup)
                return int(proc.returncode or 0), (out or "") + stop_note, "stopped_partial"
            except subprocess.TimeoutExpired:
                terminate_process_group(proc, signal.SIGTERM)
                try:
                    out, _ = proc.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    terminate_process_group(proc, signal.SIGKILL)
                    out, _ = proc.communicate()
                restore_control_dict(backup)
                finish_monitor("stopped_forced")
                return 124, (out or "") + stop_note + "\nForced process termination after stop grace period.\n", "stopped_forced"
    try:
        out, _ = proc.communicate(timeout=timeout_s)
        outcome = completed_outcome("converged_partial" if converged.is_set() else "completed")
        finish_monitor(outcome)
        return int(proc.returncode or 0), out or "", outcome
    except subprocess.TimeoutExpired:
        timeout_note = ""
        backup: Path | None = None
        try:
            backup = update_control_dict_stop_at(cdir, "writeNow")
            timeout_note = (
                f"\nTimeout reached; requested clean OpenFOAM stopAt writeNow and waited "
                f"{stop_grace_s} s. Backup: {backup}\n"
            )
        except Exception as exc:
            timeout_note = f"\nTimeout reached; clean writeNow request failed: {exc}\n"
        try:
            out, _ = proc.communicate(timeout=max(1, stop_grace_s))
        except subprocess.TimeoutExpired:
            terminate_process_group(proc, signal.SIGTERM)
            try:
                out, _ = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                terminate_process_group(proc, signal.SIGKILL)
                out, _ = proc.communicate()
            timeout_note += "Forced process termination after the clean-write grace period.\n"
        restore_control_dict(backup)
        finish_monitor("timeout_partial")
        return 124, (out or "") + timeout_note, "timeout_partial"


def write_script_if_changed(path: Path, text: str) -> dict[str, Any]:
    """Write an execution script only when its exact content changed."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest_path = path.with_name(".ramair_run_script_manifest.json")
    previous = read_json(manifest_path, {}) or {}
    current_text = (
        path.read_text(encoding="utf-8", errors="replace")
        if path.is_file()
        else None
    )
    changed = current_text != text
    if changed:
        path.write_text(text, encoding="utf-8")
        try:
            path.chmod(0o755)
        except OSError:
            pass
        revision = int(previous.get("revision") or 0) + 1
        payload = {
            "schema_version": 1,
            "script": str(path),
            "script_sha256": digest,
            "script_written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "revision": revision,
        }
        write_json_atomic(manifest_path, payload)
    else:
        payload = {
            **previous,
            "schema_version": 1,
            "script": str(path),
            "script_sha256": digest,
            "revision": int(previous.get("revision") or 1),
        }
        if not manifest_path.is_file():
            payload["script_written_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S%z"
            )
            write_json_atomic(manifest_path, payload)
    return {**payload, "changed": changed}


def write_run_script(
    path: Path,
    solver: str,
    solver_module: str,
    n_cores: int,
    nice: int,
    potential: bool = False,
    *,
    decompose_times: list[float] | None = None,
    decompose_latest: bool = False,
    reconstruct_times: list[float] | None = None,
) -> dict[str, Any]:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "echo Running in $(pwd)",
        "rm -f .ramair_solver_started_monotonic .ramair_solver_finished_monotonic .ramair_run_stage_status",
    ]
    if reconstruct_times:
        reconstruct_spec = ",".join(
            f"{float(value):.12g}" for value in reconstruct_times
        )
        reconstruct_command = (
            f"reconstructPar -time {shlex.quote(reconstruct_spec)}"
        )
    else:
        reconstruct_command = "reconstructPar -latestTime"
    if potential:
        lines += [
            "set +e",
            f"nice -n {nice} potentialFoam > log.potentialFoam 2>&1",
            "potential_rc=$?",
            "set -e",
            "echo potentialFoam=${potential_rc} >> .ramair_run_stage_status",
            "if [ ${potential_rc} -ne 0 ]; then exit ${potential_rc}; fi",
        ]
    if n_cores > 1:
        if decompose_times:
            time_spec = ",".join(f"{float(value):.12g}" for value in decompose_times)
            decompose_command = f"decomposePar -force -time {shlex.quote(time_spec)}"
        elif decompose_latest:
            decompose_command = "decomposePar -force -latestTime"
        else:
            decompose_command = "decomposePar -force"
        lines += [
            "set +e",
            f"{decompose_command} > log.decomposePar 2>&1",
            "decompose_rc=$?",
            "set -e",
            "echo decomposePar=${decompose_rc} >> .ramair_run_stage_status",
            "if [ ${decompose_rc} -ne 0 ]; then exit ${decompose_rc}; fi",
            "cut -d' ' -f1 /proc/uptime > .ramair_solver_started_monotonic",
            "set +e",
            f"nice -n {nice} mpirun -np {n_cores} {solver_command(solver, solver_module, parallel=True)} > log.{solver} 2>&1",
            "solver_rc=$?",
            "set -e",
            "echo solver=${solver_rc} >> .ramair_run_stage_status",
            "cut -d' ' -f1 /proc/uptime > .ramair_solver_finished_monotonic",
            "set +e",
            f"{reconstruct_command} > log.reconstructPar 2>&1",
            "reconstruct_rc=$?",
            "set -e",
            "echo reconstructPar=${reconstruct_rc} >> .ramair_run_stage_status",
            "if [ ${solver_rc} -ne 0 ]; then exit ${solver_rc}; fi",
            "exit ${reconstruct_rc}",
        ]
    else:
        lines += [
            "cut -d' ' -f1 /proc/uptime > .ramair_solver_started_monotonic",
            "set +e",
            f"nice -n {nice} {solver_command(solver, solver_module)} > log.{solver} 2>&1",
            "solver_rc=$?",
            "set -e",
            "echo solver=${solver_rc} >> .ramair_run_stage_status",
            "cut -d' ' -f1 /proc/uptime > .ramair_solver_finished_monotonic",
            "exit ${solver_rc}",
        ]
    return write_script_if_changed(path, "\n".join(lines) + "\n")


def run_stage_evidence(cdir: Path, solver: str) -> dict[str, Any]:
    """Identify the command that actually failed before choosing a log."""
    status_path = Path(cdir) / ".ramair_run_stage_status"
    codes: dict[str, int] = {}
    if status_path.is_file():
        for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
            name, separator, value = line.partition("=")
            if not separator:
                continue
            try:
                codes[name.strip()] = int(value.strip())
            except ValueError:
                continue
    solver_started = (Path(cdir) / ".ramair_solver_started_monotonic").is_file()
    failed_stage = next((name for name, code in codes.items() if code != 0), None)
    if failed_stage == "decomposePar":
        failed_log = Path(cdir) / "log.decomposePar"
    elif failed_stage == "reconstructPar":
        failed_log = Path(cdir) / "log.reconstructPar"
    elif failed_stage == "potentialFoam":
        failed_log = Path(cdir) / "log.potentialFoam"
    else:
        failed_stage = "solver" if failed_stage == "solver" else failed_stage
        failed_log = Path(cdir) / f"log.{solver}"
    return {
        "solver_started": solver_started,
        "stage_returncodes": codes,
        "failed_stage": failed_stage,
        "failed_log": str(failed_log) if failed_stage and failed_log.is_file() else None,
    }


def write_pyfoam_run_script(
    path: Path,
    cdir: Path,
    solver: str,
    solver_module: str,
    n_cores: int,
    nice: int,
    potential: bool = False,
    live_plot_watcher: bool = False,
) -> dict[str, Any]:
    worker = Path(__file__).resolve().parents[1] / "app" / "pyfoam_solver_runner.py"
    command = [
        sys.executable,
        str(worker),
        "--case", str(cdir.resolve()),
        "--solver", solver,
        "--solver-module", solver_module,
        "--n-cores", str(max(1, int(n_cores))),
        "--nice", str(int(nice)),
        "--report", str((cdir / "pyfoam_run_report.json").resolve()),
    ]
    if potential:
        command.append("--potential-foam")
    if live_plot_watcher:
        command.append("--live-plot-watcher")
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "echo Running through PyFoam in $(pwd)",
        "exec " + shlex.join(command),
    ]
    return write_script_if_changed(path, "\n".join(lines) + "\n")


def active_solver_log(cdir: Path, solver: str, execution_backend: str) -> Path:
    """Return the log for the stage that actually failed or ran most recently."""
    canonical = cdir / f"log.{solver}"
    if execution_backend != "pyfoam":
        return canonical
    report = read_json(cdir / "pyfoam_run_report.json", {}) or {}
    for key in ("failed_log", "solver_log"):
        value = report.get(key)
        if value:
            candidate = Path(str(value))
            if candidate.is_file():
                return candidate
    for stage in report.get("stages", []):
        if stage.get("stage") == "steady_or_transient_solver":
            candidate = Path(str(stage.get("log", "")))
            if candidate.is_file():
                return candidate
    solver_data = report.get("solver_data") or {}
    candidate = Path(str(solver_data.get("logfile", "")))
    if candidate.is_file():
        return candidate
    active_log = report.get("active_log")
    if active_log:
        candidate = Path(str(active_log))
        if candidate.is_file():
            return candidate
    if canonical.is_file():
        return canonical
    candidates = [
        path for pattern in ("PyFoam*.logfile", "*PyFoam*.logfile", "*PyFoamRunner*")
        for path in cdir.glob(pattern)
        if path.is_file()
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else canonical


def available_openmpi_slots() -> int | None:
    """Return physical core slots used by Open MPI's default mapping."""
    try:
        completed = subprocess.run(
            ["lscpu", "-p=CORE,SOCKET"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        cores = {
            tuple(int(value) for value in line.split(",")[:2])
            for line in completed.stdout.splitlines()
            if line and not line.startswith("#") and "," in line
        }
        return len(cores) or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _latest_integer_time(case: Path) -> int:
    values: list[int] = []
    for root in (
        case,
        *(
            tuple(
                (case / "steadyInitialization/history").glob(
                    "run_*/time_directories"
                )
            )
            if (case / "steadyInitialization/history").is_dir()
            else ()
        ),
    ):
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if not path.is_dir():
                continue
            try:
                value = float(path.name)
            except ValueError:
                continue
            if value > 0 and abs(value - round(value)) < 1.0e-8:
                values.append(int(round(value)))
    return max(values, default=0)


def _read_monotonic_marker(path: Path) -> float | None:
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None


def record_solver_timing_segment(
    case: Path,
    *,
    iteration_start: int,
    iteration_end: int,
    orchestration_started: float,
    orchestration_finished: float,
) -> dict[str, Any]:
    started = _read_monotonic_marker(
        case / ".ramair_solver_started_monotonic"
    )
    finished = _read_monotonic_marker(
        case / ".ramair_solver_finished_monotonic"
    )
    if started is None:
        started = orchestration_started
    if finished is None:
        finished = orchestration_finished
    active = max(0.0, finished - started)
    setup = max(0.0, started - orchestration_started)
    post = max(0.0, orchestration_finished - finished)
    run_status = read_json(case / "run_status.json", {}) or {}
    run_id = str(
        run_status.get("run_id")
        or run_status.get("checkpoint_id")
        or case.name
    )
    row = {
        "segment_id": (
            f"{run_id}_{iteration_start}_{iteration_end}_"
            f"{time.monotonic_ns()}"
        ),
        "run_id": run_id,
        "iteration_start": int(iteration_start),
        "iteration_end": int(iteration_end),
        "solver_started_monotonic": started,
        "solver_finished_monotonic": finished,
        "active_solver_seconds": active,
        "setup_seconds": setup,
        "post_seconds": post,
        "total_elapsed_seconds": max(
            0.0, orchestration_finished - orchestration_started
        ),
    }
    path = case / "solver_timing_segments.json"
    rows = read_json(path, []) or []
    if not isinstance(rows, list):
        rows = []
    rows.append(row)
    write_json_atomic(path, rows)
    return row


def run_case(
    cdir: Path,
    solver: str,
    execution_backend: str,
    n_cores: int,
    timeout_min: float,
    nice: int,
    potential: bool,
    dry_run: bool,
    stop_if_checkMesh_fails: bool,
    fail_on_timeout: bool,
    stop_after_min: float | None,
    stop_grace_min: float,
    stop_mode: str,
    pyfoam_live_monitor: bool,
    cleanup_processor_directories: bool,
    stop_when_force_stable: bool,
    convergence_minimum_time_star: float,
    convergence_window_time_star: float,
    convergence_mean_tolerance: float,
    convergence_oscillation_tolerance: float,
    convergence_poll_s: float,
    resume: bool = False,
    resume_additional_time_star: float | None = None,
    start_mode: str | None = None,
    expected_start_time: float | None = None,
    decompose_times: list[float] | None = None,
    reconstruct_times: list[float] | None = None,
) -> None:
    cdir = validate_openfoam_case_path(cdir)
    cdir = cdir.resolve()
    solver = resolve_solver(solver)
    case_cfg = read_json(cdir / "case_config.json", {}) or {}
    solver_module = str(case_cfg.get("solver_module", "incompressibleFluid"))
    mpi_slots = available_openmpi_slots()
    if n_cores > 1 and mpi_slots is not None and n_cores > mpi_slots:
        raise RuntimeError(
            f"Requested {n_cores} MPI processes, but Open MPI exposes {mpi_slots} physical slots. "
            "Use at most that value; oversubscription is intentionally disabled because it usually "
            "slows this memory-bound CFD case and can make the workstation unresponsive."
        )
    effective_start_mode = normalize_start_mode(start_mode, legacy_resume=resume)
    script = cdir / "run_case.sh"
    if n_cores > 1:
        configure_decompose_subdomains(cdir, n_cores)
    if execution_backend == "pyfoam":
        if importlib.util.find_spec("PyFoam") is None:
            raise RuntimeError(
                "PyFoam backend selected but PyFoam is not importable. "
                "Run bash 'Documents and Manuals/Application/bootstrap_cfd2d_app_wsl.sh' --install or choose --execution-backend native."
            )
        script_state = write_pyfoam_run_script(
            script,
            cdir,
            solver,
            solver_module,
            n_cores,
            nice,
            potential,
            live_plot_watcher=pyfoam_live_monitor,
        )
    else:
        script_state = write_run_script(
            script,
            solver,
            solver_module,
            n_cores,
            nice,
            potential,
            decompose_times=decompose_times,
            decompose_latest=(
                not decompose_times
                and effective_start_mode in {CONTINUE_STAGE, RESUME_EXISTING}
            ),
            reconstruct_times=reconstruct_times,
        )
    print(
        f"Run script {'written' if script_state['changed'] else 'unchanged'}: "
        f"{script}"
    )
    if resume:
        print("WARNING: --resume is retained for compatibility; use --start-mode RESUME_EXISTING.")
    if dry_run:
        print(script.read_text())
        write_status(cdir, {
            "status": "DRY_RUN",
            "solver": solver,
            "solver_module": solver_module,
            "execution_backend": execution_backend,
            "case_dir": str(cdir),
            "script": str(script),
            "run": False,
            "start_mode": effective_start_mode,
            "resume_requested": effective_start_mode == RESUME_EXISTING,
            "resume_additional_time_star": resume_additional_time_star,
            "available_time_dirs": numeric_time_dirs(cdir),
        })
        return
    positive_times = [name for name in numeric_time_dirs(cdir) if float(name) > 0.0]
    if positive_times and effective_start_mode == FRESH_FROM_CHECKPOINT:
        raise RuntimeError(
            "FRESH_FROM_CHECKPOINT refuses a case that already contains positive time directories. "
            "Create a new attempt or explicitly use RESUME_EXISTING."
        )
    if effective_start_mode == CONTINUE_STAGE:
        if not positive_times:
            raise RuntimeError(
                "CONTINUE_STAGE requires the preceding stage checkpoint, but no positive time directory exists."
            )
        latest_time = max(float(name) for name in positive_times)
        if expected_start_time is not None and not math.isclose(
            latest_time, float(expected_start_time), rel_tol=0.0, abs_tol=1.0e-10
        ):
            raise RuntimeError(
                "CONTINUE_STAGE checkpoint mismatch: expected "
                f"{expected_start_time:.12g}, found latest time {latest_time:.12g}."
            )
        control = cdir / "system" / "controlDict"
        text = control.read_text(encoding="utf-8", errors="ignore")
        text = re.sub(r"(?m)^\s*startFrom\s+[^;]+;", "startFrom       latestTime;", text)
        control.write_text(text, encoding="utf-8")
    resume_report: dict[str, Any] | None = None
    btxt = boundary_text(cdir)
    if not btxt:
        raise RuntimeError("constant/polyMesh/boundary is missing; refusing to run solver.")
    if "ram_air_inlet" in btxt:
        raise RuntimeError("Forbidden patch ram_air_inlet found in boundary file; refusing to run solver.")
    if "frontAndBack" not in btxt or "empty" not in btxt:
        raise RuntimeError("frontAndBack empty patch is missing; refusing to run 2D OpenFOAM case.")
    field_preflight = initial_field_preflight(cdir, case_cfg)
    if field_preflight["status"] != "OK":
        write_status(cdir, {
            "status": "RUN_SETUP_FAILED",
            "solver": solver,
            "solver_module": solver_module,
            "execution_backend": execution_backend,
            "case_dir": str(cdir),
            "setup_error": "missing_initial_fields",
            "initial_field_preflight": field_preflight,
            "divergence_detected": False,
        })
        raise RuntimeError(
            "OpenFOAM case preparation is incomplete; missing initial field(s): "
            + ", ".join(field_preflight["missing_fields"])
            + ". Rewrite the case with the current case writer before running the solver."
        )
    if shutil.which(solver) is None:
        raise RuntimeError(f"OpenFOAM solver executable not found on PATH: {solver}")
    if stop_if_checkMesh_fails and shutil.which("checkMesh"):
        chk = subprocess.run(["checkMesh", "-allTopology", "-allGeometry"], cwd=str(cdir), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        (cdir / "log.checkMesh.preRun").write_text(chk.stdout, encoding="utf-8", errors="ignore")
        failed_match = re.search(r"Failed\s+(\d+)\s+mesh checks", chk.stdout)
        failed_count = int(failed_match.group(1)) if failed_match else 0
        fatal_text = any(s in chk.stdout for s in [" ***Error", "negative volume", "negative cell"])
        if chk.returncode != 0 or failed_count > 0 or fatal_text:
            path_error = "fileName::stripInvalid" in chk.stdout or "invalid fileName" in chk.stdout
            write_status(cdir, {
                "status": "BLOCKED_BY_INVALID_CASE_PATH" if path_error else "BLOCKED_BY_CHECKMESH",
                "solver": solver,
                "case_dir": str(cdir),
                "checkMesh_log": str(cdir / "log.checkMesh.preRun"),
                "checkMesh_returncode": chk.returncode,
                "checkMesh_failed_checks": failed_count,
            })
            if path_error:
                raise RuntimeError(
                    "checkMesh rejected the OpenFOAM case path as an invalid fileName. "
                    "Use the native WSL runtime ~/ramair_cfd/DESIGN_APP; --no-stop-if-checkMesh-fails "
                    "cannot make an invalid OpenFOAM path runnable."
                )
            raise RuntimeError(
                f"checkMesh failed before solver run (return code {chk.returncode}, "
                f"reported failed checks {failed_count}); refusing to execute solver. "
                "For a short software-only debug run, rerun with --no-stop-if-checkMesh-fails after reviewing log.checkMesh.preRun."
            )
    if effective_start_mode == RESUME_EXISTING:
        resume_report = prepare_resume(cdir, resume_additional_time_star)
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    log_path = cdir / f"log.{solver}"
    control_path = cdir / "system" / "controlDict"
    control_before_run = control_path.read_text(encoding="utf-8", errors="ignore")
    timeout_s = max(1, int(timeout_min * 60))
    stop_after_s = None if stop_after_min is None or stop_after_min <= 0 else max(1, int(stop_after_min * 60))
    stop_grace_s = max(1, int(stop_grace_min * 60))
    iteration_before = _latest_integer_time(cdir)
    orchestration_started = time.monotonic()
    write_status(cdir, {
        "status": "RUNNING",
        "solver": solver,
        "solver_module": solver_module,
        "execution_backend": execution_backend,
        "started": started,
        "start_mode": effective_start_mode,
        "expected_start_time": expected_start_time,
        "available_time_dirs": numeric_time_dirs(cdir),
        "solver_started": False,
    })
    completed_returncode, runner_stdout, run_outcome = run_script_with_timeout(
        script,
        cdir,
        timeout_s,
        stop_after_s=stop_after_s,
        stop_grace_s=stop_grace_s,
        stop_mode=stop_mode,
        convergence_settings={
            "minimum_time_star": convergence_minimum_time_star,
            "window_time_star": convergence_window_time_star,
            "mean_tolerance": convergence_mean_tolerance,
            "oscillation_tolerance": convergence_oscillation_tolerance,
            "poll_s": convergence_poll_s,
            "minimum_samples_per_window": 20,
            "required_stable_checks": 3,
        } if stop_when_force_stable else None,
    )
    orchestration_finished = time.monotonic()
    iteration_after = _latest_integer_time(cdir)
    timing_segment = record_solver_timing_segment(
        cdir,
        iteration_start=iteration_before,
        iteration_end=iteration_after,
        orchestration_started=orchestration_started,
        orchestration_finished=orchestration_finished,
    )
    # Exit 124 is the runner's deliberate timeout sentinel.  If the timeout or
    # requested stop path reached this point, OpenFOAM has already been asked
    # to write and the wrapper has attempted reconstruction; preserve the
    # partial outcome so its fields can be postprocessed.
    # A stop requested from Streamlit edits the same run-time-modifiable
    # controlDict from outside this process. Restore the original endTime policy
    # only after MPI/PyFoam has exited and reconstruction has completed.
    control_restore_performed_by_outer_runner = False
    if control_path.is_file():
        control_after_run = control_path.read_text(encoding="utf-8", errors="ignore")
        if control_after_run != control_before_run:
            control_path.write_text(control_before_run, encoding="utf-8")
            control_restore_performed_by_outer_runner = True
    control_restored_after_run = bool(
        control_path.is_file()
        and control_path.read_text(encoding="utf-8", errors="ignore") == control_before_run
    )
    if control_restore_performed_by_outer_runner and run_outcome == "completed":
        run_outcome = "stopped_partial"
    (cdir / "log.runner").write_text(runner_stdout or "", encoding="utf-8", errors="ignore")
    stage_evidence = run_stage_evidence(cdir, solver)
    log_path = active_solver_log(cdir, solver, execution_backend)
    if completed_returncode != 0 and stage_evidence.get("failed_log"):
        log_path = Path(str(stage_evidence["failed_log"]))
    log = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
    log_event = classify_openfoam_log(log, returncode=completed_returncode).as_dict()
    processor_cleanup = (
        cleanup_reconstructed_processor_directories(cdir)
        if cleanup_processor_directories and n_cores > 1
        else {"status": "SKIPPED", "reason": "disabled_or_serial", "removed": []}
    )
    if run_outcome in {"timeout_partial", "stopped_partial", "stopped_forced", "converged_partial"}:
        is_timeout = run_outcome == "timeout_partial"
        status = {
            "status": (
                "TIMEOUT_PARTIAL" if is_timeout else
                "CONVERGED_STATISTICALLY" if run_outcome == "converged_partial" else
                "STOPPED_PARTIAL" if run_outcome == "stopped_partial" else
                "STOPPED_FORCED_PARTIAL"
            ),
            "solver": solver,
            "solver_module": solver_module,
            "execution_backend": execution_backend,
            "started": started,
            "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
            "timeout_min": timeout_min,
            "stop_after_min": stop_after_min,
            "stop_grace_min": stop_grace_min,
            "stop_mode": stop_mode,
            "run_outcome": run_outcome,
            "timeout_is_nonfatal": not fail_on_timeout,
            "case_dir": str(cdir),
            "solver_log": str(log_path),
            "solver_log_tail": tail_text(log_path),
            "available_time_dirs": numeric_time_dirs(cdir),
            "forceCoeffs_available": force_coeffs_available(cdir),
            "controlDict_restored_after_stop": control_restored_after_run,
            "controlDict_restore_performed_by_outer_runner": control_restore_performed_by_outer_runner,
            "processor_directory_cleanup": processor_cleanup,
            "convergence_monitor": read_json(cdir / "convergence_monitor.json", None),
            "resume": resume_report,
            "start_mode": effective_start_mode,
            "solver_started": bool(stage_evidence.get("solver_started")),
            "stage_returncodes": stage_evidence.get("stage_returncodes", {}),
            "openfoam_event": log_event,
        }
        write_status(cdir, status)
        print("")
        if run_outcome == "converged_partial":
            print("OpenFOAM solver reached the configured statistical force-coefficient stability criterion.")
        elif is_timeout:
            print(f"OpenFOAM solver reached the timeout after {timeout_min:g} min.")
        else:
            elapsed_label = f" after {stop_after_min:g} min" if stop_after_min is not None else " from the application"
            print(f"OpenFOAM solver was stopped by requested stopAt {stop_mode}{elapsed_label}.")
        print("Partial outputs were kept for post-processing.")
        print(f"Solver log: {log_path}")
        print("Available time directories:", ", ".join(status["available_time_dirs"][-8:]) or "none")
        print("forceCoeffs available:", status["forceCoeffs_available"])
        print("Last solver log lines:")
        print(status["solver_log_tail"])
        if is_timeout and fail_on_timeout:
            raise TimeoutError(f"OpenFOAM solver timed out after {timeout_min:g} min; inspect {log_path}")
        return
    try:
        if completed_returncode != 0:
            tail = tail_text(log_path)
            pyfoam_report = read_json(cdir / "pyfoam_run_report.json", {}) or {}
            divergence_detected = bool(
                pyfoam_report.get("status") == "RUN_DIVERGED"
                or (pyfoam_report.get("divergence_diagnostics") or {}).get("status") == "DIVERGED"
                or log_event.get("numerical_divergence")
            )
            setup_error_detected = bool(log_event.get("setup_error"))
            write_status(cdir, {
                "status": (
                    "RUN_DIVERGED" if divergence_detected else
                    "RUN_SETUP_FAILED" if setup_error_detected else
                    "RUN_COMMAND_FAILED"
                ),
                "solver": solver,
                "solver_module": solver_module,
                "execution_backend": execution_backend,
                "started": started,
                "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
                "returncode": completed_returncode,
                "divergence_detected": divergence_detected,
                "setup_error_detected": setup_error_detected,
                "initial_field_preflight": field_preflight,
                "divergence_diagnostics": pyfoam_report.get("divergence_diagnostics"),
                "pyfoam_report": pyfoam_report,
                "failed_stage": pyfoam_report.get("failed_stage") or stage_evidence.get("failed_stage"),
                "failed_log": pyfoam_report.get("failed_log") or stage_evidence.get("failed_log"),
                "solver_log": str(log_path),
                "solver_log_tail": tail,
                "available_time_dirs": numeric_time_dirs(cdir),
                "forceCoeffs_available": force_coeffs_available(cdir),
                "processor_directory_cleanup": processor_cleanup,
                "start_mode": effective_start_mode,
                "solver_started": bool(stage_evidence.get("solver_started")),
                "stage_returncodes": stage_evidence.get("stage_returncodes", {}),
                "openfoam_event": log_event,
            })
            print("")
            if divergence_detected:
                print(f"OpenFOAM solver divergence detected; process exited with code {completed_returncode}.")
            elif setup_error_detected:
                print(f"OpenFOAM case/setup failure detected; process exited with code {completed_returncode}.")
            else:
                print(f"OpenFOAM solver failed with exit code {completed_returncode}.")
            if pyfoam_report.get("failed_stage"):
                print(f"Failed stage: {pyfoam_report['failed_stage']}")
            print(f"Solver log: {log_path}")
            print("Last solver log lines:")
            print(tail)
            raise RuntimeError(f"OpenFOAM solver failed; inspect {log_path}")
        diverged = bool(log_event.get("numerical_divergence"))
        write_status(cdir, {"status": "RUN_FAILED" if diverged else "RUN_COMPLETED", "solver": solver, "solver_module": solver_module, "execution_backend": execution_backend, "started": started, "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "divergence_detected": diverged, "solver_log": str(log_path), "controlDict_restored_after_stop": control_restored_after_run, "controlDict_restore_performed_by_outer_runner": control_restore_performed_by_outer_runner, "processor_directory_cleanup": processor_cleanup, "resume": resume_report, "start_mode": effective_start_mode, "solver_started": bool(stage_evidence.get("solver_started")), "stage_returncodes": stage_evidence.get("stage_returncodes", {}), "openfoam_event": log_event})
        if diverged:
            print("")
            print(f"Solver log: {log_path}")
            print("Last solver log lines:")
            print(tail_text(log_path))
            raise RuntimeError("Solver log contains divergence/FATAL/nan markers.")
    except Exception:
        raise


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Safely run approved OpenFOAM cases. Dry-run by default.")
    p.add_argument("--case", type=Path, default=None, help="Direct OpenFOAM case directory. Overrides --case-root/--variant/--alpha.")
    p.add_argument("--case-root", type=Path, default=Path("."))
    p.add_argument("--variant", default=None)
    p.add_argument("--alpha", type=float, default=None)
    p.add_argument("--solver", default="auto", help="auto, foamRun or pimpleFoam. auto prefers foamRun for OpenFOAM 13/14, then pimpleFoam.")
    p.add_argument("--execution-backend", choices=["native", "pyfoam"], default="native", help="native uses the existing shell runner; pyfoam executes the solver through PyFoam.BasicRunner.")
    p.add_argument("--n-cores", type=int, default=4)
    p.add_argument("--timeout-min", type=float, default=120)
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--run", action="store_true")
    p.add_argument("--max-cases-parallel", type=int, default=1)
    p.add_argument("--stop-if-diverged", action="store_true", default=True)
    p.add_argument(
        "--stop-if-checkMesh-fails",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run checkMesh before the solver and refuse execution on failure. Use --no-stop-if-checkMesh-fails only for short software debugging runs.",
    )
    p.add_argument(
        "--stop-when-force-stable",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Request stopAt nextWrite after Cl/Cd/Cm statistics agree in two adjacent convective-time windows.",
    )
    p.add_argument("--convergence-minimum-time-star", type=float, default=8.0, help="Earliest convective time t*=tU/c at which statistical stopping is allowed.")
    p.add_argument("--convergence-window-time-star", type=float, default=2.0, help="Width in convective time of each of the two coefficient windows.")
    p.add_argument("--convergence-mean-tolerance", type=float, default=0.02, help="Maximum normalized change of Cl/Cd/Cm means between adjacent windows.")
    p.add_argument("--convergence-oscillation-tolerance", type=float, default=0.10, help="Maximum normalized change of Cl/Cd/Cm standard deviations between adjacent windows.")
    p.add_argument("--convergence-poll-s", type=float, default=10.0, help="Seconds between checks of the forceCoeffs history.")
    p.add_argument("--purge-write", type=int, default=3)
    p.add_argument("--nice", type=int, default=10)
    p.add_argument("--potentialFoam", action="store_true")
    p.add_argument("--fail-on-timeout", action="store_true", help="Return a hard error if the solver reaches --timeout-min. Default keeps partial results and exits successfully.")
    p.add_argument(
        "--start-mode",
        default=None,
        choices=["FRESH_FROM_CHECKPOINT", "CONTINUE_STAGE", "RESUME_EXISTING"],
        help="Execution intent. FRESH rejects existing positive times; CONTINUE_STAGE uses the preceding stage checkpoint; RESUME_EXISTING is an external restart.",
    )
    p.add_argument(
        "--expected-start-time",
        type=float,
        default=None,
        help="Checkpoint time expected by CONTINUE_STAGE; mismatch blocks the solver before launch.",
    )
    p.add_argument(
        "--decompose-time",
        type=float,
        action="append",
        default=None,
        help=(
            "Explicit reconstructed times to decompose. The staged runner uses "
            "this before backward so current and two old states reach every processor."
        ),
    )
    p.add_argument(
        "--reconstruct-time",
        type=float,
        action="append",
        default=None,
        help="Explicit processor times to reconstruct after the solver exits.",
    )
    p.add_argument("--resume", action="store_true", help="Deprecated compatibility alias for --start-mode RESUME_EXISTING.")
    p.add_argument("--resume-additional-time-star", type=float, default=None, help="When resuming, extend endTime by this additional convective duration t*=tU/c.")
    p.add_argument("--stop-after-min", type=float, default=None, help="Request a clean OpenFOAM stop after this many minutes by changing controlDict stopAt. Keeps partial outputs for postprocessing.")
    p.add_argument("--stop-grace-min", type=float, default=2.0, help="Minutes to wait after requesting stopAt before terminating the solver process.")
    p.add_argument("--stop-mode", choices=["writeNow", "nextWrite", "noWriteNow"], default="writeNow", help="controlDict stopAt mode used by --stop-after-min.")
    p.add_argument(
        "--pyfoam-live-monitor",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Render a live PyFoam/OpenFOAM diagnostic snapshot inside the Streamlit application.",
    )
    p.add_argument(
        "--cleanup-processor-directories",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete redundant processorN folders only after their latest time has been reconstructed. Use --no-cleanup-processor-directories to keep them.",
    )
    p.add_argument(
        "--keep-processor-directories",
        action="store_false",
        dest="cleanup_processor_directories",
        help="Alias for --no-cleanup-processor-directories; useful for decomposition diagnostics.",
    )
    return p.parse_args()


def main() -> None:
    activate_openfoam_environment()
    args = parse_args()
    if args.max_cases_parallel != 1:
        raise RuntimeError("This runner intentionally allows only one case by default. Use separate scheduling for batches.")
    dry = not bool(args.run)
    if args.case is not None:
        cdir = args.case
    else:
        if args.variant is None or args.alpha is None:
            raise ValueError("--variant and --alpha are required unless --case is provided.")
        cdir = case_dir(args.case_root, args.variant, args.alpha)
    if not cdir.exists():
        raise FileNotFoundError(f"OpenFOAM case not found: {cdir}. Run case writer first.")
    run_case(
        cdir,
        args.solver,
        args.execution_backend,
        max(1, args.n_cores),
        args.timeout_min,
        args.nice,
        args.potentialFoam,
        dry,
        args.stop_if_checkMesh_fails,
        args.fail_on_timeout,
        args.stop_after_min,
        args.stop_grace_min,
        args.stop_mode,
        args.pyfoam_live_monitor,
        args.cleanup_processor_directories,
        args.stop_when_force_stable,
        args.convergence_minimum_time_star,
        args.convergence_window_time_star,
        args.convergence_mean_tolerance,
        args.convergence_oscillation_tolerance,
        args.convergence_poll_s,
        args.resume,
        args.resume_additional_time_star,
        args.start_mode,
        args.expected_start_time,
        args.decompose_time,
        args.reconstruct_time,
    )


if __name__ == "__main__":
    main()
