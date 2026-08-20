#!/usr/bin/env python3
"""Evidence-driven adaptive decisions for Validation Lab schema 11.

This module reads campaign/evidence metadata and writes review reports.  It
does not prepare or execute CFD cases and never modifies solver results.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

from ramair_2d_study_registry import utc_stamp, write_json_atomic


DECISION_SCHEMA_VERSION = 1
LEVEL_ORDER = {"coarse": 0, "medium": 1, "fine": 2}


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def evidence_eligibility(row: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not bool(row.get("approved")):
        reasons.append("RUN_NOT_APPROVED")
    if not bool(row.get("settled")):
        reasons.append("SETTLING_NOT_DEMONSTRATED")
    cycles = _finite(row.get("cycles"))
    if cycles is None or cycles < 10.0:
        reasons.append("FEWER_THAN_10_CYCLES")
    if not bool(row.get("uniform_sampling")):
        reasons.append("UNIFORM_SAMPLING_NOT_DEMONSTRATED")
    if not bool(row.get("signals_continuous")):
        reasons.append("CRITICAL_SIGNAL_HISTORY_NOT_CONTINUOUS")
    return not reasons, reasons


def _percent_change(a: float, b: float) -> float:
    denominator = max(abs(b), 1.0e-15)
    return abs(a - b) / denominator * 100.0


def compare_evidence_pair(
    first: dict[str, Any],
    second: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    first_ok, first_reasons = evidence_eligibility(first)
    second_ok, second_reasons = evidence_eligibility(second)
    reasons = [f"FIRST_{value}" for value in first_reasons]
    reasons += [f"SECOND_{value}" for value in second_reasons]
    first_time = _finite(first.get("collection_time_star"))
    second_time = _finite(second.get("collection_time_star"))
    same_time = (
        first_time is not None
        and second_time is not None
        and math.isclose(first_time, second_time, rel_tol=1.0e-9, abs_tol=1.0e-12)
    )
    if not same_time:
        reasons.append("PHYSICAL_COLLECTION_TIME_MISMATCH")

    comparisons: dict[str, Any] = {}
    metric_rules = {
        "mean_CL": "mean_CL_percent",
        "mean_CD": "mean_CD_percent",
        "mean_CM": "mean_CM_percent",
        "rms_CL": "rms_percent",
        "rms_CD": "rms_percent",
        "rms_CM": "rms_percent",
        "dominant_frequency": "dominant_frequency_percent",
        "psd_peak_amplitude": "psd_peak_amplitude_percent",
    }
    first_stats = dict(first.get("statistics") or {})
    second_stats = dict(second.get("statistics") or {})
    for metric, threshold_name in metric_rules.items():
        a = _finite(first_stats.get(metric))
        b = _finite(second_stats.get(metric))
        threshold = _finite(thresholds.get(threshold_name))
        if a is None or b is None or threshold is None:
            comparisons[metric] = {
                "status": "MISSING",
                "threshold_percent": threshold,
            }
            reasons.append(f"MISSING_{metric.upper()}")
            continue
        change = _percent_change(a, b)
        passed = change <= threshold
        comparisons[metric] = {
            "status": "PASS" if passed else "FAIL",
            "change_percent": change,
            "threshold_percent": threshold,
        }
        if not passed:
            reasons.append(f"THRESHOLD_FAILED_{metric.upper()}")

    return {
        "schema_version": DECISION_SCHEMA_VERSION,
        "eligible": first_ok and second_ok and same_time,
        "accepted": not reasons,
        "same_physical_time": same_time,
        "collection_time_star": first_time if same_time else None,
        "comparisons": comparisons,
        "reasons": reasons,
    }


def three_grid_gci(
    records: Iterable[dict[str, Any]],
    *,
    value_key: str,
) -> dict[str, Any]:
    """Compute a saved three-grid GCI estimate for a scalar observable."""
    by_level = {str(row.get("mesh_level")): row for row in records}
    missing = [level for level in LEVEL_ORDER if level not in by_level]
    if missing:
        return {"status": "INSUFFICIENT_GRIDS", "missing": missing}
    coarse, medium, fine = (by_level[name] for name in ("coarse", "medium", "fine"))
    values = [_finite(row.get(value_key)) for row in (coarse, medium, fine)]
    cells = [_finite(row.get("cell_count")) for row in (coarse, medium, fine)]
    if any(value is None for value in values + cells) or any(value <= 0 for value in cells):
        return {"status": "INVALID_INPUT"}
    phi3, phi2, phi1 = (float(value) for value in values)
    n3, n2, n1 = (float(value) for value in cells)
    h3, h2, h1 = (1.0 / math.sqrt(value) for value in (n3, n2, n1))
    r21, r32 = h2 / h1, h3 / h2
    if r21 <= 1.0 or r32 <= 1.0:
        return {"status": "INVALID_REFINEMENT_ORDER"}
    e21, e32 = phi2 - phi1, phi3 - phi2
    if abs(e21) <= 1.0e-15 or abs(e32) <= 1.0e-15:
        return {"status": "ZERO_DIFFERENCE", "fine_value": phi1}
    sign = 1.0 if e32 / e21 >= 0.0 else -1.0
    p = max(0.1, abs(math.log(abs(e32 / e21)) / math.log(r21)))
    for _ in range(100):
        numerator = max(abs(r21 ** p - sign), 1.0e-15)
        denominator = max(abs(r32 ** p - sign), 1.0e-15)
        updated = abs(
            math.log(abs(e32 / e21)) + math.log(numerator / denominator)
        ) / math.log(r21)
        if abs(updated - p) < 1.0e-8:
            p = updated
            break
        p = max(0.05, min(updated, 20.0))
    extrapolated = (r21 ** p * phi1 - phi2) / (r21 ** p - 1.0)
    ea21 = abs((phi1 - phi2) / max(abs(phi1), 1.0e-15))
    ea32 = abs((phi2 - phi3) / max(abs(phi2), 1.0e-15))
    gci21 = 1.25 * ea21 / (r21 ** p - 1.0) * 100.0
    gci32 = 1.25 * ea32 / (r32 ** p - 1.0) * 100.0
    asymptotic_ratio = gci32 / max(gci21 * r21 ** p, 1.0e-15)
    return {
        "status": "COMPUTED",
        "value_key": value_key,
        "convergence_type": "monotonic" if sign > 0 else "oscillatory",
        "observed_order": p,
        "refinement_ratio_fine_medium": r21,
        "refinement_ratio_medium_coarse": r32,
        "extrapolated_value": extrapolated,
        "gci_fine_percent": gci21,
        "gci_medium_percent": gci32,
        "asymptotic_ratio": asymptotic_ratio,
        "asymptotic_range": 0.8 <= asymptotic_ratio <= 1.2,
    }


def _approved(evidence: dict[str, Any], case_key: str) -> bool:
    row = dict(evidence.get(case_key) or {})
    eligible, _ = evidence_eligibility(row)
    return eligible


def adaptive_next_cases(
    campaign: dict[str, Any],
    evidence: dict[str, Any],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    topology = str(campaign.get("topology"))
    cases = list(campaign.get("cases") or [])
    methodology = dict(campaign.get("methodology") or {})
    angle_order = [float(value) for value in methodology.get("angles_deg") or []]
    available_angles = {float(value) for value in campaign.get("angles_deg") or []}
    angle_order = [value for value in angle_order if value in available_angles]
    result: dict[str, Any] = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "campaign_id": campaign.get("campaign_id"),
        "topology": topology,
        "generated_at": utc_stamp(),
        "next_case_keys": [],
        "gate": None,
        "comparisons": [],
    }
    for angle in angle_order:
        angle_rows = [row for row in cases if float(row.get("alpha_deg")) == angle]
        if topology == "closed":
            ordered = [row for row in angle_rows if row.get("kind") == "URANS"]
            missing = next(
                (row for row in ordered if not _approved(evidence, str(row["case_key"]))),
                None,
            )
            if missing:
                result["next_case_keys"] = [missing["case_key"]]
                result["gate"] = f"CLOSED_ALPHA_{angle:g}_PROGRESSIVE_SEQUENCE"
                return result
            result["comparisons"].append({
                "angle_deg": angle,
                "status": "SEQUENCE_EVIDENCE_COMPLETE",
            })
            continue

        rans = [row for row in angle_rows if row.get("kind") == "RANS_DIAGNOSTIC"]
        missing_rans = [
            row["case_key"] for row in rans
            if not bool((evidence.get(str(row["case_key"])) or {}).get("diagnostics_pass"))
        ]
        if missing_rans:
            result["next_case_keys"] = missing_rans
            result["gate"] = f"OPEN_ALPHA_{angle:g}_RANS_DIAGNOSTICS"
            return result
        medium = sorted(
            [row for row in angle_rows if row.get("kind") == "URANS" and row.get("mesh_level") == "medium"],
            key=lambda row: -float(row.get("deltaT_star") or 0.0),
        )
        approved_medium = [row for row in medium if _approved(evidence, str(row["case_key"]))]
        temporal_accepted = False
        if len(approved_medium) >= 2:
            comparison = compare_evidence_pair(
                evidence[str(approved_medium[-2]["case_key"])],
                evidence[str(approved_medium[-1]["case_key"])],
                thresholds,
            )
            result["comparisons"].append(comparison)
            temporal_accepted = bool(comparison["accepted"])
        if not temporal_accepted:
            missing = next(
                (row for row in medium if not _approved(evidence, str(row["case_key"]))),
                None,
            )
            if missing:
                result["next_case_keys"] = [missing["case_key"]]
                result["gate"] = f"OPEN_ALPHA_{angle:g}_MEDIUM_TEMPORAL"
                return result
            result["gate"] = f"OPEN_ALPHA_{angle:g}_TEMPORAL_REVIEW_REQUIRED"
            return result
        selected_dt = float(approved_medium[-1]["deltaT_star"])
        spatial = [
            row for row in angle_rows
            if row.get("kind") == "URANS"
            and row.get("mesh_level") in {"coarse", "fine"}
            and math.isclose(float(row.get("deltaT_star")), selected_dt)
        ]
        missing_spatial = [
            row["case_key"] for row in spatial
            if not _approved(evidence, str(row["case_key"]))
        ]
        if missing_spatial:
            result["next_case_keys"] = missing_spatial
            result["gate"] = f"OPEN_ALPHA_{angle:g}_SPATIAL_CROSSING"
            return result
    result["gate"] = "CAMPAIGN_EVIDENCE_COMPLETE_REVIEW_REQUIRED"
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    campaign = json.loads(args.campaign.read_text(encoding="utf-8-sig"))
    evidence = json.loads(args.evidence.read_text(encoding="utf-8-sig"))
    thresholds = json.loads(args.thresholds.read_text(encoding="utf-8-sig"))
    report = adaptive_next_cases(campaign, evidence, thresholds)
    if args.output:
        write_json_atomic(args.output, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
