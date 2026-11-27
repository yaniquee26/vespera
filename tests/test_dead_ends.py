"""Every error a newcomer meets first names a next step that really works."""

import sqlite3
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from vespera import cli
from vespera import storage as storage_module
from vespera.providers import MockProvider, OllamaProvider, ProviderError
from vespera.services import targets
from vespera.storage import NotADatabaseError, Storage

app = cli.app
runner = CliRunner()
SMALL = ["--limit", "3", "--repeats", "1", "--no-config"]


class ServerDown(MockProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise ProviderError("Ollama request failed: connection refused")


def _example_files() -> None:
    result = runner.invoke(app, ["init", "--no-interactive"])
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------- C2: resume after an abort


def test_an_interrupted_test_tells_how_to_resume_and_that_command_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _example_files()
    with monkeypatch.context() as patch:
        patch.setattr(targets, "MockProvider", ServerDown)
        broken = runner.invoke(app, ["test", "mock", *SMALL])
    assert broken.exit_code == 1
    assert "continue with --resume 1." in broken.output

    resumed = runner.invoke(app, ["test", "mock", *SMALL, "--resume", "1"])
    assert resumed.exit_code == 0, resumed.output
    assert "Resuming run 1" in resumed.output
    assert "[OK] run 1: 3 attempts, 0 errors, 0 skipped" in resumed.output
    assert "Checked run 1" in resumed.output


def test_resuming_with_other_settings_says_what_to_do() -> None:
    _example_files()
    assert runner.invoke(app, ["test", "mock", *SMALL]).exit_code == 0
    result = runner.invoke(app, ["test", "mock", "--limit", "3", "--repeats", "2", "--no-config",
                                 "--resume", "1"])
    assert result.exit_code == 1
    assert "[FAIL] model, repeats or input files differ from the original run" in result.output
    assert "Tip: resume with the same model, repeats and files" in result.output


def test_resuming_a_run_that_does_not_exist_lists_the_runs() -> None:
    _example_files()
    result = runner.invoke(app, ["test", "mock", *SMALL, "--resume", "7"])
    assert result.exit_code == 1
    assert "[FAIL] no run with id 7 in" in result.output
    assert "`vespera runs`" in result.output


# ---------------------------------------------------------------- O2: test before init


def test_test_before_init_says_to_run_init() -> None:
    result = runner.invoke(app, ["test", "mock", "--no-config"])
    assert result.exit_code == 1
    assert "[FAIL] " in result.output
    assert "Tip: `vespera init` copies the example files here." in result.output


# ---------------------------------------------------------------- E1: --db is not a database


@pytest.mark.parametrize(
    "command", [["runs"], ["show"], ["report"], ["check"], ["compare", "1", "2"]]
)
def test_a_file_that_is_not_a_database_is_one_fail_line(command: list[str]) -> None:
    Path("notes.txt").write_text("not a database, just text\n" * 50, encoding="utf-8")
    result = runner.invoke(app, [*command, "--db", "notes.txt"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception
    assert "[FAIL] notes.txt is not a Vespera database" in result.output
    assert f"{cli.DB_TIP}\n" in result.output
    assert "Traceback" not in result.output


def test_storage_closes_the_file_when_it_is_not_a_database() -> None:
    path = Path("notes.txt")
    path.write_text("not a database, just text\n" * 50, encoding="utf-8")
    with pytest.raises(NotADatabaseError):
        Storage(path)
    path.unlink()  # on Windows this fails while a connection is still open


def test_other_database_errors_are_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_module, "_SCHEMA", "SELECT * FROM no_such_table;")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        Storage(Path("runs/new.db"))
    Path("runs/new.db").unlink()  # the connection was closed


# ---------------------------------------------------------------- E2: next step after a failure


def test_gate_without_runs_says_how_to_list_and_start_runs() -> None:
    result = runner.invoke(app, ["gate", "--max-attack-rate", "0.3", "--no-config"])
    assert result.exit_code == 1
    assert "[FAIL] no such run" in result.output
    assert "Tip: list the runs with `vespera runs`; start one with `vespera test`." in result.output


def test_check_with_an_edited_system_prompt_says_how_to_pass_the_original() -> None:
    _example_files()
    assert runner.invoke(app, ["test", "mock", *SMALL]).exit_code == 0
    prompt = Path("system_prompt.txt")
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\nOne more rule.\n", encoding="utf-8")
    result = runner.invoke(app, ["check", "1"])
    assert result.exit_code == 1
    assert "[FAIL] the system prompt differs from the one used in this run" in result.output
    assert "--system-prompt" in result.output


# ---------------------------------------------------------------- O1: Ollama is not running


def test_ollama_down_asks_whether_it_runs() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(base_url="http://localhost:11434", transport=httpx.MockTransport(refuse))
    provider = OllamaProvider("qwen2.5:7b", client=client)
    with pytest.raises(ProviderError) as caught:
        provider.generate("system", "hello")
    message = str(caught.value)
    assert message.startswith("Ollama request failed: connection refused")
    assert message.endswith("Is Ollama running? Check with `vespera doctor`")
