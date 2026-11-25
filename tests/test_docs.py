"""The READMEs and the landing pages must agree on the benchmark numbers and link to each other."""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
READMES = ("README.md", "README.ro.md", "README.ru.md")
PAGES = {"index.html": "en", "ro.html": "ro", "ru.html": "ru"}
CELL = re.compile(r"(\*\*)?\d+/10(\*\*)?")
HTML_CELL = re.compile(r'<td class="n( hit)?">(\d+/10)</td>')


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


MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)\)")
PRIVATE = {"CLAUDE.md", "CLAUDE.local.md"}  # local notes, not part of the repository
DOC_FILES = sorted(
    [p for p in REPO.glob("*.md") if p.name not in PRIVATE] + [*(REPO / "docs").rglob("*.md")],
    key=lambda path: str(path),
)


def broken_links(doc: Path) -> list[str]:
    """Relative links in a Markdown file whose target file does not exist."""
    broken = []
    for target in MARKDOWN_LINK.findall(doc.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#", 1)[0]
        if path and not (doc.parent / path).exists():
            broken.append(target)
    return broken


def test_the_link_check_finds_a_broken_link_and_ignores_web_links(tmp_path: Path) -> None:
    (tmp_path / "here.md").write_text("x", encoding="utf-8")
    doc = tmp_path / "doc.md"
    links = "[ok](here.md) [anchor](here.md#part) [gone](missing.md)"
    doc.write_text(f"{links} [web](https://example.com) [top](#a)", encoding="utf-8")
    assert broken_links(doc) == ["missing.md"]


@pytest.mark.parametrize("doc", DOC_FILES, ids=lambda path: str(path.relative_to(REPO)))
def test_relative_links_in_the_docs_point_to_existing_files(doc: Path) -> None:
    assert broken_links(doc) == []


def page_cells(page: str) -> list[str]:
    """Result cells of a landing page, written the README way: a highlighted cell is bold."""
    text = (REPO / "docs" / page).read_text(encoding="utf-8")
    return [f"**{value}**" if hit else value for hit, value in HTML_CELL.findall(text)]


@pytest.mark.parametrize("page", PAGES)
def test_landing_pages_show_the_readme_numbers_and_conclusive_marks(page: str) -> None:
    assert page_cells(page) == benchmark_cells("README.md")


@pytest.mark.parametrize(("page", "lang"), PAGES.items())
def test_every_landing_page_declares_its_language_and_links_to_the_others(
    page: str, lang: str
) -> None:
    text = (REPO / "docs" / page).read_text(encoding="utf-8")
    assert f'<html lang="{lang}">' in text
    for other in PAGES:
        if other != page:
            assert f'href="{other}"' in text, other
