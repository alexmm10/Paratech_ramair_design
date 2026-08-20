from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "CFD_2D/scripts"
sys.path.insert(0, str(SCRIPTS))

import ramair_2d_validation_campaigns as campaigns  # noqa: E402
from ramair_2d_study_registry import (  # noqa: E402
    STUDY_CONFIG_SCHEMA_VERSION,
    default_study_config,
    migrate_study_config,
    persist_study_config_migration,
)


def _study(active: Path) -> dict[str, object]:
    meshes = []
    for topology in ("closed", "open"):
        for level, cells in (("coarse", 100), ("medium", 200), ("fine", 400)):
            meshes.append({
                "id": f"{topology}_{level}",
                "topology": topology,
                "level": level,
                "cell_count": cells,
                "geometry_package": f"geometry/{topology}",
                "mesh_package": f"meshes/{topology}_{level}",
                "mesh_hash": f"hash-{topology}-{level}",
            })
    config = default_study_config()
    config["operating_condition"]["tc_s"] = 0.02
    return {
        "study_manifest": {"study_id": "test-study", "active_workspace": str(active)},
        "mesh_registry": {"meshes": meshes},
        "study_config": config,
    }


def test_schema11_defaults_and_schema10_migration_preserve_user_values() -> None:
    defaults = default_study_config()
    assert STUDY_CONFIG_SCHEMA_VERSION == 11
    assert defaults["schema_version"] == 11
    assert defaults["campaign_engine"]["full_matrix_per_angle"] == 18
    assert defaults["campaign_engine"]["closed"]["angle_order_deg"] == [16.0, 8.0]
    assert defaults["campaign_engine"]["open"]["angle_order_deg"] == [8.0, 16.0]
    assert defaults["frequency_analysis"]["wave_number_definition"] == "W=1/St"
    assert defaults["pimple_outer_study"]["mesh_level"] == "medium"

    migrated = migrate_study_config({
        "schema_version": 10,
        "purpose": "preserve-me",
        "validation_study": {"alpha_deg": 8.0},
        "custom_metadata": {"owner": "user"},
    })
    assert migrated["schema_version"] == 11
    assert migrated["purpose"] == "preserve-me"
    assert migrated["custom_metadata"] == {"owner": "user"}
    assert migrated["campaign_engine"]["preserve_existing_runs"] is True
    assert migrated["campaign_engine"]["preserve_rans_bases"] is True


def test_metadata_only_migration_writes_backup_without_touching_heavy_data(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "active/study_config.json"
    config_path.parent.mkdir(parents=True)
    heavy = tmp_path / "active/checkpoints/base/10000/U"
    heavy.parent.mkdir(parents=True)
    heavy.write_bytes(b"preserve-byte-for-byte")
    current = {"schema_version": 10, "custom": "value"}
    migrated = migrate_study_config(current)
    persist_study_config_migration(config_path, current, migrated)

    assert heavy.read_bytes() == b"preserve-byte-for-byte"
    assert json.loads(config_path.read_text())["schema_version"] == 11
    backups = list((config_path.parent / "configs/migrations").glob(
        "study_config_schema10_*.json"
    ))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text()) == current
    report = json.loads((
        config_path.parent / "configs/migrations/schema11_migration_report.json"
    ).read_text())
    assert report["metadata_only"] is True
    assert "RANS checkpoints and bases" in report["preserved"]


def test_closed_optimized_campaign_is_paired_and_indexes_existing_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = tmp_path / "active"
    study = _study(active)
    existing = active / "runs/closed/coarse/existing"
    existing.mkdir(parents=True)
    (existing / "case_manifest.json").write_text(json.dumps({
        "case_id": "existing",
        "scientific_key": {
            "topology": "closed",
            "mesh_level": "coarse",
            "alpha_deg": 16.0,
            "deltaT_s": 0.0002,
        },
        "execution_outcome": "COMPLETED",
    }))
    monkeypatch.setattr(campaigns, "load_study", lambda _root: study)
    monkeypatch.setattr(campaigns, "active_workspace_root", lambda _root: active)

    campaign = campaigns.build_campaign(
        tmp_path, topology="closed", strategy="optimized", angles_deg=[16.0]
    )
    assert len(campaign["cases"]) == 6
    assert [row["label"].split("-")[-1] for row in campaign["cases"]] == [
        "C1", "C2", "M1", "M2", "F1", "F2"
    ]
    assert campaign["cases"][0]["state"] == "COMPLETED"
    assert campaign["cases"][0]["existing_case"]["case_id"] == "existing"
    assert campaign["execution_policy"]["existing_cases_are_indexed_not_copied"] is True
    assert not (active / "campaigns").exists()


def test_open_campaign_defers_urans_until_fixed_geometry_rans_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = tmp_path / "active"
    monkeypatch.setattr(campaigns, "load_study", lambda _root: _study(active))
    monkeypatch.setattr(campaigns, "active_workspace_root", lambda _root: active)
    campaign = campaigns.build_campaign(
        tmp_path,
        topology="open",
        strategy="progressive_medium_first",
        angles_deg=[8.0],
    )
    assert len(campaign["cases"]) == 9
    assert sum(row["kind"] == "RANS_DIAGNOSTIC" for row in campaign["cases"]) == 3
    assert sum(row["state"] == "DEFERRED" for row in campaign["cases"]) == 6
    assert campaign["methodology"]["geometry_must_remain_fixed"] is True
    assert campaign["methodology"]["minimum_cycles"] == 10


def test_campaign_decisions_append_immutable_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "campaign_manifest.json"
    path.write_text(json.dumps({
        "cases": [{
            "case_key": "key",
            "state": "REVIEW_REQUIRED",
            "approval": {"state": "REVIEW_REQUIRED", "revision": 0, "history": []},
        }]
    }))
    campaigns.set_case_decision(
        path, "key", "APPROVED", actor="tester", evidence={"report": "a.json"}
    )
    row = campaigns.set_case_decision(
        path, "key", "REJECTED", actor="tester", evidence={"report": "b.json"}
    )
    assert row["approval"]["revision"] == 2
    assert [item["decision"] for item in row["approval"]["history"]] == [
        "APPROVED", "REJECTED"
    ]
