"""Reading scenarios from a YAML file and turning them into cases."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from vespera.contracts import Case, Language, Scenario


class ScenarioFileError(Exception):
    """The scenario file is missing, broken or does not follow the contract."""


def load_scenarios(path: Path) -> list[Scenario]:
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

    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        label = str(item.get("id", f"#{index}")) if isinstance(item, dict) else f"#{index}"
        try:
            scenario = Scenario.model_validate(item)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise ScenarioFileError(f"scenario {label}: {where}: {first['msg']}") from exc
        if scenario.id in seen:
            raise ScenarioFileError(f"duplicate scenario id: {scenario.id}")
        seen.add(scenario.id)
        scenarios.append(scenario)
    return scenarios


def expand_cases(scenarios: list[Scenario]) -> list[Case]:
    """Every scenario becomes one case per language."""
    return [
        Case(
            id=f"{scenario.id}:{language.value}",
            scenario_id=scenario.id,
            language=language,
            category=scenario.category,
            prompt=scenario.prompts[language],
            checks=scenario.checks,
            params=scenario.params,
        )
        for scenario in scenarios
        for language in Language
    ]