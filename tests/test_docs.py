"""The README and its translations must agree on the benchmark numbers and link to each other."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
READMES = ("README.md", "README.ro.md", "README.ru.md")
CELL = re.compile(r"(\*\*)?\d+/10(\*\*)?")


def benchmark_cells(name: str) -> list[str]:
    """Result cells such as `2/10` or `**10/10**`, in table order, bold marks included."""
    text = (REPO / name).read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("|") and "/10" in line]
    cells = [cell.strip() for row in rows for cell in row.strip("|").split("|")]
    return [cell for cell in cells if CELL.fullmatch(cell)]


def test_the_english_readme_has_the_full_benchmark_table() -> None:
    assert len(benchmark_cells("README.md")) == 12


@pytest.mark.parametrize("name", READMES[1:])
def test_translations_show_the_same_numbers_and_the_same_conclusive_marks(name: str) -> None:
    assert benchmark_cells(name) == benchmark_cells("README.md")


@pytest.mark.parametrize("name", READMES)
def test_every_readme_links_to_the_other_languages(name: str) -> None:
    text = (REPO / name).read_text(encoding="utf-8")
    for other in READMES:
        if other != name:
            assert f"]({other})" in text, other
