from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "Application Support/Tools/audit_container_strategy.py"


def test_docker_remains_experimental_with_native_wsl_selected() -> None:
    spec = importlib.util.spec_from_file_location("container_audit_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.audit(
        SCRIPT.parents[2],
        runtime={
            "host": {"cli_available": False, "server_available": False, "detail": "fixture"},
            "active_wsl_server_available": False,
            "active_wsl_detail": "fixture",
        },
    )
    assert all(report["static_checks"].values())
    assert report["decision"] == "NATIVE_WSL_PRODUCTION_DOCKER_EXPERIMENTAL"
    assert report["production_ready"] is False
    assert report["build_or_run_performed"] is False
    assert "explicit approval" in report["promotion_gate"]
