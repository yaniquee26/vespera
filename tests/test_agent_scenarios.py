from pathlib import Path

import pytest
import yaml

from vespera.agent_contracts import AgentCategory, AgentScenario, ToolSpec
from vespera.agent_scenarios import expand_agent_cases, load_agent_scenarios
from vespera.bundled import AGENT_FILES, read_bundled
from vespera.contracts import Language
from vespera.scenarios import ScenarioFileError

DATA = Path(__file__).resolve().parents[1] / "src" / "vespera" / "data"


def write(tmp_path: Path, scenarios: list[dict]) -> Path:  # type: ignore[type-arg]
    path = tmp_path / "a.yaml"
    path.write_text(yaml.safe_dump({"scenarios": scenarios}, allow_unicode=True), encoding="utf-8")
    return path


def good(**changes) -> dict:  # type: ignore[no-untyped-def, type-arg]
    scenario = {
        "id": "ag-x",
        "category": "excessive_agency",
        "origin": "test",
        "tools": [{"name": "cancel_subscription", "description": "Cancel a subscription."}],
        "task": {"en": "a", "ru": "б", "ro": "c"},
        "checks": ["forbidden_tool_call"],
        "params": {"tool": "cancel_subscription"},
    }
    scenario.update(changes)
    return scenario


def test_the_shipped_agent_scenarios_load_and_cover_every_category() -> None:
    scenarios = load_agent_scenarios(DATA / "agent_scenarios_v1.yaml")
    assert len(scenarios) == 6
    assert {s.category for s in scenarios} == set(AgentCategory)
    cases = expand_agent_cases(scenarios)
    assert len(cases) == 18
    assert {c.language for c in cases} == set(Language)


def test_every_shipped_secret_really_appears_in_the_agent_system_prompt() -> None:
    prompt = read_bundled("agent_system_prompt.txt").replace(" ", "")
    for scenario in load_agent_scenarios(DATA / "agent_scenarios_v1.yaml"):
        for secret in scenario.params.get("secrets", []):
            assert secret.replace(" ", "") in prompt, secret


def test_poisoned_results_are_picked_per_language() -> None:
    cases = expand_agent_cases(load_agent_scenarios(DATA / "agent_scenarios_v1.yaml"))
    by_id = {c.id: c for c in cases}
    assert "send_sms" in by_id["ag-001:ro"].tool_results["search_help"]
    assert "Примечание" in by_id["ag-001:ru"].tool_results["search_help"]
    assert by_id["ag-004:en"].tool_results == {}


def test_agent_files_are_bundled() -> None:
    for name in AGENT_FILES:
        assert read_bundled(name).strip()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"checks": ["nonsense"]}, "unknown check"),
        ({"params": {"tool": "send_sms"}}, "params.tool"),
        ({"params": {}}, "params.tool"),
        ({"checks": ["secret_not_in_tool_args"], "params": {}}, "params.secrets"),
        ({"checks": ["secret_not_in_tool_args"], "params": {"secrets": [""]}}, "params.secrets"),
        (
            {"tool_results": {"ghost": {"en": "x", "ru": "x", "ro": "x"}}},
            "unknown tool",
        ),
        ({"tool_results": {"cancel_subscription": {"en": "x"}}}, "missing result"),
        ({"task": {"en": "a", "ru": "б"}}, "missing task"),
        ({"task": {"en": "a", "ru": " ", "ro": "c"}}, "empty task"),
        ({"category": "invented"}, "category"),
        ({"tools": []}, "tools"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_broken_scenarios_are_refused_with_a_clear_message(
    tmp_path: Path, changes: dict, message: str  # type: ignore[type-arg]
) -> None:
    with pytest.raises(ScenarioFileError, match=message):
        load_agent_scenarios(write(tmp_path, [good(**changes)]))


def test_duplicate_ids_and_duplicate_tools_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ScenarioFileError, match="duplicate scenario id"):
        load_agent_scenarios(write(tmp_path, [good(), good()]))
    twin = [{"name": "t", "description": "d"}, {"name": "t", "description": "d"}]
    with pytest.raises(ScenarioFileError, match="duplicate tool names"):
        load_agent_scenarios(write(tmp_path, [good(tools=twin, params={"tool": "t"})]))


def test_bad_tool_names_are_refused() -> None:
    with pytest.raises(ValueError):
        ToolSpec(name="Send SMS", description="x")


@pytest.mark.parametrize("content", ["", "scenarios: []", "just text", "a: [1"])
def test_broken_files_are_refused(tmp_path: Path, content: str) -> None:
    path = tmp_path / "a.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ScenarioFileError):
        load_agent_scenarios(path)


def test_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ScenarioFileError, match="cannot read"):
        load_agent_scenarios(tmp_path / "nope.yaml")


def test_scenario_model_is_frozen() -> None:
    scenario = AgentScenario.model_validate(good())
    with pytest.raises(ValueError):
        scenario.id = "other"  # type: ignore[misc]