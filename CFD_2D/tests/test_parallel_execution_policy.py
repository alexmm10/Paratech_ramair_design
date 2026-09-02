from __future__ import annotations

from pathlib import Path

import pytest

from ramair_2d_parallel import (
    configure_decompose_dictionary,
    decompose_load_balance,
    processor_directory_audit,
    recommended_core_count,
    reconstruction_command,
)


@pytest.mark.parametrize(
    ("cells", "maximum", "expected"),
    [(80_000, 8, 1), (198_000, 8, 2), (600_000, 8, 6), (2_000_000, 8, 8)],
)
def test_automatic_rank_selection_respects_cells_per_rank(
    cells: int, maximum: int, expected: int
) -> None:
    plan = recommended_core_count(
        cells, available_slots=8, requested_maximum=maximum
    )
    assert plan["recommended_ranks"] == expected
    assert plan["recommended_ranks"] <= maximum
    if plan["recommended_ranks"] > 1 and cells <= 800_000:
        assert plan["cells_per_rank"] >= 50_000


def test_decomposition_dictionary_and_processor_count_share_rank_source(tmp_path: Path) -> None:
    system = tmp_path / "system"
    system.mkdir()
    dictionary = system / "decomposeParDict"
    dictionary.write_text("numberOfSubdomains 4;\nmethod simple;\n", encoding="utf-8")
    configure_decompose_dictionary(dictionary, 3)
    text = dictionary.read_text(encoding="utf-8")
    assert "numberOfSubdomains 3;" in text
    assert "method scotch;" in text
    for rank in range(3):
        (tmp_path / f"processor{rank}").mkdir()
    assert processor_directory_audit(tmp_path, 3)["matches_expected"] is True


def test_reconstruction_policies_are_explicit() -> None:
    assert reconstruction_command("latest") == "reconstructPar -latestTime"
    assert reconstruction_command("all") == "reconstructPar"
    assert reconstruction_command("time_range", time_range="0.5:1.0") == (
        "reconstructPar -time '0.5:1.0'"
    )
    assert reconstruction_command("fields", fields=["U", "p"]) == (
        "reconstructPar -fields '(U p)'"
    )
    with pytest.raises(ValueError):
        reconstruction_command("fields", fields=[])


def test_decompose_parser_deduplicates_repeated_processor_sections(tmp_path: Path) -> None:
    log = tmp_path / "log.decomposePar"
    log.write_text(
        """
Processor 0
    Number of cells = 100
Processor 1
    Number of cells = 102
Processor 0
    Number of cells = 100
Processor 1
    Number of cells = 102
""",
        encoding="utf-8",
    )
    report = decompose_load_balance(log)
    assert report["cells_by_rank"] == [100, 102]
    assert report["mean_cells"] == pytest.approx(101.0)
