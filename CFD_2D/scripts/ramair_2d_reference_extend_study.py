#!/usr/bin/env python3
"""Generate immutable-reference Extend variants for the experimental meshers."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Callable

from ramair_2d_closed_experimental_mesh import generate as generate_closed
from ramair_2d_gmsh_experimental import compare_openfoam_quality
from ramair_2d_open_experimental_mesh import generate as generate_open


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _run_variant(
    root: Path,
    *,
    experiment_id: str,
    base_name: str,
    candidate_name: str,
    configure: Callable[[dict[str, Any]], None],
    generate: Callable[[Path, Path | None, str | None, bool], dict[str, Any]],
) -> dict[str, Any]:
    experiment = root / "CFD_2D" / "experimental_meshes" / experiment_id
    base_revision = experiment / "revisions" / base_name
    config = copy.deepcopy(_read(base_revision / "mesh_config.json"))
    configure(config)
    config["name"] = candidate_name
    study_dir = experiment / "reference_extend_study"
    study_dir.mkdir(parents=True, exist_ok=True)
    config_path = study_dir / f"{candidate_name}.json"
    config_path.write_text(json.dumps(_jsonable(config), indent=2) + "\n", encoding="utf-8")
    candidate = generate(root, config_path, candidate_name, True)
    base = _read(base_revision / "mesh_report.json")
    comparison = compare_openfoam_quality(base, candidate)
    return {
        "base_revision": base_name,
        "candidate_revision": candidate_name,
        "preserved": [
            "geometry", "domain size", "farfield target", "boundary-layer law",
            "boundary-layer thickness", "tangential divisions", "Bump coefficients",
        ],
        "changed_only": [
            label for label, enabled in (
                ("external Extend controls", bool(config.get("external_volume", {}).get("automatic_extend_enabled"))),
                ("internal Extend controls", bool(config.get("internal_volume", {}).get("automatic_extend_enabled"))),
            ) if enabled
        ],
        "checkMesh_status": candidate.get("checkMesh_status"),
        "cells": candidate.get("checkMesh_cell_count"),
        "size_fields": {
            "external": candidate.get("external_size_law"),
            "internal": candidate.get("internal_size_law"),
        },
        "comparison": comparison,
        "report": str(experiment / "revisions" / candidate_name / "mesh_report.json"),
    }


def run(root: Path) -> dict[str, Any]:
    closed_name = "closed_validation_quality_extend_external_smooth_v2"
    open_name = "beta75_exact_shared_source_inlet_v33_interface045_bump_try_extend_both_smooth_v2"

    def configure_closed(config: dict[str, Any]) -> None:
        external = config.setdefault("external_volume", {})
        external.update({
            "automatic_extend_enabled": True,
            "extend_distance_max_chord": 20.0,
            "extend_power": 0.5,
            "extend_size_max_chord": float(external.get("farfield_size_chord", 5.0)),
        })

    def configure_open(config: dict[str, Any]) -> None:
        external = config.setdefault("external_volume", {})
        external.update({
            "automatic_extend_enabled": True,
            "extend_distance_max_chord": 20.0,
            "extend_power": 0.5,
            "extend_size_max_chord": float(external.get("farfield_size_chord", 5.0)),
        })
        internal = config.setdefault("internal_volume", {})
        internal.update({
            "automatic_extend_enabled": True,
            "extend_distance_max_chord": 0.10,
            "extend_power": 0.5,
            "extend_size_max_chord": float(internal.get("core_size_chord", 0.01)),
        })

    result = {
        "closed": _run_variant(
            root,
            experiment_id="closed_reference_from_scratch",
            base_name="closed_validation_quality",
            candidate_name=closed_name,
            configure=configure_closed,
            generate=generate_closed,
        ),
        "open": _run_variant(
            root,
            experiment_id="open_reference_from_scratch",
            base_name="beta75_exact_shared_source_inlet_v33_interface045_bump_try",
            candidate_name=open_name,
            configure=configure_open,
            generate=generate_open,
        ),
    }
    output = root / "CFD_2D" / "experimental_meshes" / "reference_extend_study.json"
    output.write_text(json.dumps(_jsonable(result), indent=2) + "\n", encoding="utf-8")
    result["summary"] = str(output)
    return result


def run_open_diagnostics(root: Path) -> dict[str, Any]:
    base_name = "beta75_exact_shared_source_inlet_v33_interface045_bump_try"

    def configure(*, external_enabled: bool, internal_enabled: bool,
                  external_distance: float = 20.0, external_power: float = 0.5,
                  internal_distance: float = 0.10, internal_power: float = 0.5):
        def apply(config: dict[str, Any]) -> None:
            external = config.setdefault("external_volume", {})
            external.update({
                "automatic_extend_enabled": external_enabled,
                "extend_distance_max_chord": external_distance,
                "extend_power": external_power,
                "extend_size_max_chord": float(external.get("farfield_size_chord", 5.0)),
            })
            internal = config.setdefault("internal_volume", {})
            internal.update({
                "automatic_extend_enabled": internal_enabled,
                "extend_distance_max_chord": internal_distance,
                "extend_power": internal_power,
                "extend_size_max_chord": float(internal.get("core_size_chord", 0.01)),
            })
        return apply

    cases = (
        ("extend_external_smooth_v3", configure(external_enabled=True, internal_enabled=False)),
        ("extend_internal_smooth_v3", configure(external_enabled=False, internal_enabled=True)),
        (
            "extend_both_ultrasmooth_v3",
            configure(
                external_enabled=True, internal_enabled=True,
                external_distance=30.0, external_power=0.35,
                internal_distance=0.20, internal_power=0.35,
            ),
        ),
    )
    rows = []
    for suffix, configure_case in cases:
        rows.append(_run_variant(
            root,
            experiment_id="open_reference_from_scratch",
            base_name=base_name,
            candidate_name=f"{base_name}_{suffix}",
            configure=configure_case,
            generate=generate_open,
        ))
    output = (
        root / "CFD_2D" / "experimental_meshes"
        / "reference_extend_open_diagnostics.json"
    )
    output.write_text(json.dumps(_jsonable(rows), indent=2) + "\n", encoding="utf-8")
    return {"open_diagnostics": rows, "summary": str(output)}


def run_open_final(root: Path) -> dict[str, Any]:
    base_name = "beta75_exact_shared_source_inlet_v33_interface045_bump_try"
    candidate_name = f"{base_name}_extend_both_gentle_v4"

    def configure(config: dict[str, Any]) -> None:
        external = config.setdefault("external_volume", {})
        external.update({
            "automatic_extend_enabled": True,
            "extend_distance_max_chord": 20.0,
            "extend_power": 0.5,
            "extend_size_max_chord": float(external.get("farfield_size_chord", 5.0)),
        })
        internal = config.setdefault("internal_volume", {})
        internal.update({
            "automatic_extend_enabled": True,
            "extend_distance_max_chord": 0.50,
            "extend_power": 0.10,
            "extend_size_max_chord": float(internal.get("core_size_chord", 0.01)),
        })

    result = _run_variant(
        root,
        experiment_id="open_reference_from_scratch",
        base_name=base_name,
        candidate_name=candidate_name,
        configure=configure,
        generate=generate_open,
    )
    output = root / "CFD_2D" / "experimental_meshes" / "reference_extend_open_final.json"
    output.write_text(json.dumps(_jsonable(result), indent=2) + "\n", encoding="utf-8")
    return {"open_final": result, "summary": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=("reference", "open-diagnostics", "open-final"), default="reference"
    )
    args = parser.parse_args()
    action = {
        "reference": run,
        "open-diagnostics": run_open_diagnostics,
        "open-final": run_open_final,
    }[args.mode]
    print(json.dumps(_jsonable(action(args.project_root.resolve())), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
