#!/usr/bin/env python3
"""Single-source parallel execution policy for RamAir OpenFOAM cases."""
from __future__ import annotations

import math
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable


CELL_PATTERNS = (
    re.compile(r"\bcells:\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bNumber of cells\s*=\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bnCells\s*[=:]\s*(\d+)", re.IGNORECASE),
)

PRACTICAL_RANKS = (1, 2, 3, 4, 6, 8, 12, 16)


def linux_parallel_preflight(case: Path | None = None) -> dict[str, Any]:
    """Collect the non-invasive hardware/runtime evidence used before a run."""
    physical: set[tuple[int, int]] = set()
    logical = os.cpu_count() or 1
    try:
        completed = subprocess.run(
            ["lscpu", "-p=CORE,SOCKET"], text=True, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=5, check=False,
        )
        physical = {
            tuple(int(item) for item in line.split(",")[:2])
            for line in completed.stdout.splitlines()
            if line and not line.startswith("#") and "," in line
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    memory: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            match = re.search(r"(\d+)", value)
            if match:
                memory[key] = int(match.group(1)) * 1024
    except OSError:
        pass
    load = os.getloadavg() if hasattr(os, "getloadavg") else (math.nan,) * 3
    case_path = Path(case).resolve() if case is not None else None
    filesystem = None
    filesystem_native_linux = None
    if case_path is not None:
        try:
            completed = subprocess.run(
                ["findmnt", "-T", str(case_path), "-n", "-o", "FSTYPE,SOURCE,TARGET"],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=5, check=False,
            )
            filesystem = completed.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            pass
        filesystem_native_linux = not str(case_path).startswith("/mnt/")
    return {
        "platform": platform.platform(),
        "openfoam_version": os.environ.get("WM_PROJECT_VERSION"),
        "openfoam_root": os.environ.get("WM_PROJECT_DIR"),
        "openmpi": shutil.which("mpirun"),
        "logical_cpus": int(logical),
        "physical_cores": len(physical) or max(1, int(logical // 2)),
        "load_average_1_5_15": [float(value) for value in load],
        "ram_total_bytes": memory.get("MemTotal"),
        "ram_available_bytes": memory.get("MemAvailable"),
        "swap_total_bytes": memory.get("SwapTotal"),
        "swap_free_bytes": memory.get("SwapFree"),
        "case_path": str(case_path) if case_path else None,
        "filesystem": filesystem,
        "native_linux_filesystem": filesystem_native_linux,
        "preflight_timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def practical_rank_candidates(
    cell_count: int,
    *,
    physical_cores: int,
    minimum_cells_per_rank: int = 50_000,
    maximum_cells_per_rank: int = 200_000,
) -> list[int]:
    """Return practical ranks in/near the screening envelope, including one upper probe."""
    cells = max(1, int(cell_count))
    cap = max(1, int(physical_cores))
    lower = max(1, math.ceil(cells / max(1, maximum_cells_per_rank)))
    upper = min(cap, max(1, math.floor(cells / max(1, minimum_cells_per_rank))))
    available = [value for value in PRACTICAL_RANKS if value <= cap]
    selected = [value for value in available if lower <= value <= upper]
    if not selected:
        selected = [min(available, key=lambda value: abs(cells / value - 100_000))]
    higher = [value for value in available if value > max(selected)]
    if higher:
        selected.append(higher[0])
    if 1 not in selected and cells < 100_000:
        selected.insert(0, 1)
    return sorted(set(selected))


def mesh_identity_hash(case: Path) -> str:
    """Hash mesh topology files without reading transient fields."""
    digest = hashlib.sha256()
    poly = Path(case) / "constant" / "polyMesh"
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        path = poly / name
        digest.update(name.encode("ascii"))
        if not path.is_file():
            digest.update(b"MISSING")
            continue
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def parallel_profile_key(
    case: Path,
    *,
    solver: str,
    stage: str,
    numerical_signature: str = "",
) -> str:
    preflight = linux_parallel_preflight(case)
    payload = {
        "mesh_sha256": mesh_identity_hash(case),
        "solver": str(solver),
        "stage": str(stage).upper(),
        "numerical_signature": str(numerical_signature),
        "openfoam": preflight.get("openfoam_version"),
        "mpi": preflight.get("openmpi"),
        "physical_cores": preflight.get("physical_cores"),
        "platform": preflight.get("platform"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def load_parallel_profile(cache_path: Path, key: str) -> dict[str, Any] | None:
    try:
        cache = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = dict(cache.get("profiles", {})).get(str(key))
    return dict(value) if isinstance(value, dict) else None


def store_parallel_profile(cache_path: Path, key: str, profile: dict[str, Any]) -> None:
    path = Path(cache_path)
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache = {"schema_version": 1, "profiles": {}}
    cache.setdefault("profiles", {})[str(key)] = {
        **profile,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def select_benchmark_winner(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Select minimum projected wall-time; prefer fewer ranks inside a 5% tie."""
    valid = [
        item for item in candidates
        if math.isfinite(float(item.get("projected_wall_time_s", math.inf)))
        and not bool(item.get("rejected"))
    ]
    if not valid:
        raise ValueError("No valid parallel benchmark candidates")
    valid.sort(key=lambda item: float(item["projected_wall_time_s"]))
    best = valid[0]
    threshold = float(best["projected_wall_time_s"]) * 1.05
    tied = [item for item in valid if float(item["projected_wall_time_s"]) <= threshold]
    return min(tied, key=lambda item: int(item.get("ranks", 1)))


def case_cell_count(case: Path) -> tuple[int | None, str | None]:
    """Read the exact mesh count from current case evidence."""
    case = Path(case)
    for name in ("log.checkMesh.preRun", "log.checkMesh", "log.checkMesh.caseBuild"):
        path = case / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in CELL_PATTERNS:
            matches = pattern.findall(text)
            if matches:
                return int(matches[-1]), str(path)
    owner = case / "constant" / "polyMesh" / "owner"
    if owner.is_file():
        header = owner.read_text(encoding="utf-8", errors="ignore")[:12000]
        match = re.search(r"\bnCells\s*:\s*(\d+)", header)
        if match:
            return int(match.group(1)), str(owner)
    return None, None


def recommended_core_count(
    cell_count: int | None,
    *,
    available_slots: int | None,
    requested_maximum: int,
    target_cells_per_core: int = 100_000,
    minimum_cells_per_core: int = 50_000,
    maximum_cells_per_core: int = 200_000,
) -> dict[str, Any]:
    """Choose ranks inside the efficient single-node cells/core envelope."""
    requested_maximum = max(1, int(requested_maximum))
    slot_cap = max(1, int(available_slots)) if available_slots else requested_maximum
    cap = min(requested_maximum, slot_cap)
    if not cell_count or int(cell_count) <= 0:
        ranks = min(4, cap)
        reason = "cell_count_unavailable_conservative_fallback"
    else:
        cells = int(cell_count)
        efficient_upper = max(1, cells // max(1, minimum_cells_per_core))
        cap = min(cap, efficient_upper)
        minimum_ranks = max(1, math.ceil(cells / max(1, maximum_cells_per_core)))
        target_ranks = max(1, int(round(cells / max(1, target_cells_per_core))))
        ranks = min(cap, max(minimum_ranks, target_ranks))
        reason = "nearest_100k_cells_per_rank_bounded_50k_to_200k"
    cells_per_rank = (float(cell_count) / ranks) if cell_count else None
    return {
        "selection_mode": "automatic",
        "cell_count": int(cell_count) if cell_count else None,
        "requested_maximum_ranks": requested_maximum,
        "available_mpi_slots": available_slots,
        "recommended_ranks": ranks,
        "cells_per_rank": cells_per_rank,
        "target_cells_per_rank": target_cells_per_core,
        "efficient_range_cells_per_rank": [minimum_cells_per_core, maximum_cells_per_core],
        "reason": reason,
        "decomposition_method": "scotch" if ranks > 1 else "serial",
    }


def configure_decompose_dictionary(path: Path, ranks: int, method: str = "scotch") -> None:
    """Set rank count and decomposition method atomically in one dictionary."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Missing decomposition dictionary: {path}")
    text = path.read_text(encoding="utf-8", errors="ignore")
    count_line = f"numberOfSubdomains {max(1, int(ranks))};"
    method_line = f"method {str(method).strip()};"
    if re.search(r"\bnumberOfSubdomains\s+\d+\s*;", text):
        text = re.sub(r"\bnumberOfSubdomains\s+\d+\s*;", count_line, text, count=1)
    else:
        text += "\n" + count_line + "\n"
    if re.search(r"\bmethod\s+\w+\s*;", text):
        text = re.sub(r"\bmethod\s+\w+\s*;", method_line, text, count=1)
    else:
        text += method_line + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def reconstruction_command(
    mode: str,
    *,
    time_range: str | None = None,
    fields: Iterable[str] | None = None,
) -> str:
    """Return an explicit reconstructPar policy command."""
    mode = str(mode).strip().lower()
    if mode == "latest":
        return "reconstructPar -latestTime"
    if mode == "all":
        return "reconstructPar"
    if mode == "time_range":
        if not time_range or not str(time_range).strip():
            raise ValueError("time_range reconstruction requires a non-empty OpenFOAM time selector")
        safe = str(time_range).replace("'", "'\\''")
        return f"reconstructPar -time '{safe}'"
    if mode == "fields":
        names = [str(value).strip() for value in fields or () if str(value).strip()]
        if not names:
            raise ValueError("fields reconstruction requires at least one field")
        return "reconstructPar -fields '(" + " ".join(names) + ")'"
    raise ValueError(f"Unsupported reconstruction mode: {mode}")


def processor_directory_audit(case: Path, expected_ranks: int) -> dict[str, Any]:
    case = Path(case)
    indices = sorted(
        int(path.name.removeprefix("processor"))
        for path in case.glob("processor*")
        if path.is_dir() and re.fullmatch(r"processor\d+", path.name)
    )
    expected = list(range(max(0, int(expected_ranks))))
    return {
        "expected_ranks": int(expected_ranks),
        "processor_directories": indices,
        "count": len(indices),
        "matches_expected": indices == expected,
        "missing": sorted(set(expected).difference(indices)),
        "unexpected": sorted(set(indices).difference(expected)),
    }


def decompose_load_balance(log_path: Path) -> dict[str, Any]:
    """Extract Scotch cell populations and report imbalance when available."""
    path = Path(log_path)
    if not path.is_file():
        return {"status": "NOT_AVAILABLE", "source": str(path)}
    text = path.read_text(encoding="utf-8", errors="ignore")
    populations_by_rank: dict[int, int] = {}
    for processor, block in re.findall(
        r"Processor\s+(\d+)\s*(.*?)(?=Processor\s+\d+|$)", text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        match = re.search(r"Number of cells\s*=\s*(\d+)", block, re.IGNORECASE)
        if match:
            populations_by_rank[int(processor)] = int(match.group(1))
    populations = [populations_by_rank[key] for key in sorted(populations_by_rank)]
    if not populations:
        return {"status": "NOT_PARSED", "source": str(path)}
    mean = sum(populations) / len(populations)
    maximum_deviation = max(abs(value - mean) for value in populations)
    imbalance = 100.0 * maximum_deviation / mean if mean else math.inf
    return {
        "status": "OK" if imbalance <= 20.0 else "WARNING",
        "source": str(path),
        "cells_by_rank": populations,
        "mean_cells": mean,
        "maximum_deviation_percent": imbalance,
        "within_five_percent": imbalance <= 5.0,
        "within_twenty_percent": imbalance <= 20.0,
    }
