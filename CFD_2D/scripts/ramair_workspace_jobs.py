"""Bounded stage commands for the workspace UI; solvers require --run."""
from __future__ import annotations
import argparse
import copy
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from ramair_workspace import read_json, write_json, bind_mesh_config, prepare_solver_cases, alpha_name


def mesh_job(root: Path, config_path: Path, output: Path, name: str) -> int:
    config = read_json(config_path)
    config = bind_mesh_config(config, Path(config["_context"]["geometry_dir"]))
    write_json(config_path, config)
    topology = config["topology"]
    command = [sys.executable, str(root / f"CFD_2D/scripts/ramair_2d_{topology}_experimental_mesh.py"),
               "--project-root", str(root), "--generate", "--config", str(config_path),
               "--name", name, "--output-dir", str(output), "--check-mesh"]
    timeout = int(config["execution"]["gmsh_timeout_s"]) + int(config["execution"]["openfoam_timeout_s"])*2 + 90
    process = subprocess.Popen(command, start_new_session=os.name != "nt")
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=20)
        report_path = output / name / "mesh_report.json"
        report = read_json(report_path)
        report.update(status="FAILED", error=f"Tiempo máximo de generación agotado ({timeout}s)", checkMesh_status="TIMEOUT")
        write_json(report_path, report)
        return 124


def execution_command(root: Path, setup: Path, case: Path, cores: int, run: bool, resume: bool = True, options: dict | None = None) -> list[str]:
    solver = read_json(setup / "solver_config.json")
    execution = {"backend": "native", "timeout_min": 1440., "steady_timeout_min": 1440.,
                 "stop_grace_min": 5., "renumber_before_decompose": False, "steady_potential_foam": True,
                 "steady_additional_iterations": 500, "phase_action": "continue", **(options or solver.get("execution_controls", {}))}
    gate = solver.get("validation_rans_convergence", {})
    command = [sys.executable, str(root / "CFD_2D/scripts/ramair_2d_openfoam_staged_runner.py"),
               "--case", str(case), "--solver", str(solver.get("solver", "foamRun")),
               "--n-cores", str(cores), "--no-automatic-core-selection", "--steady-initialization",
               "--transient-phase-plan", str(setup / "phase_plan.json"),
               "--execution-backend", str(execution["backend"]),
               "--timeout-min", str(execution["timeout_min"]), "--steady-timeout-min", str(execution["steady_timeout_min"]),
               "--stop-grace-min", str(execution["stop_grace_min"]), "--stop-mode", "writeNow",
               "--steady-additional-iterations", str(int(execution["steady_additional_iterations"])),
               "--renumber-before-decompose" if execution["renumber_before_decompose"] else "--no-renumber-before-decompose",
               "--steady-potential-foam" if execution["steady_potential_foam"] else "--no-steady-potential-foam",
               "--no-steady-pyfoam-live-monitor",
               "--steady-force-window-samples", str(gate.get("window_samples", 1000)),
               "--steady-force-mean-tolerance-percent", str(gate.get("mean_change_tolerance_percent", .5)),
               "--steady-force-fluctuation-tolerance-percent", str(gate.get("fluctuation_tolerance_percent", 1.))]
    if run:
        command.append("--run")
    if read_json(case / "reset_manifest.json").get("rans_initialization_preserved"):
        command[command.index("--steady-initialization")] = "--no-steady-initialization"
    if resume:
        command.append("--resume")
    pending = (case / "steadyInitialization/pending_stage.json").is_file()
    if pending and execution.get("rans_end_iteration") is not None:
        from ramair_simulation_inventory import case_progress
        latest = case_progress(case)["rans_iteration"]
        remaining = int(execution["rans_end_iteration"]) - latest
        if remaining <= 0:
            raise ValueError("El caso ya alcanzó el límite RANS seleccionado. Aumenta el límite o archiva y reinicia el ángulo.")
        command[command.index("--steady-additional-iterations")+1] = str(remaining)
    if execution["phase_action"] == "rans_only":
        command.append("--steady-only")
    if pending and execution["phase_action"] in {"continue", "rans_only"}:
        command += ["--steady-decision", "extend"]
    if execution["phase_action"] == "start_urans":
        if not (case / "steadyInitialization/pending_stage.json").is_file():
            raise ValueError("Iniciar URANS desde RANS requiere un checkpoint RANS pendiente en este ángulo.")
        command += ["--steady-decision", "start-transient"]
    return command


def queue_manifest(root: Path, setup: Path, angles: list[float], cores: int, concurrent: int, run: bool, *, cores_by_angle: dict | None = None, options: dict | None = None) -> Path:
    budget = min(8, os.cpu_count() or 1)
    if not 1 <= cores <= budget or not 1 <= concurrent <= 8:
        raise ValueError(f"La cola admite de 1 a {budget} cores por caso.")
    cases = []
    for alpha in angles:
        case = setup / "Angles" / alpha_name(alpha)
        if not (case / "case_config.json").is_file():
            raise FileNotFoundError(f"Ángulo sin preparar: {alpha}")
        count = int((cores_by_angle or {}).get(alpha, cores))
        if not 1 <= count <= budget: raise ValueError(f"Recursos inválidos para α={alpha}: {count}")
        cases.append({"case_id": case.name, "case_path": str(case), "n_cores": count, "alpha_deg": alpha,
                      "command": execution_command(root, setup, case, count, run, options=options)})
    path = setup / "Queues" / f"queue_{time.time_ns()}.json"
    return write_json(path, {"schema_version": 1, "campaign_id": path.stem, "project_root": str(root),
                     "total_core_budget": budget, "max_concurrent_cases": concurrent, "execution_controls": options or {}, "cases": cases})


def postprocess_cases(root: Path, setup: Path, angles: list[float], options: dict) -> list[dict]:
    from ramair_2d_postprocess import postprocess
    manifest = read_json(setup / "solver_setup.json")
    results = []
    for alpha in angles:
        case = setup / "Angles" / alpha_name(alpha)
        try:
            result = postprocess(root, manifest["context"]["topology"], alpha,
                float(read_json(setup / "phase_plan.json").get("average_from_fraction", 14/64)),
                direct_case_dir=case, direct_output_dir=case / "Postprocess", simulation_mode="AUTO", **options)
            summary = read_json(case / "Postprocess/case_summary.json")
            from ramair_workspace import file_digest
            force_sources = set(case.glob("postProcessing/forceCoeffs/**/forceCoeffs.dat"))
            force_sources.update(case.glob("steadyInitialization/history/**/forceCoeffs.dat"))
            write_json(case/"Postprocess/input_evidence.json", {"schema_version": 1,
                "rans_force_files": {str(p.relative_to(case)): file_digest(p) for p in sorted(force_sources)},
                "postprocessed_at": time.time()})
            smoke = read_json(setup / "solver_config.json").get("workflow_test")
            if smoke:
                for summary_file in (case / "Postprocess").glob("**/case_summary.json"):
                    item = read_json(summary_file)
                    item.update(workflow_test=smoke, processing_status=item.get("status"), status="PROCESSED_PARTIAL")
                    write_json(summary_file, item)
                summary = read_json(case / "Postprocess/case_summary.json")
            status = str(summary.get("status", "POSTPROCESS_ERROR"))
            results.append({"alpha": alpha, "status": "FAILED" if status in {"POSTPROCESS_ERROR", "NO_RESULTS"} else status, "result": str(result)})
        except Exception as exc:
            results.append({"alpha": alpha, "status": "FAILED", "error": str(exc)})
        write_json(setup / "postprocess_queue.json", results)
    return results


def catia_preprocess(root: Path, model: Path) -> int:
    model = model.resolve()
    cfg = read_json(model / "model_config.json")
    cfg["cfd_2d_exports"] = {"enable_2d_cae_exports": False}
    cfg.pop("cfd_2d", None)
    cfg.setdefault("project_paths", {})["catia_inputs_dir"] = str(model / "Inputs")
    cfg["project_paths"]["catia_exports_dir"] = str(model / "Exports")
    cfg["profile_inputs"] = {k: v for k, v in cfg.get("profile_inputs", {}).items() if k in {"main_profile", "profile_input_order"}}
    write_json(model / "model_config.json", cfg)
    command = [sys.executable, str(root / "preprocess_ramair_main.py"), "--config", str(model / "model_config.json"),
               "--input-csv", str(cfg["profile_inputs"]["main_profile"]), "--out", str(model / "Inputs"), "--cad-only"]
    return subprocess.call(command, cwd=root)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["mesh", "prepare", "postprocess", "catia", "xfoil"])
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--config", type=Path)
    p.add_argument("--path", type=Path, required=True)
    p.add_argument("--name")
    p.add_argument("--angles", type=float, nargs="+")
    p.add_argument("--additional-path", type=Path, action="append", default=[])
    a = p.parse_args()
    if a.action == "mesh":
        return mesh_job(a.root, a.config, a.path, a.name)
    if a.action == "prepare":
        print(json.dumps([str(p) for setup in [a.path, *a.additional_path] for p in prepare_solver_cases(a.root, setup)]))
    elif a.action == "postprocess":
        rows = postprocess_cases(a.root, a.path, a.angles, read_json(a.config) if a.config else {})
        print(json.dumps(rows))
        return int(any(r["status"] == "FAILED" for r in rows))
    elif a.action == "catia":
        return catia_preprocess(a.root, a.path)
    elif a.action == "xfoil":
        from ramair_2d_inlet_designer import design_inlet
        from ramair_profile_library import process_profile
        from ramair_workspace import file_digest
        config = read_json(a.config)
        config["output_root"] = "Airfoil Profiles/Designs"
        write_json(a.config, config)
        result = design_inlet(a.root, a.config)
        repanelled = Path(result["metadata_path"]).parent / "repanelled_profile.dat"
        # The actual XFOIL panel contour is the exact uncut base of its cut.
        base_name = "XFOIL_base_" + file_digest(repanelled)[:12]
        base = a.root / "Airfoil Profiles/Reference" / (base_name + "_roundedTE")
        if not base.exists():
            base = process_profile(a.root, repanelled, base_name, "rounded", reference=True, topology="closed")
        metadata = read_json(Path(result["metadata_path"]))
        generated = Path(result["generated_profile"])
        import re
        gap_token = re.search(r"_Gap([0-9p]+)pct", generated.stem)
        name = (a.name or generated.stem).replace("{gap}", gap_token.group(1) if gap_token else "unknown")
        process_profile(a.root, a.root / generated, name, "rounded", base, topology="open")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
