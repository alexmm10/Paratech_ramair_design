#!/usr/bin/env python3
"""Run one OpenFOAM case through the PyFoam Python API.

This module is a worker for ``ramair_2d_openfoam_runner.py``.  Timeout and clean
stop policy remain owned by the parent runner, while PyFoam handles execution,
logging and fatal-error detection.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from openfoam_environment import activate_openfoam_environment  # noqa: E402
from openfoam_history import (  # noqa: E402
    read_force_coefficient_history,
    read_recent_force_coefficient_history,
)
from ramair_live_monitor import write_static_monitor_products  # noqa: E402


FORCE_PLOT_Y_MIN = -0.8
FORCE_PLOT_Y_MAX = 2.0
FORCE_DIVERGENCE_ABS_LIMIT = 20.0
CONTINUITY_RUNAWAY_LIMIT = 100.0
_NUTILDA_BOUND_RE = re.compile(
    r"bounding\s+nuTilda,\s*min:\s*([^,]+),\s*max:\s*([^,]+),\s*average:\s*([^\s,]+)",
    re.IGNORECASE,
)
_CONTINUITY_LOCAL_RE = re.compile(
    r"time step continuity errors\s*:\s*sum local\s*=\s*([^,]+)",
    re.IGNORECASE,
)


class SolverDivergenceError(RuntimeError):
    pass


def _finite_float(value: str) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def divergence_reason_from_line(line: str, nu_tilda_limit: float) -> dict[str, Any] | None:
    match = _NUTILDA_BOUND_RE.search(line)
    if match:
        values = [_finite_float(value) for value in match.groups()]
        if any(value is None for value in values):
            return {"reason": "nuTilda_non_finite", "line": line.strip()}
        minimum, maximum, average = (float(value) for value in values)
        if max(abs(minimum), abs(maximum), abs(average)) > nu_tilda_limit:
            return {
                "reason": "nuTilda_runaway",
                "minimum": minimum,
                "maximum": maximum,
                "average": average,
                "limit": nu_tilda_limit,
                "line": line.strip(),
            }
    continuity = _CONTINUITY_LOCAL_RE.search(line)
    if continuity:
        local = _finite_float(continuity.group(1))
        if local is None or abs(local) > CONTINUITY_RUNAWAY_LIMIT:
            return {
                "reason": "continuity_runaway" if local is not None else "continuity_non_finite",
                "sum_local": local,
                "limit": CONTINUITY_RUNAWAY_LIMIT,
                "line": line.strip(),
            }
    return None


def solver_divergence_diagnostics(log_text: str, molecular_nu: float) -> dict[str, Any]:
    limit = max(1.0, abs(float(molecular_nu)) * 1.0e6)
    triggers = [
        trigger for line in log_text.splitlines()
        if (trigger := divergence_reason_from_line(line, limit)) is not None
    ]
    # The normal OpenFOAM banner contains ``sigFpe : Enabling...``. Treating
    # that setup message as a fatal marker made every otherwise clean log look
    # divergent. Only actual fatal diagnostics belong here.
    fatal_markers = [
        marker for marker in ("Floating point exception", "FOAM FATAL ERROR", "FOAM FATAL IO ERROR")
        if marker in log_text
    ]
    return {
        "status": "DIVERGED" if triggers or fatal_markers else "NO_DIVERGENCE_DETECTED",
        "molecular_nu_m2_s": molecular_nu,
        "nuTilda_absolute_limit_m2_s": limit,
        "trigger_count": len(triggers),
        "first_trigger": triggers[0] if triggers else None,
        "last_trigger": triggers[-1] if triggers else None,
        "fatal_markers": fatal_markers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OpenFOAM through PyFoam.BasicRunner.")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--solver", required=True)
    parser.add_argument("--solver-module", default="incompressibleFluid")
    parser.add_argument("--n-cores", type=int, default=1)
    parser.add_argument("--nice", type=int, default=10)
    parser.add_argument("--potential-foam", action="store_true")
    parser.add_argument(
        "--live-plot-watcher",
        action="store_true",
        help="Render residual/iteration and Cl/Cd/Cm snapshots from PyFoam logs inside the Streamlit application.",
    )
    parser.add_argument(
        "--force-plot-skip-initial-samples",
        type=int,
        default=20,
        help="Omit this many startup force samples from PyFoam coefficient plots only; raw forceCoeffs data is untouched.",
    )
    parser.add_argument(
        "--force-plot-window-samples",
        type=int,
        default=500,
        help="Use at most this many recent force samples in replay plots so late behavior remains readable.",
    )
    parser.add_argument("--report", type=Path, default=None)
    return parser.parse_args()


def write_report(path: Path | None, data: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def command_for_solver(solver: str, module: str, parallel: bool) -> list[str]:
    command = [solver]
    if solver == "foamRun":
        command += ["-solver", module]
    if parallel:
        command.append("-parallel")
    return command


def configure_parallel_decomposition(case_dir: Path, n_cores: int) -> list[str]:
    """Set the requested partition count and report stale processor dirs.

    ``decomposePar -force`` owns replacement of a previous decomposition. It
    starts from reconstructed root fields and avoids deleting processor data in
    Python before OpenFOAM has had a chance to validate the case.
    """
    from PyFoam.RunDictionary.ParsedParameterFile import ParsedParameterFile  # type: ignore

    decompose_path = case_dir / "system" / "decomposeParDict"
    if not decompose_path.is_file():
        raise FileNotFoundError(f"Missing decomposition dictionary: {decompose_path}")
    decompose = ParsedParameterFile(str(decompose_path))
    decompose["numberOfSubdomains"] = int(n_cores)
    decompose.writeFile()
    return sorted(
        path.name for path in case_dir.iterdir()
        if path.is_dir() and re.fullmatch(r"processor\d+", path.name)
    )


def force_regexp_file(
    case_dir: Path,
    y_min: float = FORCE_PLOT_Y_MIN,
    y_max: float = FORCE_PLOT_Y_MAX,
) -> Path:
    if not y_min < y_max:
        raise ValueError(f"Invalid force plot y-range: {y_min} >= {y_max}")
    path = case_dir / "ramairForceCoeffsRegexp"
    path.write_text(f"""ramairForceCoefficients
{{
    theTitle "Aerodynamic coefficients";
    ylabel "coefficient";
    expr "RamAir force coefficients: Cl=(.+) Cd=(.+) Cm=(.+)";
    titles (Cl Cd Cm);
    gnuplotCommands
    (
        "set yrange [{y_min:g}:{y_max:g}]"
        "set ytics 0.25"
        "set grid ytics"
    );
}}
""", encoding="utf-8")
    return path


def live_watcher_plot_options() -> list[str]:
    """Legacy PyFoam/Gnuplot options retained for reproducible hardcopy tests.

    Live WSLg windows now use :mod:`ramair_live_monitor` because the Gnuplot
    FIFO transport can open a visible but empty copy-mode window.  PyFoam's
    post-run hardcopy replay remains available and authoritative.
    """
    return [
        "--gnuplot-terminal=x11",
        "--gnuplot-use-fifo",
        "--non-persist",
    ]


def force_coefficient_divergence_reason(
    row: dict[str, float],
    absolute_limit: float = FORCE_DIVERGENCE_ABS_LIMIT,
) -> dict[str, Any] | None:
    """Identify a clearly nonphysical force row before fields become unusable."""
    values = {label: _finite_float(str(row.get(label))) for label in ("Cl", "Cd", "Cm")}
    if any(value is None for value in values.values()):
        return {"reason": "force_coefficient_non_finite", "values": values}
    worst_label = max(values, key=lambda label: abs(float(values[label])))
    worst_value = float(values[worst_label])
    if abs(worst_value) > float(absolute_limit):
        return {
            "reason": "force_coefficient_runaway",
            "coefficient": worst_label,
            "value": worst_value,
            "limit": float(absolute_limit),
            "time_or_iteration": row.get("Time"),
            "values": values,
        }
    return None


def live_monitor_preflight(requested: bool) -> dict[str, Any]:
    """Report whether the steady/transient live monitor can actually start."""
    monitor = Path(__file__).with_name("ramair_live_monitor.py")
    matplotlib_available = importlib.util.find_spec("matplotlib") is not None
    missing = [
        name for name, value in (
            ("ramair_live_monitor.py", monitor.is_file()),
            ("matplotlib", matplotlib_available),
        )
        if not value
    ]
    return {
        "requested": bool(requested),
        "status": "DISABLED" if not requested else "READY" if not missing else "UNAVAILABLE",
        "implementation": "PyFoam logs + headless Matplotlib snapshot embedded in Streamlit",
        "monitor_script": str(monitor),
        "matplotlib_available": matplotlib_available,
        "missing": missing,
        "coefficient_display_range": [FORCE_PLOT_Y_MIN, FORCE_PLOT_Y_MAX],
    }


def _force_plot_rows(
    rows: list[dict[str, float]],
    *,
    skip_initial_samples: int = 0,
    max_samples: int | None = None,
) -> list[dict[str, float]]:
    usable = [row for row in rows if all(label in row for label in ("Time", "Cl", "Cd", "Cm"))]
    selected = usable[max(0, int(skip_initial_samples)):]
    if max_samples is not None and max_samples > 0:
        selected = selected[-int(max_samples):]
    return selected


def write_force_monitor_log(
    case_dir: Path,
    output: Path,
    stop_event: threading.Event,
    skip_initial_samples: int = 20,
    divergence_callback: Any | None = None,
    preexisting_keys: set[tuple[float, float, float, float]] | None = None,
) -> None:
    """Mirror new forceCoeffs rows into a PyFoam-readable time log."""
    displayed: set[tuple[float, float, float, float]] = set()
    inspected: set[tuple[float, float, float, float]] = set()
    historical = set(preexisting_keys or set())
    output.write_text("", encoding="utf-8")

    def process(rows: list[dict[str, float]]) -> list[dict[str, float]]:
        # Safety checks must inspect the complete history.  The startup skip is
        # a display-only choice and must never hide an early runaway.
        usable = [row for row in rows if all(label in row for label in ("Time", "Cl", "Cd", "Cm"))]
        for row in usable:
            key = (row["Time"], row["Cl"], row["Cd"], row["Cm"])
            if key in inspected:
                continue
            inspected.add(key)
            trigger = force_coefficient_divergence_reason(row)
            if key not in historical and trigger is not None and divergence_callback is not None:
                divergence_callback(trigger)

        visible = _force_plot_rows(usable, skip_initial_samples=skip_initial_samples)
        new_rows: list[dict[str, float]] = []
        for row in visible:
            key = (row["Time"], row["Cl"], row["Cd"], row["Cm"])
            if key not in displayed:
                displayed.add(key)
                new_rows.append(row)
        return new_rows

    def append_rows(rows: list[dict[str, float]]) -> None:
        if not rows:
            return
        with output.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(f"Time = {row['Time']:.12g}\n")
                handle.write(
                    "RamAir force coefficients: "
                    f"Cl={row['Cl']:.12g} Cd={row['Cd']:.12g} Cm={row['Cm']:.12g}\n"
                )
            handle.flush()

    while True:
        rows, _ = read_recent_force_coefficient_history(
            case_dir,
            max_rows=max(2000, skip_initial_samples + 1000),
            include_processor0=True,
        )
        append_rows(process(rows))
        if stop_event.wait(1.0):
            # One final pass catches a force row written immediately before the
            # solver process exits.
            final_rows, _ = read_recent_force_coefficient_history(
                case_dir,
                max_rows=max(2000, skip_initial_samples + 1000),
                include_processor0=True,
            )
            append_rows(process(final_rows))
            return


def write_force_monitor_snapshot(
    case_dir: Path,
    output: Path,
    skip_initial_samples: int = 0,
    max_samples: int | None = None,
) -> int:
    """Write a restart-aware, display-only force window for post-run replay."""
    rows, _ = read_force_coefficient_history(case_dir, include_processor0=True)
    usable = _force_plot_rows(
        rows,
        skip_initial_samples=skip_initial_samples,
        max_samples=max_samples,
    )
    with output.open("w", encoding="utf-8") as handle:
        for row in usable:
            handle.write(f"Time = {row['Time']:.12g}\n")
            handle.write(
                "RamAir force coefficients: "
                f"Cl={row['Cl']:.12g} Cd={row['Cd']:.12g} Cm={row['Cm']:.12g}\n"
            )
    return len(usable)


def selected_pyfoam_plot_files(output_dir: Path) -> list[Path]:
    """Return only the monitors requested by the current workflow."""
    selected: set[Path] = set()
    for pattern in ("linear_residuals.png", "lift_coefficient.png", "drag_moment_coefficients.png"):
        selected.update(path for path in output_dir.glob(pattern) if path.is_file())
    return sorted(selected)


def run_pyfoam_plot_replay(
    case_dir: Path,
    solver_log: Path,
    *,
    force_skip_initial_samples: int = 20,
    force_window_samples: int = 500,
) -> dict[str, Any]:
    """Create one stable plot per diagnostic from PyFoam's authoritative log."""
    if not solver_log.is_file():
        return {
            "status": "SKIPPED",
            "reason": "solver log missing",
        }
    output_dir = case_dir / "postProcessing" / "PyFoamPlots"
    try:
        result = write_static_monitor_products(
            case_dir,
            solver_log,
            output_dir,
            force_skip_initial_samples=force_skip_initial_samples,
            force_window_samples=force_window_samples,
        )
        result["output_dir"] = str(output_dir)
        result["force_plot_skip_initial_samples"] = int(force_skip_initial_samples)
        result["force_plot_window_samples"] = int(force_window_samples)
        return result
    except Exception as exc:
        return {
            "status": "WARNING",
            "error": f"{type(exc).__name__}: {exc}",
            "output_dir": str(output_dir),
        }


def run_basic(
    argv: list[str],
    logname: str,
    parameters: dict[str, Any],
    *,
    live_plot_watcher: bool = False,
    monitor_force_coefficients: bool = False,
    force_plot_skip_initial_samples: int = 20,
    molecular_nu: float | None = None,
) -> dict[str, Any]:
    from PyFoam.Execution.BasicRunner import BasicRunner  # type: ignore

    nu_tilda_limit = max(1.0, abs(float(molecular_nu or 0.0)) * 1.0e6)

    class RamAirMonitoredRunner(BasicRunner):
        def lineHandle(self, line: str) -> None:  # noqa: N802 - PyFoam API name
            if molecular_nu is None or self.data.get("ramairDivergenceDetected"):
                return
            trigger = divergence_reason_from_line(line, nu_tilda_limit)
            if trigger is not None:
                self.data["ramairDivergenceDetected"] = True
                self.data["ramairDivergenceTrigger"] = trigger
                self.stopGracefully()

    runner_class = RamAirMonitoredRunner if molecular_nu is not None else BasicRunner
    runner = runner_class(
        argv=argv,
        silent=False,
        logname=logname,
        parameters=parameters,
        writeState=True,
        echoCommandLine="PyFoam:",
    )
    watcher_state: dict[str, Any] = {"status": "DISABLED"}
    watcher_process: list[subprocess.Popen[str]] = []
    watcher_thread: threading.Thread | None = None
    watcher_log_handle: list[Any] = []
    watcher_stop_path = Path.cwd() / "ramair_live_monitor.stop"
    watcher_stop_path.unlink(missing_ok=True)
    force_stop = threading.Event()
    force_log = Path.cwd() / "PyFoamForceCoeffs.logfile"
    force_thread: threading.Thread | None = None
    divergence_lock = threading.Lock()
    existing_force_rows, _ = read_force_coefficient_history(Path.cwd(), include_processor0=True)
    preexisting_force_keys = {
        (row["Time"], row["Cl"], row["Cd"], row["Cm"])
        for row in existing_force_rows
        if all(label in row for label in ("Time", "Cl", "Cd", "Cm"))
    }

    def request_divergence_stop(trigger: dict[str, Any]) -> None:
        with divergence_lock:
            if runner.data.get("ramairDivergenceDetected"):
                return
            runner.data["ramairDivergenceDetected"] = True
            runner.data["ramairDivergenceTrigger"] = trigger
            runner.stopGracefully()

    if monitor_force_coefficients:
        force_thread = threading.Thread(
            target=write_force_monitor_log,
            args=(
                Path.cwd(),
                force_log,
                force_stop,
                force_plot_skip_initial_samples,
                request_divergence_stop,
                preexisting_force_keys,
            ),
            name="pyfoam-force-coefficients-log",
            daemon=True,
        )
        force_thread.start()
    if live_plot_watcher:
        monitor_script = Path(__file__).with_name("ramair_live_monitor.py")
        if monitor_script.is_file() and importlib.util.find_spec("matplotlib") is not None:
            solver_log = Path(runner.logName()).resolve()

            def launch_watcher() -> None:
                for _ in range(120):
                    if solver_log.is_file() and solver_log.stat().st_size > 0:
                        break
                    time.sleep(0.25)
                if not solver_log.is_file():
                    watcher_state.update(status="WARNING", reason="solver_log_not_created")
                    return
                command = [
                    sys.executable,
                    str(monitor_script),
                    "--case", str(Path.cwd().resolve()),
                    "--solver-log", str(solver_log),
                    "--parent-pid", str(os.getpid()),
                    "--force-skip-initial-samples", str(max(0, int(force_plot_skip_initial_samples))),
                    "--snapshot-s", "30",
                    "--headless",
                ]
                watcher_log = solver_log.parent / "log.ramair_live_monitor"
                handle = watcher_log.open("w", encoding="utf-8")
                watcher_log_handle.append(handle)
                process = subprocess.Popen(
                    command,
                    cwd=str(solver_log.parent),
                    text=True,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    env=os.environ.copy(),
                    start_new_session=True,
                )
                watcher_process.append(process)
                watcher_state.update(
                    status="RUNNING",
                    implementation="PyFoam logs + headless Matplotlib snapshot embedded in Streamlit",
                    command=command,
                    pid=process.pid,
                    log=str(watcher_log),
                    status_file=str(solver_log.parent / "ramair_live_monitor_status.json"),
                )

            watcher_thread = threading.Thread(target=launch_watcher, name="pyfoam-live-plot-watcher", daemon=True)
            watcher_thread.start()
        else:
            watcher_state.update(
                status="SKIPPED",
                reason="Matplotlib live monitor is unavailable; PyFoam post-run PNG replay remains enabled",
            )
    try:
        data = dict(runner.start() or {})
    finally:
        if force_thread is not None:
            force_stop.set()
            force_thread.join(timeout=3.0)
        if watcher_thread is not None:
            watcher_thread.join(timeout=2)
        if watcher_process:
            watcher_stop_path.write_text("solver_finished\n", encoding="utf-8")
        for process in watcher_process:
            if process.poll() is None:
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
        for handle in watcher_log_handle:
            handle.close()
        if watcher_state.get("status") == "RUNNING":
            try:
                monitor_status = json.loads(
                    Path(str(watcher_state.get("status_file", ""))).read_text(encoding="utf-8")
                )
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                monitor_status = {}
            watcher_state["status"] = str(monitor_status.get("status", "FINISHED"))
            watcher_state["final_status"] = monitor_status
    data["live_plot_watcher"] = watcher_state
    data["logfile"] = str(Path(runner.logName()).resolve())
    get_return_code = getattr(runner.run, "getReturnCode", None)
    return_code = get_return_code() if callable(get_return_code) else None
    data["returncode"] = return_code
    if data.get("ramairDivergenceDetected"):
        raise SolverDivergenceError(
            "OpenFOAM divergence monitor requested a clean writeNow stop: "
            f"{data.get('ramairDivergenceTrigger')}"
        )
    if not runner.runOK() or return_code not in {None, 0}:
        raise RuntimeError(
            f"PyFoam/OpenFOAM command failed with return code {return_code}: {' '.join(argv)}; "
            f"log={data['logfile']}"
        )
    return data


def main() -> int:
    activate_openfoam_environment()
    args = parse_args()
    case_dir = args.case.resolve()
    if not (case_dir / "system" / "controlDict").is_file():
        print(f"Invalid OpenFOAM case: {case_dir}", file=sys.stderr)
        return 2
    try:
        case_config = json.loads((case_dir / "case_config.json").read_text(encoding="utf-8"))
    except Exception:
        case_config = {}
    molecular_nu = float(case_config.get("nu", 0.0) or 0.0)
    if molecular_nu <= 0.0:
        rho = float(case_config.get("rho", 0.0) or 0.0)
        mu = float(case_config.get("mu", 0.0) or 0.0)
        molecular_nu = mu / rho if rho > 0.0 and mu > 0.0 else 1.0e-5
    try:
        pyfoam_version = version("PyFoam")
    except PackageNotFoundError:
        print("PyFoam is not installed in this Python environment.", file=sys.stderr)
        return 2

    report: dict[str, Any] = {
        "backend": "pyfoam",
        "pyfoam_version": pyfoam_version,
        "case": str(case_dir),
        "solver": args.solver,
        "solver_module": args.solver_module,
        "n_cores": max(1, int(args.n_cores)),
        "molecular_nu_m2_s": molecular_nu,
        "live_monitor_preflight": live_monitor_preflight(bool(args.live_plot_watcher)),
        "commands": [],
        "stages": [],
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    old_cwd = Path.cwd()
    os.chdir(case_dir)
    started = time.perf_counter()
    reconstructed = False
    solver_log_path: Path | None = None
    active_stage: str | None = None
    active_log: Path | None = None

    def interrupt_as_keyboard(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    previous_sigterm = signal.signal(signal.SIGTERM, interrupt_as_keyboard)
    try:
        parameters = {
            "ramair_backend": "pyfoam",
            "ramair_solver_module": args.solver_module,
            "ramair_n_cores": report["n_cores"],
        }

        def run_stage(
            stage: str,
            argv: list[str],
            logname: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            nonlocal active_stage, active_log
            active_stage = stage
            active_log = case_dir / f"{logname}.logfile"
            report["active_stage"] = active_stage
            report["active_log"] = str(active_log)
            try:
                data = run_basic(argv, logname, parameters, **kwargs)
            except Exception:
                report["failed_stage"] = active_stage
                report["failed_log"] = str(active_log)
                raise
            report["stages"].append({
                "stage": stage,
                "status": "OK",
                "log": str(data.get("logfile", active_log)),
                "returncode": data.get("returncode"),
                "live_plot_watcher": data.get("live_plot_watcher"),
            })
            return data

        if args.potential_foam:
            cmd = ["nice", "-n", str(args.nice), "potentialFoam"]
            report["commands"].append(cmd)
            run_stage("potentialFoam", cmd, "PyFoamPotential")
        if report["n_cores"] > 1:
            report["stale_processor_directories_detected"] = configure_parallel_decomposition(
                case_dir, int(report["n_cores"])
            )
            decompose = ["decomposePar", "-force"]
            report["commands"].append(decompose)
            run_stage("decomposePar", decompose, "PyFoamDecompose")
            solver_cmd = [
                "nice", "-n", str(args.nice), "mpirun", "-np", str(report["n_cores"]),
                *command_for_solver(args.solver, args.solver_module, parallel=True),
            ]
        else:
            solver_cmd = ["nice", "-n", str(args.nice), *command_for_solver(args.solver, args.solver_module, parallel=False)]
        report["commands"].append(solver_cmd)
        solver_data = run_stage(
            "steady_or_transient_solver",
            solver_cmd,
            f"PyFoamRunner.{args.solver}",
            live_plot_watcher=bool(args.live_plot_watcher),
            monitor_force_coefficients=True,
            force_plot_skip_initial_samples=max(0, int(args.force_plot_skip_initial_samples)),
            molecular_nu=molecular_nu,
        )
        pyfoam_log = Path(str(solver_data.get("logfile", "")))
        solver_log_path = pyfoam_log
        canonical_log = case_dir / f"log.{args.solver}"
        if pyfoam_log.is_file():
            shutil.copy2(pyfoam_log, canonical_log)
        if report["n_cores"] > 1:
            reconstruct = ["reconstructPar"]
            report["commands"].append(reconstruct)
            run_stage("reconstructPar", reconstruct, "PyFoamReconstruct")
            reconstructed = True
        report["pyfoam_plot_replay"] = run_pyfoam_plot_replay(
            case_dir,
            pyfoam_log,
            force_skip_initial_samples=max(0, int(args.force_plot_skip_initial_samples)),
            force_window_samples=max(20, int(args.force_plot_window_samples)),
        )
        report.update(status="RUN_COMPLETED", solver_data=solver_data)
        return_code = 0
    except KeyboardInterrupt:
        report.update(status="INTERRUPTED")
        return_code = 130
    except SolverDivergenceError as exc:
        report.update(status="RUN_DIVERGED", error=f"{type(exc).__name__}: {exc}")
        print(report["error"], file=sys.stderr)
        return_code = 3
    except Exception as exc:
        report.update(status="RUN_FAILED", error=f"{type(exc).__name__}: {exc}")
        print(report["error"], file=sys.stderr)
        return_code = 1
    finally:
        if report["n_cores"] > 1 and not reconstructed and any(case_dir.glob("processor[0-9]*")):
            try:
                # A timeout or clean stop can leave several retained write
                # intervals in processorN. Reconstruct all of them so the
                # ParaView time series remains usable; purgeWrite bounds this
                # operation and the subsequent processor cleanup controls disk.
                reconstruct = ["reconstructPar"]
                report["commands"].append(reconstruct)
                run_basic(reconstruct, "PyFoamReconstructPartial", parameters)
                reconstructed = True
                report["partial_reconstruction"] = {
                    "status": "OK",
                    "scope": "all_retained_times",
                }
            except Exception as exc:
                report["partial_reconstruction"] = {
                    "status": "WARNING",
                    "scope": "all_retained_times",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        if solver_log_path is None:
            candidate = case_dir / f"PyFoamRunner.{args.solver}.logfile"
            solver_log_path = candidate if candidate.is_file() else None
        if solver_log_path is not None and solver_log_path.is_file():
            canonical_log = case_dir / f"log.{args.solver}"
            if solver_log_path.resolve() != canonical_log.resolve():
                shutil.copy2(solver_log_path, canonical_log)
            diagnostics = solver_divergence_diagnostics(
                solver_log_path.read_text(encoding="utf-8", errors="ignore"),
                molecular_nu,
            )
            report["divergence_diagnostics"] = diagnostics
            if diagnostics["status"] == "DIVERGED" and report.get("status") == "RUN_FAILED":
                report["status"] = "RUN_DIVERGED"
        if solver_log_path is not None and "pyfoam_plot_replay" not in report:
            report["pyfoam_plot_replay"] = run_pyfoam_plot_replay(
                case_dir,
                solver_log_path,
                force_skip_initial_samples=max(0, int(args.force_plot_skip_initial_samples)),
                force_window_samples=max(20, int(args.force_plot_window_samples)),
            )
        report["active_stage"] = active_stage
        report["active_log"] = str(active_log) if active_log is not None else None
        signal.signal(signal.SIGTERM, previous_sigterm)
        os.chdir(old_cwd)
        report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        report["wall_time_s"] = float(time.perf_counter() - started)
        write_report(args.report, report)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
