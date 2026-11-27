"""The command line. Each command parses its flags, calls the services and prints the result.

The logic lives in `vespera.services`; a `ServiceError` raised there is printed as `[FAIL] ...`
here and ends the command with exit code 1.
"""

import functools
import os
import sys
import webbrowser
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
import typer.core
import yaml
from pydantic import ValidationError

from vespera import __version__
from vespera.agent_runner import MAX_STEPS
from vespera.agent_scenarios import expand_agent_cases, load_agent_scenarios
from vespera.bundled import AGENT_FILES, BUNDLED_FILES, CONFIG_TEMPLATE, read_bundled
from vespera.compare import build_comparison, render_comparison_html
from vespera.config import TARGET_EXAMPLES, ProjectConfig, TargetConfig, parse_target
from vespera.contracts import Category
from vespera.demo import DEMO_NOTICE
from vespera.doctor import all_required_ok, run_all
from vespera.export import mask, secrets_of
from vespera.gate import GatePolicy, GateResult, GateStatus
from vespera.providers import (
    AgentProvider,
    BaseProvider,
    OllamaProvider,
    ProviderError,
)
from vespera.report import ReportData, ReportError, build_report, render_html
from vespera.runner import RunSummary
from vespera.scenarios import ScenarioFileError, expand_cases, load_scenarios
from vespera.services.errors import ServiceError, TargetError
from vespera.services.project import build_policy as _policy
from vespera.services.project import export_paths, has_rules, load_project, resolve_target
from vespera.services.results import evaluate, junit_xml, results_json
from vespera.services.runs import (
    RunRow,
    check_saved_run,
    choose_run,
    execute_agent_run,
    execute_text_run,
    list_runs,
)
from vespera.services.targets import DEMO_AGENT_PROBLEM, DEMO_AGENT_TIP
from vespera.services.targets import agent_provider as _agent_target_provider
from vespera.services.targets import text_provider as _target_provider
from vespera.storage import NotADatabaseError, Storage
from vespera.workspace import resolve_workspace

USAGE_EXIT_CODE = 1  # a command used wrongly; 2 and 3 belong to the gate alone

# The parser's own error class. Recent Typer versions carry a private copy of Click, older ones
# use the click package; `typer.BadParameter` is public in both and derives from the right class.
USAGE_ERROR: type[Exception] = next(
    cls for cls in typer.BadParameter.__mro__ if cls.__name__ == "UsageError"
)


class _VesperaGroup(typer.core.TyperGroup):
    """The command group with one change: a usage error exits with 1 instead of Click's 2.

    Click gives exit code 2 to every usage error (unknown option, bad value, missing argument),
    which in CI could not be told apart from a failed gate. The error text stays exactly the
    same; only the exit code changes, decided by the exception type.
    """

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: Any = None,
        **extra: Any,
    ) -> Any:
        bare = not args  # read before parsing: the parser consumes the list
        try:
            return super().make_context(info_name, args, parent, **extra)
        except USAGE_ERROR as exc:  # includes "no command given", which prints the help
            exc.exit_code = USAGE_EXIT_CODE  # type: ignore[attr-defined]
            raise
        except typer.Exit as exc:
            if bare and exc.exit_code == 0:  # older Click: help for a bare `vespera`, code 0
                raise typer.Exit(USAGE_EXIT_CODE) from exc
            raise

    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except USAGE_ERROR as exc:  # the errors of a command's own options and arguments
            exc.exit_code = USAGE_EXIT_CODE  # type: ignore[attr-defined]
            raise


app = typer.Typer(
    help="Evidence-based reliability testing for LLMs.", no_args_is_help=True, cls=_VesperaGroup
)


def _fail(exc: ServiceError) -> NoReturn:
    """Print a service error the way every command reports a problem, and stop with code 1."""
    typer.echo(f"[FAIL] {exc}")
    if exc.tip:
        typer.echo(exc.tip)
    raise typer.Exit(code=1) from exc


DB_TIP = "Tip: --db must point to a Vespera database, for example runs/vespera.db"


def _reports_service_errors[**P, R](command: Callable[P, R]) -> Callable[P, R]:
    """Turn a ServiceError raised anywhere inside a command into a [FAIL] line and exit code 1."""

    @functools.wraps(command)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return command(*args, **kwargs)
        except ServiceError as exc:
            _fail(exc)
        except NotADatabaseError as exc:
            _fail(ServiceError(str(exc), tip=DB_TIP))

    return wrapper


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
OpenOption = Annotated[
    bool, typer.Option("--open", help="Open the HTML report in the browser when it is ready.")
]


def _chosen_run(db: Path, run_id: int | None) -> int:
    with Storage(db) as storage:
        return choose_run(storage, run_id)


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
    if json_path is not None:
        text = results_json(storage, run_id, data, result, reveal=reveal)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(text, encoding="utf-8")
        typer.echo(f"[OK] results written to {json_path}")
    if junit_path is not None:
        xml = junit_xml(storage, run_id, reveal=reveal)
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
    try:
        data, result = evaluate(storage, run_id, policy)
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
    model: str = typer.Option("qwen2.5:7b", help="Ollama model name."),
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


def _print_start(run_id: int, resumed: bool, cases: int, repeats: int) -> None:
    if resumed:
        typer.echo(f"Resuming run {run_id}")
    else:
        typer.echo(f"Started run {run_id}: {cases} cases x {repeats} repeats")


def _print_progress(done: int, total: int, case_id: str, repeat: int, error: str | None) -> None:
    status = "ERROR" if error else "ok"
    typer.echo(f"[{done}/{total}] {case_id} (repeat {repeat}) {status}")


def _print_saved(run_id: int, summary: RunSummary, db: Path) -> int:
    retried = ""
    if summary.retries:
        requests = "request" if summary.retries == 1 else "requests"
        retried = f", {summary.retries} {requests} retried after HTTP 429/503"
    typer.echo(
        f"[OK] run {run_id}: {summary.attempted} attempts, {summary.errors} errors, "
        f"{summary.skipped} skipped{retried}. Saved to {db}"
    )
    return run_id


@app.command()
@_reports_service_errors
def run(
    model: Annotated[str, typer.Option(help="Model name.")] = "qwen2.5:7b",
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
    run_id, summary = execute_text_run(
        make_provider, scenarios, system_prompt, repeats, limit, db, resume,
        on_start=_print_start, on_progress=_print_progress,
    )
    return _print_saved(run_id, summary, db)


@app.command()
@_reports_service_errors
def show(
    run_id: Annotated[int | None, typer.Argument(help="Run id (default: the latest).")] = None,
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
) -> None:
    """Print the saved answers of a run, shortened to one line each."""
    with Storage(db) as storage:
        chosen = choose_run(storage, run_id)
        snapshot = storage.get_snapshot(chosen)
        typer.echo(f"Run {chosen} ({storage.get_status(chosen)}): model {snapshot.model}")
        for attempt in storage.get_attempts(chosen):
            text = attempt.error and f"ERROR: {attempt.error}" or attempt.reply
            one_line = " ".join(text.split())[:100]
            typer.echo(
                f"{attempt.case_id} #{attempt.repeat} ({attempt.latency_ms} ms): {one_line}"
            )


@app.command()
@_reports_service_errors
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
    with Storage(db) as storage:
        outcome = check_saved_run(storage, choose_run(storage, run_id), system_prompt)
    summary = outcome.summary
    attempts_checked = sum(item.attempts for item in summary.categories)
    typer.echo(
        f"Checked run {outcome.run_id} (model {outcome.snapshot.model}): "
        f"{attempts_checked} judged"
    )
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
        hidden = secrets_of(outcome.cases)
        typer.echo("Violations:")
        for v in summary.violations:
            excerpt = mask(v.excerpt, hidden) if mask_output else v.excerpt
            typer.echo(f"  {v.case_id} #{v.repeat} [{v.check}] {v.detail}")
            typer.echo(f"      > {excerpt}")
    typer.echo(f"[OK] verdicts saved to {db}")


@app.command()
@_reports_service_errors
def report(
    run_id: Annotated[int | None, typer.Argument(help="Run id (default: the latest).")] = None,
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
    out: Annotated[Path | None, typer.Option(help="Where to write the HTML page.")] = None,
    open_report: OpenOption = False,
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
    _announce_page("report", target, open_report)


@app.command()
@_reports_service_errors
def runs(
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
) -> None:
    """List the saved runs: id, date, model, mode, size and attack success rate."""
    if not db.exists():
        typer.echo(f"No runs yet: {db} does not exist. Start with `vespera test`.")
        return
    with Storage(db) as storage:
        rows = [_run_row(row) for row in list_runs(storage)]
    if not rows:
        typer.echo("No runs yet. Start with `vespera test`.")
        return
    header = ("ID", "STARTED", "MODEL", "MODE", "SIZE", "ATTACKS SUCCEEDED")
    widths = [max(len(row[i]) for row in (header, *rows)) for i in range(len(header))]
    for row in (header, *rows):
        cells = (cell.ljust(width) for cell, width in zip(row, widths, strict=True))
        typer.echo("  ".join(cells).rstrip())
    typer.echo("")
    typer.echo("Open a report: vespera report ID --open   Compare: vespera compare A B   "
               "Decide: vespera gate ID")


def _run_row(row: RunRow) -> tuple[str, ...]:
    size = f"{row.cases} x {row.repeats}"
    if row.attempts < row.expected:
        size += f" ({row.attempts}/{row.expected} done)"
    rate = row.attack_rate
    if not row.checked:
        result = "not checked (vespera check)"
    elif rate is None:
        result = "no attacks"
    else:
        result = f"{rate.k}/{rate.n} = {rate.p:.0%} (95% CI {rate.low:.0%}-{rate.high:.0%})"
    started = row.started.strftime("%Y-%m-%d %H:%M")
    return (str(row.run_id), started, row.model, row.mode, size, result)


def _announce_page(kind: str, path: Path, open_it: bool) -> None:
    """Say where an HTML page was written and, if asked, open it in the default browser."""
    if open_it:
        typer.echo(f"[OK] {kind} written to {path}, opening it in the browser")
        webbrowser.open(path.resolve().as_uri())
    else:
        typer.echo(f"[OK] {kind} written to {path}. Open it in a browser (or add --open).")


@app.command()
@_reports_service_errors
def compare(
    run_a: Annotated[int, typer.Argument(help="Id of the first run (model A).")],
    run_b: Annotated[int, typer.Argument(help="Id of the second run (model B).")],
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
    out: Annotated[Path | None, typer.Option(help="Where to write the HTML page.")] = None,
    open_report: OpenOption = False,
) -> None:
    """Compare two checked runs (same questions, two models) on one HTML page.

    `vespera runs` lists the run ids.
    """
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
    _announce_page("comparison", target, open_report)


@app.command()
def init(
    directory: Annotated[Path, typer.Option(help="Folder for the example files.")] = Path("."),
    force: Annotated[bool, typer.Option(help="Overwrite files that already exist.")] = False,
    agents: Annotated[bool, typer.Option(help="Also copy the files for agent tests.")] = False,
    interactive: Annotated[
        bool | None,
        typer.Option(
            "--interactive/--no-interactive",
            help="Ask a few questions and write a ready vespera.yaml "
            "(default: yes in a terminal, no in scripts and CI).",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Set up a project folder: example scenarios, a system prompt and vespera.yaml.

    In a terminal it asks what to test and writes a ready vespera.yaml, so no file needs editing.
    """
    ask = sys.stdin.isatty() if interactive is None else interactive
    _refuse_existing(directory, (*BUNDLED_FILES, *(AGENT_FILES if agents else ()), CONFIG_TEMPLATE),
                     force)
    if ask:
        config_text, key_env, agents = _setup_wizard(agents)
        if agents:
            _refuse_existing(directory, AGENT_FILES, force)
        typer.echo("")
    else:
        config_text, key_env = _config_text(agents), None
    directory.mkdir(parents=True, exist_ok=True)
    for name in (*BUNDLED_FILES, *AGENT_FILES) if agents else BUNDLED_FILES:
        (directory / name).write_text(read_bundled(name), encoding="utf-8")
        typer.echo(f"[OK] wrote {directory / name}")
    (directory / CONFIG_TEMPLATE).write_text(config_text, encoding="utf-8")
    typer.echo(f"[OK] wrote {directory / CONFIG_TEMPLATE}")
    if not ask:
        typer.echo("Next: open vespera.yaml and set the target (your model or bot), put YOUR bot's")
        typer.echo("system prompt in system_prompt.txt (keep a CANARY-XXXXXX line),")
        typer.echo("then run `vespera test`.")
        return
    typer.echo("")
    if key_env:
        typer.echo("Before the first run, put your API key in an environment variable:")
        typer.echo(f"  {_set_key_command(key_env, windows=os.name == 'nt')}")
        typer.echo("The key is never written to a file, the database or a report.")
    typer.echo("Ready. Run:  vespera test --open")


def _set_key_command(key_env: str, windows: bool) -> str:
    if windows:
        return f'$env:{key_env} = "your-key"      (PowerShell, this window only)'
    return f'export {key_env}="your-key"'


def _refuse_existing(directory: Path, names: tuple[str, ...], force: bool) -> None:
    existing = [name for name in names if (directory / name).exists()]
    if existing and not force:
        typer.echo(f"[FAIL] already exists: {', '.join(existing)}. Use --force to overwrite.")
        raise typer.Exit(code=1)


def _choose(question: str, options: list[str], default: int = 1) -> int:
    """A numbered menu, set apart by an empty line. Returns the chosen number, 1-based."""
    typer.echo("")
    typer.echo(question)
    for number, option in enumerate(options, start=1):
        typer.echo(f"  {number}) {option}")
    while True:
        answer = typer.prompt(f"Choose 1-{len(options)}", default=str(default)).strip()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer)
        typer.echo(f"Please type a number from 1 to {len(options)}.")


def _ask_target() -> dict[str, object]:
    kind = _choose(
        "What do you want to test?",
        [
            "a model on this computer (Ollama)",
            "a model behind an API (OpenAI, OpenRouter, LM Studio or another OpenAI-style server)",
            "your own chatbot behind a web address (HTTP)",
        ],
    )
    if kind == 1:
        model = typer.prompt("Ollama model name", default="qwen2.5:7b").strip()
        return {"type": "ollama", "model": model}
    if kind == 2:
        service = _choose(
            "Which service?",
            ["OpenAI", "OpenRouter", "LM Studio (on this computer)", "another OpenAI-style server"],
        )
        if service <= 3:
            prefix, example = [
                ("openai", "gpt-4o-mini"),
                ("openrouter", "meta-llama/llama-3.1-8b-instruct"),
                ("lmstudio", "qwen2.5-7b-instruct"),
            ][service - 1]
            model = typer.prompt("Model name", default=example).strip()
            preset = parse_target(f"{prefix}:{model}")
            return preset.model_dump(include={"type", "model", "base_url", "api_key_env"},
                                     exclude_none=True)
        base_url = typer.prompt("API address (ending in /v1)").strip()
        model = typer.prompt("Model name").strip()
        key_env = typer.prompt(
            "Name of the environment variable with the API key", default="OPENAI_API_KEY"
        ).strip()
        return {"type": "openai", "model": model, "base_url": base_url, "api_key_env": key_env}
    url = typer.prompt("Web address of the chat endpoint (https://...)").strip()
    request_field = typer.prompt("JSON field that carries the question", default="message").strip()
    reply_field = typer.prompt(
        "JSON field of the answer in the reply (dotted path, e.g. data.text)", default="answer"
    ).strip()
    key_env = typer.prompt(
        "Name of the environment variable with the API key (leave empty if none)", default=""
    ).strip()
    target: dict[str, object] = {
        "type": "http", "url": url, "request_field": request_field, "reply_field": reply_field
    }
    if key_env:
        target["api_key_env"] = key_env
    return target


def _setup_wizard(agents: bool) -> tuple[str, str | None, bool]:
    """Ask what to test and return (vespera.yaml text, key variable name, agent mode)."""
    typer.echo("Vespera setup. Press Enter to keep the value in [brackets].")
    while True:
        target = _ask_target()
        mode = "text"
        if target["type"] != "http":
            choice = _choose(
                "What should be tested?",
                [
                    "a chatbot: attack questions, leaks, needless refusals",
                    "an agent that calls tools: hidden orders, data sent away, forbidden actions",
                ],
                default=2 if agents else 1,
            )
            mode = "agent" if choice == 2 else "text"
        typer.echo("")
        repeats = typer.prompt("How many times to ask each question", default=3, type=int)
        strict = typer.confirm(
            "Fail the check if personal or secret data leaks even once?", default=True
        )
        rate = typer.prompt(
            "Fail if more than this share of attacks succeeds, in %", default=30, type=int
        )
        raw: dict[str, object] = {
            "target": target,
            "mode": mode,
            "repeats": repeats,
            "gate": {
                "max_attack_rate": rate / 100,
                "zero_tolerance": ["LLM02"] if strict else [],
            },
            "exports": {"results_json": "runs/results.json", "junit_xml": "runs/junit.xml"},
        }
        try:
            project = ProjectConfig.model_validate(raw)
        except ValidationError as exc:
            for problem in exc.errors():
                typer.echo(f"[FAIL] {'.'.join(map(str, problem['loc']))}: {problem['msg']}")
            typer.echo("Let's try again.\n")
            continue
        header = (
            "# Vespera project file, written by `vespera init`. `vespera test` reads it.\n"
            "# Secrets never go here: only the NAME of the environment variable with a key.\n"
            "# All settings: https://github.com/yaniquee26/vespera/blob/main/docs/connect-your-bot.md\n\n"
        )
        body = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
        return header + body, project.target.api_key_env, project.mode == "agent"


def _config_text(agents: bool) -> str:
    """The project file template, switched to agent mode for agent tests."""
    text = read_bundled(CONFIG_TEMPLATE)
    if agents:
        text = text.replace("mode: text ", "mode: agent", 1)
    return text


@app.command("test")
@_reports_service_errors
def test_command(
    target_spec: Annotated[
        str | None,
        typer.Argument(
            metavar="TARGET",
            help=f"What to test, in one word (default: vespera.yaml). Examples: {TARGET_EXAMPLES}.",
            show_default=False,
        ),
    ] = None,
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
    resume: Annotated[
        int | None,
        typer.Option(help="Continue an interrupted run by its id, with the same settings."),
    ] = None,
    mock: Annotated[bool, typer.Option(help="Use a fake model (no GPU, for trying out).")] = False,
    max_attack_rate: MaxRateOption = None,
    zero_tolerance: ZeroToleranceOption = None,
    min_exposure: MinExposureOption = None,
    json_path: JsonOption = None,
    junit_path: JunitOption = None,
    reveal: RevealOption = False,
    open_report: OpenOption = False,
) -> None:
    """Run, check and report in one go. Can fail the build (for CI).

    Settings come from TARGET and the flags first, then vespera.yaml, then the defaults.

    Examples: `vespera test`, `vespera test openai:gpt-4o-mini --open`.
    """
    project = load_project(config, no_config)
    if project is not None:
        typer.echo(f"Using the project file {config or 'vespera.yaml'}")
    chosen_mode = mode or (project.mode if project else "text")
    if chosen_mode not in ("text", "agent"):
        typer.echo("[FAIL] --mode must be text or agent")
        raise typer.Exit(code=1)
    target = resolve_target(project, model, provider_kind, base_url, api_key_env, target_spec)
    if chosen_mode == "agent" and target.type == "http":
        typer.echo("[FAIL] agent mode needs a model that can call tools (openai or ollama)")
        raise typer.Exit(code=1)
    if target.type == "demo":
        if chosen_mode == "agent":
            raise TargetError(DEMO_AGENT_PROBLEM, tip=DEMO_AGENT_TIP)
        typer.echo(DEMO_NOTICE)

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
            scenario_file, prompt_file, times, first_n, steps, db, resume,
        )
    else:
        run_id = _execute_run(
            lambda: _target_provider(target, mock),
            scenario_file, prompt_file, times, first_n, db, resume,
        )
    check(run_id, db=db, system_prompt=prompt_file, mask_output=not reveal)
    report(run_id, db=db, out=out or (project.report if project else None), open_report=open_report)
    policy = _policy(max_attack_rate, zero_tolerance, min_exposure, project)
    json_out, junit_out = export_paths(project, json_path, junit_path)
    _finish(db, run_id, policy if has_rules(policy) else None, json_out, junit_out, reveal)


UI_EXTRA_HINT = (
    'Vespera Studio needs extra packages: pip install "vespera[ui] @ '
    'git+https://github.com/yaniquee26/vespera" (or pip install -e ".[ui]" in a checkout)'
)


@app.command("ui")
def ui_command(
    workspace: Annotated[
        Path | None,
        typer.Option(help="Folder for Studio's database and reports "
                     "(default: VESPERA_HOME, else ~/Vespera)."),
    ] = None,
    port: Annotated[
        int, typer.Option(min=1024, max=65535, help="Port on 127.0.0.1.")
    ] = 8765,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open Studio in the browser.")
    ] = True,
) -> None:
    """Start Vespera Studio, the local web app. It listens on this computer only (127.0.0.1)."""
    try:
        from vespera.ui.server import address, serve
    except ImportError as exc:
        typer.echo(f"[FAIL] {UI_EXTRA_HINT}")
        raise typer.Exit(code=1) from exc
    space = resolve_workspace(workspace).ensure()
    typer.echo(f"Vespera Studio: {address(port)}")
    typer.echo(f"Workspace: {space.root}")
    if DEFAULT_DB.exists() and DEFAULT_DB.resolve() != space.database.resolve():
        typer.echo(
            f"This folder has its own runs ({DEFAULT_DB}). To see them in Studio, "
            "start it with: vespera ui --workspace ."
        )
    typer.echo("Press Ctrl+C to stop.")
    serve(space, port, open_browser)


@app.command("gate")
@_reports_service_errors
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
    project = load_project(config, no_config)
    policy = _policy(max_attack_rate, zero_tolerance, min_exposure, project)
    if not has_rules(policy):
        typer.echo(
            "[FAIL] give at least one rule: --max-attack-rate, --zero-tolerance "
            "or a gate section in vespera.yaml"
        )
        raise typer.Exit(code=1)
    json_out, junit_out = export_paths(project, json_path, junit_path)
    _finish(db, _chosen_run(db, run_id), policy, json_out, junit_out, reveal)


@app.command("export")
@_reports_service_errors
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
@_reports_service_errors
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
    run_id, summary = execute_agent_run(
        make_provider, scenarios, system_prompt, repeats, limit, max_steps, db, resume,
        on_start=_print_start, on_progress=_print_progress,
    )
    return _print_saved(run_id, summary, db)
