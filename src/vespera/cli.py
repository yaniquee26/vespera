"""Command line interface: `vespera version`, `doctor`, `ask`."""

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from vespera import __version__
from vespera.contracts import Category
from vespera.doctor import all_required_ok, run_all
from vespera.providers import OllamaProvider, ProviderError
from vespera.scenarios import ScenarioFileError, expand_cases, load_scenarios

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