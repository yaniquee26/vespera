"""Command line interface: `vespera version`, `doctor`, `ask`."""

import os
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from vespera import __version__
from vespera.agent_contracts import AgentTurn
from vespera.agent_runner import MAX_STEPS, run_agent_experiment
from vespera.agent_scenarios import expand_agent_cases, load_agent_scenarios
from vespera.bundled import AGENT_FILES, BUNDLED_FILES, CONFIG_TEMPLATE, read_bundled
from vespera.checks import CheckConfigError, run_checks
from vespera.compare import build_comparison, render_comparison_html
from vespera.config import ConfigError, ProjectConfig, TargetConfig, load_config
from vespera.contracts import Case, Category, Snapshot
from vespera.doctor import all_required_ok, run_all
from vespera.export import build_junit, build_results, mask, render_json, secrets_of
from vespera.gate import (
    GateConfigError,
    GatePolicy,
    GateResult,
    GateStatus,
    evaluate_gate,
)
from vespera.providers import (
    AgentProvider,
    BaseProvider,
    HttpProvider,
    MockAgentProvider,
    MockProvider,
    OllamaProvider,
    OpenAICompatibleAgentProvider,
    OpenAICompatibleProvider,
    ProviderError,
)
from vespera.report import ReportData, ReportError, build_report, render_html
from vespera.runner import RunAborted, RunSummary, run_experiment
from vespera.scenarios import ScenarioFileError, expand_cases, load_scenarios
from vespera.snapshot import build_snapshot, sha256_text
from vespera.storage import Storage
from vespera.summary import summarize

app = typer.Typer(help="Evidence-based reliability testing for LLMs.", no_args_is_help=True)


MaxRateOption = Annotated[
    float | None,
    typer.Option(
        min=0.0,
        max=1.0,
        help="Fail (exit code 2) if the share of successful attacks is above this, 0 to 1.",
    ),
]
ZeroToleranceOption = Annotated[
    str | None,
    typer.Option(
        help="Comma-separated OWASP ids or categories that must have no violation at all, "
        "for example LLM02,LLM03. Any violation fails the gate (exit code 2)."
    ),
]
MinExposureOption = Annotated[
    float | None,
    typer.Option(
        min=0.0,
        max=1.0,
        help="Agent runs: below this share of attempts that reached the poisoned tool, "
        "the gate is inconclusive (exit code 3). Default 0.5.",
    ),
]
JsonOption = Annotated[
    Path | None, typer.Option("--json", help="Also write machine-readable results.json here.")
]
JunitOption = Annotated[
    Path | None, typer.Option("--junit", help="Also write a JUnit XML file here (for CI).")
]
RevealOption = Annotated[
    bool, typer.Option("--reveal", help="Do not mask secrets and long numbers in console/json/xml.")
]
ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help="Project file (default: vespera.yaml in this folder, if any)."),
]
NoConfigOption = Annotated[
    bool, typer.Option("--no-config", help="Ignore vespera.yaml and use only the flags.")
]


def _policy(
    max_rate: float | None,
    zero_tolerance: str | None,
    min_exposure: float | None,
    project: ProjectConfig | None = None,
) -> GatePolicy:
    """Flags win over the project file; the file wins over the built-in defaults."""
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


def _project(config: Path | None, no_config: bool) -> ProjectConfig | None:
    """vespera.yaml from --config, or from the current folder if it is there."""
    if no_config:
        return None
    path = config if config is not None else Path("vespera.yaml")
    if config is None and not path.exists():
        return None
    try:
        return load_config(path)
    except ConfigError as exc:
        typer.echo(f"[FAIL] {exc}")
        raise typer.Exit(code=1) from exc


def _key_from_env(name: str | None) -> str | None:
    if name is None:
        return None
    key = os.environ.get(name)
    if not key:
        typer.echo(f"[FAIL] set the environment variable {name} with your API key")
        raise typer.Exit(code=1)
    return key


def _target_provider(target: TargetConfig, mock: bool = False) -> BaseProvider:
    """The text provider for a target. The API key comes from the environment, never from a file."""
    if mock or target.type == "mock":
        return MockProvider(["I cannot help with that."])
    if target.type == "http":
        assert target.url is not None and target.reply_field is not None  # config validated
        return HttpProvider(
            target.url,
            reply_field=target.reply_field,
            request_field=target.request_field,
            extra_body=target.extra_body,
            system_field=target.system_field,
            api_key=_key_from_env(target.api_key_env),
            api_key_header=target.api_key_header,
            api_key_prefix=target.api_key_prefix,
            timeout=target.timeout,
        )
    assert target.model is not None  # config validated
    if target.type == "openai":
        address = target.base_url or "https://api.openai.com/v1"
        return OpenAICompatibleProvider(
            model=target.model,
            base_url=address,
            api_key=_api_key_for(address, target.api_key_env or "OPENAI_API_KEY"),
        )
    return OllamaProvider(model=target.model, base_url=target.base_url or "http://localhost:11434")


def _agent_target_provider(target: TargetConfig, mock: bool = False) -> AgentProvider:
    if mock or target.type == "mock":
        return MockAgentProvider([AgentTurn(text="I cannot help with that.")])
    assert target.model is not None  # config validated
    default = "https://api.openai.com/v1" if target.type == "openai" else "http://localhost:11434/v1"
    address = target.base_url or default
    return OpenAICompatibleAgentProvider(
        model=target.model,
        base_url=address,
        api_key=_api_key_for(address, target.api_key_env or "OPENAI_API_KEY"),
    )


def _resolve_target(
    project: ProjectConfig | None,
    model: str | None,
    provider_kind: str | None,
    base_url: str | None,
    api_key_env: str | None,
) -> TargetConfig:
    """The target from vespera.yaml (or the old defaults), with any flag laid on top."""
    target = project.target if project else TargetConfig(type="ollama", model="gemma3:4b")
    updates: dict[str, str] = {}
    if provider_kind is not None:
        if provider_kind not in ("ollama", "openai"):
            typer.echo("[FAIL] --provider must be ollama or openai")
            raise typer.Exit(code=1)
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
        typer.echo(f"[FAIL] the flags do not fit the target: {problems}")
        raise typer.Exit(code=1) from exc


def _export_paths(
    project: ProjectConfig | None, json_path: Path | None, junit_path: Path | None
) -> tuple[Path | None, Path | None]:
    """Flags win over the `exports` section of the project file."""
    if project is None:
        return json_path, junit_path
    return (
        json_path or project.exports.results_json,
        junit_path or project.exports.junit_xml,
    )


def _has_rules(policy: GatePolicy) -> bool:
    return policy.max_attack_rate is not None or bool(policy.zero_tolerance)


def _chosen_run(db: Path, run_id: int | None) -> int:
    storage = Storage(db)
    try:
        chosen = run_id if run_id is not None else storage.latest_run_id()
        if chosen is None or not storage.run_exists(chosen):
            typer.echo("[FAIL] no such run")
            raise typer.Exit(code=1)
        return chosen
    finally:
        storage.close()


def _print_gate(result: GateResult) -> None:
    label = {
        GateStatus.PASSED: "[OK]",
        GateStatus.FAILED: "[FAIL]",
        GateStatus.INCONCLUSIVE: "[INCONCLUSIVE]",
    }
    for note in result.notes:
        typer.echo(f"[OK] {note}")
    for reason in result.reasons:
        typer.echo(f"{label[result.status]} {reason}")
    typer.echo(f"Gate: {result.status.value}")


def _write_exports(
    storage: Storage,
    run_id: int,
    data: ReportData,
    result: GateResult | None,
    json_path: Path | None,
    junit_path: Path | None,
    reveal: bool,
) -> None:
    cases = storage.get_cases(run_id)
    hidden = secrets_of(cases)
    if json_path is not None:
        payload = build_results(data, result, mask_values=not reveal, secrets=hidden)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(render_json(payload), encoding="utf-8")
        typer.echo(f"[OK] results written to {json_path}")
    if junit_path is not None:
        xml = build_junit(
            run_id,
            storage.get_snapshot(run_id),
            cases,
            storage.get_attempts(run_id),
            storage.get_check_results(run_id),
            mask_values=not reveal,
            secrets=hidden,
        )
        junit_path.parent.mkdir(parents=True, exist_ok=True)
        junit_path.write_text(xml, encoding="utf-8")
        typer.echo(f"[OK] JUnit XML written to {junit_path}")


def _finish(
    db: Path,
    run_id: int,
    policy: GatePolicy | None,
    json_path: Path | None,
    junit_path: Path | None,
    reveal: bool,
) -> None:
    """Build the report data, apply the gate if there is a policy, write exports, set exit code."""
    storage = Storage(db)
    result: GateResult | None = None
    try:
        try:
            data = build_report(storage, run_id)
            if policy is not None:
                result = evaluate_gate(data, policy)
        except (ReportError, GateConfigError) as exc:
            typer.echo(f"[FAIL] {exc}")
            raise typer.Exit(code=1) from exc
        _write_exports(storage, run_id, data, result, json_path, junit_path, reveal)
    finally:
        storage.close()
    if result is not None:
        _print_gate(result)
        if result.exit_code:
            raise typer.Exit(code=result.exit_code)


@app.command()
def version() -> None:
    """Print the Vespera version."""
    typer.echo(f"vespera {__version__}")


@app.command()
def doctor() -> None:
    """Check that the environment is ready."""
    results = run_all()
    for result in results:
        label = "OK" if result.ok else ("FAIL" if result.required else "WARN")
        typer.echo(f"[{label}] {result.name}: {result.detail}")
    if not all_required_ok(results):
        raise typer.Exit(code=1)


@app.command()
def ask(
    prompt: str,
    model: str = typer.Option("gemma3:4b", help="Ollama model name."),
    system: str = typer.Option("You are a helpful assistant.", help="System prompt."),
) -> None:
    """Send one question to a local model (a manual smoke test)."""
    provider = OllamaProvider(model=model)
    try:
        answer = provider.generate(system, prompt)
    except ProviderError as exc:
        typer.echo(f"[FAIL] {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(answer)


@app.command()
def validate(
    path: Annotated[Path, typer.Argument(help="Scenario file to check.")] = Path(
        "scenarios_v1.yaml"
    ),
) -> None:
    """Check that a scenario file (questions or agent tasks) is valid and show what it contains."""
    agent_file = _is_agent_file(path)
    try:
        if agent_file:
            agent_scenarios = load_agent_scenarios(path)
            case_count = len(expand_agent_cases(agent_scenarios))
            per_category = Counter(scenario.category.value for scenario in agent_scenarios)
            total = len(agent_scenarios)
        else:
            scenarios = load_scenarios(path)
            case_count = len(expand_cases(scenarios))
            per_category = Counter(scenario.category.value for scenario in scenarios)
            total = len(scenarios)
    except (ScenarioFileError, OSError) as exc:
        typer.echo(f"[FAIL] {exc}")
        raise typer.Exit(code=1) from exc
    kind = " (agent tasks)" if agent_file else ""
    typer.echo(f"[OK] {path}: {total} scenarios, {case_count} cases{kind}")
    for category in Category:
        if per_category[category.value]:
            typer.echo(f"  {category.value}: {per_category[category.value]}")


def _is_agent_file(path: Path) -> bool:
    """Agent scenarios give a `task` and tools; question scenarios give `prompts`."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False  # the normal loader reports the real problem
    items = raw.get("scenarios") if isinstance(raw, dict) else None
    return isinstance(items, list) and any(isinstance(i, dict) and "task" in i for i in items)


DEFAULT_DB = Path("runs/vespera.db")


def _api_key_for(address: str, api_key_env: str) -> str | None:
    """The key from the environment. A local server needs none, any other server does."""
    key = os.environ.get(api_key_env)
    if not key and "localhost" not in address and "127.0.0.1" not in address:
        typer.echo(f"[FAIL] set the environment variable {api_key_env} with your API key")
        raise typer.Exit(code=1)
    return key


def _begin_run(
    storage: Storage, db: Path, snapshot: Snapshot, cases: list[Case], resume: int | None
) -> int:
    """Start a new run, or continue an old one if nothing that matters has changed."""
    if resume is None:
        run_id = storage.create_run(snapshot)
        storage.save_cases(run_id, cases)
        typer.echo(f"Started run {run_id}: {len(cases)} cases x {snapshot.repeats} repeats")
        return run_id
    if not storage.run_exists(resume):
        typer.echo(f"[FAIL] no run with id {resume} in {db}")
        raise typer.Exit(code=1)
    old = storage.get_snapshot(resume)
    same = (
        old.model == snapshot.model
        and old.system_prompt_sha256 == snapshot.system_prompt_sha256
        and old.scenarios_sha256 == snapshot.scenarios_sha256
        and old.repeats == snapshot.repeats
        and old.max_steps == snapshot.max_steps
    )
    if not same:
        typer.echo("[FAIL] model, repeats or input files differ from the original run")
        raise typer.Exit(code=1)
    typer.echo(f"Resuming run {resume}")
    return resume


def _print_progress(done: int, total: int, case_id: str, repeat: int, error: str | None) -> None:
    status = "ERROR" if error else "ok"
    typer.echo(f"[{done}/{total}] {case_id} (repeat {repeat}) {status}")


def _read_inputs[T](
    scenarios: Path, system_prompt: Path, load: Callable[[Path], T], tip: str | None = None
) -> tuple[T, str, str]:
    """Load the scenario file and read both input files as text, or stop with a clear message."""
    try:
        loaded = load(scenarios)
        system_text = system_prompt.read_text(encoding="utf-8")
        scenarios_text = scenarios.read_text(encoding="utf-8")
    except (ScenarioFileError, OSError) as exc:
        typer.echo(f"[FAIL] {exc}")
        if tip and (not scenarios.exists() or not system_prompt.exists()):
            typer.echo(tip)
        raise typer.Exit(code=1) from exc
    return loaded, system_text, scenarios_text


def _run_and_save(
    db: Path,
    snapshot: Snapshot,
    cases: list[Case],
    resume: int | None,
    execute: Callable[[Storage, int], RunSummary],
) -> int:
    """Open the database, start or resume the run, execute it and report. Returns the run id."""
    storage = Storage(db)
    try:
        run_id = _begin_run(storage, db, snapshot, cases, resume)
        try:
            summary = execute(storage, run_id)
        except RunAborted as exc:
            typer.echo(f"[FAIL] {exc}")
            raise typer.Exit(code=1) from exc
        typer.echo(
            f"[OK] run {run_id}: {summary.attempted} attempts, {summary.errors} errors, "
            f"{summary.skipped} skipped. Saved to {db}"
        )
        return run_id
    finally:
        storage.close()


@app.command()
def run(
    model: Annotated[str, typer.Option(help="Model name.")] = "gemma3:4b",
    provider_kind: Annotated[
        str, typer.Option("--provider", help="ollama (local) or openai (any OpenAI-style API).")
    ] = "ollama",
    base_url: Annotated[
        str | None, typer.Option(help="API address for --provider openai.")
    ] = None,
    api_key_env: Annotated[
        str, typer.Option(help="Name of the environment variable that holds the API key.")
    ] = "OPENAI_API_KEY",
    repeats: Annotated[int, typer.Option(min=1, help="How many times to ask each case.")] = 1,
    limit: Annotated[int | None, typer.Option(min=1, help="Only the first N cases.")] = None,
    scenarios: Annotated[Path, typer.Option(help="Scenario file.")] = Path("scenarios_v1.yaml"),
    system_prompt: Annotated[Path, typer.Option(help="System prompt file.")] = Path(
        "system_prompt.txt"
    ),
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
    resume: Annotated[int | None, typer.Option(help="Continue a run by its id.")] = None,
    mock: Annotated[bool, typer.Option(help="Use a fake model (no GPU, for trying out).")] = False,
) -> None:
    """Ask the model every case and save all answers."""
    if provider_kind not in ("ollama", "openai"):
        typer.echo("[FAIL] --provider must be ollama or openai")
        raise typer.Exit(code=1)
    target = TargetConfig(
        type=provider_kind,  # type: ignore[arg-type]
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
    )
    _execute_run(
        lambda: _target_provider(target, mock), scenarios, system_prompt, repeats, limit, db, resume
    )


def _execute_run(
    make_provider: Callable[[], BaseProvider],
    scenarios: Path,
    system_prompt: Path,
    repeats: int,
    limit: int | None,
    db: Path,
    resume: int | None,
) -> int:
    """Load the files, build the provider, ask every case, save everything. Returns the run id."""
    loaded, system_text, scenarios_text = _read_inputs(scenarios, system_prompt, load_scenarios)
    cases = expand_cases(loaded)[:limit]
    provider = make_provider()
    snapshot = build_snapshot(
        model=provider.model,
        temperature=provider.temperature,
        num_predict=provider.num_predict,
        repeats=repeats,
        system_prompt=system_text,
        scenarios_text=scenarios_text,
    )

    return _run_and_save(
        db,
        snapshot,
        cases,
        resume,
        lambda storage, run_id: run_experiment(
            provider, cases, system_text, storage, run_id, repeats, on_progress=_print_progress
        ),
    )


@app.command()
def show(
    run_id: Annotated[int | None, typer.Argument(help="Run id (default: the latest).")] = None,
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
) -> None:
    """Print the saved answers of a run, shortened to one line each."""
    storage = Storage(db)
    try:
        chosen = run_id if run_id is not None else storage.latest_run_id()
        if chosen is None or not storage.run_exists(chosen):
            typer.echo("[FAIL] no such run")
            raise typer.Exit(code=1)
        snapshot = storage.get_snapshot(chosen)
        typer.echo(f"Run {chosen} ({storage.get_status(chosen)}): model {snapshot.model}")
        for attempt in storage.get_attempts(chosen):
            text = attempt.error and f"ERROR: {attempt.error}" or attempt.reply
            one_line = " ".join(text.split())[:100]
            typer.echo(
                f"{attempt.case_id} #{attempt.repeat} ({attempt.latency_ms} ms): {one_line}"
            )
    finally:
        storage.close()


@app.command()
def check(
    run_id: Annotated[int | None, typer.Argument(help="Run id (default: the latest).")] = None,
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
    system_prompt: Annotated[
        Path | None,
        typer.Option(help="System prompt file (default: the one that fits this kind of run)."),
    ] = None,
    mask_output: Annotated[
        bool, typer.Option("--mask", help="Hide secrets and long numbers in the printed proof.")
    ] = False,
) -> None:
    """Run the automatic checks on a saved run and print what the model got wrong."""
    storage = Storage(db)
    try:
        chosen = run_id if run_id is not None else storage.latest_run_id()
        if chosen is None or not storage.run_exists(chosen):
            typer.echo("[FAIL] no such run")
            raise typer.Exit(code=1)
        prompt_path = system_prompt or Path(
            "agent_system_prompt.txt" if storage.is_agent_run(chosen) else "system_prompt.txt"
        )
        try:
            system_text = prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            typer.echo(f"[FAIL] cannot read {prompt_path}: {exc}")
            raise typer.Exit(code=1) from exc
        snapshot = storage.get_snapshot(chosen)
        if sha256_text(system_text) != snapshot.system_prompt_sha256:
            typer.echo("[FAIL] the system prompt differs from the one used in this run")
            raise typer.Exit(code=1)

        cases = storage.get_cases(chosen)
        try:
            results = run_checks(
                cases, storage.get_attempts(chosen), system_text, storage.get_traces(chosen)
            )
        except CheckConfigError as exc:
            typer.echo(f"[FAIL] {exc}")
            raise typer.Exit(code=1) from exc
        storage.save_check_results(chosen, results)

        summary = summarize(cases, results)
        attempts_checked = sum(item.attempts for item in summary.categories)
        typer.echo(f"Checked run {chosen} (model {snapshot.model}): {attempts_checked} judged")
        for item in summary.categories:
            label = f"{item.violations} / {item.attempts}"
            typer.echo(f"  {item.category.value:<20} violations {label}")
        total = summary.language_known
        typer.echo(
            f"Reply language matched the prompt language: {summary.language_matches} / {total} "
            f"({summary.language_unknown} undetermined, information only)"
        )
        if summary.hidden_order_total:
            typer.echo(
                f"Hidden order reached the agent: {summary.hidden_order_delivered} / "
                f"{summary.hidden_order_total} tool-output attempts "
                "(in the rest the agent never opened the tool)"
            )
        if summary.text_tool_calls:
            typer.echo(
                f"Tool call written as plain text: {summary.text_tool_calls} / "
                f"{summary.text_tool_calls_total} attempts (counted as 'tool not called')"
            )
        if summary.violations:
            hidden = secrets_of(cases)
            typer.echo("Violations:")
            for v in summary.violations:
                excerpt = mask(v.excerpt, hidden) if mask_output else v.excerpt
                typer.echo(f"  {v.case_id} #{v.repeat} [{v.check}] {v.detail}")
                typer.echo(f"      > {excerpt}")
        typer.echo(f"[OK] verdicts saved to {db}")
    finally:
        storage.close()


@app.command()
def report(
    run_id: Annotated[int | None, typer.Argument(help="Run id (default: the latest).")] = None,
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
    out: Annotated[Path | None, typer.Option(help="Where to write the HTML page.")] = None,
) -> None:
    """Build a single HTML report page for a run (run `vespera check` first)."""
    storage = Storage(db)
    try:
        chosen = run_id if run_id is not None else storage.latest_run_id()
        if chosen is None:
            typer.echo("[FAIL] no runs in the database")
            raise typer.Exit(code=1)
        try:
            html = render_html(build_report(storage, chosen))
        except ReportError as exc:
            typer.echo(f"[FAIL] {exc}")
            raise typer.Exit(code=1) from exc
    finally:
        storage.close()
    target = out or db.parent / f"report-run-{chosen}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    typer.echo(f"[OK] report written to {target}. Open it in a browser.")


@app.command()
def compare(
    run_a: Annotated[int, typer.Argument(help="Id of the first run (model A).")],
    run_b: Annotated[int, typer.Argument(help="Id of the second run (model B).")],
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
    out: Annotated[Path | None, typer.Option(help="Where to write the HTML page.")] = None,
) -> None:
    """Compare two checked runs (same questions, two models) on one HTML page."""
    storage = Storage(db)
    try:
        try:
            html = render_comparison_html(build_comparison(storage, run_a, run_b))
        except ReportError as exc:
            typer.echo(f"[FAIL] {exc}")
            raise typer.Exit(code=1) from exc
    finally:
        storage.close()
    target = out or db.parent / f"compare-run-{run_a}-vs-{run_b}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")
    typer.echo(f"[OK] comparison written to {target}. Open it in a browser.")


@app.command()
def init(
    directory: Annotated[Path, typer.Option(help="Folder for the example files.")] = Path("."),
    force: Annotated[bool, typer.Option(help="Overwrite files that already exist.")] = False,
    agents: Annotated[bool, typer.Option(help="Also copy the files for agent tests.")] = False,
) -> None:
    """Copy the example scenarios and system prompt into a folder, ready to edit."""
    directory.mkdir(parents=True, exist_ok=True)
    names = (*BUNDLED_FILES, *AGENT_FILES) if agents else BUNDLED_FILES
    existing = [name for name in (*names, CONFIG_TEMPLATE) if (directory / name).exists()]
    if existing and not force:
        typer.echo(f"[FAIL] already exists: {', '.join(existing)}. Use --force to overwrite.")
        raise typer.Exit(code=1)
    for name in names:
        (directory / name).write_text(read_bundled(name), encoding="utf-8")
        typer.echo(f"[OK] wrote {directory / name}")
    (directory / CONFIG_TEMPLATE).write_text(_config_text(agents), encoding="utf-8")
    typer.echo(f"[OK] wrote {directory / CONFIG_TEMPLATE}")
    typer.echo("Next: open vespera.yaml and set the target (your model or bot), put YOUR bot's")
    typer.echo("system prompt in system_prompt.txt (keep a CANARY-XXXXXX line),")
    typer.echo("then run `vespera test`.")


def _config_text(agents: bool) -> str:
    """The project file template. For agent tests: agent mode and a model that can call tools."""
    text = read_bundled(CONFIG_TEMPLATE)
    if agents:
        text = text.replace("  model: gemma3:4b", "  model: qwen2.5:7b", 1)
        text = text.replace("mode: text ", "mode: agent", 1)
    return text


@app.command("test")
def test_command(
    config: ConfigOption = None,
    no_config: NoConfigOption = False,
    mode: Annotated[
        str | None, typer.Option(help="text (questions) or agent (tasks with tools).")
    ] = None,
    model: Annotated[str | None, typer.Option(help="Model name.")] = None,
    provider_kind: Annotated[
        str | None,
        typer.Option("--provider", help="ollama (local) or openai (any OpenAI-style API)."),
    ] = None,
    base_url: Annotated[str | None, typer.Option(help="API address for --provider openai.")] = None,
    api_key_env: Annotated[
        str | None, typer.Option(help="Name of the environment variable that holds the API key.")
    ] = None,
    repeats: Annotated[
        int | None, typer.Option(min=1, help="How many times to ask each case (default 3).")
    ] = None,
    limit: Annotated[int | None, typer.Option(min=1, help="Only the first N cases.")] = None,
    max_steps: Annotated[
        int | None, typer.Option(min=1, max=20, help="Agent mode: most model turns per task.")
    ] = None,
    scenarios: Annotated[Path | None, typer.Option(help="Scenario file.")] = None,
    system_prompt: Annotated[Path | None, typer.Option(help="System prompt file.")] = None,
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
    out: Annotated[Path | None, typer.Option(help="Where to write the HTML report.")] = None,
    mock: Annotated[bool, typer.Option(help="Use a fake model (no GPU, for trying out).")] = False,
    max_attack_rate: MaxRateOption = None,
    zero_tolerance: ZeroToleranceOption = None,
    min_exposure: MinExposureOption = None,
    json_path: JsonOption = None,
    junit_path: JunitOption = None,
    reveal: RevealOption = False,
) -> None:
    """Run, check and report in one go, using vespera.yaml. Can fail the build (for CI).

    Settings come from the flags first, then from vespera.yaml, then from the defaults.
    """
    project = _project(config, no_config)
    if project is not None:
        typer.echo(f"Using the project file {config or 'vespera.yaml'}")
    chosen_mode = mode or (project.mode if project else "text")
    if chosen_mode not in ("text", "agent"):
        typer.echo("[FAIL] --mode must be text or agent")
        raise typer.Exit(code=1)
    target = _resolve_target(project, model, provider_kind, base_url, api_key_env)
    if chosen_mode == "agent" and target.type == "http":
        typer.echo("[FAIL] agent mode needs a model that can call tools (openai or ollama)")
        raise typer.Exit(code=1)

    agent = chosen_mode == "agent"
    scenario_file = scenarios or (project.scenarios if project else None) or Path(
        "agent_scenarios_v1.yaml" if agent else "scenarios_v1.yaml"
    )
    prompt_file = system_prompt or (project.system_prompt if project else None) or Path(
        "agent_system_prompt.txt" if agent else "system_prompt.txt"
    )
    times = repeats or (project.repeats if project else None) or 3
    first_n = limit or (project.limit if project else None)

    if agent:
        steps = max_steps or (project.max_steps if project else None) or MAX_STEPS
        run_id = _execute_agent_run(
            lambda: _agent_target_provider(target, mock),
            scenario_file, prompt_file, times, first_n, steps, db, None,
        )
    else:
        run_id = _execute_run(
            lambda: _target_provider(target, mock),
            scenario_file, prompt_file, times, first_n, db, None,
        )
    check(run_id, db=db, system_prompt=prompt_file, mask_output=not reveal)
    report(run_id, db=db, out=out or (project.report if project else None))
    policy = _policy(max_attack_rate, zero_tolerance, min_exposure, project)
    json_out, junit_out = _export_paths(project, json_path, junit_path)
    _finish(db, run_id, policy if _has_rules(policy) else None, json_out, junit_out, reveal)


@app.command("gate")
def gate_command(
    run_id: Annotated[int | None, typer.Argument(help="Run id (default: the latest).")] = None,
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
    config: ConfigOption = None,
    no_config: NoConfigOption = False,
    max_attack_rate: MaxRateOption = None,
    zero_tolerance: ZeroToleranceOption = None,
    min_exposure: MinExposureOption = None,
    json_path: JsonOption = None,
    junit_path: JunitOption = None,
    reveal: RevealOption = False,
) -> None:
    """Decide pass / fail / inconclusive for a checked run. Exit code 0, 2 or 3 (for CI).

    Rules come from the flags, or from the `gate` section of vespera.yaml.
    """
    project = _project(config, no_config)
    policy = _policy(max_attack_rate, zero_tolerance, min_exposure, project)
    if not _has_rules(policy):
        typer.echo(
            "[FAIL] give at least one rule: --max-attack-rate, --zero-tolerance "
            "or a gate section in vespera.yaml"
        )
        raise typer.Exit(code=1)
    json_out, junit_out = _export_paths(project, json_path, junit_path)
    _finish(db, _chosen_run(db, run_id), policy, json_out, junit_out, reveal)


@app.command("export")
def export_command(
    run_id: Annotated[int | None, typer.Argument(help="Run id (default: the latest).")] = None,
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
    json_path: JsonOption = None,
    junit_path: JunitOption = None,
    reveal: RevealOption = False,
) -> None:
    """Write machine-readable results (JSON and/or JUnit XML) of a checked run."""
    if json_path is None and junit_path is None:
        typer.echo("[FAIL] choose at least one of --json and --junit")
        raise typer.Exit(code=1)
    _finish(db, _chosen_run(db, run_id), None, json_path, junit_path, reveal)


@app.command("run-agent")
def run_agent(
    model: Annotated[str, typer.Option(help="A model that can call tools.")] = "qwen2.5:7b",
    base_url: Annotated[
        str, typer.Option(help="OpenAI-style API address (default: local Ollama).")
    ] = "http://localhost:11434/v1",
    api_key_env: Annotated[
        str, typer.Option(help="Name of the environment variable that holds the API key.")
    ] = "OPENAI_API_KEY",
    repeats: Annotated[int, typer.Option(min=1, help="How many times to run each case.")] = 1,
    limit: Annotated[int | None, typer.Option(min=1, help="Only the first N cases.")] = None,
    max_steps: Annotated[
        int, typer.Option(min=1, max=20, help="Most model turns allowed for one task.")
    ] = MAX_STEPS,
    scenarios: Annotated[Path, typer.Option(help="Agent scenario file.")] = Path(
        "agent_scenarios_v1.yaml"
    ),
    system_prompt: Annotated[Path, typer.Option(help="Agent system prompt file.")] = Path(
        "agent_system_prompt.txt"
    ),
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
    resume: Annotated[int | None, typer.Option(help="Continue a run by its id.")] = None,
    mock: Annotated[bool, typer.Option(help="Use a fake agent (no GPU, for trying out).")] = False,
) -> None:
    """Give the agent every task, let it use simulated tools, and save everything it did."""
    target = TargetConfig(
        type="openai", model=model, base_url=base_url, api_key_env=api_key_env
    )
    run_id = _execute_agent_run(
        lambda: _agent_target_provider(target, mock),
        scenarios,
        system_prompt,
        repeats,
        limit,
        max_steps,
        db,
        resume,
    )
    typer.echo(f"Next: vespera check {run_id}, then vespera report {run_id}")


def _execute_agent_run(
    make_provider: Callable[[], AgentProvider],
    scenarios: Path,
    system_prompt: Path,
    repeats: int,
    limit: int | None,
    max_steps: int,
    db: Path,
    resume: int | None,
) -> int:
    """Same as _execute_run, for an agent that uses simulated tools. Returns the run id."""
    loaded, system_text, scenarios_text = _read_inputs(
        scenarios,
        system_prompt,
        load_agent_scenarios,
        tip="Tip: `vespera init --agents` copies the example agent files here.",
    )
    cases = expand_agent_cases(loaded)[:limit]
    provider = make_provider()
    snapshot = build_snapshot(
        model=provider.model,
        temperature=provider.temperature,
        num_predict=provider.num_predict,
        repeats=repeats,
        system_prompt=system_text,
        scenarios_text=scenarios_text,
        max_steps=max_steps,
    )

    return _run_and_save(
        db,
        snapshot,
        [case.to_case() for case in cases],
        resume,
        lambda storage, run_id: run_agent_experiment(
            provider,
            cases,
            system_text,
            storage,
            run_id,
            repeats,
            max_steps=max_steps,
            on_progress=_print_progress,
        ),
    )
