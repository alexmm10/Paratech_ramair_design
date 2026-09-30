"""Persistent, independent CAD/CFD workspaces. No active-global input mutation.

The small manifests own identity; paths own containment. Large meshes and fields
stay in native Linux storage and Windows receives selected products and links.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path: Path, data: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False, default=str) + "\n", encoding="utf-8")
    temp.replace(path)
    return path


def slug(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    if not value or value.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
        raise ValueError("El nombre debe contener letras o números y no puede ser un nombre reservado.")
    return value[:100]


def digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False, default=str).encode()).hexdigest()


def file_digest(path: Path) -> str:
    with Path(path).open("rb") as stream:
        checksum = hashlib.sha256()
        for block in iter(lambda: stream.read(1024*1024), b""):
            checksum.update(block)
        return checksum.hexdigest()


def initialize(root: Path) -> None:
    for name in ("Workspaces", "Mesh Presets", "Airfoil Profiles/Processed", "Airfoil Profiles/Reference", "Validation Gallery"):
        (root / name).mkdir(parents=True, exist_ok=True)


def new_manifest(kind: str, name: str, **values: Any) -> dict:
    return {"schema_version": SCHEMA_VERSION, "kind": kind, "id": uuid.uuid4().hex,
            "name": name.strip(), "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **values}


def workspaces(root: Path) -> list[dict]:
    index = read_json(root / "Workspaces/index.json", {"paths": []})
    paths = {p.parent.resolve() for p in (root / "Workspaces").glob("*/workspace.json")}
    paths.update(Path(p).resolve() for p in index.get("paths", []) if Path(p).is_dir())
    return [dict(read_json(p / "workspace.json"), path=str(p)) for p in sorted(paths) if (p / "workspace.json").is_file()]


def create_workspace(root: Path, name: str, parent: Path | None = None) -> Path:
    initialize(root)
    path = (parent or root / "Workspaces") / slug(name)
    path.mkdir(parents=True, exist_ok=False)
    write_json(path / "workspace.json", new_manifest("workspace", name))
    for folder in ("CATIA", "CFD_2D"):
        (path / folder).mkdir()
    register_workspace(root, path)
    return path


def register_workspace(root: Path, path: Path) -> None:
    if not (path / "workspace.json").is_file():
        raise ValueError("Esta carpeta no contiene workspace.json.")
    entries = read_json(root / "Workspaces/index.json", {"paths": []})
    entries["paths"] = sorted(set(entries.get("paths", [])) | {str(path.resolve())})
    write_json(root / "Workspaces/index.json", entries)


def physical_conditions(reynolds: float = 1.9e6, mach: float = 0.15,
                        pressure_pa: float = 101325., temperature_k: float = 288.15) -> dict:
    inputs = {"reynolds": reynolds, "mach": mach, "pressure_ref_pa": pressure_pa, "temperature_K": temperature_k}
    if any(not math.isfinite(float(v)) or float(v) <= 0 for v in inputs.values()):
        raise ValueError("Re, Mach, presión y temperatura deben ser positivos y finitos.")
    rho = pressure_pa / (287.05 * temperature_k)
    sound = math.sqrt(1.4 * 287.05 * temperature_k)
    velocity = mach * sound
    nu = velocity / reynolds
    return {**inputs, "chord_m": 1., "rho": rho, "mu": rho * nu, "nu": nu,
            "speed_of_sound_m_s": sound, "velocity_m_s": velocity, "velocity_source": "mach",
            "viscosity_policy": "match_Re_at_Mach_with_unit_chord", "gas_constant_J_kg_K": 287.05, "gamma": 1.4}


def create_cfd_case(workspace: Path, name: str, conditions: dict) -> Path:
    # Recompute all derived properties: a stale imported viscosity cannot win.
    physical = physical_conditions(conditions["reynolds"], conditions["mach"],
                                   conditions.get("pressure_ref_pa", 101325.), conditions.get("temperature_K", 288.15))
    path = workspace / "CFD_2D" / slug(name)
    path.mkdir(parents=True, exist_ok=False)
    write_json(path / "cfd_case.json", new_manifest("cfd_case", name, physical=physical, physical_revision=digest(physical)))
    (path / "Geometries").mkdir()
    return path


def list_entities(parent: Path, marker: str) -> list[Path]:
    return sorted(p.parent for p in Path(parent).glob(f"*/{marker}") if p.is_file())


def geometry_context(geometry_dir: Path) -> dict:
    geometry_dir = geometry_dir.resolve()
    manifest = read_json(geometry_dir / "geometry.json")
    cfd = geometry_dir.parent.parent
    case = read_json(cfd / "cfd_case.json")
    if not manifest or not case:
        raise ValueError("Geometría sin caso CFD o manifiesto.")
    return {"geometry_dir": str(geometry_dir), "cfd_case_dir": str(cfd),
            "geometry_id": manifest["id"], "geometry_revision": digest({"profile": file_digest(geometry_dir / "Inputs/profile/profile_points.csv"), "base": file_digest(geometry_dir / "Inputs/base/profile_points.csv") if (geometry_dir / "Inputs/base/profile_points.csv").is_file() else None}),
            "cfd_case_id": case["id"], "physical_revision": digest(case["physical"]),
            "physical": case["physical"], "topology": manifest["topology"]}


def bind_mesh_config(config: dict, geometry_dir: Path) -> dict:
    context = geometry_context(geometry_dir)
    geometry_dir = Path(context["geometry_dir"])
    data = copy.deepcopy(config)
    p = context["physical"]
    g = data.setdefault("geometry", {})
    g.update(reynolds=p["reynolds"], rho_kg_m3=p["rho"], mu_pa_s=p["mu"],
             velocity_m_s=p["velocity_m_s"], chord_m=1., input_directory=str(geometry_dir / "Inputs"),
             preserve_processed_te=True)
    g.update({"open_variant": "profile", "base_variant": "base"} if context["topology"] == "open" else {"closed_variant": "profile"})
    g.setdefault("spanwise_thickness_chord", .01)
    g.setdefault("spanwise_layers", 1)
    if int(g["spanwise_layers"]) != 1:
        raise ValueError("El caso CFD 2D requiere exactamente una capa transversal para frontAndBack empty.")
    data["_context"] = context
    data["topology"] = context["topology"]
    e = data.setdefault("execution", {})
    e.update(max_cells=2_000_000)
    e.setdefault("gmsh_timeout_s", 900)
    e.setdefault("openfoam_timeout_s", 900)
    e.setdefault("minimum_cells_warning", 10000)
    data.setdefault("wake_refinement", {"enabled": False, "size_chord": .01, "length_chord": 5., "half_width_chord": .15})
    return data


def parametric_mesh_config(config: dict) -> dict:
    data = copy.deepcopy(config)
    data.pop("_context", None)
    data["geometry"] = {k: v for k, v in data.get("geometry", {}).items() if k in {"spanwise_thickness_chord", "spanwise_layers"}}
    for key in ("optimization_base_revision",):
        data.get("execution", {}).pop(key, None)
    data["schema_version"] = 3
    return data


def save_mesh_preset(root: Path, config: dict, name: str, refinement: str, overwrite: bool = False) -> Path:
    data = parametric_mesh_config(config)
    topology = data.get("topology") or ("open" if "internal_volume" in data else "closed")
    radius = float(data["external_volume"]["domain_radius_chord"])
    filename = f"{topology}_{slug(refinement)}_R{radius:g}c_{slug(name)}.json"
    target = root / "Mesh Presets" / filename
    if target.exists() and not overwrite:
        raise FileExistsError("Ya existe ese preset. Selecciona sobrescribir o usa otro nombre.")
    if target.exists():
        backup = root / "Mesh Presets/.history" / f"{target.stem}_{time.time_ns()}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
    write_json(target, data)
    from ramair_workspace_storage import publish_shared
    publish_shared(root, target)
    return target


@lru_cache(maxsize=128)
def _cached_evidence_hash(path: str, size: int, modified_ns: int) -> str:
    return file_digest(Path(path))


def mesh_evidence(mesh: Path) -> dict:
    from ramair_2d_openfoam_case_writer import find_converted_polymesh
    poly = find_converted_polymesh(mesh)
    if poly is None:
        raise ValueError("Falta la malla convertida de OpenFOAM.")
    paths = [mesh / "mesh_final.msh", mesh / "mesh_config.json", *[poly / name for name in ("points", "faces", "owner", "neighbour", "boundary")]]
    result = {}
    for path in paths:
        stat = path.stat()
        result[str(path.relative_to(mesh))] = _cached_evidence_hash(str(path), stat.st_size, stat.st_mtime_ns)
    return result


def compatible_mesh(mesh: Path, geometry_dir: Path, require_approval: bool = True) -> tuple[bool, str]:
    report = read_json(mesh / "mesh_report.json")
    cfg = read_json(mesh / "mesh_config.json")
    stored = cfg.get("_context", {})
    current = geometry_context(geometry_dir)
    for key in ("geometry_id", "geometry_revision", "cfd_case_id", "physical_revision"):
        if stored.get(key) != current.get(key):
            return False, "Configuración de otro contexto: es necesario regenerar la malla."
    if report.get("checkMesh_status") != "OK":
        return False, "Requiere checkMesh OK."
    if not (mesh / "mesh_final.msh").is_file():
        return False, "Falta la malla."
    if require_approval and not (mesh / "MESH_APPROVED.flag").is_file():
        return False, "Pendiente de aprobación de la malla."
    if require_approval:
        approved = read_json(mesh / "MESH_APPROVED.flag").get("content_evidence")
        try:
            if approved != mesh_evidence(mesh):
                return False, "La malla o su configuración cambió desde su aprobación."
        except (OSError, ValueError):
            return False, "Faltan archivos de la malla aprobada."
    return True, "Compatible"


def parse_angles(mode: str, values: str, start: float = 0, end: float = 16, step: float = 4) -> list[float]:
    if mode == "range":
        if not all(math.isfinite(float(v)) for v in (start, end, step)) or step <= 0 or end < start:
            raise ValueError("Intervalo no válido: inicio ≤ fin y salto positivo.")
        if (end-start)/step > 1000:
            raise ValueError("Máximo 1001 ángulos por configuración.")
        result = [start+i*step for i in range(int(math.floor((end-start)/step+1e-9))+1)]
    else:
        result = [float(v) for v in re.split(r"[;,\s]+", values.strip()) if v]
    if not result or any(not math.isfinite(v) or abs(v) > 180 for v in result):
        raise ValueError("Introduce ángulos finitos entre -180 y 180 grados.")
    if any(abs(v-round(v, 3)) > 1e-9 for v in result):
        raise ValueError("Los ángulos admiten como máximo tres decimales.")
    return sorted(set(round(v, 3) for v in result))


def alpha_name(alpha: float) -> str:
    return f"alpha_{alpha:+.3f}".replace("+", "p").replace("-", "m").replace(".", "p")


def solver_defaults(root: Path) -> tuple[dict, dict]:
    from ramair_2d_ls1_validation_study import validation_solver_profile, validation_phase_plan, STUDY_NAME
    base = read_json(root / "CFD_2D/CFD_2D_inputs/config/cfd2d_solver_config.json")
    base.update(read_json(root / "CFD_2D/validation_studies" / STUDY_NAME / "configurations/cfd2d_solver_config.json"))
    solver = validation_solver_profile(base)
    solver.pop("topology_profiles", None)
    # Defaults come from validation; general cases may subsequently customize them.
    solver.pop("validation_polar_protocol", None)
    solver["workspace_solver_contract"] = True
    return solver, validation_phase_plan()


def create_solver_setup(root: Path, mesh: Path, name: str, solver: dict, plan: dict, angles: list[float]) -> Path:
    geometry_dir = mesh.parent.parent
    ok, reason = compatible_mesh(mesh, geometry_dir)
    if not ok:
        raise ValueError(reason)
    setup = mesh / "OpenFOAM" / slug(name)
    setup.mkdir(parents=True, exist_ok=False)
    context = geometry_context(geometry_dir)
    write_json(setup / "solver_setup.json", new_manifest("solver_setup", name, context=context,
               mesh=str(mesh), angles=angles, mesh_configuration_revision=digest(parametric_mesh_config(read_json(mesh / "mesh_config.json")))))
    write_json(setup / "solver_config.json", solver)
    write_json(setup / "phase_plan.json", plan)
    (setup / "Angles").mkdir()
    return setup


def prepare_solver_cases(root: Path, setup: Path) -> list[Path]:
    from ramair_2d_openfoam_case_writer import write_case
    manifest = read_json(setup / "solver_setup.json")
    mesh = Path(manifest["mesh"])
    geometry = mesh.parent.parent
    ok, reason = compatible_mesh(mesh, geometry)
    if not ok:
        raise ValueError(reason)
    context = geometry_context(geometry)
    if context != manifest["context"]:
        raise ValueError("El contexto del caso OpenFOAM cambió; crea otra configuración.")
    variant_manifest = {"chord_m": 1., "has_ram_air_opening_feature": context["topology"] == "open", "spanwise_thickness_chord": read_json(mesh / "mesh_config.json")["geometry"]["spanwise_thickness_chord"]}
    paths = []
    for angle in manifest["angles"]:
        case = setup / "Angles" / alpha_name(angle)
        if (case / "case_config.json").exists():
            continue  # Never overwrite generated cases or checkpoints on a repeated click.
        paths.append(write_case(root, context["topology"], angle, require_converted_polymesh=True,
                               solver_config_path=setup / "solver_config.json", mesh_directory=mesh,
                               output_directory=case, physical_config=context["physical"],
                               geometry_manifest=variant_manifest))
    return paths


def all_solver_setups(workspace: Path) -> list[Path]:
    return sorted(p.parent for p in workspace.glob("CFD_2D/*/Geometries/*/Meshes/*/OpenFOAM/*/solver_setup.json"))


def comparable_setups(first: Path, second: Path) -> tuple[bool, str]:
    a = read_json(first / "solver_setup.json").get("context", {})
    b = read_json(second / "solver_setup.json").get("context", {})
    keys = ("cfd_case_id", "physical_revision")
    compatible = bool(a and b and all(a.get(k) == b.get(k) for k in keys))
    return compatible, "Mismas condiciones CFD" if compatible else "Los casos no comparten condiciones CFD e identidad del caso."


def export_products(setup: Path, destination: Path, categories: list[str]) -> dict:
    patterns = {"polars": ["Polar/**/*"], "coefficients": ["Angles/*/Postprocess/*coeff*.csv", "Angles/*/Postprocess/**/force*.csv", "Angles/*/Postprocess/**/case_summary.json"],
                "wall": ["Angles/*/Postprocess/**/*Cp*.csv", "Angles/*/Postprocess/**/*Cf*.csv", "Angles/*/Postprocess/**/*wall*.csv", "Angles/*/Postprocess/**/*yplus*.csv"],
                "images": ["Angles/*/Postprocess/**/*.png"], "animations": ["Angles/*/Postprocess/**/*.gif", "Angles/*/Postprocess/**/*.mp4"]}
    destination = destination.resolve()
    if destination == setup.resolve() or destination.is_relative_to(setup.resolve()):
        raise ValueError("El destino debe estar fuera de la ejecución original.")
    copied = []
    for category in categories:
        for pattern in patterns[category]:
            for source in setup.glob(pattern):
                if source.is_file():
                    relative = source.relative_to(setup)
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    if file_digest(source) != file_digest(target):
                        raise IOError(f"Falló la verificación de {relative}")
                    copied.append(str(relative))
    return dict(write_manifest=str(write_json(destination / "retention_manifest.json", {"source": str(setup), "files": sorted(set(copied)), "source_preserved": True})), files=len(set(copied)))
