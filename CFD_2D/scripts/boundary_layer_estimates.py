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
    """Estimate FV first-cell thickness with the Schlichting correlation.

    ``y+`` defines the distance from the wall to the location where the
    velocity is stored.  OpenFOAM is cell-centred finite volume, so this is the
    first-cell centre and the geometric cell thickness is approximately
    ``2*y``.
    """
    reynolds = max(float(reynolds), 1.0)
    rho = max(float(rho_kg_m3), 1.0e-30)
    mu = max(float(mu_pa_s), 1.0e-30)
    chord = max(float(chord_m), 1.0e-30)
    velocity = reynolds * mu / (rho * chord)
    if reynolds >= 1.0e9:
        raise ValueError("The Schlichting y+ estimate is only valid for Re_x < 1e9.")
    skin_friction = (2.0 * math.log10(reynolds) - 0.65) ** -2.3
    friction_velocity = velocity * math.sqrt(0.5 * skin_friction)
    wall_distance = max(float(target_y_plus), 0.0) * mu / max(rho * friction_velocity, 1.0e-30)
    y1 = 2.0 * wall_distance
    return {
        "y1_m": y1,
        "y1_over_chord": y1 / chord,
        "first_cell_centre_distance_m": wall_distance,
        "first_cell_centre_distance_over_chord": wall_distance / chord,
        "finite_volume_height_multiplier": 2.0,
        "velocity_m_s": velocity,
        "skin_friction_coefficient": skin_friction,
        "friction_velocity_m_s": friction_velocity,
        "reynolds": reynolds,
        "correlation": "CFD-Online/Schlichting Cf=[2log10(Re_x)-0.65]^-2.3",
    }


def beta_law_coefficient(
    *, first_cell_height_m: float, total_thickness_m: float, layers: int
) -> float:
    """Solve Gmsh's Beta-law coefficient for ``Size``, thickness and layers.

    Gmsh's BoundaryLayer field does not derive ``Beta`` from ``Thickness``.
    For ``BetaLaw=1`` its first normalized cumulative coordinate is

    ``t0 = 1 + beta*tanh((1/N - 1)*atanh(1/beta))``

    and the final cumulative distance is ``Size/t0``.  Solving
    ``t0=Size/Thickness`` therefore makes all three requested quantities
    mutually consistent.
    """
    first = float(first_cell_height_m)
    thickness = float(total_thickness_m)
    count = int(layers)
    if first <= 0.0 or thickness <= 0.0 or count <= 0:
        raise ValueError("Beta-law first height, thickness and layer count must be positive.")
    if first * count >= thickness:
        raise ValueError(
            "Beta law requires first_cell_height * layers < total_thickness; "
            f"received {first * count:.8g} >= {thickness:.8g}."
        )
    target = first / thickness

    def residual(beta: float) -> float:
        coordinate = 1.0 + beta * math.tanh(
            (1.0 / count - 1.0) * math.atanh(1.0 / beta)
        )
        return coordinate - target

    lower = 1.0 + 1.0e-12
    upper = 5.0
    f_lower = residual(lower)
    f_upper = residual(upper)
    if not (f_lower < 0.0 < f_upper):
        raise ValueError(
            "No Gmsh Beta-law solution exists in 1 < Beta <= 5 for the requested stack."
        )
    for _ in range(160):
        middle = 0.5 * (lower + upper)
        value = residual(middle)
        if abs(value) <= 1.0e-14:
            return middle
        if value < 0.0:
            lower = middle
        else:
            upper = middle
    return 0.5 * (lower + upper)


def beta_law_cumulative_distances(
    *, first_cell_height_m: float, beta: float, layers: int
) -> list[float]:
    """Return the cumulative wall-normal coordinates used by Gmsh BetaLaw."""
    first = float(first_cell_height_m)
    coefficient = float(beta)
    count = int(layers)
    if first <= 0.0 or coefficient <= 1.0 or count <= 0:
        raise ValueError("Beta-law Size must be positive, Beta > 1 and layers positive.")
    zlog = math.log((1.0 + coefficient) / (coefficient - 1.0))
    normalized = []
    for index in range(count):
        eta = float(index + 1) / count
        power = math.exp(zlog * (1.0 - eta))
        normalized.append(1.0 + coefficient * (1.0 - power) / (1.0 + power))
    return [first * value / normalized[0] for value in normalized]


def geometric_layers_for_thickness(
    *, first_cell_height_m: float, growth_rate: float, minimum_thickness_m: float
) -> dict[str, float | int]:
    """Choose the smallest integer layer count whose geometric stack covers a target."""
    first = float(first_cell_height_m)
    growth = float(growth_rate)
    target = float(minimum_thickness_m)
    if first <= 0.0 or target <= 0.0 or growth < 1.0:
        raise ValueError("Geometric Size/thickness must be positive and growth rate >= 1.")
    if math.isclose(growth, 1.0, abs_tol=1.0e-14):
        continuous = target / first
    else:
        continuous = math.log1p(target * (growth - 1.0) / first) / math.log(growth)
    count = max(1, int(math.ceil(continuous - 1.0e-12)))
    generated = geometric_prism_stack_thickness(first, count, growth)
    return {
        "continuous_layer_count": continuous,
        "layers": count,
        "total_thickness_m": generated,
        "target_thickness_m": target,
        "last_layer_height_m": first * growth ** max(0, count - 1),
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
            "Turbulent zero-pressure-gradient flat-plate estimate. y+ fixes the wall-to-centre "
            "distance; the reported y1 is twice that distance for OpenFOAM's cell-centred finite "
            "volume method. Pressure gradients, curvature, transition and separation can change "
            "the real boundary layer."
        ),
    }
