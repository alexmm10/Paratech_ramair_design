"""Static, compact plots for the Validation & Convergence Lab monitors."""
from __future__ import annotations

import math
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from ramair_scientific_plot_style import apply_scientific_style  # noqa: E402

apply_scientific_style()


_FIELD_LABELS = {
    "p": "p",
    "U": "U",
    "Ux": "Ux",
    "Uy": "Uy",
    "Uz": "Uz",
    "U.x": "U.x",
    "U.y": "U.y",
    "U.z": "U.z",
    "nuTilda": "nuTilda",
}


def _downsample(frame: pd.DataFrame, maximum: int = 700) -> pd.DataFrame:
    if len(frame) <= maximum:
        return frame
    indices = np.linspace(0, len(frame) - 1, maximum, dtype=int)
    return frame.iloc[np.unique(indices)]


def _robust_limits(values: np.ndarray, *, padding: float = 0.08) -> tuple[float, float] | None:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    if finite.size >= 20:
        lower, upper = np.nanpercentile(finite, [1.0, 99.0])
    else:
        lower, upper = float(np.nanmin(finite)), float(np.nanmax(finite))
    if math.isclose(float(lower), float(upper), rel_tol=0.0, abs_tol=1e-12):
        span = max(abs(float(lower)) * 0.1, 1e-3)
    else:
        span = float(upper) - float(lower)
    return float(lower) - padding * span, float(upper) + padding * span


def residual_figure(
    snapshot: dict[str, Any],
    *,
    mode: str,
) -> tuple[plt.Figure, dict[str, int]]:
    """Return a non-interactive residual figure without modifying raw values."""
    mode = mode.upper()
    frame = pd.DataFrame(snapshot.get("residuals") or [])
    figure, axis = plt.subplots(figsize=(7.4, 2.85))
    figure.subplots_adjust(left=0.11, right=0.97, bottom=0.19, top=0.84)
    invalid_count = 0
    if not frame.empty and {"iteration", "field", "value"}.issubset(frame.columns):
        frame = frame.copy()
        frame["iteration"] = pd.to_numeric(frame["iteration"], errors="coerce")
        value_column = (
            "initial_residual"
            if "initial_residual" in frame.columns
            else "value"
        )
        frame["value"] = pd.to_numeric(
            frame[value_column], errors="coerce"
        )
        frame = frame[
            ~frame["field"].astype(str).str.fullmatch("phi", case=False)
        ]
        frame = (
            frame.sort_values("iteration")
            .groupby(["iteration", "field"], as_index=False, sort=False)
            .first()
        )
        invalid = (~np.isfinite(frame["value"])) | (frame["value"] <= 0)
        invalid_count = int(invalid.sum())
        frame.loc[invalid, "value"] = np.nan
        frame = _downsample(frame.dropna(subset=["iteration"]))
        for field, group in frame.groupby("field", sort=False):
            axis.plot(
                group["iteration"],
                group["value"],
                linewidth=1.0,
                label=_FIELD_LABELS.get(str(field), str(field)),
            )
    axis.set_yscale("log")
    axis.set_xlabel("Iteracion SIMPLE" if mode == "RANS" else "Tiempo fisico [s]")
    axis.set_ylabel("Residuo inicial")
    axis.set_title(
        "Convergencia de residuos - RANS/SIMPLE"
        if mode == "RANS"
        else "Residuos por paso fisico - URANS/PIMPLE"
    )
    axis.grid(True, which="major", alpha=0.35)
    axis.grid(True, which="minor", alpha=0.15)
    if axis.lines:
        axis.legend(loc="best", ncols=min(4, len(axis.lines)), fontsize=8)
    else:
        diagnostics = snapshot.get("parser_diagnostics") or {}
        detail = str(
            diagnostics.get("parser_error")
            or "Esperando residuales SIMPLE"
        )
        inspected = diagnostics.get("inspected_paths") or []
        if inspected:
            detail += "\nFuentes inspeccionadas: " + ", ".join(
                str(path) for path in inspected[-2:]
            )
        axis.text(
            0.5,
            0.5,
            detail,
            ha="center",
            va="center",
            fontsize=8,
            wrap=True,
        )
    return figure, {"nonpositive_or_nonfinite_hidden": invalid_count}


def coefficient_figure(
    snapshot: dict[str, Any],
    *,
    mode: str,
    separate_cd_cm: bool = False,
    cd_epsilon: float = 1e-12,
) -> tuple[plt.Figure, dict[str, int]]:
    """Return the requested aerodynamic-coefficient plot with safe Cl/Cd."""
    mode = mode.upper()
    frame = pd.DataFrame(snapshot.get("forces") or [])
    if separate_cd_cm:
        figure, axes = plt.subplots(
            2,
            1,
            figsize=(7.4, 4.6),
            sharex=True,
        )
        figure.subplots_adjust(
            left=0.11, right=0.88, bottom=0.12, top=0.91, hspace=0.18
        )
        primary, secondary_panel = axes
    else:
        figure, primary = plt.subplots(
            figsize=(7.4, 2.95),
        )
        figure.subplots_adjust(left=0.11, right=0.86, bottom=0.19, top=0.84)
        secondary_panel = None
    discarded = 0
    x_label = "Iteracion SIMPLE" if mode == "RANS" else "Tiempo fisico [s]"
    if not frame.empty and "Time" in frame.columns:
        frame = frame.copy()
        frame["Time"] = pd.to_numeric(frame["Time"], errors="coerce")
        for column in ("Cl", "Cd", "Cm"):
            frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
        frame = _downsample(frame.dropna(subset=["Time"]))
        efficiency = np.full(len(frame), np.nan, dtype=float)
        valid = (
            np.isfinite(frame["Cl"].to_numpy(dtype=float))
            & np.isfinite(frame["Cd"].to_numpy(dtype=float))
            & (np.abs(frame["Cd"].to_numpy(dtype=float)) >= cd_epsilon)
        )
        efficiency[valid] = (
            frame.loc[valid, "Cl"].to_numpy(dtype=float)
            / frame.loc[valid, "Cd"].to_numpy(dtype=float)
        )
        discarded = int((~valid).sum())
        primary.plot(frame["Time"], frame["Cl"], label="Cl", linewidth=1.1)
        target_for_small = (
            secondary_panel if secondary_panel is not None else primary
        )
        target_for_small.plot(frame["Time"], frame["Cd"], label="Cd", linewidth=1.0)
        target_for_small.plot(frame["Time"], frame["Cm"], label="Cm", linewidth=1.0)
        efficiency_axis = primary.twinx()
        efficiency_axis.plot(
            frame["Time"],
            efficiency,
            color="#8c564b",
            alpha=0.78,
            linewidth=1.0,
            label="Cl/Cd",
        )
        efficiency_axis.set_ylabel("Eficiencia Cl/Cd [-]")
        limits = _robust_limits(efficiency)
        if limits is not None:
            efficiency_axis.set_ylim(*limits)
        left_values = frame[["Cl", "Cd", "Cm"]].to_numpy(dtype=float)
        limits = _robust_limits(left_values.ravel())
        if limits is not None and not separate_cd_cm:
            primary.set_ylim(*limits)
        if separate_cd_cm:
            limits = _robust_limits(frame[["Cd", "Cm"]].to_numpy(dtype=float).ravel())
            if limits is not None:
                secondary_panel.set_ylim(*limits)
            secondary_panel.set_ylabel("Cd, Cm [-]")
            secondary_panel.grid(True, alpha=0.25)
            secondary_panel.legend(loc="best", fontsize=8)
        lines = primary.lines + efficiency_axis.lines
        primary.legend(
            lines,
            [line.get_label() for line in lines],
            loc="best",
            ncols=min(4, len(lines)),
            fontsize=8,
        )
    else:
        primary.text(0.5, 0.5, "Esperando forceCoeffs", ha="center", va="center")
    primary.set_ylabel("Coeficiente aerodinamico [-]")
    (
        secondary_panel if secondary_panel is not None else primary
    ).set_xlabel(x_label)
    primary.set_title(
        "Evolucion de coeficientes aerodinamicos - RANS/SIMPLE"
        if mode == "RANS"
        else "Evolucion de coeficientes aerodinamicos - URANS/PIMPLE"
    )
    primary.grid(True, alpha=0.3)
    return figure, {"efficiency_values_discarded": discarded}


def close_figures(*figures: plt.Figure) -> None:
    for figure in figures:
        plt.close(figure)
