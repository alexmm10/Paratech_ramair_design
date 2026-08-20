#!/usr/bin/env python3
"""Detect CATIA V5 on Windows and optionally launch the RamAir CATScript.

Detection is read-only and works both from native Windows and from the WSL
runtime used by the Streamlit application.  CATIA is never imported through
COM: the visible CATIA process is started only after an explicit ``--run``.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def is_wsl() -> bool:
    return sys.platform.startswith("linux") and (
        "microsoft" in os.uname().release.lower()
        or Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
    )


def powershell_executable() -> str | None:
    return shutil.which("powershell.exe") or shutil.which("pwsh.exe")


def decode_windows_stream(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16-le", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def run_powershell(script: str, timeout_s: int = 20) -> subprocess.CompletedProcess[str]:
    executable = powershell_executable()
    if executable is None:
        raise FileNotFoundError("powershell.exe was not found from WSL.")
    encoding_prefix = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new();"
        "$OutputEncoding = [Console]::OutputEncoding;"
    )
    encoded = base64.b64encode((encoding_prefix + script).encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_s,
        check=False,
    )
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        decode_windows_stream(completed.stdout),
        decode_windows_stream(completed.stderr),
    )


def windows_path(path: Path) -> str:
    resolved = path.resolve()
    if not is_wsl():
        return str(resolved)
    completed = subprocess.run(
        ["wslpath", "-w", str(resolved)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise RuntimeError(f"wslpath could not translate {resolved}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def windows_detection_script() -> str:
    return r"""
$ErrorActionPreference = 'SilentlyContinue'
$candidates = New-Object System.Collections.Generic.List[string]
if ($env:RAMAIR_CATIA_CNEXT) { $candidates.Add($env:RAMAIR_CATIA_CNEXT) }
$command = Get-Command CNEXT.exe -ErrorAction SilentlyContinue
if ($command) { $candidates.Add($command.Source) }
foreach ($registryPath in @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\CNEXT.exe',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\CNEXT.exe'
)) {
    $entry = Get-ItemProperty -LiteralPath $registryPath -ErrorAction SilentlyContinue
    if ($entry -and $entry.'(default)') { $candidates.Add($entry.'(default)') }
}
foreach ($programRoot in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if (-not $programRoot) { continue }
    foreach ($pattern in @(
        'Dassault Systemes\B*\win_b64\code\bin\CNEXT.exe',
        'Dassault Systemes\*\win_b64\code\bin\CNEXT.exe',
        'Dassault Systemes\B*\intel_a\code\bin\CNEXT.exe'
    )) {
        Get-Item -Path (Join-Path $programRoot $pattern) -ErrorAction SilentlyContinue |
            ForEach-Object { $candidates.Add($_.FullName) }
    }
}
$found = $candidates |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
    Select-Object -Unique -First 1
if ($found) {
    [ordered]@{available=$true; cnext=$found; source='windows'} |
        ConvertTo-Json -Compress
} else {
    [ordered]@{available=$false; cnext=$null; source='windows'; reason='CNEXT.exe not found'} |
        ConvertTo-Json -Compress
}
"""


def detect_catia() -> dict[str, Any]:
    configured = os.environ.get("RAMAIR_CATIA_CNEXT", "").strip()
    if not is_wsl():
        direct = configured or shutil.which("CNEXT.exe")
        if direct and Path(direct).is_file():
            return {"available": True, "cnext": str(Path(direct).resolve()), "source": "native_windows"}
        if sys.platform != "win32":
            return {
                "available": False,
                "cnext": None,
                "source": sys.platform,
                "reason": "CATIA V5 is supported only through Windows or WSL.",
            }
    try:
        completed = run_powershell(windows_detection_script())
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "cnext": None, "source": "detection", "reason": str(exc)}
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    if completed.returncode == 0 and lines:
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError:
            pass
    return {
        "available": False,
        "cnext": None,
        "source": "detection",
        "reason": completed.stderr.strip() or completed.stdout.strip() or "CATIA detection failed.",
    }


def launch_catia(project_root: Path, cnext: str | None = None) -> dict[str, Any]:
    project_root = project_root.resolve()
    macro = project_root / "Generate_RamAir_Canopy_MAIN.CATScript"
    inputs = project_root / "CATIA" / "Inputs"
    required_input = inputs / "ramair_global_inputs.csv"
    if not macro.is_file():
        raise FileNotFoundError(f"CATScript not found: {macro}")
    if not required_input.is_file():
        raise FileNotFoundError(
            f"CATIA inputs are not ready: {required_input}. Run the preprocessor first."
        )
    detected = detect_catia()
    executable = str(cnext or detected.get("cnext") or "")
    if not executable:
        raise FileNotFoundError(
            "CATIA V5 CNEXT.exe was not found. Set RAMAIR_CATIA_CNEXT to its full Windows path."
        )

    if is_wsl():
        win_project = windows_path(project_root)
        win_macro = windows_path(macro)
        win_inputs = windows_path(inputs)
        payload = json.dumps(
            {
                "cnext": executable,
                "project": win_project,
                "macro": win_macro,
                "inputs": win_inputs,
            },
            ensure_ascii=False,
        )
        script = rf"""
$ErrorActionPreference = 'Stop'
$cfg = ConvertFrom-Json @'
{payload}
'@
$env:RAMAIR_CATIA_INPUTS = $cfg.inputs
$process = Start-Process -FilePath $cfg.cnext `
    -ArgumentList @('-macro', $cfg.macro) `
    -WorkingDirectory $cfg.project `
    -PassThru
[ordered]@{{status='STARTED'; pid=$process.Id; cnext=$cfg.cnext; macro=$cfg.macro; inputs=$cfg.inputs}} |
    ConvertTo-Json -Compress
"""
        completed = run_powershell(script, timeout_s=30)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip().startswith("{")]
        if not lines:
            raise RuntimeError(f"CATIA launch returned no process information: {completed.stdout}")
        return json.loads(lines[-1])

    environment = os.environ.copy()
    environment["RAMAIR_CATIA_INPUTS"] = str(inputs)
    process = subprocess.Popen(
        [executable, "-macro", str(macro)],
        cwd=str(project_root),
        env=environment,
    )
    return {
        "status": "STARTED",
        "pid": process.pid,
        "cnext": executable,
        "macro": str(macro),
        "inputs": str(inputs),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--detect", action="store_true")
    action.add_argument("--run", action="store_true")
    parser.add_argument("--cnext")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = detect_catia() if args.detect else launch_catia(args.project_root, args.cnext)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("available", True) or report.get("status") == "STARTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
