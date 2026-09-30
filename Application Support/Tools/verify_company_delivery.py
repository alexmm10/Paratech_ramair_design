"""Repeatable, bounded company-delivery acceptance on native Linux/WSL.

Creates an isolated workspace; never changes existing scientific campaigns.
CAD is prepared with Python only. Solver execution requires the 'run' action.
"""
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"CFD_2D/scripts"), str(ROOT/"CFD_2D/app")]
from ramair_workspace import *
from ramair_profile_library import process_profile, import_geometry
from ramair_workspace_jobs import catia_preprocess, mesh_job, postprocess_cases
from ramair_rans_mesh_study import create_study, prepare_queue, analyze


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "mesh", "run", "postprocess", "cad"])
    p.add_argument("--workspace", default="Company_Delivery_"+time.strftime("%Y%m%d"))
    p.add_argument("--report", type=Path)
    args = p.parse_args()
    workspace = ROOT/"Workspaces"/args.workspace
    report = args.report or ROOT/"Application Support/Reports"/args.workspace
    report.mkdir(parents=True, exist_ok=True)
    evidence = read_json(report/"workflow.json")
    if args.action == "prepare":
        create_workspace(ROOT, args.workspace)
        profile = process_profile(ROOT, ROOT/"Airfoil Profiles/NASA LS1-0417.dat", args.workspace+"_LS1", "rounded", reference=True, topology="closed")
        cfd = create_cfd_case(workspace, "Re1900000_M015", physical_conditions())
        geom = import_geometry(cfd, profile, "LS1_closed")
        model = workspace/"CATIA/Prepared_model"
        model.mkdir(parents=True)
        cfg = read_json(ROOT/"Application Support/Configurations/default_case_config.json")
        cfg["profile_inputs"] = {"main_profile": str(next(profile.glob("*.dat"))), "profile_input_order": "auto"}
        cfg["optional_modules"]["system_config_json"] = str(model/"system_config.json")
        cfg["debug_plots"] = {k: True for k in ("write_chord_distribution_plot", "write_cell_span_plot", "write_anhedral_plot", "write_crossports_plot", "write_stabilizer_plot", "write_suspension_plot")}
        system = read_json(ROOT/"Application Support/Configurations/ramair_catia_system_config.json")
        from ramair_suspension_layout import migrate
        system = migrate(system, new=True)
        system["brakes"]["active"] = True
        for b in system["banks"]:
            if b["name"] == "BRK": b["enabled"] = True
        write_json(model/"system_config.json", system)
        write_json(model/"model_config.json", cfg)
        write_json(model/"model.json", new_manifest("catia_model", "Prepared_model"))
        shutil.copy2(ROOT/"Generate_RamAir_Canopy_MAIN.CATScript", model)
        evidence.update(workspace=str(workspace), profile=str(profile), geometry=str(geom), catia_executed=False)
        write_json(report/"workflow.json", evidence)
    if args.action in {"prepare", "cad"}:
        model = workspace/"CATIA/Prepared_model"
        with (report/"cad.log").open("w") as log:
            result = subprocess.run([sys.executable, ROOT/"CFD_2D/scripts/ramair_workspace_jobs.py", "catia", "--root", ROOT, "--path", model], stdout=log, stderr=subprocess.STDOUT, timeout=180)
        evidence["cad_returncode"] = result.returncode
        write_json(report/"workflow.json", evidence)
        if result.returncode: raise RuntimeError("CAD preparation failed; see cad.log")
    if args.action == "mesh":
        geom = Path(evidence["geometry"])
        evidence.setdefault("meshes", {})
        for level in ("coarse", "medium"):
            name = "Delivery_"+level
            config = bind_mesh_config(read_json(ROOT/f"Mesh Presets/Standard_20260911/Standard_closed_{level}_20260911.json"), geom)
            config["execution"].update(gmsh_timeout_s=180, openfoam_timeout_s=180)
            path = write_json(geom/(name+".json"), config)
            cmd = [sys.executable, ROOT/"CFD_2D/scripts/ramair_workspace_jobs.py", "mesh", "--root", ROOT, "--path", geom/"Meshes", "--config", path, "--name", name]
            with (report/(name+".log")).open("w") as log:
                result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=650)
            mesh = geom/"Meshes"/name
            quality = read_json(mesh/"mesh_report.json")
            evidence["meshes"][level] = {"returncode": result.returncode, "checkMesh": quality.get("checkMesh_status"),
                "cells": quality.get("total_2d_cells"), "mesh": str(mesh)}
            write_json(report/"workflow.json", evidence)
            if result.returncode or quality.get("checkMesh_status") != "OK": raise RuntimeError(name+" requires mesh correction; see log")
            from ramair_2d_closed_experimental_mesh import approve
            approve(ROOT, name, geom/"Meshes")
        solver, plan = solver_defaults(ROOT)
        solver.update(steady_max_iterations=80, steady_write_interval_iterations=20, workflow_test={"purpose":"company delivery diagnostic", "scientific_acceptance":False})
        solver["execution_controls"] = {"phase_action":"rans_only", "backend":"native", "timeout_min":10., "steady_timeout_min":10., "steady_additional_iterations":20,
            "renumber_before_decompose":False, "steady_potential_foam":True}
        meshes = [Path(evidence["meshes"][level]["mesh"]) for level in ("coarse", "medium")]
        reference = create_solver_setup(ROOT, meshes[0], "Delivery_RANS", solver, plan, [0., 8.])
        study = create_study(ROOT, geom, meshes, reference, [0., 8.], "Delivery_RANS_2x2")
        evidence["study"] = str(study)
        write_json(report/"workflow.json", evidence)
    if args.action == "run":
        study = Path(evidence["study"])
        queue = prepare_queue(ROOT, study, cores=1, concurrent=2, run=True)
        evidence["queue"] = str(queue)
        write_json(report/"workflow.json", evidence)
        with (report/"queue.log").open("a") as log:
            result = subprocess.run([sys.executable, ROOT/"CFD_2D/scripts/ramair_2d_parallel_campaign.py", "--manifest", queue], stdout=log, stderr=subprocess.STDOUT, timeout=2700)
        evidence["queue_returncode"] = result.returncode
        write_json(report/"workflow.json", evidence)
    if args.action == "postprocess":
        study = Path(evidence["study"])
        entries = read_json(study/"study.json")["entries"]
        geom = Path(evidence["geometry"])
        results = {}
        for relative in sorted({r["setup"] for r in entries}):
            results[relative] = postprocess_cases(ROOT, geom/relative, [r["alpha"] for r in entries if r["setup"] == relative], {})
        evidence["postprocess"] = results
        analyze(study)
        write_json(report/"workflow.json", evidence)
    print(json.dumps(evidence, indent=2), flush=True)


if __name__ == "__main__": main()
