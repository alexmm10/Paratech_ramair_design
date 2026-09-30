from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from ramair_2d_openfoam_log_events import classify_openfoam_log
from ramair_hardware import aggregate_throughput


def test_adaptive_courant_cannot_hide_timestep_collapse():
    text = "\n".join(f"deltaT = {dt}" for dt in [1e-5,1e-6,1e-8,1e-10,1e-14,1e-15,1e-16,1e-17])
    event = classify_openfoam_log(text+"\nCourant Number mean: 1e-10 max: 5\nEnd\n", returncode=0)
    assert event.numerical_divergence
    assert "ADAPTIVE_TIMESTEP_COLLAPSE" in event.fatal_markers
    assert not classify_openfoam_log("\n".join("deltaT = 1e-5" for _ in range(30))+"\ndeltaT = 1e-15\n").numerical_divergence


def test_timeout_is_partial_not_numerical_failure():
    event = classify_openfoam_log("End\n", returncode=124)
    assert event.status == "TIMEOUT_PARTIAL"
    assert not event.solver_failure and not event.numerical_divergence


def test_simultaneous_throughput_sums_both_cases_over_common_wall_time():
    measured = aggregate_throughput([{"initial_iteration":20,"final_iteration":50}, {"initial_iteration":100,"final_iteration":140}], 2., 12.)
    assert measured["completed_case_steps"] == 70
    assert measured["aggregate_case_steps_per_s"] == 7.
