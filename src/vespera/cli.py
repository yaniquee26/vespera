"""Command line interface: `vespera version`, `doctor`, `ask`."""

import typer

from vespera import __version__
from vespera.doctor import all_required_ok, run_all
from vespera.providers import OllamaProvider, ProviderError

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