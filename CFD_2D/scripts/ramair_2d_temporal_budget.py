#!/usr/bin/env python3
"""Physical and computational budgets for the validation convergence lab."""
from __future__ import annotations

import math
from typing import Any, Iterable


DEFAULT_OPERATING_CONDITION: dict[str, float] = {
    "mach": 0.15,
    "temperature_K": 288.15,
    "speed_of_sound_m_s": 340.292266,
    "velocity_m_s": 51.04384,
    "rho_kg_m3": 0.6660666,
    "mu_pa_s": 1.7894e-5,
    "pressure_ref_pa": 55093.0,
    "reynolds": 1.9e6,
    "chord_m": 1.0,
    "alpha_deg": 8.0,
}

REFERENCE_DT_VALUES_S = (
    2.5e-4,
    1.25e-4,
    6.25e-5,
    3.125e-5,
    1.5625e-5,
    7.8125e-6,
)

REFERENCE_STROUHAL_VALUES = (0.05, 0.2, 1.0, 2.0, 8.0, 10.0, 20.0)


def operating_condition(values: dict[str, Any] | None = None) -> dict[str, float]:
    """Return a thermodynamically audited operating condition."""
    result = dict(DEFAULT_OPERATING_CONDITION)
    result.update({key: float(value) for key, value in (values or {}).items()})
    for name in ("mach", "speed_of_sound_m_s", "rho_kg_m3", "mu_pa_s", "chord_m"):
        if result[name] <= 0.0:
            raise ValueError(f"{name} must be positive")

    result["velocity_m_s"] = result["mach"] * result["speed_of_sound_m_s"]
    result["nu_m2_s"] = result["mu_pa_s"] / result["rho_kg_m3"]
    result["reynolds_from_properties"] = (
        result["rho_kg_m3"]
        * result["velocity_m_s"]
        * result["chord_m"]
        / result["mu_pa_s"]
    )
    result["tc_s"] = result["chord_m"] / result["velocity_m_s"]
    result["dynamic_pressure_pa"] = (
        0.5 * result["rho_kg_m3"] * result["velocity_m_s"] ** 2
    )
    result["reynolds_relative_error"] = abs(
        result["reynolds_from_properties"] - result["reynolds"]
    ) / result["reynolds"]
    return result


def time_scales(dt_s: float, condition: dict[str, Any]) -> dict[str, float]:
    dt_s = float(dt_s)
    if dt_s <= 0.0:
        raise ValueError("dt_s must be positive")
    effective = operating_condition(condition)
    dt_star = dt_s / effective["tc_s"]
    return {
        "tc_s": effective["tc_s"],
        "dt_s": dt_s,
        "dt_star": dt_star,
        "nyquist_frequency_hz": 0.5 / dt_s,
        "nyquist_strouhal": 0.5 / dt_star,
    }


def samples_per_cycle(dt_star: float, strouhal: float) -> float:
    if dt_star <= 0.0 or strouhal <= 0.0:
        raise ValueError("dt_star and strouhal must be positive")
    return 1.0 / (dt_star * strouhal)


def build_reference_dt_table(
    condition: dict[str, Any],
    dt_values_s: Iterable[float] = REFERENCE_DT_VALUES_S,
    *,
    total_time_s: float = 6.25,
    sampling_time_s: float = 2.5,
) -> list[dict[str, float | int]]:
    effective = operating_condition(condition)
    rows: list[dict[str, float | int]] = []
    for dt_s in dt_values_s:
        scales = time_scales(float(dt_s), effective)
        rows.append(
            {
                "dt_s": float(dt_s),
                "dt_star": scales["dt_star"],
                "steps_total": math.ceil(total_time_s / float(dt_s)),
                "steps_sampling": math.ceil(sampling_time_s / float(dt_s)),
                "samples_per_cycle_St_1p67": samples_per_cycle(
                    scales["dt_star"], 1.67
                ),
                "samples_per_cycle_St_20": samples_per_cycle(
                    scales["dt_star"], 20.0
                ),
            }
        )
    return rows


def pilot_ladder(
    seed_dt_s: float,
    *,
    multipliers: Iterable[float] = (0.5, 1.0, 2.0, 4.0),
) -> list[float]:
    seed_dt_s = float(seed_dt_s)
    if seed_dt_s <= 0.0:
        raise ValueError("seed_dt_s must be positive")
    values = sorted({seed_dt_s * float(value) for value in multipliers})
    if not values or values[0] <= 0.0:
        raise ValueError("Pilot multipliers must be positive")
    return values


def temporal_computational_budget(
    *,
    dt_s: float,
    condition: dict[str, Any],
    settling_tc: float,
    sampling_tc: float,
    startup_tc: float = 4.0,
    field_write_interval_s: float,
    measured_seconds_per_step: float,
    measured_snapshot_size_bytes: float,
    mpi_ranks: int,
    free_space_bytes: float | None = None,
    wall_time_limit_s: float | None = None,
    strouhal_values: Iterable[float] = REFERENCE_STROUHAL_VALUES,
) -> dict[str, Any]:
    effective = operating_condition(condition)
    if settling_tc < 0.0 or sampling_tc <= 0.0 or startup_tc < 0.0:
        raise ValueError("Temporal durations are invalid")
    if field_write_interval_s <= 0.0:
        raise ValueError("field_write_interval_s must be positive")
    if measured_seconds_per_step < 0.0 or measured_snapshot_size_bytes < 0.0:
        raise ValueError("Measured costs cannot be negative")
    if not 1 <= int(mpi_ranks) <= 8:
        raise ValueError("The validation lab permits 1 to 8 MPI ranks")

    tc_s = effective["tc_s"]
    total_tc = startup_tc + settling_tc + sampling_tc
    total_time_s = total_tc * tc_s
    sampling_time_s = sampling_tc * tc_s
    steps_total = math.ceil(total_time_s / dt_s)
    steps_sampling = math.ceil(sampling_time_s / dt_s)
    snapshot_count = math.floor(total_time_s / field_write_interval_s) + 1
    estimated_wall_seconds = steps_total * measured_seconds_per_step
    estimated_storage_bytes = snapshot_count * measured_snapshot_size_bytes
    scales = time_scales(dt_s, effective)
    frequency_rows = [
        {
            "strouhal": float(st),
            "frequency_hz": float(st) / tc_s,
            "samples_per_cycle": samples_per_cycle(scales["dt_star"], float(st)),
        }
        for st in strouhal_values
    ]
    result: dict[str, Any] = {
        "condition": effective,
        "dt_s": float(dt_s),
        "dt_star": scales["dt_star"],
        "startup_tc": float(startup_tc),
        "settling_tc": float(settling_tc),
        "sampling_tc": float(sampling_tc),
        "total_tc": total_tc,
        "total_physical_time_s": total_time_s,
        "sampling_time_s": sampling_time_s,
        "steps_total": steps_total,
        "steps_sampling": steps_sampling,
        "field_write_interval_s": float(field_write_interval_s),
        "estimated_snapshot_count": snapshot_count,
        "estimated_storage_bytes": estimated_storage_bytes,
        "estimated_wall_seconds": estimated_wall_seconds,
        "mpi_ranks": int(mpi_ranks),
        "nyquist_frequency_hz": scales["nyquist_frequency_hz"],
        "nyquist_strouhal": scales["nyquist_strouhal"],
        "frequency_resolution_hz": 1.0 / sampling_time_s,
        "frequency_resolution_strouhal": 1.0 / sampling_tc,
        "frequency_sampling": frequency_rows,
        "host_specific_estimate": measured_seconds_per_step > 0.0,
    }
    warnings: list[str] = []
    if free_space_bytes is not None:
        result["free_space_bytes"] = float(free_space_bytes)
        if estimated_storage_bytes > 0.8 * float(free_space_bytes):
            warnings.append("ESTIMATED_STORAGE_EXCEEDS_80_PERCENT_OF_FREE_SPACE")
    if wall_time_limit_s is not None:
        result["wall_time_limit_s"] = float(wall_time_limit_s)
        if estimated_wall_seconds > float(wall_time_limit_s):
            warnings.append("ESTIMATED_WALL_TIME_EXCEEDS_LIMIT")
    if effective["reynolds_relative_error"] > 5.0e-3:
        warnings.append("MACH_REYNOLDS_PROPERTY_MISMATCH")
    result["warnings"] = warnings
    return result
