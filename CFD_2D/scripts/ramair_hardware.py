"""Live host inventory and campaign throughput with explicit measurement windows."""
from pathlib import Path
import os
import platform
import shutil
import subprocess
import time


def hardware_snapshot(root: Path) -> dict:
    from ramair_2d_parallel import linux_parallel_preflight
    inventory = linux_parallel_preflight(root)
    def cpu_ticks():
        try:
            values = list(map(int, Path("/proc/stat").read_text().splitlines()[0].split()[1:9]))
            return sum(values), values[3]+values[4]
        except (OSError, ValueError, IndexError): return (0, 0)
    before = cpu_ticks()
    time.sleep(.1)
    after = cpu_ticks()
    cpu = 100*(1-(after[1]-before[1])/max(1,after[0]-before[0])) if after[0]>before[0] else float("nan")
    disk = shutil.disk_usage(root)
    model = platform.processor()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        model = next((s.split(":",1)[1].strip() for s in cpuinfo.read_text().splitlines() if s.startswith("model name")), model)
    def version(command):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=3)
            return next((line for line in result.stdout.splitlines() if line.strip()), "No disponible")
        except (OSError, subprocess.TimeoutExpired): return "No disponible"
    foam = os.environ.get("WM_PROJECT_VERSION")
    if not foam:
        installed = Path.home()/".local/opt"
        names = [p.name for p in installed.glob("openfoam*") if (p/"etc/bashrc").exists()]
        foam = ", ".join(names) or "No detectado"
    mounts = Path("/proc/mounts")
    storage = platform.system()
    if mounts.exists():
        candidates = []
        for line in mounts.read_text().splitlines():
            parts = line.split()
            if len(parts)>2 and (str(root.resolve())+"/").startswith(parts[1].rstrip("/")+"/"):
                candidates.append((len(parts[1]), parts[2]))
        if candidates: storage = max(candidates)[1]
    return {"cpu_model":model,"physical_cores":inventory["physical_cores"],
            "logical_cores":inventory["logical_cpus"],"cpu_percent":cpu,
            "ram_total_gib":float(inventory.get("ram_total_bytes") or float("nan"))/2**30,
            "ram_available_gib":float(inventory.get("ram_available_bytes") or float("nan"))/2**30,
            "disk_free_gib":disk.free/2**30,"storage":storage,"root":str(root.resolve()),
            "mpi":version(["mpirun","--version"]),"openfoam":foam,"platform":platform.platform()}


def aggregate_throughput(runs: list[dict], started: float, finished: float) -> dict:
    """Sum both cases' completed steps over the same campaign wall interval.

    Per-case steps/s must not be averaged: their windows can differ. Include
    startup and finalization in campaign elapsed; report solver-only separately.
    """
    elapsed = float(finished)-float(started)
    if elapsed <= 0: raise ValueError("Measurement interval must be positive")
    steps = sum(max(0, int(r["final_iteration"])-int(r["initial_iteration"])) for r in runs)
    return {"completed_case_steps":steps,"campaign_wall_time_s":elapsed,
            "aggregate_case_steps_per_s":steps/elapsed,"case_count":len(runs),
            "formula":"sum(final_iteration - initial_iteration) / common campaign elapsed seconds"}
