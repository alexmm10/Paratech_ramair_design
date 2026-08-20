#!/usr/bin/env python3
"""Lightweight boundary-layer estimates shared by the mesh UI and reports.

These relations are engineering estimates for a zero-pressure-gradient flat
plate.  They are useful for sizing and comparison, but they do not replace the
wall-resolved OpenFOAM result around a curved or separated ram-air profile.
"""
from __future__ import annotations

import math
from typing import Any


def first_cell_height_from_yplus(
    *,
    target_y_plus: float,
    reynolds: float,
    rho_kg_m3: float,
    mu_pa_s: float,
    chord_m: float,
) -> dict[str, float]:
    """Estimate wall-normal first-cell height using a turbulent 1/7-law Cf."""
    reynolds = max(float(reynolds), 1.0)
    rho = max(float(rho_kg_m3), 1.0e-30)
    mu = max(float(mu_pa_s), 1.0e-30)
    chord = max(float(chord_m), 1.0e-30)
    velocity = reynolds * mu / (rho * chord)
    skin_friction = 0.026 / reynolds ** (1.0 / 7.0)
    friction_velocity = velocity * math.sqrt(0.5 * skin_friction)
    y1 = max(float(target_y_plus), 0.0) * mu / max(rho * friction_velocity, 1.0e-30)
    return {
        "y1_m": y1,
        "y1_over_chord": y1 / chord,
        "velocity_m_s": velocity,
        "skin_friction_coefficient": skin_friction,
        "friction_velocity_m_s": friction_velocity,
        "reynolds": reynolds,
    }


def geometric_prism_stack_thickness(first_cell_height_m: float, layers: int, growth_rate: float) -> float:
    """Return the total height of ``layers`` geometrically growing cells."""
    y1 = max(float(first_cell_height_m), 0.0)
    count = max(int(layers), 0)
    growth = float(growth_rate)
    if count == 0 or y1 == 0.0:
        return 0.0
    if growth <= 0.0:
        raise ValueError("Boundary-layer growth rate must be positive.")
    if abs(growth - 1.0) < 1.0e-12:
        return y1 * count
    return y1 * (growth**count - 1.0) / (growth - 1.0)


def turbulent_flat_plate_delta99(*, chord_m: float, reynolds_chord: float, x_over_chord: float) -> float:
    """Estimate turbulent delta_99 with ``delta/x = 0.37 Re_x^-1/5``."""
    chord = max(float(chord_m), 1.0e-30)
    xc = min(max(float(x_over_chord), 1.0e-6), 1.0)
    x = xc * chord
    reynolds_x = max(float(reynolds_chord) * xc, 1.0)
    return 0.37 * x / reynolds_x**0.2


def boundary_layer_comparison(
    *,
    chord_m: float,
    reynolds: float,
    target_y_plus: float,
    rho_kg_m3: float,
    mu_pa_s: float,
    layers: int,
    growth_rate: float,
    manual_y1_m: float | None = None,
    use_yplus_y1: bool = True,
    x_over_chord: float = 1.0,
) -> dict[str, Any]:
    """Collect the y1, prism-stack and flat-plate estimates used by the UI."""
    yplus_result = first_cell_height_from_yplus(
        target_y_plus=target_y_plus,
        reynolds=reynolds,
        rho_kg_m3=rho_kg_m3,
        mu_pa_s=mu_pa_s,
        chord_m=chord_m,
    )
    if use_yplus_y1:
        y1 = yplus_result["y1_m"]
        source = "target_y_plus_flat_plate_skin_friction"
    else:
        if manual_y1_m is None or float(manual_y1_m) <= 0.0:
            raise ValueError("A positive manual y1 in metres is required when the y+ estimate is disabled.")
        y1 = float(manual_y1_m)
        source = "manual_metres"
    prism = geometric_prism_stack_thickness(y1, layers, growth_rate)
    theoretical = turbulent_flat_plate_delta99(
        chord_m=chord_m,
        reynolds_chord=reynolds,
        x_over_chord=x_over_chord,
    )
    return {
        **yplus_result,
        "y1_m": y1,
        "y1_over_chord": y1 / max(float(chord_m), 1.0e-30),
        "y1_source": source,
        "layers": max(int(layers), 0),
        "growth_rate": float(growth_rate),
        "prism_stack_thickness_m": prism,
        "prism_stack_thickness_over_chord": prism / max(float(chord_m), 1.0e-30),
        "theoretical_delta99_m": theoretical,
        "theoretical_delta99_over_chord": theoretical / max(float(chord_m), 1.0e-30),
        "comparison_x_over_chord": float(x_over_chord),
        "prism_to_theoretical_delta99_ratio": prism / theoretical if theoretical > 0.0 else None,
        "model_note": (
            "Turbulent zero-pressure-gradient flat-plate estimate. Pressure gradients, curvature, "
            "transition and separation in the ram-air case can change the real boundary layer."
        ),
    }
