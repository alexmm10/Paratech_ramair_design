#!/usr/bin/env python3
"""Shared, deterministic scientific plotting and provenance helpers."""
from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any, Iterable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ACCESSIBLE_COLORS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9")
MARKERS = ("o", "s", "^", "D", "v", "P")
LINESTYLES = ("-", "--", "-.", ":")


def apply_scientific_style() -> None:
    """Apply one Matplotlib style compatible with the project's pinned stack."""
    matplotlib.rcParams.update({
        "font.family": "serif",
        "font.serif": ["STIXGeneral", "DejaVu Serif"],
        "mathtext.fontset": "stix",
        "text.usetex": False,
        "font.size": 10.0,
        "axes.titlesize": 11.0,
        "axes.labelsize": 10.0,
        "axes.prop_cycle": matplotlib.cycler(color=ACCESSIBLE_COLORS),
        "axes.grid": True,
        "grid.alpha": 0.22,
        "grid.linewidth": 0.55,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.direction": "out",
        "ytick.direction": "out",
        "legend.frameon": False,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "svg.fonttype": "none",
    })


def data_limits(values: Iterable[float], *, padding: float = 0.06) -> tuple[float, float] | None:
    finite = np.asarray(list(values), dtype=float)
    finite = finite[np.isfinite(finite)]
    if not finite.size:
        return None
    low, high = float(finite.min()), float(finite.max())
    span = high - low
    margin = max(span * padding, max(abs(low), abs(high), 1.0) * 1.0e-3)
    return low - margin, high + margin


def _records(data: pd.DataFrame | Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, pd.DataFrame):
        clean = data.replace({np.nan: None})
        return clean.to_dict(orient="records")
    return [dict(row) for row in data]


def save_scientific_figure(
    figure: plt.Figure,
    png_path: Path,
    *,
    data: pd.DataFrame | Iterable[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    close: bool = True,
) -> dict[str, str]:
    """Export PNG/SVG/data/manifest without modifying source data."""
    apply_scientific_style()
    png = Path(png_path)
    png.parent.mkdir(parents=True, exist_ok=True)
    stem = png.with_suffix("")
    svg = stem.with_suffix(".svg")
    csv_path = stem.with_name(f"{stem.name}_plot_data.csv")
    json_path = stem.with_name(f"{stem.name}_plot_data.json")
    manifest_path = stem.with_name(f"{stem.name}_figure_manifest.json")
    rows = _records(data)
    figure.savefig(png, dpi=300)
    figure.savefig(svg, format="svg")
    frame = pd.DataFrame(rows)
    if rows:
        frame.to_csv(csv_path, index=False)
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "source": (metadata or {}).get("source"),
        "transformation": (metadata or {}).get("transformation", "none"),
        "filters": (metadata or {}).get("filters", []),
        "grouping": (metadata or {}).get("grouping"),
        "sorting": (metadata or {}).get("sorting"),
        "deduplication": (metadata or {}).get("deduplication"),
        "missing_values": (metadata or {}).get("missing_values", "preserved_as_gaps"),
        "style": "ramair-scientific-stix-v1",
        "versions": {
            "python": platform.python_version(),
            "matplotlib": matplotlib.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "figure_size_inches": [float(value) for value in figure.get_size_inches()],
        "dpi": 300,
        "rows": len(rows),
        "files": {
            "png": str(png),
            "svg": str(svg),
            "csv": str(csv_path) if rows else None,
            "json": str(json_path),
        },
        **{key: value for key, value in (metadata or {}).items() if key not in {
            "source", "transformation", "filters", "grouping", "sorting",
            "deduplication", "missing_values",
        }},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if close:
        plt.close(figure)
    return {
        "png": str(png), "svg": str(svg),
        "csv": str(csv_path) if rows else "",
        "json": str(json_path), "manifest": str(manifest_path),
    }


apply_scientific_style()
