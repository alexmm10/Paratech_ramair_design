#!/usr/bin/env python3
"""Generate bounded final-state ParaView products for one RANS checkpoint."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from openfoam_environment import activate_openfoam_environment
from paraview_case_viewer import (
    generate_automatic_paraview_products,
    generate_vtk_final_screenshots,
)
from ramair_2d_postprocess_registry import write_postprocess_manifest
from ramair_2d_urans_cases import complete_time_history
from ramair_2d_study_registry import utc_stamp


def _times(root: Path) -> list[tuple[float, Path]]:
    values: list[tuple[float, Path]] = []
    if not root.is_dir():
        return values
    for path in root.iterdir():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if value > 0:
            values.append((value, path))
    return sorted(values)


def prepare_final_state(case: Path) -> dict[str, Any]:
    """Reconstruct only the last decomposed SIMPLE iteration when required."""
    case = Path(case).resolve()
    if not (case / "constant/polyMesh/boundary").is_file():
        return {"status": "MISSING_CASE", "reason": "constant/polyMesh/boundary is missing"}
    direct = _times(case)
    history_states: list[tuple[float, Path]] = []
    history = case / "steadyInitialization/history"
    if history.is_dir():
        for root in history.glob("run_*/time_directories"):
            history_states.extend(_times(root))
    history_states.sort(key=lambda item: item[0])
    if history_states and (
        not direct or history_states[-1][0] > direct[-1][0]
    ):
        value, source = history_states[-1]
        link = case / f"{value:g}"
        if not link.exists():
            link.symlink_to(source, target_is_directory=True)
        direct = _times(case)
    processor_history = complete_time_history(case, required_fields=("U", "p", "nuTilda"))
    processor_times = [float(value) for value in processor_history.get("common_processor_times_s", [])]
    latest_direct = direct[-1][0] if direct else None
    latest_processor = processor_times[-1] if processor_times else None
    reconstruction: dict[str, Any] = {
        "status": "NOT_REQUIRED",
        "latest_direct": latest_direct,
        "latest_processor": latest_processor,
    }
    if (
        latest_processor is not None
        and (latest_direct is None or latest_processor > latest_direct)
    ):
        executable = shutil.which("reconstructPar")
        if not executable:
            return {
                "status": "RECONSTRUCTION_REQUIRED",
                "reason": "latest complete state exists only in processorN and reconstructPar is unavailable",
                "latest_direct": latest_direct,
                "latest_processor": latest_processor,
            }
        activate_openfoam_environment()
        command = [executable, "-time", f"{latest_processor:g}"]
        completed = subprocess.run(
            command,
            cwd=str(case),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=900,
            check=False,
        )
        reconstruction = {
            "status": "OK" if completed.returncode == 0 else "FAILED",
            "command": command,
            "returncode": int(completed.returncode),
            "latest_direct": latest_direct,
            "latest_processor": latest_processor,
            "log_tail": (completed.stdout or "")[-5000:],
        }
        if completed.returncode != 0:
            return {
                "status": "RECONSTRUCTION_FAILED",
                "reason": "Final-only reconstructPar failed; processor data were preserved.",
                "reconstruction": reconstruction,
            }
    final = _times(case)
    if not final:
        return {"status": "MISSING_FIELDS", "reason": "No reconstructed positive SIMPLE state is available"}
    iteration, final_path = final[-1]
    fields = sorted(
        path.name.removesuffix(".gz")
        for path in final_path.iterdir()
        if path.is_file()
    )
    required = ["U", "p", "nuTilda"]
    missing = [field for field in required if field not in fields]
    return {
        "status": "MISSING_FIELDS" if missing else "READY",
        "reason": f"Required fields are missing: {missing}" if missing else None,
        "final_iteration": iteration,
        "final_state": str(final_path),
        "fields": fields,
        "required_fields": required,
        "reconstruction": reconstruction,
        "processor_data_preserved": (case / "processor0").is_dir(),
        "history_state_linked_without_copy": bool(history_states),
    }


def resolve_final_vtk_artifacts(
    case: Path,
    *,
    generate_if_missing: bool = False,
    timeout_s: int = 900,
) -> dict[str, Any]:
    """Resolve one iteration-consistent VTK set, generating latestTime only."""
    case = Path(case).resolve()
    readiness = prepare_final_state(case)
    if readiness.get("status") != "READY":
        return {
            "schema_version": 1,
            "status": "NOT_GENERATED",
            "reason": readiness.get("reason") or readiness.get("status"),
            "case": str(case),
            "iteration": readiness.get("final_iteration"),
            "artifacts": {},
        }
    iteration = float(readiness["final_iteration"])

    def collect() -> dict[str, dict[str, Any]]:
        candidates = sorted((case / "VTK").rglob("*.vtk")) if (case / "VTK").is_dir() else []
        matching: list[Path] = []
        for path in candidates:
            stem_suffix = path.stem.rsplit("_", 1)[-1]
            try:
                value = float(stem_suffix)
            except ValueError:
                continue
            if abs(value - iteration) <= max(1.0e-9, abs(iteration) * 1.0e-10):
                matching.append(path.resolve())
        volume = next(
            (path for path in matching if path.parent == (case / "VTK").resolve()),
            None,
        )
        walls = [path for path in matching if "wall" in path.parent.name.lower()]
        farfield = next((path for path in matching if "farfield" in path.parent.name.lower()), None)
        return {
            "case": {
                "status": "READY" if volume else "NOT_GENERATED",
                **({"path": str(volume)} if volume else {}),
            },
            "wall": {
                "status": "READY" if walls else "NOT_GENERATED",
                **({"paths": [str(path) for path in walls]} if walls else {}),
            },
            "farfield": {
                "status": "READY" if farfield else "NOT_GENERATED",
                **({"path": str(farfield)} if farfield else {}),
            },
        }

    artifacts = collect()
    generation: dict[str, Any] = {"status": "NOT_REQUIRED"}
    if artifacts["case"]["status"] != "READY" and generate_if_missing:
        activate_openfoam_environment()
        executable = shutil.which("foamToVTK")
        if executable:
            command = [executable, "-latestTime"]
            completed = subprocess.run(
                command,
                cwd=str(case),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=int(timeout_s),
                check=False,
            )
            generation = {
                "status": "OK" if completed.returncode == 0 else "FAILED",
                "command": command,
                "returncode": int(completed.returncode),
                "log_tail": (completed.stdout or "")[-5000:],
            }
            artifacts = collect()
        else:
            generation = {"status": "MISSING_EXECUTABLE", "command": ["foamToVTK", "-latestTime"]}
    ready_paths: list[str] = []
    if artifacts["case"].get("path"):
        ready_paths.append(str(artifacts["case"]["path"]))
    ready_paths.extend(str(path) for path in artifacts["wall"].get("paths") or [])
    if artifacts["farfield"].get("path"):
        ready_paths.append(str(artifacts["farfield"]["path"]))
    marker = case / "case.foam"
    marker.touch(exist_ok=True)
    return {
        "schema_version": 1,
        "status": "READY" if artifacts["case"]["status"] == "READY" else "NOT_GENERATED",
        "case": str(case),
        "iteration": iteration,
        "artifacts": artifacts,
        "reader_paths": ready_paths,
        "fallback": {"status": "READY", "path": str(marker.resolve()), "time": iteration},
        "generation": generation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--field-scale-mode",
        choices=("exact", "robust", "manual"),
        default="exact",
    )
    parser.add_argument(
        "--robust-percentiles",
        type=float,
        nargs=2,
        default=(1.0, 99.0),
        metavar=("LOW", "HIGH"),
    )
    parser.add_argument("--manual-cp-range", type=float, nargs=2)
    parser.add_argument("--manual-u-range", type=float, nargs=2)
    args = parser.parse_args()
    case = args.case.resolve()
    output = args.output.resolve()
    readiness = prepare_final_state(case)
    if readiness.get("status") != "READY":
        output.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": 2,
            "selected_mesh_id": "UNRESOLVED",
            "selected_checkpoint_id": case.parent.name,
            "case_path": str(case),
            "required_fields": readiness.get("required_fields", ["U", "p", "nuTilda"]),
            "available_fields": readiness.get("fields", []),
            "reconstructed": False,
            "products": {},
            "status": readiness.get("status"),
            "reason": readiness.get("reason"),
            "generated_at": utc_stamp(),
        }
        if readiness.get("final_iteration") is not None:
            report["final_iteration_or_time"] = readiness["final_iteration"]
        (output / "paraview_products.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 2
    vtk_artifacts = resolve_final_vtk_artifacts(
        case, generate_if_missing=True, timeout_s=900
    )
    products = generate_automatic_paraview_products(
        case,
        output,
        maximum_frames=1,
        timeout_s=900,
        time_semantics="SIMPLE iteration",
        stage_label="RANS",
        field_scale_mode=args.field_scale_mode,
        robust_percentiles=tuple(args.robust_percentiles),
        manual_scales={
            name: tuple(bounds)
            for name, bounds in {
                "Cp": args.manual_cp_range,
                "U": args.manual_u_range,
            }.items()
            if bounds is not None
        },
    )
    rendered_products = dict(products.get("products") or {})
    vtk_screenshots = generate_vtk_final_screenshots(
        [Path(value) for value in vtk_artifacts.get("reader_paths") or []],
        output,
        timeout_s=900,
    )
    rendered_products.update(vtk_screenshots.get("products") or {})
    checkpoint_manifest_path = case.parent / "checkpoint_manifest.json"
    try:
        checkpoint_manifest = json.loads(
            checkpoint_manifest_path.read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError):
        checkpoint_manifest = {}
    run_id = str(
        checkpoint_manifest.get("checkpoint_id")
        or checkpoint_manifest.get("source_run_id")
        or case.parent.name
    )
    foam_marker = case / "case.foam"
    foam_marker.touch(exist_ok=True)
    readiness_path = output / "rans_paraview_readiness.json"
    postprocess_manifest_path = output / "postprocess_manifest.json"
    product_entries = {
        key: {"status": "READY", "path": str(value)}
        for key, value in rendered_products.items()
        if isinstance(value, (str, Path)) and value and Path(str(value)).exists()
    }
    report = {
        "schema_version": 2,
        "status": (
            "READY"
            if products.get("status") in {"OK", "TIMEOUT_PARTIAL"}
            else "VIEWER_FAILED"
        ),
        "selected_mesh_id": checkpoint_manifest.get("mesh_id") or case.parent.name,
        "selected_checkpoint_id": run_id,
        "case_path": str(case),
        "foam_marker": str(foam_marker.resolve()),
        "vtk_artifacts": vtk_artifacts,
        "vtk_screenshots": vtk_screenshots,
        "final_iteration_or_time": readiness.get("final_iteration"),
        "required_fields": readiness.get("required_fields", ["U", "p", "nuTilda"]),
        "available_fields": readiness.get("fields") or [],
        "reconstructed": (readiness.get("reconstruction") or {}).get("status") == "OK",
        "products": product_entries,
        "reason": None,
        "generated_at": utc_stamp(),
        "internal_mesh": (
            case / "constant/polyMesh/boundary"
        ).is_file(),
        "postprocess_manifest": str(postprocess_manifest_path),
        "policy": {
            "final_state_only": True,
            "automatic_animations": False,
            "all_time_vtk_export": False,
            "processor_data_preserved": True,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    readiness_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output / "paraview_products.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    product_paths = [
        readiness_path,
        output / "automatic_products_report.json",
        output / "paraview_products.json",
    ]
    for value in (
        rendered_products.get("cp_final_png"),
        rendered_products.get("velocity_final_png"),
        rendered_products.get("pressure_final_vtk_png"),
        rendered_products.get("velocity_final_vtk_png"),
        rendered_products.get("state"),
    ):
        if value:
            product_paths.append(Path(str(value)))
    write_postprocess_manifest(
        output,
        run_id=run_id,
        mode="RANS",
        inputs={
            "case": str(case),
            "final_iteration": readiness.get("final_iteration"),
            "field_scale_mode": args.field_scale_mode,
            "robust_percentiles": list(args.robust_percentiles),
        },
        products=product_paths,
        errors=(
            []
            if report["status"] == "READY"
            else [str(products.get("status") or "PARAVIEW_PRODUCTS_FAILED")]
        ),
        metadata={
            "fields": readiness.get("fields") or [],
            "internal_mesh": report["internal_mesh"],
            "courant_policy": "NOT_APPLICABLE_TO_RANS",
            "scale_policy": rendered_products.get("scale_policy") or {},
            "vtk_artifacts": vtk_artifacts,
        },
        regeneration_commands={
            "field_images": [sys.executable, *sys.argv],
            "paraview": [sys.executable, *sys.argv],
            "technical_files": [sys.executable, *sys.argv],
        },
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
