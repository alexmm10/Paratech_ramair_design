#!/usr/bin/env python3
"""Reliable live plots for a PyFoam-managed OpenFOAM run.

PyFoam remains the solver runner and produces the authoritative log files.  This
renderer replaces only ``pyFoamPlotWatcher``'s Gnuplot/FIFO display layer,
which is unreliable under WSLg and can leave blank copy-mode windows.  The
default headless mode writes a snapshot consumed by Streamlit; it never writes
or changes an OpenFOAM case.
"""
from __future__ import annotations

import argparse
from collections import deque
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from openfoam_history import read_recent_force_coefficient_history  # noqa: E402
from ramair_monitor_core import (  # noqa: E402
    SolverLogAccumulator as SharedSolverLogAccumulator,
    solver_plot_series,
)
from ramair_scientific_plot_style import apply_scientific_style  # noqa: E402

apply_scientific_style()


RESIDUAL_RE = re.compile(
    r"Solving for\s+([^,]+),\s+Initial residual\s*=\s*([0-9.eE+\-]+).*?No Iterations\s+([0-9]+)"
)
TIME_RE = re.compile(r"^\s*Time\s*=\s*([0-9.eE+\-]+)\s*$", re.MULTILINE)
CL_Y_LIMITS = (-0.8, 2.0)
CD_CM_Y_LIMITS = (-0.2, 0.2)
EFFICIENCY_Y_LIMITS = (0.0, 100.0)


def aerodynamic_efficiency(rows: list[dict[str, float]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        cl = float(row.get("Cl", math.nan))
        cd = float(row.get("Cd", math.nan))
        values.append(
            cl / cd
            if math.isfinite(cl) and math.isfinite(cd) and abs(cd) > 1.0e-12
            else math.nan
        )
    return values


def configure_lift_efficiency_axes(lift_axis: Any, efficiency_axis: Any) -> None:
    """Align both coefficient scales without drawing a second horizontal grid."""
    from matplotlib.ticker import MultipleLocator

    lift_axis.set_ylim(*CL_Y_LIMITS)
    lift_axis.yaxis.set_major_locator(MultipleLocator(0.4))
    efficiency_axis.set_ylim(*EFFICIENCY_Y_LIMITS)
    efficiency_axis.yaxis.set_major_locator(MultipleLocator(20.0))
    efficiency_axis.spines["right"].set_position(("outward", 36))
    efficiency_axis.yaxis.set_label_position("right")
    efficiency_axis.yaxis.tick_right()
    efficiency_axis.set_ylabel("Cl/Cd", color="#14866d", rotation=270, labelpad=20)
    efficiency_axis.yaxis.set_label_coords(1.16, 0.5)
    efficiency_axis.tick_params(axis="y", labelcolor="#14866d")
    efficiency_axis.grid(False)


def parent_is_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_log_tail(path: Path, max_bytes: int = 12_000_000) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        size = path.stat().st_size
        if size > max_bytes:
            handle.seek(-max_bytes, os.SEEK_END)
            handle.readline()
        return handle.read().decode("utf-8", errors="ignore")


def parse_solver_monitor_data(text: str, max_points: int = 1200) -> dict[str, Any]:
    """Parse residual and linear-iteration series without modifying the log."""
    return solver_plot_series(text, max_points=max_points)


class SolverLogAccumulator:
    """Incrementally parse a growing solver log with bounded in-memory series."""

    def __init__(self, max_points: int = 1200) -> None:
        self.max_points = max(50, int(max_points))
        self.offset = 0
        self.partial_line = ""
        self.current_abscissa = 0.0
        self.residuals: dict[str, deque[tuple[float, float]]] = {}
        self.linear_iterations: dict[str, deque[tuple[float, float]]] = {}

    def reset(self) -> None:
        self.offset = 0
        self.partial_line = ""
        self.current_abscissa = 0.0
        self.residuals.clear()
        self.linear_iterations.clear()

    def _append_line(self, line: str) -> None:
        time_match = re.match(r"^\s*Time\s*=\s*([0-9.eE+\-]+)\s*s?\s*$", line)
        if time_match:
            self.current_abscissa = float(time_match.group(1))
            return
        match = RESIDUAL_RE.search(line)
        if not match:
            return
        field = match.group(1).strip()
        try:
            residual = float(match.group(2))
            count = float(match.group(3))
        except ValueError:
            return
        if math.isfinite(residual) and residual > 0.0:
            self.residuals.setdefault(field, deque(maxlen=self.max_points)).append(
                (self.current_abscissa, residual)
            )
        if math.isfinite(count):
            self.linear_iterations.setdefault(field, deque(maxlen=self.max_points)).append(
                (self.current_abscissa, count)
            )

    def update(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return self.snapshot()
        size = path.stat().st_size
        if size < self.offset:
            self.reset()
        with path.open("rb") as handle:
            handle.seek(self.offset)
            chunk = handle.read()
            self.offset = handle.tell()
        text = self.partial_line + chunk.decode("utf-8", errors="ignore")
        lines = text.splitlines(keepends=True)
        self.partial_line = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self.partial_line = lines.pop()
        for line in lines:
            self._append_line(line.rstrip("\r\n"))
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "residuals": {name: list(values) for name, values in self.residuals.items()},
            "linear_iterations": {
                name: list(values) for name, values in self.linear_iterations.items()
            },
            "log_bytes_consumed": self.offset,
        }


# All current monitor entry points use the common incremental parser.  The
# local class remains above only as a source-compatible fallback for old
# pickled/imported application sessions during a hot reload.
SolverLogAccumulator = SharedSolverLogAccumulator


def draw_monitor_axes(
    axes: Any,
    efficiency_axis: Any,
    parsed: dict[str, Any],
    force_rows: list[dict[str, float]],
    *,
    x_label: str,
) -> None:
    """Draw the same three diagnostics for the live window and static proof."""
    for axis in axes:
        axis.clear()
        axis.grid(True, alpha=0.25)
    efficiency_axis.clear()
    efficiency_axis.grid(False)
    residual_axis, lift_axis, drag_moment_axis = axes
    for field, values in parsed["residuals"].items():
        if values:
            residual_axis.semilogy(
                [item[0] for item in values], [item[1] for item in values],
                label=field, linewidth=1.0, alpha=0.8,
            )
    residual_axis.set_ylabel("Initial residual")
    residual_axis.set_title("OpenFOAM linear-solver residuals")
    if parsed["residuals"]:
        residual_axis.legend(loc="best", fontsize=8, ncol=2)
    else:
        residual_axis.text(0.5, 0.5, "Waiting for solver residuals...", ha="center", va="center", transform=residual_axis.transAxes)

    if force_rows:
        abscissa = [row["Time"] for row in force_rows]
        lift_axis.plot(
            abscissa, [row.get("Cl", math.nan) for row in force_rows],
            label="Cl", linewidth=1.2, alpha=0.9,
        )
        efficiency_axis.plot(
            abscissa,
            aerodynamic_efficiency(force_rows),
            label="Cl/Cd",
            linewidth=1.0,
            linestyle="--",
            color="#14866d",
            alpha=0.85,
        )
        for label in ("Cd", "Cm"):
            drag_moment_axis.plot(
                abscissa, [row.get(label, math.nan) for row in force_rows],
                label=label, linewidth=1.2, alpha=0.9,
            )
    lift_axis.set_ylabel("Cl")
    configure_lift_efficiency_axes(lift_axis, efficiency_axis)
    lift_axis.set_title("Lift coefficient and aerodynamic efficiency")
    drag_moment_axis.set_ylim(*CD_CM_Y_LIMITS)
    drag_moment_axis.set_ylabel("Coefficient")
    drag_moment_axis.set_title("Drag and pitching-moment coefficients")
    if force_rows:
        lift_axis.legend(loc="upper left", fontsize=8)
        efficiency_axis.legend(loc="upper right", fontsize=8)
        drag_moment_axis.legend(loc="best", fontsize=8, ncol=2)
    else:
        lift_axis.text(0.5, 0.5, "Waiting for forceCoeffs...", ha="center", va="center", transform=lift_axis.transAxes)
        drag_moment_axis.text(0.5, 0.5, "Waiting for forceCoeffs...", ha="center", va="center", transform=drag_moment_axis.transAxes)
    for axis in axes:
        axis.set_xlabel(x_label)


def write_static_monitor_products(
    case_dir: Path,
    solver_log: Path,
    output_dir: Path,
    *,
    force_skip_initial_samples: int = 20,
    force_window_samples: int = 500,
    max_points: int = 2000,
) -> dict[str, Any]:
    """Generate one deterministic PNG per requested monitor without Gnuplot."""
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    for pattern in (
        "PyFoam_*.png",
        "*ramairForceCoefficients*.png",
        "live_monitor_*_latest.png",
        "linear_iterations.png",
        "force_coefficients.png",
    ):
        for stale in output_dir.glob(pattern):
            stale.unlink()
    stage = detect_stage(case_dir)
    x_label = "SIMPLE iteration" if stage == "steady" else "Physical time [s]"
    parsed = parse_solver_monitor_data(read_log_tail(solver_log), max_points=max_points)
    force_rows, force_sources = read_recent_force_coefficient_history(
        case_dir,
        max_rows=max(force_window_samples + force_skip_initial_samples, 100),
        include_processor0=True,
    )
    skip = max(0, int(force_skip_initial_samples))
    trimmed_force_rows = force_rows[skip:]
    # A software smoke test can end before the configured startup exclusion.
    # Show its real samples instead of an empty axes; normal runs still omit
    # the requested startup points once at least five later samples exist.
    force_rows = trimmed_force_rows if len(trimmed_force_rows) >= 5 else force_rows
    force_rows = force_rows[-max(20, int(force_window_samples)):]

    products: list[str] = []
    definitions = (("linear_residuals.png", "residuals", "Initial residual", True),)
    for filename, key, ylabel, logarithmic in definitions:
        fig, axis = plt.subplots(figsize=(8.0, 4.2))
        for field, values in parsed[key].items():
            if not values:
                continue
            plotter = axis.semilogy if logarithmic else axis.plot
            plotter([item[0] for item in values], [item[1] for item in values], label=field, alpha=0.8)
        axis.set_xlabel(x_label)
        axis.set_ylabel(ylabel)
        axis.grid(True, which="both", alpha=0.25)
        if parsed[key]:
            axis.legend(fontsize=8, ncol=2)
        fig.tight_layout()
        path = output_dir / filename
        fig.savefig(path, dpi=180)
        plt.close(fig)
        products.append(str(path))

    coefficient_plots = (
        ("lift_coefficient.png", ("Cl",), CL_Y_LIMITS, "Lift coefficient and aerodynamic efficiency"),
        ("drag_moment_coefficients.png", ("Cd", "Cm"), CD_CM_Y_LIMITS, "Drag and pitching-moment coefficients"),
    )
    for filename, labels, limits, title in coefficient_plots:
        fig, axis = plt.subplots(figsize=(8.0, 4.2))
        for label in labels:
            axis.plot(
                [row["Time"] for row in force_rows],
                [row.get(label, math.nan) for row in force_rows],
                label=label,
            )
        axis.set_ylim(*limits)
        axis.set_xlabel(x_label)
        axis.set_ylabel("Coefficient")
        axis.set_title(title)
        axis.grid(True, alpha=0.25)
        if force_rows:
            axis.legend(fontsize=8, ncol=len(labels))
        if filename == "lift_coefficient.png":
            efficiency_axis = axis.twinx()
            efficiency_axis.plot(
                [row["Time"] for row in force_rows],
                aerodynamic_efficiency(force_rows),
                label="Cl/Cd",
                color="#14866d",
                linestyle="--",
                linewidth=1.0,
            )
            configure_lift_efficiency_axes(axis, efficiency_axis)
            if force_rows:
                efficiency_axis.legend(fontsize=8, loc="upper right")
        fig.tight_layout()
        path = output_dir / filename
        fig.savefig(path, dpi=180)
        plt.close(fig)
        products.append(str(path))
    return {
        "status": "OK",
        "implementation": "PyFoam BasicRunner logs + deterministic Matplotlib replay",
        "stage": stage,
        "png_files": products,
        "residual_fields": sorted(parsed["residuals"]),
        "force_history_rows_plotted": len(force_rows),
        "force_sources": [str(path) for path in force_sources],
        "coefficient_plot_y_ranges": {
            "Cl": list(CL_Y_LIMITS),
            "Cl_over_Cd": list(EFFICIENCY_Y_LIMITS),
            "Cd_Cm": list(CD_CM_Y_LIMITS),
        },
    }


def detect_stage(case_dir: Path) -> str:
    schemes = case_dir / "system" / "fvSchemes"
    text = schemes.read_text(encoding="utf-8", errors="ignore") if schemes.is_file() else ""
    return "steady" if re.search(r"\bsteadyState\b", text) else "transient"


def write_status(path: Path, **values: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_monitor(args: argparse.Namespace) -> int:
    if not args.headless and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise RuntimeError("No WSLg/X11 display is available for the live monitor.")

    import matplotlib

    matplotlib.use("Agg" if args.headless else "TkAgg", force=True)
    import matplotlib.pyplot as plt

    case_dir = args.case.resolve()
    solver_log = args.solver_log.resolve()
    stage = args.stage if args.stage != "auto" else detect_stage(case_dir)
    status_path = case_dir / "ramair_live_monitor_status.json"
    stop_path = case_dir / "ramair_live_monitor.stop"
    x_label = "SIMPLE iteration" if stage == "steady" else "Physical time [s]"
    accumulator = SolverLogAccumulator(args.max_points)
    # Keep one audit snapshot outside PyFoamPlots. The application displays
    # only the three deterministic replay products, so this evidence cannot
    # appear as a duplicate monitor in the UI.
    snapshot_path = case_dir / f"ramair_live_monitor_{stage}_snapshot.png"
    last_snapshot = 0.0
    last_status: dict[str, Any] = {}

    if not args.headless:
        plt.ion()
    figure, axes = plt.subplots(3, 1, figsize=(11.0, 10.4))
    efficiency_axis = axes[1].twinx()
    figure.subplots_adjust(
        left=0.09,
        right=0.89,
        bottom=0.07,
        top=0.94,
        hspace=0.34,
    )
    try:
        figure.canvas.manager.set_window_title(f"RamAir PyFoam monitor - {stage}")
    except Exception:
        pass
    write_status(
        status_path,
        status="SNAPSHOT_READY" if args.headless else "WINDOW_READY",
        stage=stage,
        pid=os.getpid(),
        parent_pid=args.parent_pid,
        solver_log=str(solver_log),
        started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        implementation="Streamlit snapshot" if args.headless else "Matplotlib/Tk window",
    )

    while (
        parent_is_alive(args.parent_pid)
        and not stop_path.exists()
        and (args.headless or plt.fignum_exists(figure.number))
    ):
        now = time.monotonic()
        render_due = (
            not args.headless
            or now - last_snapshot >= max(2.0, float(args.snapshot_s))
        )
        if render_due:
            parsed = accumulator.update(solver_log)
            force_rows, force_sources = read_recent_force_coefficient_history(
                case_dir,
                max_rows=max(int(args.max_points), int(args.force_skip_initial_samples) + 100),
                include_processor0=True,
            )
            skip = max(0, int(args.force_skip_initial_samples))
            trimmed_force_rows = force_rows[skip:]
            force_rows = trimmed_force_rows if len(trimmed_force_rows) >= 5 else force_rows
            force_rows = force_rows[-max(50, int(args.max_points)):]

            draw_monitor_axes(axes, efficiency_axis, parsed, force_rows, x_label=x_label)
            if not args.headless:
                figure.canvas.draw_idle()
                figure.canvas.flush_events()
            figure.savefig(snapshot_path, dpi=140)
            last_snapshot = now
            last_status = dict(
                status="UPDATING",
                stage=stage,
                pid=os.getpid(),
                parent_pid=args.parent_pid,
                solver_log=str(solver_log),
                residual_fields=sorted(parsed["residuals"]),
                force_rows=len(force_rows),
                force_sources=[str(path) for path in force_sources],
                snapshot=str(snapshot_path),
                log_bytes_consumed=parsed.get("log_bytes_consumed", 0),
                updated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            write_status(status_path, **last_status)
        if args.headless:
            time.sleep(max(0.25, float(args.poll_s)))
        else:
            plt.pause(max(0.25, float(args.poll_s)))

    if plt.fignum_exists(figure.number):
        figure.savefig(snapshot_path, dpi=140)
    last_status.update(status="FINISHED", finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    write_status(status_path, **last_status)
    plt.close(figure)
    stop_path.unlink(missing_ok=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live Matplotlib monitor for a PyFoam-managed OpenFOAM run.")
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--solver-log", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--stage", choices=["auto", "steady", "transient"], default="auto")
    parser.add_argument("--poll-s", type=float, default=1.5)
    parser.add_argument("--max-points", type=int, default=1200)
    parser.add_argument("--force-skip-initial-samples", type=int, default=20)
    parser.add_argument(
        "--snapshot-s",
        type=float,
        default=30.0,
        help="Seconds between Streamlit snapshot rewrites; solver data parsing remains read-only.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Do not open WSLg/Tk; update a PNG snapshot for the Streamlit application.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run_monitor(parse_args()))
