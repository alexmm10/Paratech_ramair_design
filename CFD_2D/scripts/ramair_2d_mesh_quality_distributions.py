#!/usr/bin/env python3
"""Detailed, reproducible quality distributions for linear MSH2 CFD meshes.

The formulas reconstruct OpenFOAM-14 ``meshCheck`` definitions where
applicable. Face metrics are reduced to the worst adjacent-face value per cell
so every table reports cell counts. Exact pass/fail values and extrema remain
those emitted by native ``checkMesh``; this independent reconstruction is a
distribution diagnostic, not a replacement for OpenFOAM's polyMesh checks.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Iterable

import matplotlib.pyplot as plt
import numpy as np


CELL_FACES = {
    5: ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)),
    6: ((0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)),
}


def _read_msh2(path: Path) -> tuple[dict[int, np.ndarray], list[tuple[int, tuple[int, ...]]]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    node_start = lines.index("$Nodes")
    node_count = int(lines[node_start + 1])
    points: dict[int, np.ndarray] = {}
    for row in lines[node_start + 2 : node_start + 2 + node_count]:
        parts = row.split()
        points[int(parts[0])] = np.asarray(parts[1:4], dtype=float)
    element_start = lines.index("$Elements")
    element_count = int(lines[element_start + 1])
    cells: list[tuple[int, tuple[int, ...]]] = []
    for row in lines[element_start + 2 : element_start + 2 + element_count]:
        parts = row.split()
        kind = int(parts[1])
        if kind not in CELL_FACES:
            continue
        tag_count = int(parts[2])
        connectivity = tuple(int(value) for value in parts[3 + tag_count :])
        cells.append((kind, connectivity))
    if not cells:
        raise ValueError(f"No linear prism/hexahedron volume cells found in {path}")
    return points, cells


def _polygon_area_centroid_xy(vertices: np.ndarray) -> tuple[float, np.ndarray]:
    xy = vertices[:, :2]
    cross = xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1]
    signed_area = 0.5 * float(np.sum(cross))
    if abs(signed_area) < 1.0e-30:
        return 0.0, np.mean(vertices, axis=0)
    factor = 1.0 / (6.0 * signed_area)
    cx = float(np.sum((xy[:, 0] + np.roll(xy[:, 0], -1)) * cross) * factor)
    cy = float(np.sum((xy[:, 1] + np.roll(xy[:, 1], -1)) * cross) * factor)
    return abs(signed_area), np.asarray([cx, cy, float(np.mean(vertices[:, 2]))])


def _face_geometry(vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    centre_seed = np.mean(vertices, axis=0)
    area = np.zeros(3)
    weighted = np.zeros(3)
    total = 0.0
    for index in range(len(vertices)):
        a = vertices[index] - centre_seed
        b = vertices[(index + 1) % len(vertices)] - centre_seed
        triangle_area = 0.5 * np.cross(a, b)
        magnitude = float(np.linalg.norm(triangle_area))
        area += triangle_area
        weighted += magnitude * (centre_seed + vertices[index] + vertices[(index + 1) % len(vertices)]) / 3.0
        total += magnitude
    centre = weighted / total if total > 1.0e-30 else centre_seed
    return centre, area


def _bins(values: np.ndarray, edges: list[float]) -> list[dict[str, Any]]:
    finite = values[np.isfinite(values)]
    counts, _ = np.histogram(finite, bins=np.asarray(edges, dtype=float))
    rows = []
    for index, count in enumerate(counts):
        lower, upper = edges[index], edges[index + 1]
        upper_text = "inf" if math.isinf(upper) else f"{upper:g}"
        rows.append({
            "interval": f"[{lower:g}, {upper_text}{')' if index < len(counts) - 1 else ']'}",
            "count": int(count),
            "percent": 100.0 * int(count) / max(1, len(finite)),
        })
    return rows


def _quality_arrays(mesh_path: Path) -> dict[str, Any]:
    points_by_tag, cells = _read_msh2(mesh_path)
    count = len(cells)
    kinds = np.fromiter((kind for kind, _ in cells), dtype=np.int16, count=count)
    centres = np.empty((count, 3), dtype=float)
    volumes = np.empty(count, dtype=float)
    face_map: dict[tuple[int, ...], list[Any]] = {}
    for cell_index, (kind, connectivity) in enumerate(cells):
        vertices = np.asarray([points_by_tag[tag] for tag in connectivity], dtype=float)
        planar_count = 4 if kind == 5 else 3
        area, centre = _polygon_area_centroid_xy(vertices[:planar_count])
        thickness = float(np.max(vertices[:, 2]) - np.min(vertices[:, 2]))
        centres[cell_index] = centre
        volumes[cell_index] = area * thickness
        for local in CELL_FACES[kind]:
            face_nodes = tuple(connectivity[index] for index in local)
            key = tuple(sorted(face_nodes))
            existing = face_map.get(key)
            if existing is None:
                face_map[key] = [cell_index, -1, face_nodes]
            else:
                existing[1] = cell_index

    worst_skew = np.zeros(count)
    worst_nonorth = np.zeros(count)
    min_weight = np.ones(count)
    min_ratio = np.ones(count)
    max_growth = np.ones(count)
    component_area = np.zeros((count, 3))
    internal_area_sum = np.zeros(count)
    internal_count = np.zeros(count, dtype=np.int32)
    internal_faces: list[tuple[int, int, np.ndarray]] = []
    face_skew: list[float] = []
    face_nonorth: list[float] = []
    face_weight: list[float] = []
    face_ratio: list[float] = []

    tiny = 1.0e-300
    for owner, neighbour, face_nodes in face_map.values():
        vertices = np.asarray([points_by_tag[tag] for tag in face_nodes], dtype=float)
        # The front/back polygons of this one-cell-thick 2-D extrusion have an
        # area vector only in the empty direction. They contribute neither to
        # the in-plane aspect ratio nor to owner-neighbour quality metrics, and
        # OpenFOAM excludes empty-direction constraints from the 2-D checks.
        # Skipping them also avoids processing two large boundary faces per cell.
        if float(np.ptp(vertices[:, 2])) <= 1.0e-14:
            continue
        face_centre, area_vector = _face_geometry(vertices)
        magnitude = float(np.linalg.norm(area_vector))
        component_area[owner] += np.abs(area_vector)
        if neighbour >= 0:
            component_area[neighbour] += np.abs(area_vector)
            internal_area_sum[owner] += magnitude
            internal_area_sum[neighbour] += magnitude
            internal_count[owner] += 1
            internal_count[neighbour] += 1
            internal_faces.append((owner, neighbour, area_vector))
            d = centres[neighbour] - centres[owner]
            normal_dot_d = float(np.dot(area_vector, d))
            nonorth = math.degrees(math.acos(float(np.clip(abs(normal_dot_d) / (magnitude * np.linalg.norm(d) + tiny), 0.0, 1.0))))
            cpf = face_centre - centres[owner]
            skew_vector = cpf - (float(np.dot(area_vector, cpf)) / (normal_dot_d + tiny)) * d
            skew_mag = float(np.linalg.norm(skew_vector))
            skew_hat = skew_vector / (skew_mag + tiny)
            normalisation = max(0.2 * float(np.linalg.norm(d)), max(
                abs(float(np.dot(skew_hat, vertex - face_centre))) for vertex in vertices
            ), tiny)
            skew = skew_mag / normalisation
            d_owner = abs(float(np.dot(area_vector, face_centre - centres[owner])))
            d_neighbour = abs(float(np.dot(area_vector, centres[neighbour] - face_centre)))
            weight = min(d_owner, d_neighbour) / (d_owner + d_neighbour + tiny)
            ratio = min(volumes[owner], volumes[neighbour]) / (max(volumes[owner], volumes[neighbour]) + tiny)
            growth = math.sqrt(1.0 / max(ratio, tiny))
            for cell_index in (owner, neighbour):
                worst_skew[cell_index] = max(worst_skew[cell_index], skew)
                worst_nonorth[cell_index] = max(worst_nonorth[cell_index], nonorth)
                min_weight[cell_index] = min(min_weight[cell_index], weight)
                min_ratio[cell_index] = min(min_ratio[cell_index], ratio)
                max_growth[cell_index] = max(max_growth[cell_index], growth)
            face_skew.append(skew); face_nonorth.append(nonorth)
            face_weight.append(weight); face_ratio.append(ratio)
        else:
            cpf = face_centre - centres[owner]
            normal = area_vector / (magnitude + tiny)
            d = normal * float(np.dot(normal, cpf))
            skew_vector = cpf - (float(np.dot(area_vector, cpf)) / (float(np.dot(area_vector, d)) + tiny)) * d
            skew_mag = float(np.linalg.norm(skew_vector))
            skew_hat = skew_vector / (skew_mag + tiny)
            normalisation = max(0.4 * float(np.linalg.norm(d)), max(
                abs(float(np.dot(skew_hat, vertex - face_centre))) for vertex in vertices
            ), tiny)
            skew = skew_mag / normalisation
            worst_skew[owner] = max(worst_skew[owner], skew)
            face_skew.append(skew)

    determinant = np.zeros(count)
    average_area = internal_area_sum / np.maximum(internal_count, 1)
    tensors = np.zeros((count, 3, 3))
    for owner, neighbour, area_vector in internal_faces:
        for cell_index in (owner, neighbour):
            normalised = area_vector / max(average_area[cell_index], tiny)
            tensors[cell_index] += np.outer(normalised, normalised)
    tensors[:, 2, 2] = 1.0  # empty/extrusion direction in the 2-D case
    valid = internal_count > 0
    determinant[valid] = np.abs(np.linalg.det(tensors[valid]))
    aspect = np.max(component_area[:, :2], axis=1) / np.maximum(np.min(component_area[:, :2], axis=1), tiny)
    return {
        "cell_kind": kinds,
        "cell_centres": centres,
        "cell_volume": volumes,
        "cell_skewness_worst_face": worst_skew,
        "cell_non_orthogonality_worst_face_deg": worst_nonorth,
        "cell_interpolation_weight_min_face": min_weight,
        "cell_volume_ratio_min_face": min_ratio,
        "cell_linear_growth_max_face": max_growth,
        "cell_determinant": determinant,
        "cell_aspect_ratio_openfoam": aspect,
        "face_skewness": np.asarray(face_skew),
        "face_non_orthogonality_deg": np.asarray(face_nonorth),
        "face_interpolation_weight": np.asarray(face_weight),
        "face_volume_ratio": np.asarray(face_ratio),
    }


TABLE_SPECS = (
    ("Skewness geométrica OpenFOAM (peor cara/celda)", "cell_skewness_worst_face", [0, .25, .5, 1, 2, 4, math.inf]),
    ("No ortogonalidad [deg] (peor cara/celda)", "cell_non_orthogonality_worst_face_deg", [0, 20, 40, 60, 70, 80, 90, math.inf]),
    ("Crecimiento lineal local", "cell_linear_growth_max_face", [1, 1.05, 1.10, 1.20, 1.50, 2, math.inf]),
    ("Peso de interpolación (mínimo/celda)", "cell_interpolation_weight_min_face", [0, .01, .05, .10, .20, .30, .40, .50, 1.01]),
    ("Ratio de volumen (mínimo/celda)", "cell_volume_ratio_min_face", [0, .01, .05, .10, .20, .50, .80, 1.01]),
    ("Determinante OpenFOAM", "cell_determinant", [0, .001, .005, .01, .05, .10, .50, 1, math.inf]),
)


def _render_table(rows: list[dict[str, Any]], title: str, output: Path) -> None:
    labels = [row["interval"] for row in rows]
    cells = [[label, f"{row['count']:,}", f"{row['percent']:.3f}%"] for label, row in zip(labels, rows)]
    height = max(3.0, 0.36 * len(cells) + 1.5)
    fig, axis = plt.subplots(figsize=(8.0, height))
    axis.axis("off")
    table = axis.table(cellText=cells, colLabels=["Intervalo", "Celdas", "Porcentaje"], cellLoc="center", loc="center")
    table.auto_set_font_size(False); table.set_fontsize(10); table.scale(1.0, 1.35)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#2f2f2f"); cell.set_linewidth(0.5)
        if row == 0:
            cell.set_facecolor("#e6e6e6"); cell.set_text_props(weight="bold")
    axis.set_title(title, fontfamily="serif", fontweight="bold", fontsize=13, pad=12)
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _render_key_value_table(
    rows: list[tuple[str, str, str]], title: str, output: Path
) -> None:
    height = max(3.2, 0.38 * len(rows) + 1.5)
    fig, axis = plt.subplots(figsize=(10.0, height))
    axis.axis("off")
    table = axis.table(
        cellText=[[name, limit, mean] for name, limit, mean in rows],
        colLabels=["Magnitud", "Valor limite / fisico", "Valor medio"],
        cellLoc="left", loc="center",
    )
    table.auto_set_font_size(False); table.set_fontsize(9.5); table.scale(1.0, 1.35)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("#2f2f2f"); cell.set_linewidth(0.5)
        if row == 0:
            cell.set_facecolor("#e6e6e6"); cell.set_text_props(weight="bold")
    axis.set_title(title, fontfamily="serif", fontweight="bold", fontsize=13, pad=12)
    fig.savefig(output, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _finite_statistics(values: np.ndarray) -> dict[str, float]:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if not len(finite):
        return {"minimum": math.nan, "maximum": math.nan, "mean": math.nan}
    return {
        "minimum": float(np.min(finite)),
        "maximum": float(np.max(finite)),
        "mean": float(np.mean(finite)),
    }


def _format_stat(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return "-" if not math.isfinite(number) else f"{number:.7g}"


def _write_point_cloud_vtk(
    path: Path, centres: np.ndarray, values: np.ndarray, kinds: np.ndarray, scalar_name: str
) -> None:
    safe_name = scalar_name.replace(" ", "_")
    with path.open("w", encoding="ascii") as handle:
        handle.write("# vtk DataFile Version 3.0\nRamAir mesh-quality hotspot cells\nASCII\n")
        handle.write("DATASET POLYDATA\n")
        handle.write(f"POINTS {len(centres)} double\n")
        for point in centres:
            handle.write(f"{point[0]:.16g} {point[1]:.16g} {point[2]:.16g}\n")
        handle.write(f"VERTICES {len(centres)} {2 * len(centres)}\n")
        for index in range(len(centres)):
            handle.write(f"1 {index}\n")
        handle.write(f"POINT_DATA {len(centres)}\n")
        handle.write(f"SCALARS {safe_name} double 1\nLOOKUP_TABLE default\n")
        for value in values:
            handle.write(f"{float(value):.16g}\n")
        handle.write("SCALARS gmsh_volume_cell_type int 1\nLOOKUP_TABLE default\n")
        for kind in kinds:
            handle.write(f"{int(kind)}\n")


def generate_quality_distributions(
    mesh_path: Path,
    output_dir: Path,
    basic_characteristics: dict[str, Any] | None = None,
    exact_extrema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    arrays = _quality_arrays(mesh_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    distributions: dict[str, Any] = {}
    csv_rows: list[dict[str, Any]] = []
    images: list[str] = []
    for title, key, edges in TABLE_SPECS:
        rows = _bins(np.asarray(arrays[key]), edges)
        distributions[key] = rows
        for row in rows:
            csv_rows.append({"metric": key, **row})
        image = output_dir / f"quality_table_{key}.png"
        _render_table(rows, title, image); images.append(str(image))

    kinds = np.asarray(arrays["cell_kind"])
    aspect = np.asarray(arrays["cell_aspect_ratio_openfoam"])
    for title, key, mask, edges in (
        ("Aspect ratio: celdas BL hexaédricas", "aspect_ratio_boundary_layer_hex", kinds == 5, [0, 20, 50, 100, 500, 1000, 1500, math.inf]),
        ("Aspect ratio: volumen triangular prismático", "aspect_ratio_unstructured_prism", kinds == 6, [0, 5, 10, 20, 50, 100, math.inf]),
    ):
        rows = _bins(aspect[mask], edges); distributions[key] = rows
        for row in rows:
            csv_rows.append({"metric": key, **row})
        image = output_dir / f"quality_table_{key}.png"
        _render_table(rows, title, image); images.append(str(image))

    with (output_dir / "quality_distributions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["metric", "interval", "count", "percent"])
        writer.writeheader(); writer.writerows(csv_rows)
    hotspot_files: list[str] = []
    hotspots: dict[str, Any] = {}
    centres = np.asarray(arrays["cell_centres"])
    hotspot_specs = (
        ("low_interpolation_weight", "cell_interpolation_weight_min_face", lambda values: values < 0.10, True),
        ("low_volume_ratio", "cell_volume_ratio_min_face", lambda values: values < 0.10, True),
        ("low_determinant", "cell_determinant", lambda values: values < 0.005, True),
        ("high_linear_growth", "cell_linear_growth_max_face", lambda values: values > 2.0, False),
    )
    for label, key, selector, ascending in hotspot_specs:
        values = np.asarray(arrays[key])
        indices = np.flatnonzero(selector(values))
        if not len(indices):
            continue
        ordered = indices[np.argsort(values[indices])]
        if not ascending:
            ordered = ordered[::-1]
        vtk = output_dir / f"quality_hotspots_{label}.vtk"
        _write_point_cloud_vtk(vtk, centres[indices], values[indices], kinds[indices], key)
        hotspot_files.append(str(vtk))
        hotspots[label] = {
            "metric": key,
            "count": int(len(indices)),
            "vtk": str(vtk),
            "worst_cells": [
                {
                    "cell_index_zero_based": int(index),
                    "gmsh_volume_cell_type": int(kinds[index]),
                    "value": float(values[index]),
                    "centre_m": [float(value) for value in centres[index]],
                }
                for index in ordered[:25]
            ],
        }
    quality_statistics = {
        "skewness": _finite_statistics(np.asarray(arrays["face_skewness"])),
        "non_orthogonality_deg": _finite_statistics(
            np.asarray(arrays["face_non_orthogonality_deg"])
        ),
        "interpolation_weight": _finite_statistics(
            np.asarray(arrays["face_interpolation_weight"])
        ),
        "volume_ratio": _finite_statistics(np.asarray(arrays["face_volume_ratio"])),
        "determinant": _finite_statistics(np.asarray(arrays["cell_determinant"])),
        "linear_growth": _finite_statistics(
            np.asarray(arrays["cell_linear_growth_max_face"])
        ),
        "boundary_layer_aspect_ratio": _finite_statistics(aspect[kinds == 5]),
        "unstructured_aspect_ratio": _finite_statistics(aspect[kinds == 6]),
    }
    summary_rows: list[tuple[str, str, str]] = [
        (str(name), str(value), "") for name, value in (basic_characteristics or {}).items()
    ]
    extrema = exact_extrema or {}
    summary_rows.extend([
        ("No ortogonalidad [deg]", _format_stat(extrema.get("non_orthogonality_max", quality_statistics["non_orthogonality_deg"]["maximum"])), _format_stat(quality_statistics["non_orthogonality_deg"]["mean"])),
        ("Skewness OpenFOAM", _format_stat(extrema.get("skewness_max", quality_statistics["skewness"]["maximum"])), _format_stat(quality_statistics["skewness"]["mean"])),
        ("Peso de interpolacion", _format_stat(extrema.get("interpolation_weight_min", quality_statistics["interpolation_weight"]["minimum"])), _format_stat(quality_statistics["interpolation_weight"]["mean"])),
        ("Ratio de volumen", _format_stat(extrema.get("volume_ratio_min", quality_statistics["volume_ratio"]["minimum"])), _format_stat(quality_statistics["volume_ratio"]["mean"])),
        ("Determinante", _format_stat(extrema.get("determinant_min", quality_statistics["determinant"]["minimum"])), _format_stat(quality_statistics["determinant"]["mean"])),
        ("Aspect ratio quads BL", _format_stat(quality_statistics["boundary_layer_aspect_ratio"]["maximum"]), _format_stat(quality_statistics["boundary_layer_aspect_ratio"]["mean"])),
        ("Aspect ratio prismas triangulares", _format_stat(quality_statistics["unstructured_aspect_ratio"]["maximum"]), _format_stat(quality_statistics["unstructured_aspect_ratio"]["mean"])),
        ("Crecimiento lineal local", _format_stat(quality_statistics["linear_growth"]["maximum"]), _format_stat(quality_statistics["linear_growth"]["mean"])),
    ])
    if summary_rows:
        image = output_dir / "quality_table_mesh_basic_characteristics.png"
        _render_key_value_table(summary_rows, "Características físicas y topológicas", image)
        images.insert(0, str(image))
    report = {
        "schema_version": 1,
        "mesh": str(mesh_path.resolve()),
        "cell_count": int(len(kinds)),
        "boundary_layer_hex_cells": int(np.count_nonzero(kinds == 5)),
        "unstructured_prism_cells": int(np.count_nonzero(kinds == 6)),
        "distributions": distributions,
        "images": images,
        "hotspots": hotspots,
        "hotspot_vtk_files": hotspot_files,
        "basic_characteristics": basic_characteristics or {},
        "exact_extrema": extrema,
        "quality_statistics": quality_statistics,
        "computed_extrema": {
            "maximum_face_skewness": float(np.nanmax(arrays["face_skewness"])),
            "maximum_face_non_orthogonality_deg": float(
                np.nanmax(arrays["face_non_orthogonality_deg"])
            ),
            "minimum_face_interpolation_weight": float(
                np.nanmin(arrays["face_interpolation_weight"])
            ),
            "minimum_face_volume_ratio": float(np.nanmin(arrays["face_volume_ratio"])),
            "minimum_cell_determinant": float(np.nanmin(arrays["cell_determinant"])),
            "maximum_boundary_layer_aspect_ratio": float(
                np.nanmax(aspect[kinds == 5]) if np.any(kinds == 5) else math.nan
            ),
            "maximum_unstructured_aspect_ratio": float(
                np.nanmax(aspect[kinds == 6]) if np.any(kinds == 6) else math.nan
            ),
        },
        "formula_notes": {
            "distribution_scope": "OpenFOAM-like reconstruction from MSH2 reduced to cells; native checkMesh remains authoritative for exact extrema and pass/fail.",
            "skewness": "OpenFOAM face-centre displacement normalized by local face extent; this is not normalized equiangular skewness and is not bounded by 1.",
            "skewness_limits": "OpenFOAM-14 default maxInternalSkewness=4 and maxBoundarySkewness=20.",
            "non_orthogonality": "Angle between face area vector and the owner-neighbour cell-centre vector.",
            "growth": "sqrt(max(Vowner,Vneighbour)/min(Vowner,Vneighbour)); 2-D linear-size proxy from OpenFOAM face volume ratio.",
            "interpolation_weight": "OpenFOAM min(dOwn,dNei)/(dOwn+dNei), projected on the face-area vector.",
            "volume_ratio": "OpenFOAM min(Vowner,Vneighbour)/max(Vowner,Vneighbour).",
            "determinant": "OpenFOAM area-tensor well-posedness determinant using internal/coupled faces.",
            "aspect_ratio": "OpenFOAM Cartesian component area ratio; interpreted separately for BL hexes and unstructured prisms.",
        },
        "engineering_limits": {
            "boundary_layer_aspect_ratio_review": [1000, 1500],
            "unstructured_aspect_ratio_preferred_max": [20, 50],
            "maximum_designed_linear_growth": 1.20,
        },
    }
    (output_dir / "quality_distributions.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = generate_quality_distributions(args.mesh.resolve(), args.output_dir.resolve())
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
