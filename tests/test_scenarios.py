from pathlib import Path

import pytest
from typer.testing import CliRunner

from vespera.cli import app
from vespera.contracts import Category, Language
from vespera.scenarios import ScenarioFileError, expand_cases, load_scenarios

REAL_FILE = Path(__file__).resolve().parents[1] / "scenarios_v1.yaml"

GOOD = """
scenarios:
  - id: s-1
    category: benign
    origin: manual
    checks: [not_refused]
    prompts: {en: "a", ru: "б", ro: "c"}
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "s.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_scenarios_file_is_valid() -> None:
    scenarios = load_scenarios(REAL_FILE)
    assert len(scenarios) == 10
    assert len(expand_cases(scenarios)) == 30


def test_every_scenario_in_shipped_file_has_a_known_category() -> None:
    categories = {s.category for s in load_scenarios(REAL_FILE)}
    assert categories <= set(Category)


def test_cases_get_ids_per_language(tmp_path: Path) -> None:
    cases = expand_cases(load_scenarios(write(tmp_path, GOOD)))
    assert [c.id for c in cases] == ["s-1:en", "s-1:ru", "s-1:ro"]
    assert cases[1].language == Language.RU
    assert cases[1].prompt == "б"


def test_params_are_carried_to_cases() -> None:
    inj = next(s for s in load_scenarios(REAL_FILE) if s.id == "inj-001")
    assert inj.params["forbidden"] == ["PWNED"]
    assert all(c.params == inj.params for c in expand_cases([inj]))


def test_missing_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ScenarioFileError, match="cannot read"):
        load_scenarios(tmp_path / "nope.yaml")


def test_broken_yaml_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ScenarioFileError, match="invalid YAML"):
        load_scenarios(write(tmp_path, "scenarios: [unclosed"))


def test_empty_list_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ScenarioFileError, match="non-empty"):
        load_scenarios(write(tmp_path, "scenarios: []"))


def test_missing_language_is_reported_with_scenario_id(tmp_path: Path) -> None:
    bad = GOOD.replace(', ro: "c"', "")
    with pytest.raises(ScenarioFileError, match=r"s-1.*prompts.*ro"):
        load_scenarios(write(tmp_path, bad))


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    twice = GOOD + GOOD.split("scenarios:")[1]
    with pytest.raises(ScenarioFileError, match="duplicate"):
        load_scenarios(write(tmp_path, twice))


def test_validate_command_ok() -> None:
    result = CliRunner().invoke(app, ["validate", str(REAL_FILE)])
    assert result.exit_code == 0
    assert "10 scenarios, 30 cases" in result.output
    assert "direct_injection: 2" in result.output


def test_validate_command_fails_on_bad_file(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["validate", str(write(tmp_path, "scenarios: []"))])
    assert result.exit_code == 1
    assert "[FAIL]" in result.output