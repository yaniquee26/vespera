"""Command line interface: `vespera version`, `doctor`, `ask`."""

import os
from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from vespera import __version__
from vespera.bundled import BUNDLED_FILES, read_bundled
from vespera.checks import CheckConfigError, run_checks
from vespera.compare import build_comparison, render_comparison_html
from vespera.contracts import Category
from vespera.doctor import all_required_ok, run_all
from vespera.providers import (
    BaseProvider,
    MockProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderError,
)
from vespera.report import ReportError, build_report, render_html
from vespera.runner import RunAborted, run_experiment
from vespera.scenarios import ScenarioFileError, expand_cases, load_scenarios
from vespera.snapshot import build_snapshot, sha256_text
from vespera.storage import Storage
from vespera.summary import summarize

app = typer.Typer(help="Evidence-based reliability testing for LLMs.", no_args_is_help=True)


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
    """Check that a scenario file is valid and show what it contains."""
    try:
        scenarios = load_scenarios(path)
    except ScenarioFileError as exc:
        typer.echo(f"[FAIL] {exc}")
        raise typer.Exit(code=1) from exc
    cases = expand_cases(scenarios)
    typer.echo(f"[OK] {path}: {len(scenarios)} scenarios, {len(cases)} cases")
    per_category = Counter(scenario.category for scenario in scenarios)
    for category in Category:
        if per_category[category]:
            typer.echo(f"  {category.value}: {per_category[category]}")


DEFAULT_DB = Path("runs/vespera.db")


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
    try:
        loaded = load_scenarios(scenarios)
        system_text = system_prompt.read_text(encoding="utf-8")
        scenarios_text = scenarios.read_text(encoding="utf-8")
    except (ScenarioFileError, OSError) as exc:
        typer.echo(f"[FAIL] {exc}")
        raise typer.Exit(code=1) from exc

    cases = expand_cases(loaded)[:limit]
    if provider_kind not in ("ollama", "openai"):
        typer.echo("[FAIL] --provider must be ollama or openai")
        raise typer.Exit(code=1)
    provider: BaseProvider
    if mock:
        provider = MockProvider(["I cannot help with that."])
    elif provider_kind == "openai":
        key = os.environ.get(api_key_env)
        address = base_url or "https://api.openai.com/v1"
        if not key and "localhost" not in address and "127.0.0.1" not in address:
            typer.echo(f"[FAIL] set the environment variable {api_key_env} with your API key")
            raise typer.Exit(code=1)
        provider = OpenAICompatibleProvider(model=model, base_url=address, api_key=key)
    else:
        provider = OllamaProvider(model=model)
    snapshot = build_snapshot(
        model=provider.model,
        temperature=provider.temperature,
        num_predict=provider.num_predict,
        repeats=repeats,
        system_prompt=system_text,
        scenarios_text=scenarios_text,
    )

    storage = Storage(db)
    try:
        if resume is None:
            run_id = storage.create_run(snapshot)
            storage.save_cases(run_id, cases)
            typer.echo(f"Started run {run_id}: {len(cases)} cases x {repeats} repeats")
        else:
            if not storage.run_exists(resume):
                typer.echo(f"[FAIL] no run with id {resume} in {db}")
                raise typer.Exit(code=1)
            old = storage.get_snapshot(resume)
            same = (
                old.model == snapshot.model
                and old.system_prompt_sha256 == snapshot.system_prompt_sha256
                and old.scenarios_sha256 == snapshot.scenarios_sha256
                and old.repeats == snapshot.repeats
            )
            if not same:
                typer.echo("[FAIL] model, repeats or input files differ from the original run")
                raise typer.Exit(code=1)
            run_id = resume
            typer.echo(f"Resuming run {run_id}")

        def progress(done: int, total: int, case_id: str, repeat: int, error: str | None) -> None:
            status = "ERROR" if error else "ok"
            typer.echo(f"[{done}/{total}] {case_id} (repeat {repeat}) {status}")

        try:
            summary = run_experiment(
                provider, cases, system_text, storage, run_id, repeats, on_progress=progress
            )
        except RunAborted as exc:
            typer.echo(f"[FAIL] {exc}")
            raise typer.Exit(code=1) from exc
        typer.echo(
            f"[OK] run {run_id}: {summary.attempted} attempts, {summary.errors} errors, "
            f"{summary.skipped} skipped. Saved to {db}"
        )
    finally:
        storage.close()


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
    system_prompt: Annotated[Path, typer.Option(help="System prompt file.")] = Path(
        "system_prompt.txt"
    ),
) -> None:
    """Run the automatic checks on a saved run and print what the model got wrong."""
    storage = Storage(db)
    try:
        chosen = run_id if run_id is not None else storage.latest_run_id()
        if chosen is None or not storage.run_exists(chosen):
            typer.echo("[FAIL] no such run")
            raise typer.Exit(code=1)
        try:
            system_text = system_prompt.read_text(encoding="utf-8")
        except OSError as exc:
            typer.echo(f"[FAIL] cannot read {system_prompt}: {exc}")
            raise typer.Exit(code=1) from exc
        snapshot = storage.get_snapshot(chosen)
        if sha256_text(system_text) != snapshot.system_prompt_sha256:
            typer.echo("[FAIL] the system prompt differs from the one used in this run")
            raise typer.Exit(code=1)

        cases = storage.get_cases(chosen)
        try:
            results = run_checks(cases, storage.get_attempts(chosen), system_text)
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
        if summary.violations:
            typer.echo("Violations:")
            for v in summary.violations:
                typer.echo(f"  {v.case_id} #{v.repeat} [{v.check}] {v.detail}")
                typer.echo(f"      > {v.excerpt}")
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
) -> None:
    """Copy the example scenarios and system prompt into a folder, ready to edit."""
    directory.mkdir(parents=True, exist_ok=True)
    existing = [name for name in BUNDLED_FILES if (directory / name).exists()]
    if existing and not force:
        typer.echo(f"[FAIL] already exists: {', '.join(existing)}. Use --force to overwrite.")
        raise typer.Exit(code=1)
    for name in BUNDLED_FILES:
        (directory / name).write_text(read_bundled(name), encoding="utf-8")
        typer.echo(f"[OK] wrote {directory / name}")
    typer.echo("Next: put YOUR bot's system prompt in system_prompt.txt")
    typer.echo("(keep a CANARY-XXXXXX line), then run `vespera test --model <name>`.")


@app.command("test")
def test_command(
    model: Annotated[str, typer.Option(help="Model name.")] = "gemma3:4b",
    provider_kind: Annotated[
        str, typer.Option("--provider", help="ollama (local) or openai (any OpenAI-style API).")
    ] = "ollama",
    base_url: Annotated[str | None, typer.Option(help="API address for --provider openai.")] = None,
    api_key_env: Annotated[
        str, typer.Option(help="Name of the environment variable that holds the API key.")
    ] = "OPENAI_API_KEY",
    repeats: Annotated[int, typer.Option(min=1, help="How many times to ask each case.")] = 3,
    limit: Annotated[int | None, typer.Option(min=1, help="Only the first N cases.")] = None,
    scenarios: Annotated[Path, typer.Option(help="Scenario file.")] = Path("scenarios_v1.yaml"),
    system_prompt: Annotated[Path, typer.Option(help="System prompt file.")] = Path(
        "system_prompt.txt"
    ),
    db: Annotated[Path, typer.Option(help="SQLite file with results.")] = DEFAULT_DB,
    out: Annotated[Path | None, typer.Option(help="Where to write the HTML report.")] = None,
    mock: Annotated[bool, typer.Option(help="Use a fake model (no GPU, for trying out).")] = False,
    max_attack_rate: Annotated[
        float | None,
        typer.Option(
            min=0.0,
            max=1.0,
            help="Fail (exit code 2) if the share of successful attacks is above this, 0 to 1.",
        ),
    ] = None,
) -> None:
    """Run, check and report in one go. Optionally fail when the bot is too weak (for CI)."""
    run(
        model=model,
        provider_kind=provider_kind,
        base_url=base_url,
        api_key_env=api_key_env,
        repeats=repeats,
        limit=limit,
        scenarios=scenarios,
        system_prompt=system_prompt,
        db=db,
        resume=None,
        mock=mock,
    )
    storage = Storage(db)
    try:
        run_id = storage.latest_run_id()
    finally:
        storage.close()
    if run_id is None:
        typer.echo("[FAIL] no run was saved")
        raise typer.Exit(code=1)
    check(run_id, db=db, system_prompt=system_prompt)
    report(run_id, db=db, out=out)
    if max_attack_rate is None:
        return
    storage = Storage(db)
    try:
        rate = build_report(storage, run_id).attack_rate
    finally:
        storage.close()
    if rate is None:
        typer.echo("[FAIL] no attack attempts were judged, so the limit cannot be checked")
        raise typer.Exit(code=1)
    if rate.p > max_attack_rate:
        typer.echo(
            f"[FAIL] attacks succeeded in {rate.p:.0%} of attempts, "
            f"above the allowed {max_attack_rate:.0%}"
        )
        raise typer.Exit(code=2)
    typer.echo(f"[OK] attacks succeeded in {rate.p:.0%}, within the allowed {max_attack_rate:.0%}")