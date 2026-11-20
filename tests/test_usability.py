"""The short ways in: a target in one word, the setup questions, the run list, --open."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from vespera import cli
from vespera.config import ConfigError, load_config, parse_target

app = cli.app
runner = CliRunner()


# ---------------------------------------------------------------- target in one word


def test_a_plain_name_is_a_local_ollama_model_even_with_a_colon() -> None:
    target = parse_target("qwen2.5:7b")
    assert (target.type, target.model, target.base_url) == ("ollama", "qwen2.5:7b", None)
    assert parse_target("ollama:llama3.1:8b").model == "llama3.1:8b"


def test_a_service_prefix_fills_the_address_and_the_key_variable() -> None:
    openai = parse_target("openai:gpt-4o-mini")
    assert (openai.type, openai.model) == ("openai", "gpt-4o-mini")
    assert openai.base_url == "https://api.openai.com/v1"
    assert openai.api_key_env == "OPENAI_API_KEY"
    router = parse_target("openrouter:meta-llama/llama-3.1-8b-instruct")
    assert router.model == "meta-llama/llama-3.1-8b-instruct"
    assert router.api_key_env == "OPENROUTER_API_KEY"
    local = parse_target(" lmstudio:my-model ")
    assert local.base_url == "http://localhost:1234/v1" and local.api_key_env is None
    assert parse_target("mock").type == "mock"


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("https://bot.example.com/chat", "vespera.yaml"),
        ("openai:", "give a model"),
        ("", "empty target"),
        ("ollama:", "empty target"),
    ],
)
def test_a_target_that_does_not_fit_in_one_word_is_refused_with_a_hint(
    spec: str, message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_target(spec)


def _example_files() -> None:
    result = runner.invoke(app, ["init", "--agents", "--no-interactive"])
    assert result.exit_code == 0, result.output


def test_test_takes_the_target_from_the_command_line() -> None:
    _example_files()
    result = runner.invoke(app, ["test", "mock", "--limit", "2", "--repeats", "1", "--no-config"])
    assert result.exit_code == 0, result.output
    assert "Started run 1: 2 cases x 1 repeats" in result.output
    assert "model mock" in result.output


def test_the_target_word_wins_over_vespera_yaml() -> None:
    _example_files()  # vespera.yaml points at a local Ollama model
    result = runner.invoke(
        app, ["test", "mock", "--mode", "agent", "--limit", "1", "--repeats", "1"]
    )
    assert "Using the project file vespera.yaml" in result.output
    assert "model mock" in result.output
    # the gate still comes from the file: the mock agent never opens the poisoned tool
    assert result.exit_code == 3 and "Gate: inconclusive" in result.output


def test_a_hosted_target_without_its_key_stops_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _example_files()
    result = runner.invoke(app, ["test", "openrouter:some/model", "--no-config", "--limit", "1"])
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY" in result.output


def test_a_bad_target_word_is_a_usage_error() -> None:
    result = runner.invoke(app, ["test", "https://bot.example.com", "--no-config"])
    assert result.exit_code == 1
    assert "vespera.yaml" in result.output


# ---------------------------------------------------------------- --open


def test_open_shows_the_report_in_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    _example_files()
    result = runner.invoke(
        app, ["test", "mock", "--limit", "1", "--repeats", "1", "--no-config", "--open"]
    )
    assert result.exit_code == 0, result.output
    assert "opening it in the browser" in result.output
    assert len(opened) == 1 and opened[0].startswith("file:")
    assert opened[0].endswith("report-run-1.html")


def test_without_open_nothing_is_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    _example_files()
    result = runner.invoke(app, ["test", "mock", "--limit", "1", "--repeats", "1", "--no-config"])
    assert result.exit_code == 0, result.output
    assert opened == [] and "or add --open" in result.output


# ---------------------------------------------------------------- vespera runs


def test_runs_without_a_database_says_how_to_start() -> None:
    result = runner.invoke(app, ["runs"])
    assert result.exit_code == 0
    assert "No runs yet" in result.output and "vespera test" in result.output


def test_runs_with_an_empty_database() -> None:
    Path("runs").mkdir()
    sqlite_file = Path("runs/vespera.db")
    cli.Storage(sqlite_file).close()
    result = runner.invoke(app, ["runs"])
    assert result.exit_code == 0 and "No runs yet" in result.output


def test_runs_lists_every_run_with_its_mode_size_and_attack_rate() -> None:
    _example_files()
    runner.invoke(app, ["test", "mock", "--limit", "3", "--repeats", "2", "--no-config"])
    runner.invoke(app, ["run-agent", "--mock", "--limit", "2"])  # saved but not checked
    result = runner.invoke(app, ["runs"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["ID", "STARTED", "MODEL", "MODE", "SIZE", "ATTACKS", "SUCCEEDED"]
    first = next(line for line in lines if line.startswith("1 "))
    assert "mock" in first and "text" in first and "3 x 2" in first
    assert "95% CI" in first
    second = next(line for line in lines if line.startswith("2 "))
    assert "agent" in second and "not checked" in second
    assert "vespera compare A B" in result.output


def test_runs_shows_an_unfinished_run() -> None:
    _example_files()
    runner.invoke(app, ["test", "mock", "--limit", "1", "--repeats", "1", "--no-config"])
    db = Path("runs/vespera.db")
    import sqlite3

    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM attempts WHERE run_id = 1")
    result = runner.invoke(app, ["runs"])
    assert "(0/1 done)" in result.output


# ---------------------------------------------------------------- setup questions


def _init(answers: list[str], *extra: str) -> str:
    result = runner.invoke(app, ["init", "--interactive", *extra], input="\n".join(answers) + "\n")
    assert result.exit_code == 0, result.output
    return result.output


def test_setup_for_a_local_model_writes_a_ready_agent_project() -> None:
    # local Ollama, default model, agent mode, defaults for repeats, leaks and the rate
    output = _init(["1", "", "2", "", "", ""])
    project = load_config(Path("vespera.yaml"))
    assert (project.target.type, project.target.model) == ("ollama", "qwen2.5:7b")
    assert project.mode == "agent" and project.repeats == 3
    assert project.gate.zero_tolerance == ["LLM02"] and project.gate.max_attack_rate == 0.3
    assert Path("agent_scenarios_v1.yaml").exists()
    assert "Ready. Run:  vespera test --open" in output
    assert "environment variable" not in output  # a local model needs no key


def test_setup_for_a_hosted_api_reminds_to_set_the_key_and_never_asks_for_it() -> None:
    # API, OpenRouter, a model, text mode, 5 repeats, no zero tolerance, 20%
    output = _init(["2", "2", "meta-llama/llama-3.1-8b-instruct", "1", "5", "n", "20"])
    project = load_config(Path("vespera.yaml"))
    assert project.target.base_url == "https://openrouter.ai/api/v1"
    assert project.target.api_key_env == "OPENROUTER_API_KEY"
    assert project.mode == "text" and project.repeats == 5
    assert project.gate.zero_tolerance == [] and project.gate.max_attack_rate == 0.2
    assert "OPENROUTER_API_KEY" in output and "never written" in output
    assert not Path("agent_scenarios_v1.yaml").exists()


def test_setup_for_another_openai_style_server() -> None:
    _init(["2", "4", "http://localhost:8000/v1", "my-model", "MY_KEY", "1", "", "", ""])
    target = load_config(Path("vespera.yaml")).target
    assert (target.type, target.base_url, target.model) == (
        "openai", "http://localhost:8000/v1", "my-model"
    )
    assert target.api_key_env == "MY_KEY"


def test_setup_for_an_http_bot_skips_the_mode_question() -> None:
    _init(["3", "https://bot.example.com/chat", "", "data.text", "", "", "", ""])
    project = load_config(Path("vespera.yaml"))
    assert project.target.type == "http" and project.target.reply_field == "data.text"
    assert project.target.request_field == "message" and project.target.api_key_env is None
    assert project.mode == "text"


def test_setup_for_an_http_bot_with_a_key() -> None:
    output = _init(["3", "https://bot.example.com/chat", "text", "reply", "BOT_KEY", "", "", ""])
    assert load_config(Path("vespera.yaml")).target.api_key_env == "BOT_KEY"
    assert "BOT_KEY" in output and "never written" in output


def test_the_key_reminder_uses_the_shell_of_the_system() -> None:
    assert cli._set_key_command("BOT_KEY", windows=True).startswith('$env:BOT_KEY = "your-key"')
    assert cli._set_key_command("BOT_KEY", windows=False) == 'export BOT_KEY="your-key"'


def test_setup_asks_again_after_a_wrong_number() -> None:
    output = _init(["7", "x", "1", "", "1", "", "", ""])
    assert output.count("Please type a number from 1 to 3") == 2
    assert load_config(Path("vespera.yaml")).mode == "text"


def test_setup_asks_again_when_the_answers_do_not_make_a_valid_project() -> None:
    # 0 repeats is refused by the project rules; the second round uses valid answers
    output = _init(["1", "", "1", "0", "", "", "1", "", "1", "", "", ""])
    assert "Let's try again" in output
    assert load_config(Path("vespera.yaml")).repeats == 3


def test_setup_does_not_start_when_files_already_exist() -> None:
    Path("vespera.yaml").write_text("target: {type: mock}\n", encoding="utf-8")
    result = runner.invoke(app, ["init", "--interactive"], input="1\n")
    assert result.exit_code == 1 and "already exists" in result.output
    assert "What do you want to test" not in result.output


def test_without_a_terminal_init_copies_the_template_without_questions() -> None:
    result = runner.invoke(app, ["init"])  # the test runner is not a terminal
    assert result.exit_code == 0, result.output
    assert "What do you want to test" not in result.output
    assert "open vespera.yaml and set the target" in result.output
