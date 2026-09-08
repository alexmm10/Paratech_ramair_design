#!/usr/bin/env python3
"""Prepare an open-airfoil validation pilot from an accepted convergence RANS base."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


STUDY = "closed_open_M0p15_Re1p9e6_alpha8"
VARIANT = "open_ramair_validation_1m"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def poly_mesh_digest(poly_mesh: Path) -> str:
    digest = hashlib.sha256()
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        path = poly_mesh / name
        if not path.is_file():
            raise FileNotFoundError(path)
        digest.update(name.encode("ascii"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def archive_previous_execution(case: Path, reports: Path) -> Path | None:
    """Keep a compact scientific record before the pilot case is regenerated."""
    staged = read_json(case / "staged_run_status.json")
    if not staged:
        return None
    phases = []
    for row in list(staged.get("transient_phases") or []):
        run_status = row.get("run_status") if isinstance(row.get("run_status"), dict) else {}
        event = (
            run_status.get("openfoam_event")
            if isinstance(run_status.get("openfoam_event"), dict)
            else {}
        )
        phases.append({
            "phase": row.get("phase"),
            "returncode": row.get("returncode"),
            "status": run_status.get("status"),
            "maximum_courant": event.get("maximum_courant"),
            "numerical_divergence": bool(event.get("numerical_divergence", False)),
        })
    reached = [str(row.get("phase")) for row in phases if row.get("status") == "RUN_COMPLETED"]
    path = reports / f"open_validation_pilot_execution_{time.strftime('%Y%m%d_%H%M%S')}.json"
    write_json(path, {
        "schema_version": 1,
        "case": str(case),
        "status": staged.get("status"),
        "completion_reason": staged.get("completion_reason"),
        "production_complete": bool(staged.get("production_complete", False)),
        "software_phase_transitions_verified": reached,
        "scientific_result_accepted": bool(staged.get("production_complete", False)),
        "scientific_interpretation": (
            "The bounded smoke plan verified A-B-C-D-E orchestration but is not a "
            "production-duration stability demonstration. Diverged or incomplete data "
            "must not be published."
        ),
        "phases": phases,
        "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return path


def prepare_open_validation_pilot(
    root: Path,
    *,
    mesh_id: str = "open_medium",
    alpha: float = 8.0,
    solver_config: Path,
) -> dict[str, Any]:
    root = Path(root).resolve()
    study = root / "CFD_2D/validation_studies" / STUDY
    registry = read_json(study / "mesh_registry.json")
    row = next(
        (
            item for item in list(registry.get("meshes") or [])
            if str(item.get("id")) == mesh_id
        ),
        None,
    )
    if not row or row.get("accepted") is not True or row.get("checkMesh_status") != "OK":
        raise RuntimeError(f"{mesh_id} is not an accepted checkMesh-OK study mesh")
    source_mesh = Path(str(row["mesh_package"])) / "Mesh Data"
    checkpoint = study / "checkpoints" / mesh_id
    checkpoint_case = checkpoint / "case"
    if not (checkpoint_case / "0").is_dir():
        raise FileNotFoundError(checkpoint_case / "0")
    source_digest = poly_mesh_digest(source_mesh / "constant/polyMesh")
    checkpoint_digest = poly_mesh_digest(checkpoint_case / "constant/polyMesh")
    if source_digest != checkpoint_digest:
        raise RuntimeError("Registered medium mesh and accepted RANS checkpoint do not match")

    alpha_dir = f"alpha_{float(alpha):+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")
    previous_case = root / "CFD_2D/openfoam_cases" / VARIANT / alpha_dir
    previous_execution = archive_previous_execution(previous_case, study / "reports")

    target_mesh = root / "CFD_2D/meshes" / VARIANT
    backup = None
    if target_mesh.exists() and poly_mesh_digest(target_mesh / "constant/polyMesh") != source_digest:
        backup = (
            root / "CFD_2D/meshes/.validation_selection_backups"
            / f"{VARIANT}_{time.strftime('%Y%m%d_%H%M%S')}"
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target_mesh), str(backup))
    if not target_mesh.exists():
        shutil.copytree(source_mesh, target_mesh)
    (target_mesh / "MESH_APPROVED.flag").write_text(
        "\n".join([
            f"approved_at={time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"variant={VARIANT}",
            "status=PASS",
            "openfoam_check_ok=True",
            f"purpose=open_closed_validation_pilot_from_{mesh_id}",
            f"poly_mesh_digest={source_digest}",
        ]) + "\n",
        encoding="utf-8",
    )

    writer = root / "CFD_2D/scripts/ramair_2d_openfoam_case_writer.py"
    command = [
        sys.executable, str(writer),
        "--case-root", str(root), "--variant", VARIANT,
        "--alpha", str(float(alpha)), "--write-case",
        "--mesh-approved-required", "--require-converted-polymesh",
        "--overwrite", "--existing-case-action", "archive",
        "--solver-config", str(Path(solver_config).resolve()),
    ]
    completed = subprocess.run(command, cwd=str(root), text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"Open validation case writer failed with {completed.returncode}")
    case = root / "CFD_2D/openfoam_cases" / VARIANT / alpha_dir
    if poly_mesh_digest(case / "constant/polyMesh") != checkpoint_digest:
        raise RuntimeError("Prepared validation case does not retain the checkpoint mesh")
    if (case / "0").exists():
        shutil.rmtree(case / "0")
    shutil.copytree(checkpoint_case / "0", case / "0")
    case_config_path = case / "case_config.json"
    case_config = read_json(case_config_path)
    case_config.update({
        "validation_comparison_role": "open_candidate",
        "rans_seed_source": str(checkpoint_case / "0"),
        "rans_seed_mesh_id": mesh_id,
        "rans_seed_iteration": int(
            read_json(checkpoint / "checkpoint_manifest.json").get("iterations_completed") or 0
        ),
        "rans_seed_poly_mesh_digest": checkpoint_digest,
        "time_step_mode": "adaptive",
        "maxCo": 5.0,
    })
    write_json(case_config_path, case_config)
    report = {
        "schema_version": 1,
        "status": "OPEN_VALIDATION_PILOT_READY",
        "variant": VARIANT,
        "alpha_deg": float(alpha),
        "selected_mesh_id": mesh_id,
        "selected_mesh_level": row.get("level"),
        "cell_count": row.get("cell_count"),
        "mesh_digest": checkpoint_digest,
        "mesh_backup": str(backup) if backup else None,
        "rans_checkpoint": str(checkpoint),
        "rans_iterations": case_config["rans_seed_iteration"],
        "case": str(case),
        "solver_config": str(Path(solver_config).resolve()),
        "time_step_policy": "validation adaptive dt; open-lip maxCo=5; maxDeltaT from phase plan",
        "prepared_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "previous_execution_evidence": str(previous_execution) if previous_execution else None,
    }
    smoke_durations = [0.003125, 0.00625, 0.0125, 0.0125, 0.025]
    smoke_plan = {
        "schema_version": 1,
        "purpose": "bounded software and stability pilot; not scientific production",
        "target_deltaT_star": 0.0025,
        "adjust_time_step": True,
        "maxCo": 5.0,
        "max_outer_correctors": 5,
        "outer_residual_control_enabled": True,
        "stages": [
            {
                "stage": stage,
                "scheme": "Euler" if stage in {"A", "B", "C"} else "backward",
                "dt_factor": factor,
                "duration_time_star": duration,
                "sampling": stage == "E",
            }
            for stage, factor, duration in zip(
                ("A", "B", "C", "D", "E"),
                (0.25, 0.5, 1.0, 1.0, 1.0),
                smoke_durations,
            )
        ],
        "production_stage": "E",
        "production_start_time_star": sum(smoke_durations[:-1]),
        "total_time_star": sum(smoke_durations),
        "average_from_fraction": sum(smoke_durations[:-1]) / sum(smoke_durations),
    }
    smoke_plan_path = study / "configs/open_validation_pilot_smoke_plan.json"
    write_json(smoke_plan_path, smoke_plan)
    report["smoke_plan"] = str(smoke_plan_path)
    report["smoke_plan_scientific_eligible"] = False
    write_json(case / "open_validation_pilot_manifest.json", report)
    write_json(study / "reports/open_validation_pilot.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--mesh-id", default="open_medium")
    parser.add_argument("--alpha", type=float, default=8.0)
    parser.add_argument("--solver-config", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_open_validation_pilot(
        args.project_root,
        mesh_id=args.mesh_id,
        alpha=args.alpha,
        solver_config=args.solver_config,
    ), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
