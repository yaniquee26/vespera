"""Reading agent scenarios from YAML and turning them into cases."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from vespera.agent_contracts import AGENT_CHECKS, AgentCase, AgentScenario
from vespera.contracts import Language
from vespera.scenarios import ScenarioFileError


def _non_empty_strings(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(x, str) and x for x in value)


def _check_rules(scenario: AgentScenario) -> None:
    """Rules that depend on the check names and on the tools of this scenario."""
    tool_names = [tool.name for tool in scenario.tools]
    if len(set(tool_names)) != len(tool_names):
        raise ScenarioFileError(f"scenario {scenario.id}: duplicate tool names")
    for tool in scenario.tool_results:
        if tool not in tool_names:
            raise ScenarioFileError(
                f"scenario {scenario.id}: tool_results mentions unknown tool '{tool}'"
            )
    for name in scenario.checks:
        if name not in AGENT_CHECKS:
            raise ScenarioFileError(f"scenario {scenario.id}: unknown check '{name}'")
    if "forbidden_tool_call" in scenario.checks or "expected_tool_called" in scenario.checks:
        wanted = scenario.params.get("tool")
        if wanted not in tool_names:
            raise ScenarioFileError(
                f"scenario {scenario.id}: params.tool must be one of this scenario's tools"
            )
    if "secret_not_in_tool_args" in scenario.checks and not _non_empty_strings(
        scenario.params.get("secrets")
    ):
        raise ScenarioFileError(
            f"scenario {scenario.id}: secret_not_in_tool_args needs params.secrets "
            "as a non-empty list of strings"
        )


def load_agent_scenarios(path: Path) -> list[AgentScenario]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScenarioFileError(f"cannot read {path}: {exc}") from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScenarioFileError(f"invalid YAML in {path}: {exc}") from exc

    items = raw.get("scenarios") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        raise ScenarioFileError("the file must contain a non-empty 'scenarios' list")

    scenarios: list[AgentScenario] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        label = str(item.get("id", f"#{index}")) if isinstance(item, dict) else f"#{index}"
        try:
            scenario = AgentScenario.model_validate(item)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise ScenarioFileError(f"scenario {label}: {where}: {first['msg']}") from exc
        _check_rules(scenario)
        if scenario.id in seen:
            raise ScenarioFileError(f"duplicate scenario id: {scenario.id}")
        seen.add(scenario.id)
        scenarios.append(scenario)
    return scenarios


def expand_agent_cases(scenarios: list[AgentScenario]) -> list[AgentCase]:
    """Every scenario becomes one case per language."""
    return [
        AgentCase(
            id=f"{scenario.id}:{language.value}",
            scenario_id=scenario.id,
            language=language,
            category=scenario.category,
            task=scenario.task[language],
            tools=scenario.tools,
            tool_results={
                tool: texts[language] for tool, texts in scenario.tool_results.items()
            },
            checks=scenario.checks,
            params=scenario.params,
        )
        for scenario in scenarios
        for language in Language
    ]