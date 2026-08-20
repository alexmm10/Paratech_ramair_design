#!/usr/bin/env python3
"""Build a physical and numerical time-step budget for RamAir CFD 2D.

The advisor does not claim that one time step is universally correct.  It
keeps four separate limits visible:

1. the highest physical frequency selected by the analyst;
2. the duration needed to observe the slowest selected frequency;
3. the configured OpenFOAM maxDeltaT/fixed deltaT;
4. the local mesh/Courant limit measured by a real solver run.

Dimensionless time follows t* = t U_inf / c and Strouhal number follows
St = f c / U_inf.  Therefore a period is 1/St in convective-time units and
the samples per period are 1 / (St deltaT*).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


ASSESSMENT_SCHEMA_VERSION = 1


def read_required_json(path: Path, label: str) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"{label} does not exist: {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label} is not valid JSON: {source} ({exc.msg} at line "
            f"{exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} must contain a JSON object: {source}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _positive_float(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be a finite value greater than zero")
    return result


def _fraction(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result < 1.0:
        raise ValueError(f"{name} must be in [0, 1)")
    return result


def topology_temporal_config(
    solver_config: dict[str, Any],
    topology: str,
) -> dict[str, Any]:
    """Return common temporal settings with topology-specific overrides."""
    merged = dict(solver_config.get("temporal_accuracy") or {})
    profile = (
        solver_config.get("topology_profiles", {})
        .get(topology, {})
        .get("temporal_accuracy", {})
    )
    if profile is not None and not isinstance(profile, dict):
        raise ValueError(
            f"topology_profiles.{topology}.temporal_accuracy must be a JSON object"
        )
    merged.update(profile or {})
    return merged


def temporal_frequency_budget(
    *,
    delta_t_star: float,
    max_delta_t_star: float,
    end_time_star: float,
    average_from_fraction: float,
    temporal_config: dict[str, Any],
) -> dict[str, Any]:
    """Compute sampling and duration metrics in convective-time units."""
    dt_star = _positive_float(delta_t_star, "delta_t_star")
    max_dt_star = _positive_float(max_delta_t_star, "max_delta_t_star")
    end_star = _positive_float(end_time_star, "end_time_star")
    average_fraction = _fraction(average_from_fraction, "average_from_fraction")
    st_min = _positive_float(
        temporal_config.get("target_min_strouhal", 0.05),
        "target_min_strouhal",
    )
    st_max = _positive_float(
        temporal_config.get("target_max_strouhal", 20.0),
        "target_max_strouhal",
    )
    if st_min > st_max:
        raise ValueError("target_min_strouhal cannot exceed target_max_strouhal")
    samples_target = _positive_float(
        temporal_config.get("target_samples_per_cycle", 20),
        "target_samples_per_cycle",
    )
    minimum_cycles = _positive_float(
        temporal_config.get("minimum_cycles_for_statistics", 10),
        "minimum_cycles_for_statistics",
    )

    nyquist_ceiling = 1.0 / (2.0 * st_max)
    engineering_ceiling = 1.0 / (samples_target * st_max)
    average_window_star = end_star * (1.0 - average_fraction)
    minimum_average_star = minimum_cycles / st_min
    reference_end_star = temporal_config.get("reference_end_time_star")
    reference_average_star = temporal_config.get("reference_average_time_star")
    smoke_end_star = temporal_config.get("smoke_end_time_star")
    study_values = [
        _positive_float(value, "time_step_study_values_star")
        for value in temporal_config.get("time_step_study_values_star", [])
    ]

    return {
        "target_strouhal_range": {
            "minimum": st_min,
            "maximum": st_max,
            "scope": temporal_config.get("strouhal_scope"),
        },
        "target_samples_per_cycle": samples_target,
        "minimum_cycles_for_statistics": minimum_cycles,
        "nyquist_deltaT_star_ceiling": nyquist_ceiling,
        "engineering_deltaT_star_ceiling": engineering_ceiling,
        "configured_initial_samples_per_fastest_cycle": 1.0 / (dt_star * st_max),
        "configured_maximum_samples_per_fastest_cycle": 1.0 / (max_dt_star * st_max),
        "configured_end_time_star": end_star,
        "configured_average_window_time_star": average_window_star,
        "configured_cycles_at_slowest_target": average_window_star * st_min,
        "minimum_average_window_time_star": minimum_average_star,
        "reference_end_time_star": (
            float(reference_end_star) if reference_end_star is not None else None
        ),
        "reference_average_time_star": (
            float(reference_average_star) if reference_average_star is not None else None
        ),
        "smoke_end_time_star": float(smoke_end_star) if smoke_end_star is not None else None,
        "time_step_study_values_star": study_values,
        "time_step_study_samples_per_fastest_cycle": [
            {
                "deltaT_star": value,
                "samples_per_cycle_at_target_St_max": 1.0 / (value * st_max),
            }
            for value in study_values
        ],
    }


def measured_mesh_budget(
    courant_diagnostics: dict[str, Any] | None,
    *,
    selected_max_co: float,
    engineering_delta_t_star: float,
) -> dict[str, Any]:
    """Project the local mesh time-step limit from a real Courant diagnostic."""
    if not courant_diagnostics:
        return {
            "status": "NOT_MEASURED",
            "note": (
                "Run a bounded real solver step and ramair_2d_courant_diagnostics.py "
                "before claiming that the mesh supports the selected time step."
            ),
        }
    measured = courant_diagnostics.get("measured_final") or {}
    measured_dt_star = measured.get("deltaT_star")
    measured_co = measured.get("courant_max")
    if measured_dt_star is None or measured_co is None:
        return {
            "status": "INCOMPLETE_DIAGNOSTIC",
            "source_status": courant_diagnostics.get("status"),
        }
    measured_dt_star = _positive_float(measured_dt_star, "measured deltaT_star")
    measured_co = _positive_float(measured_co, "measured courant_max")
    max_co = _positive_float(selected_max_co, "selected_max_co")
    projected = measured_dt_star * max_co / measured_co
    ratio = projected / _positive_float(
        engineering_delta_t_star,
        "engineering_delta_t_star",
    )
    maximum_cell = courant_diagnostics.get("maximum_courant_cell") or {}
    return {
        "status": "MEASURED",
        "source_status": courant_diagnostics.get("status"),
        "measured_deltaT_star": measured_dt_star,
        "measured_max_courant": measured_co,
        "selected_maxCo": max_co,
        "projected_deltaT_star_at_selected_maxCo": projected,
        "fraction_of_engineering_frequency_ceiling": ratio,
        "active_limiter": (
            "LOCAL_MESH_COURANT"
            if projected < engineering_delta_t_star
            else "PHYSICAL_FREQUENCY_OR_CONFIGURED_CEILING"
        ),
        "maximum_courant_cell": {
            "cell_id": maximum_cell.get("cell_id"),
            "location": maximum_cell.get("location"),
            "maximum_Co": maximum_cell.get("maximum_Co"),
        },
    }


def build_timestep_assessment(
    *,
    chord_m: float,
    velocity_m_s: float,
    topology: str,
    time_step_mode: str,
    delta_t_star: float,
    max_delta_t_star: float,
    max_co: float,
    end_time_star: float,
    average_from_fraction: float,
    temporal_config: dict[str, Any],
    courant_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the complete auditable time-step assessment."""
    chord = _positive_float(chord_m, "chord_m")
    velocity = _positive_float(velocity_m_s, "velocity_m_s")
    convective_time = chord / velocity
    frequency = temporal_frequency_budget(
        delta_t_star=delta_t_star,
        max_delta_t_star=max_delta_t_star,
        end_time_star=end_time_star,
        average_from_fraction=average_from_fraction,
        temporal_config=temporal_config,
    )
    mesh = measured_mesh_budget(
        courant_diagnostics,
        selected_max_co=max_co,
        engineering_delta_t_star=frequency["engineering_deltaT_star_ceiling"],
    )
    configured_ceiling_star = (
        _positive_float(delta_t_star, "delta_t_star")
        if time_step_mode == "fixed"
        else _positive_float(max_delta_t_star, "max_delta_t_star")
    )
    warnings: list[str] = []
    if configured_ceiling_star > frequency["nyquist_deltaT_star_ceiling"]:
        warnings.append(
            "The configured time-step ceiling violates even the Nyquist limit for "
            "the selected maximum Strouhal number."
        )
    elif configured_ceiling_star > frequency["engineering_deltaT_star_ceiling"]:
        warnings.append(
            "The configured ceiling passes Nyquist but does not meet the selected "
            "samples-per-cycle engineering target."
        )
    if (
        frequency["configured_average_window_time_star"]
        < frequency["minimum_average_window_time_star"]
    ):
        warnings.append(
            "The configured averaging window contains fewer than the requested "
            "cycles of the slowest selected frequency."
        )
    smoke_end = frequency.get("smoke_end_time_star")
    if smoke_end is not None and end_time_star <= smoke_end:
        warnings.append(
            "This duration is a software smoke test, not a statistically converged "
            "unsteady aerodynamic result."
        )
    if (
        mesh.get("status") == "MEASURED"
        and mesh["fraction_of_engineering_frequency_ceiling"] < 0.1
    ):
        warnings.append(
            "The measured local mesh/Courant limit is more than one order of "
            "magnitude below the physical frequency-resolution ceiling. Remesh the "
            "hotspot before increasing maxCo."
        )

    return {
        "assessment_schema_version": ASSESSMENT_SCHEMA_VERSION,
        "status": "ASSESSMENT_ONLY_NOT_VALIDATION",
        "topology": topology,
        "profile_id": temporal_config.get("profile_id", topology),
        "definitions": {
            "convective_time": "t_c = c / U_inf",
            "dimensionless_time": "t* = t U_inf / c",
            "strouhal": "St = f c / U_inf",
            "samples_per_cycle": "N = 1 / (St deltaT*)",
            "nyquist": "deltaT* <= 1 / (2 St_max)",
            "engineering_sampling": "deltaT* <= 1 / (N_target St_max)",
        },
        "physical_scales": {
            "chord_m": chord,
            "velocity_m_s": velocity,
            "convective_time_s": convective_time,
            "convective_frequency_hz": 1.0 / convective_time,
            "target_frequency_hz": {
                "minimum": (
                    frequency["target_strouhal_range"]["minimum"] / convective_time
                ),
                "maximum": (
                    frequency["target_strouhal_range"]["maximum"] / convective_time
                ),
            },
        },
        "configured": {
            "time_step_mode": time_step_mode,
            "deltaT_star": float(delta_t_star),
            "deltaT_s": float(delta_t_star) * convective_time,
            "maxDeltaT_star": float(max_delta_t_star),
            "maxDeltaT_s": float(max_delta_t_star) * convective_time,
            "maxCo": float(max_co),
            "endTime_star": float(end_time_star),
            "endTime_s": float(end_time_star) * convective_time,
            "average_from_fraction": float(average_from_fraction),
        },
        "frequency_resolution": frequency,
        "mesh_courant_limit": mesh,
        "recommended_workflow": [
            "Run the same physical duration for every time-step candidate.",
            "Halve deltaT* sequentially and compare mean loads, amplitudes, phase and PSD peaks.",
            "At one accepted deltaT*, compare PIMPLE outer-corrector counts; these are not identical to Cobalt Newton subiterations.",
            "Use the least dissipative spatial/time schemes that remain bounded and pass the same sensitivity comparison.",
            "Average at least the configured number of cycles of the slowest retained spectral peak.",
        ],
        "warnings": warnings,
        "scope_note": temporal_config.get("scope_note"),
    }


def assessment_markdown(report: dict[str, Any]) -> str:
    """Render a concise human-readable companion to the JSON assessment."""
    scales = report["physical_scales"]
    configured = report["configured"]
    frequency = report["frequency_resolution"]
    mesh = report["mesh_courant_limit"]
    lines = [
        "# Time-step and physical-frequency assessment",
        "",
        f"Status: **{report['status']}**",
        f"Topology/profile: `{report['topology']}` / `{report['profile_id']}`",
        "",
        "## Definitions",
        "",
        "- `t_c = c/U_inf`",
        "- `t* = t U_inf/c`",
        "- `St = f c/U_inf`",
        "- samples per period: `N = 1/(St deltaT*)`",
        "",
        "## Case scales",
        "",
        f"- Chord: {scales['chord_m']:.8g} m",
        f"- Velocity: {scales['velocity_m_s']:.8g} m/s",
        f"- Convective time: {scales['convective_time_s']:.8g} s",
        (
            "- Target frequency range: "
            f"{scales['target_frequency_hz']['minimum']:.8g} to "
            f"{scales['target_frequency_hz']['maximum']:.8g} Hz"
        ),
        "",
        "## Sampling budget",
        "",
        f"- Configured mode: `{configured['time_step_mode']}`",
        (
            f"- Initial deltaT*: {configured['deltaT_star']:.8g} "
            f"({configured['deltaT_s']:.8g} s)"
        ),
        (
            f"- maxDeltaT*: {configured['maxDeltaT_star']:.8g} "
            f"({configured['maxDeltaT_s']:.8g} s)"
        ),
        (
            "- Selected Strouhal range: "
            f"{frequency['target_strouhal_range']['minimum']:.8g} to "
            f"{frequency['target_strouhal_range']['maximum']:.8g}"
        ),
        f"- Nyquist ceiling deltaT*: {frequency['nyquist_deltaT_star_ceiling']:.8g}",
        (
            "- Engineering ceiling deltaT*: "
            f"{frequency['engineering_deltaT_star_ceiling']:.8g} "
            f"for {frequency['target_samples_per_cycle']:.8g} samples/cycle"
        ),
        (
            "- Samples/cycle at target St_max using initial deltaT*: "
            f"{frequency['configured_initial_samples_per_fastest_cycle']:.8g}"
        ),
        "",
        "## Duration budget",
        "",
        f"- Configured end time: t*={configured['endTime_star']:.8g}",
        (
            "- Configured averaging window: "
            f"t*={frequency['configured_average_window_time_star']:.8g}"
        ),
        (
            "- Minimum averaging window from selected St_min/cycles: "
            f"t*={frequency['minimum_average_window_time_star']:.8g}"
        ),
        "",
        "## Mesh/Courant evidence",
        "",
        f"- Status: `{mesh['status']}`",
    ]
    if mesh["status"] == "MEASURED":
        lines.extend(
            [
                f"- Measured deltaT*: {mesh['measured_deltaT_star']:.8g}",
                f"- Measured maximum Co: {mesh['measured_max_courant']:.8g}",
                (
                    "- Projected deltaT* at selected maxCo: "
                    f"{mesh['projected_deltaT_star_at_selected_maxCo']:.8g}"
                ),
                f"- Active limiter: `{mesh['active_limiter']}`",
                (
                    "- Limiting cell/location: "
                    f"{mesh['maximum_courant_cell'].get('cell_id')} / "
                    f"{mesh['maximum_courant_cell'].get('location')}"
                ),
            ]
        )
    else:
        lines.append(f"- Note: {mesh.get('note', 'No complete real-solver diagnostic supplied.')}")
    lines.extend(["", "## Warnings", ""])
    if report["warnings"]:
        lines.extend(f"- {warning}" for warning in report["warnings"])
    else:
        lines.append("- No automatic warning. A time-step independence study is still required.")
    lines.extend(["", "## Required study", ""])
    lines.extend(f"1. {item}" for item in report["recommended_workflow"])
    lines.extend(
        [
            "",
            "This file is an engineering assessment, not proof of temporal, spatial or model-form convergence.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a dimensionless time-step/frequency/mesh-Courant assessment."
    )
    parser.add_argument("--solver-config", type=Path, required=True)
    parser.add_argument(
        "--topology",
        choices=["closed_external_airfoil", "open_internal_cavity"],
        required=True,
    )
    parser.add_argument("--chord-m", type=float, required=True)
    parser.add_argument("--velocity-m-s", type=float, required=True)
    parser.add_argument("--courant-diagnostics", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    solver = read_required_json(args.solver_config, "Solver configuration")
    effective = dict(solver)
    topology_profile = (
        solver.get("topology_profiles", {}).get(args.topology, {}) or {}
    )
    for key, value in topology_profile.items():
        if key != "temporal_accuracy":
            effective[key] = value
    temporal = topology_temporal_config(solver, args.topology)
    diagnostics = None
    if args.courant_diagnostics is not None:
        diagnostics = read_required_json(
            args.courant_diagnostics,
            "Courant diagnostics",
        )
    report = build_timestep_assessment(
        chord_m=args.chord_m,
        velocity_m_s=args.velocity_m_s,
        topology=args.topology,
        time_step_mode=str(effective.get("time_step_mode", "adaptive_courant")),
        delta_t_star=float(effective.get("deltaT_star", 0.005)),
        max_delta_t_star=float(effective.get("maxDeltaT_star", 0.02)),
        max_co=float(effective.get("maxCo", 1.0)),
        end_time_star=float(effective.get("endTime_star", 20.0)),
        average_from_fraction=float(effective.get("average_from_fraction", 0.6)),
        temporal_config=temporal,
        courant_diagnostics=diagnostics,
    )
    write_json(args.output_json, report)
    if args.output_md is not None:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(assessment_markdown(report), encoding="utf-8")
    print(f"Time-step assessment written: {args.output_json.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
