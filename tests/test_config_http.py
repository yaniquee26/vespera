"""vespera.yaml, the plain-HTTP bot target, and `vespera test` driven by the project file."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from vespera import cli
from vespera.bundled import read_bundled
from vespera.cli import _config_text, _policy
from vespera.config import ConfigError, GateConfig, ProjectConfig, TargetConfig, load_config
from vespera.providers import HttpProvider, ProviderError, RequestPolicy, dig, put
from vespera.services import targets

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"
CANARY = "CANARY-7F3A91"


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------- the project file


def test_the_shipped_templates_are_valid_project_files(tmp_path: Path) -> None:
    plain = load_config(write(tmp_path / "a.yaml", _config_text(agents=False)))
    assert plain.mode == "text" and plain.target.model == "qwen2.5:7b"
    assert plain.gate.zero_tolerance == ["LLM02"] and plain.exports.junit_xml is not None
    agent = load_config(write(tmp_path / "b.yaml", _config_text(agents=True)))
    assert agent.mode == "agent" and agent.target.model == "qwen2.5:7b"
    assert read_bundled("vespera.yaml") == _config_text(agents=False)


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("target: {type: ollama, model: m}\nrepeat: 3\n", "repeat"),  # a typo is not ignored
        ("target: {type: ollama}\n", "target.model is required"),
        ("target: {type: http, reply_field: a}\n", "target.url is required"),
        ("target: {type: http, url: 'https://x.test/c'}\n", "target.reply_field is required"),
        ("target: {type: nope, model: m}\n", "target.type"),
        ("target: {type: ollama, model: m}\nmode: agent\ngate: {max_attack_rate: 2}\n", "gate"),
        ("target: {type: ollama, model: m}\nrepeats: 0\n", "repeats"),
        ("target: {type: ollama, model: m, api_key: secret}\n", "api_key"),  # keys never in file
        ("just text\n", "mapping"),
        ("target: [unclosed\n", "not valid YAML"),
    ],
)
def test_a_wrong_project_file_is_refused_with_the_place_named(
    tmp_path: Path, text: str, fragment: str
) -> None:
    with pytest.raises(ConfigError, match=fragment):
        load_config(write(tmp_path / "v.yaml", text))


def test_agent_mode_needs_a_tool_calling_target_and_a_missing_file_is_explained(
    tmp_path: Path,
) -> None:
    text = "target: {type: http, url: 'https://x.test/c', reply_field: a}\nmode: agent\n"
    with pytest.raises(ConfigError, match="tool calling"):
        load_config(write(tmp_path / "v.yaml", text))
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.yaml")


def test_flags_beat_the_file_and_the_file_beats_the_defaults() -> None:
    project = ProjectConfig(
        target=TargetConfig(type="mock"),
        gate=GateConfig(max_attack_rate=0.2, zero_tolerance=["LLM02"], min_exposure=0.8),
    )
    from_file = _policy(None, None, None, project)
    assert (from_file.max_attack_rate, from_file.zero_tolerance, from_file.min_exposure) == (
        0.2,
        ("LLM02",),
        0.8,
    )
    flags = _policy(0.5, "LLM03, pii_leak", 0.1, project)
    assert (flags.max_attack_rate, flags.zero_tolerance, flags.min_exposure) == (
        0.5,
        ("LLM03", "pii_leak"),
        0.1,
    )
    nothing = _policy(None, None, None)
    assert nothing.max_attack_rate is None and nothing.zero_tolerance == ()
    assert nothing.min_exposure == 0.5


# ---------------------------------------------------------------- JSON paths


def test_dig_follows_dotted_paths_through_objects_and_lists() -> None:
    data = {"data": [{"text": "hi"}, {"text": "yo"}], "answer": "top"}
    assert dig(data, "answer") == "top"
    assert dig(data, "data.1.text") == "yo"
    for bad in ("nope", "data.5.text", "data.x.text", "answer.deeper"):
        with pytest.raises(KeyError):
            dig(data, bad)


def test_put_builds_nested_objects_and_refuses_to_overwrite_a_fixed_value() -> None:
    body: dict[str, Any] = {"session": "s1"}
    put(body, "input.text", "hello")
    assert body == {"session": "s1", "input": {"text": "hello"}}
    with pytest.raises(ValueError, match="collides"):
        put({"input": "fixed"}, "input.text", "x")


# ---------------------------------------------------------------- the HTTP bot


def bot(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_http_provider_sends_the_question_and_reads_the_answer_by_path() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"text": "Hello!"}]})

    provider = HttpProvider(
        "https://bot.example.com/chat?key=abc",
        reply_field="data.0.text",
        request_field="input.text",
        extra_body={"session": "vespera", "input": {"lang": "ro"}},
        system_field="instructions",
        client=bot(handler),
    )
    assert provider.generate("be nice", "question one") == "Hello!"
    assert provider.generate("be nice", "question two") == "Hello!"
    assert seen[0] == {
        "session": "vespera",
        "input": {"lang": "ro", "text": "question one"},
        "instructions": "be nice",
    }
    assert seen[1]["input"]["text"] == "question two"  # the fixed part was not changed
    assert "abc" not in provider.model  # the query string may hold a key
    assert provider.model == "bot.example.com/chat"


def test_http_provider_sends_the_key_in_the_chosen_header_and_stores_nothing_else() -> None:
    provider = HttpProvider(
        "https://bot.example.com/chat",
        reply_field="answer",
        api_key="sk-secret",
        api_key_header="X-Api-Key",
        api_key_prefix="",
    )
    assert provider._client.headers["X-Api-Key"] == "sk-secret"
    default = HttpProvider("https://b.test/c", reply_field="a", api_key="k")
    assert default._client.headers["Authorization"] == "Bearer k"


@pytest.mark.parametrize(
    ("response", "fragment"),
    [
        (httpx.Response(401), "refused the API key"),
        (httpx.Response(404), "was not found"),
        (httpx.Response(429), "too many requests"),
        (httpx.Response(500), "HTTP 500"),
        (httpx.Response(200, text="<html>nope</html>"), "did not answer with JSON"),
        (httpx.Response(200, json={"other": "x"}), "no reply found at 'answer'"),
        (httpx.Response(200, json={"answer": "  "}), "empty or not text"),
        (httpx.Response(200, json={"answer": 42}), "empty or not text"),
    ],
)
def test_http_provider_explains_what_went_wrong(response: httpx.Response, fragment: str) -> None:
    provider = HttpProvider(
        "https://bot.example.com/chat",
        reply_field="answer",
        client=bot(lambda r: response),
        policy=RequestPolicy(sleep=lambda seconds: None),  # a 429 is retried, but not waited for
    )
    with pytest.raises(ProviderError, match=fragment):
        provider.generate("s", "q")


def test_http_provider_reports_a_dead_server_without_repeating_the_address() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom https://secret.example.com/?key=abc")

    provider = HttpProvider(
        "https://secret.example.com/chat", reply_field="answer", client=bot(down)
    )
    with pytest.raises(ProviderError) as caught:
        provider.generate("s", "q")
    assert "ConnectError" in str(caught.value)
    assert "secret.example.com" not in str(caught.value) and "abc" not in str(caught.value)


def test_http_provider_refuses_a_request_field_that_hits_a_fixed_value() -> None:
    provider = HttpProvider(
        "https://b.test/c",
        reply_field="a",
        request_field="input.text",
        extra_body={"input": "fixed"},
        client=bot(lambda r: httpx.Response(200, json={"a": "x"})),
    )
    with pytest.raises(ProviderError, match="collides"):
        provider.generate("s", "q")


# ---------------------------------------------------------------- `vespera test` with the file


def mock_project(tmp_path: Path, extra: str = "") -> Path:
    return write(
        tmp_path / "vespera.yaml",
        f"""
target: {{type: mock}}
repeats: 1
limit: 6
scenarios: {EXAMPLES / "scenarios_v1.yaml"}
system_prompt: {EXAMPLES / "system_prompt.txt"}
report: {tmp_path / "report.html"}
exports:
  results_json: {tmp_path / "results.json"}
  junit_xml: {tmp_path / "junit.xml"}
{extra}
""",
    )


def test_test_reads_vespera_yaml_from_the_current_folder(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mock_project(tmp_path, "gate: {zero_tolerance: [LLM01]}\n")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app_(), ["test", "--db", str(tmp_path / "c.db")])
    assert result.exit_code == 0, result.output
    assert "Using the project file vespera.yaml" in result.output
    assert "Started run 1: 6 cases x 1 repeats" in result.output
    assert "Gate: passed" in result.output
    assert (tmp_path / "report.html").exists() and (tmp_path / "junit.xml").exists()
    payload = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert payload["gate"]["status"] == "passed" and payload["run"]["model"] == "mock"


def app_():  # type: ignore[no-untyped-def]
    return cli.app


def test_flags_override_the_project_file(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mock_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app_(),
        ["test", "--db", str(tmp_path / "c.db"), "--limit", "2", "--repeats", "2",
         "--json", str(tmp_path / "other.json")],
    )
    assert result.exit_code == 0, result.output
    assert "2 cases x 2 repeats" in result.output
    assert (tmp_path / "other.json").exists()


def test_no_config_ignores_even_a_broken_file_but_a_broken_file_is_reported(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    write(tmp_path / "vespera.yaml", "target: {type: ollama}\nrepeat: 3\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    broken = runner.invoke(app_(), ["test", "--mock", "--db", str(tmp_path / "c.db")])
    assert broken.exit_code == 1
    assert "vespera.yaml has problems" in broken.output and "repeat" in broken.output
    skipped = runner.invoke(
        app_(),
        ["test", "--no-config", "--mock", "--limit", "2",
         "--scenarios", str(EXAMPLES / "scenarios_v1.yaml"),
         "--system-prompt", str(EXAMPLES / "system_prompt.txt"), "--db", str(tmp_path / "c.db")],
    )
    assert skipped.exit_code == 0, skipped.output
    assert "Using the project file" not in skipped.output
    missing = runner.invoke(app_(), ["test", "--config", str(tmp_path / "nope.yaml")])
    assert missing.exit_code == 1 and "cannot read" in missing.output


def test_agent_mode_from_the_file_runs_the_agent_scenarios(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    data = REPO / "src" / "vespera" / "data"
    write(
        tmp_path / "vespera.yaml",
        f"""
target: {{type: mock}}
mode: agent
repeats: 1
limit: 6
max_steps: 3
scenarios: {data / "agent_scenarios_v1.yaml"}
system_prompt: {data / "agent_system_prompt.txt"}
report: {tmp_path / "agent.html"}
""",
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app_(), ["test", "--db", str(tmp_path / "c.db")])
    assert result.exit_code == 0, result.output
    assert "Started run 1: 6 cases x 1 repeats" in result.output
    assert "Hidden order reached the agent" in result.output
    assert (tmp_path / "agent.html").exists()


def test_agent_mode_is_refused_for_a_plain_http_bot(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    write(
        tmp_path / "vespera.yaml",
        "target: {type: http, url: 'https://b.test/c', reply_field: answer}\n",
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app_(), ["test", "--mode", "agent"])
    assert result.exit_code == 1 and "can call tools" in result.output
    wrong = CliRunner().invoke(app_(), ["test", "--mode", "chaos"])
    assert wrong.exit_code == 1 and "--mode must be text or agent" in wrong.output


def test_flags_that_do_not_fit_the_target_are_explained(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    bad = CliRunner().invoke(app_(), ["test", "--provider", "http"])
    assert bad.exit_code == 1 and "--provider must be ollama or openai" in bad.output


def test_gate_reads_its_rules_from_the_project_file(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mock_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    db = str(tmp_path / "c.db")
    assert runner.invoke(app_(), ["test", "--db", db]).exit_code == 0
    none = runner.invoke(app_(), ["gate", "--db", db])
    assert none.exit_code == 1 and "gate section in vespera.yaml" in none.output
    mock_project(tmp_path, "gate: {zero_tolerance: [LLM01]}\n")
    ruled = runner.invoke(app_(), ["gate", "--db", db])
    assert ruled.exit_code == 0 and "Gate: passed" in ruled.output


# ---------------------------------------------------------------- an HTTP bot, end to end


def test_a_leaking_http_bot_fails_the_gate_and_receives_the_configured_requests(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    received: list[dict[str, Any]] = []

    def leaky(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(200, json={"result": {"text": f"My secret code is {CANARY}"}})

    real = HttpProvider

    def with_fake_server(url: str, **options: Any) -> HttpProvider:
        return real(url, client=bot(leaky), **options)

    monkeypatch.setattr(targets, "HttpProvider", with_fake_server)
    write(
        tmp_path / "vespera.yaml",
        f"""
target:
  type: http
  url: https://bot.example.com/chat
  request_field: input.question
  reply_field: result.text
  extra_body: {{session: vespera-test}}
repeats: 1
limit: 10
scenarios: {EXAMPLES / "scenarios_v1.yaml"}
system_prompt: {EXAMPLES / "system_prompt.txt"}
report: {tmp_path / "r.html"}
gate: {{max_attack_rate: 0.05}}
""",
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app_(), ["test", "--db", str(tmp_path / "c.db")])
    assert result.exit_code == 2, result.output
    assert "[FAIL] attacks succeeded in" in result.output and "Gate: failed" in result.output
    assert len(received) == 10
    assert all(body["session"] == "vespera-test" and body["input"]["question"] for body in received)
    assert CANARY not in result.output  # the console proof is masked by default
    assert CANARY in (tmp_path / "r.html").read_text(encoding="utf-8")  # the report keeps it


def test_a_target_key_is_read_from_the_environment_only(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    write(
        tmp_path / "vespera.yaml",
        "target: {type: http, url: 'https://b.test/c', reply_field: a, api_key_env: BOT_KEY}\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BOT_KEY", raising=False)
    result = CliRunner().invoke(app_(), ["test", "--scenarios", str(EXAMPLES / "scenarios_v1.yaml"),
                                         "--system-prompt", str(EXAMPLES / "system_prompt.txt"),
                                         "--db", str(tmp_path / "c.db")])
    assert result.exit_code == 1 and "set the environment variable BOT_KEY" in result.output


# ---------------------------------------------------------------- init


def test_init_writes_the_project_file_and_will_not_overwrite_it(tmp_path: Path) -> None:
    runner = CliRunner()
    folder = tmp_path / "bot"
    first = runner.invoke(app_(), ["init", "--directory", str(folder)])
    assert first.exit_code == 0, first.output
    assert (folder / "vespera.yaml").read_text(encoding="utf-8") == _config_text(agents=False)
    write(folder / "vespera.yaml", "mine")
    again = runner.invoke(app_(), ["init", "--directory", str(folder)])
    assert again.exit_code == 1 and "vespera.yaml" in again.output
    assert (folder / "vespera.yaml").read_text(encoding="utf-8") == "mine"
    agents = runner.invoke(app_(), ["init", "--agents", "--force", "--directory", str(folder)])
    assert agents.exit_code == 0
    assert "mode: agent" in (folder / "vespera.yaml").read_text(encoding="utf-8")


# ---------------------------------------------------------------- validate and the documentation


def yaml_blocks(page: str) -> list[str]:
    parts = (REPO / "docs" / page).read_text(encoding="utf-8").split("```yaml\n")[1:]
    return [part.split("```", 1)[0] for part in parts]


def test_validate_understands_question_files_and_agent_files(tmp_path: Path) -> None:
    runner = CliRunner()
    questions = runner.invoke(app_(), ["validate", str(EXAMPLES / "scenarios_v1.yaml")])
    assert questions.exit_code == 0 and "10 scenarios, 30 cases" in questions.output
    assert "agent" not in questions.output
    agents = runner.invoke(
        app_(), ["validate", str(REPO / "src" / "vespera" / "data" / "agent_scenarios_v1.yaml")]
    )
    assert agents.exit_code == 0 and "6 scenarios, 18 cases (agent tasks)" in agents.output
    assert "tool_output_injection: 2" in agents.output
    write(tmp_path / "bad.yaml", "scenarios:\n  - id: x\n    task: {en: a}\n")
    broken = runner.invoke(app_(), ["validate", str(tmp_path / "bad.yaml")])
    assert broken.exit_code == 1 and "[FAIL]" in broken.output
    gone = runner.invoke(app_(), ["validate", str(tmp_path / "nope.yaml")])
    assert gone.exit_code == 1 and "cannot read" in gone.output
    write(tmp_path / "junk.yaml", "key: [unclosed")
    junk = runner.invoke(app_(), ["validate", str(tmp_path / "junk.yaml")])
    assert junk.exit_code == 1


def test_every_scenario_example_in_the_docs_is_valid(tmp_path: Path) -> None:
    blocks = yaml_blocks("write-a-scenario.md")
    assert len(blocks) == 2
    for number, block in enumerate(blocks):
        path = write(tmp_path / f"example{number}.yaml", block)
        result = CliRunner().invoke(app_(), ["validate", str(path)])
        assert result.exit_code == 0, (block, result.output)


def test_the_http_example_in_the_docs_is_a_valid_target(tmp_path: Path) -> None:
    blocks = [b for b in yaml_blocks("connect-your-bot.md") if b.startswith("target:")]
    assert blocks and "type: http" in blocks[0]
    target = load_config(write(tmp_path / "v.yaml", blocks[0])).target
    assert target.type == "http" and target.reply_field == "result.messages.0.text"
    gate = [b for b in yaml_blocks("connect-your-bot.md") if b.startswith("gate:")]
    with_target = "target: {type: mock}\n" + gate[0]
    assert load_config(write(tmp_path / "g.yaml", with_target)).gate.zero_tolerance == ["LLM02"]
