"""The project settings: vespera.yaml, the target under test and the rules of the gate.

Command-line flags win over the project file, and the file wins over the built-in defaults.
"""

from pathlib import Path

from pydantic import ValidationError

from vespera.config import ConfigError, ProjectConfig, TargetConfig, load_config, parse_target
from vespera.gate import GatePolicy
from vespera.services.errors import ProjectFileError, TargetError

DEFAULT_TARGET = TargetConfig(type="ollama", model="gemma3:4b")


def load_project(config: Path | None, no_config: bool) -> ProjectConfig | None:
    """vespera.yaml from `config`, or from the current folder if it is there."""
    if no_config:
        return None
    path = config if config is not None else Path("vespera.yaml")
    if config is None and not path.exists():
        return None
    try:
        return load_config(path)
    except ConfigError as exc:
        raise ProjectFileError(str(exc)) from exc


def resolve_target(
    project: ProjectConfig | None,
    model: str | None,
    provider_kind: str | None,
    base_url: str | None,
    api_key_env: str | None,
    spec: str | None = None,
) -> TargetConfig:
    """The target from a one-word spec, else vespera.yaml, else the default; flags on top."""
    if spec is not None:
        try:
            target = parse_target(spec)
        except ConfigError as exc:
            raise TargetError(str(exc)) from exc
    else:
        target = project.target if project else DEFAULT_TARGET
    updates: dict[str, str] = {}
    if provider_kind is not None:
        if provider_kind not in ("ollama", "openai"):
            raise TargetError("--provider must be ollama or openai")
        updates["type"] = provider_kind
    for name, value in (("model", model), ("base_url", base_url), ("api_key_env", api_key_env)):
        if value is not None:
            updates[name] = value
    if not updates:
        return target
    try:
        return TargetConfig.model_validate({**target.model_dump(), **updates})
    except ValidationError as exc:
        problems = "; ".join(str(problem["msg"]) for problem in exc.errors())
        raise TargetError(f"the flags do not fit the target: {problems}") from exc


def build_policy(
    max_rate: float | None,
    zero_tolerance: str | None,
    min_exposure: float | None,
    project: ProjectConfig | None = None,
) -> GatePolicy:
    """The rules of the gate: flags win over the project file, the file over the defaults."""
    rules = project.gate if project else None
    if zero_tolerance is not None:
        selectors = tuple(part.strip() for part in zero_tolerance.split(",") if part.strip())
    else:
        selectors = tuple(rules.zero_tolerance) if rules else ()
    if max_rate is None and rules:
        max_rate = rules.max_attack_rate
    if min_exposure is None:
        min_exposure = rules.min_exposure if rules else 0.5
    return GatePolicy(
        max_attack_rate=max_rate, zero_tolerance=selectors, min_exposure=min_exposure
    )


def has_rules(policy: GatePolicy) -> bool:
    """A gate needs at least one rule that can fail: a rate limit or a zero-tolerance list."""
    return policy.max_attack_rate is not None or bool(policy.zero_tolerance)


def export_paths(
    project: ProjectConfig | None, json_path: Path | None, junit_path: Path | None
) -> tuple[Path | None, Path | None]:
    """Where to write results.json and JUnit XML: flags win over the `exports` section."""
    if project is None:
        return json_path, junit_path
    return (
        json_path or project.exports.results_json,
        junit_path or project.exports.junit_xml,
    )
