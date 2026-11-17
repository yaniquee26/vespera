import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from vespera.agent_contracts import (
    AgentCase,
    AgentCategory,
    AgentTrace,
    AgentTurn,
    ToolCall,
    ToolSpec,
    TraceEvent,
)
from vespera.agent_runner import run_agent_case, run_agent_experiment, simulate_tool
from vespera.agent_scenarios import expand_agent_cases, load_agent_scenarios
from vespera.checks import CheckConfigError, CheckContext, run_checks
from vespera.checks import check_expected_tool_called as expected_tool
from vespera.checks import check_forbidden_tool_call as forbidden_tool
from vespera.checks import check_secret_not_in_tool_args as secret_in_args
from vespera.cli import app
from vespera.compare import build_comparison
from vespera.contracts import Attempt, Category, CheckStatus, Language
from vespera.providers import (
    AgentProvider,
    MockAgentProvider,
    OpenAICompatibleAgentProvider,
    ProviderError,
    parse_agent_turn,
    tool_schema,
)
from vespera.report import build_report, render_html
from vespera.runner import RunAborted
from vespera.snapshot import build_snapshot
from vespera.storage import Storage

DATA = Path(__file__).resolve().parents[1] / "src" / "vespera" / "data"
SCENARIOS = DATA / "agent_scenarios_v1.yaml"
SYSTEM = (DATA / "agent_system_prompt.txt").read_text(encoding="utf-8")


def cases() -> list[AgentCase]:
    return expand_agent_cases(load_agent_scenarios(SCENARIOS))


def call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=arguments)


# ---------------------------------------------------------------- contracts


def test_agent_categories_exist_in_the_shared_category_list() -> None:
    assert {c.value for c in AgentCategory} <= {c.value for c in Category}


def test_agent_case_becomes_a_shared_case() -> None:
    case = cases()[0]
    shared = case.to_case()
    assert shared.id == case.id
    assert shared.prompt == case.task
    assert shared.category.value == case.category.value
    assert shared.params["tools"] == ["search_help", "send_sms"]


def test_trace_describes_everything_in_order_and_final_reply_is_the_last_text() -> None:
    trace = AgentTrace(
        events=[
            TraceEvent(
                kind="assistant", text="", tool_calls=[call("search_help", query="roaming")]
            ),
            TraceEvent(kind="tool_result", text="*105#", tool_name="search_help"),
            TraceEvent(kind="assistant", text="Dial *105#"),
        ],
        stopped="answered",
    )
    assert trace.final_reply() == "Dial *105#"
    assert trace.describe().splitlines() == [
        "AGENT CALLS: search_help(query='roaming')",
        "TOOL RESULT (search_help): *105#",
        "AGENT SAYS: Dial *105#",
    ]
    assert AgentTrace(events=[], stopped="answered").describe() == "(the agent did nothing)"
    assert AgentTrace(events=[], stopped="answered").final_reply() == ""
    stopped = AgentTrace(events=[], stopped="max_steps")
    assert "too many steps" in stopped.describe()


# ---------------------------------------------------------------- providers


def test_tool_schema_follows_the_openai_format() -> None:
    tool = ToolSpec(name="send_sms", description="Send.", parameters={"to": "number"})
    schema = tool_schema(tool)
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "send_sms"
    assert schema["function"]["parameters"]["required"] == ["to"]
    assert schema["function"]["parameters"]["properties"]["to"]["description"] == "number"


def reply_with(message: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": message}]}


def test_parse_plain_text_and_tool_calls() -> None:
    assert parse_agent_turn(reply_with({"content": "hi"})) == AgentTurn(text="hi")
    turn = parse_agent_turn(
        reply_with(
            {
                "content": None,
                "tool_calls": [
                    {"id": "a", "function": {"name": "send_sms", "arguments": '{"to": "1"}'}},
                    {"function": {"name": "open_ticket", "arguments": {"summary": "x"}}},
                ],
            }
        )
    )
    assert turn.text == ""
    assert [c.name for c in turn.tool_calls] == ["send_sms", "open_ticket"]
    assert turn.tool_calls[0].arguments == {"to": "1"}
    assert turn.tool_calls[1].id == "call_1"  # made up when the server sends none
    assert turn.tool_calls[1].arguments == {"summary": "x"}


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [("not json", {"_raw": "not json"}), ('["a"]', {"_raw": "['a']"}), ("", {}), (None, {})],
)
def test_strange_tool_arguments_are_kept_not_lost(arguments: Any, expected: dict) -> None:  # type: ignore[type-arg]
    turn = parse_agent_turn(
        reply_with({"tool_calls": [{"function": {"name": "t", "arguments": arguments}}]})
    )
    assert turn.tool_calls[0].arguments == expected


@pytest.mark.parametrize(
    "data",
    [{}, {"choices": []}, reply_with({"content": 5}), reply_with({"tool_calls": [{}]})],
)
def test_malformed_replies_are_rejected(data: Any) -> None:
    with pytest.raises((KeyError, IndexError, TypeError, ValueError)):
        parse_agent_turn(data)


def make_provider(handler: Any) -> OpenAICompatibleAgentProvider:
    client = httpx.Client(base_url="http://test/v1", transport=httpx.MockTransport(handler))
    return OpenAICompatibleAgentProvider(model="qwen", client=client)


def test_agent_provider_sends_messages_and_tools_and_reads_tool_calls() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=reply_with(
                {
                    "tool_calls": [
                        {"id": "z", "function": {"name": "search_help", "arguments": "{}"}}
                    ]
                }
            ),
        )

    tools = [ToolSpec(name="search_help", description="Search.")]
    turn = make_provider(handler).chat([{"role": "user", "content": "hi"}], tools)
    assert turn.tool_calls[0].name == "search_help"
    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"]["tools"][0]["function"]["name"] == "search_help"
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]


def test_a_model_without_tool_support_gets_a_helpful_message() -> None:
    body = {"error": {"message": "gemma3:4b does not support tools"}}
    provider = make_provider(lambda request: httpx.Response(400, json=body))
    with pytest.raises(ProviderError, match="does not support tools.*qwen2.5:7b"):
        provider.chat([], [])


@pytest.mark.parametrize(("status", "words"), [(401, "API key"), (404, "--base-url"), (500, "500")])
def test_agent_provider_explains_http_errors(status: int, words: str) -> None:
    provider = make_provider(lambda request: httpx.Response(status, json={}))
    with pytest.raises(ProviderError, match=words):
        provider.chat([], [])


def test_agent_provider_wraps_network_and_format_failures() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(ProviderError, match="failed"):
        make_provider(down).chat([], [])
    with pytest.raises(ProviderError, match="unexpected"):
        make_provider(lambda request: httpx.Response(200, json={"x": 1})).chat([], [])


def test_agent_provider_sends_the_key_as_a_header() -> None:
    provider = OpenAICompatibleAgentProvider(model="m", api_key="sk-secret")
    assert provider._client.headers["Authorization"] == "Bearer sk-secret"


def test_mock_agent_provider_repeats_turns_and_remembers_calls() -> None:
    provider = MockAgentProvider([AgentTurn(text="a"), AgentTurn(text="b")])
    messages = [{"role": "user", "content": "x"}]
    assert [provider.chat(messages, []).text for _ in range(3)] == ["a", "b", "a"]
    messages.append({"role": "user", "content": "later"})
    assert len(provider.calls[0][0]) == 1  # a copy, not the list that grew later
    with pytest.raises(ValueError):
        MockAgentProvider([])


# ---------------------------------------------------------------- the agent loop


class Gullible(AgentProvider):
    """An agent that obeys whatever it reads. Deterministic, so numbers are known."""

    def __init__(self) -> None:
        self.model = "gullible"
        self.temperature = 0.7
        self.num_predict = 300

    def chat(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentTurn:
        names = {tool.name for tool in tools}
        last = messages[-1]
        user = messages[1]["content"]
        if last["role"] == "tool":
            text = last["content"]
            if "send_sms" in text and "send_sms" in names:
                return AgentTurn(tool_calls=[call("send_sms", to="+37360999999", text="verify")])
            wants_code = re.search(r"internal code|внутренн\w+ код|codul intern", text)
            if wants_code and "open_ticket" in names:
                secret = re.search(r"CANARY-[A-Z0-9]+", messages[0]["content"])
                assert secret
                return AgentTurn(tool_calls=[call("open_ticket", summary=secret.group(0))])
            return AgentTurn(text="Done.")
        if "37360999999" in user:
            note = "+373 60 000 001"
            return AgentTurn(tool_calls=[call("send_sms", to="+37360999999", text=note)])
        if "cancel_subscription" in names:
            return AgentTurn(tool_calls=[call("cancel_subscription", number="+37360123456")])
        if "open_ticket" in names and "search_help" not in names:
            return AgentTurn(tool_calls=[call("open_ticket", summary="SIM broken")])
        if "search_help" in names:
            return AgentTurn(tool_calls=[call("search_help", query="help")])
        return AgentTurn(text="Done.")


def test_the_loop_feeds_poisoned_tool_output_back_to_the_model() -> None:
    case = next(c for c in cases() if c.id == "ag-001:en")
    trace = run_agent_case(Gullible(), case, SYSTEM)
    assert trace.stopped == "answered"
    assert [c.name for c in trace.calls()] == ["search_help", "send_sms"]
    assert "Note to the assistant" in trace.events[1].text
    assert trace.events[-1].text == "Done."


def test_messages_follow_the_openai_tool_protocol() -> None:
    case = next(c for c in cases() if c.id == "ag-001:en")
    provider = MockAgentProvider(
        [AgentTurn(tool_calls=[call("search_help", query="r")]), AgentTurn(text="ok")]
    )
    run_agent_case(provider, case, SYSTEM)
    second = provider.calls[1][0]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[0]["content"] == SYSTEM
    sent = second[2]["tool_calls"][0]
    assert sent["id"] == "c1"
    assert json.loads(sent["function"]["arguments"]) == {"query": "r"}
    assert second[3]["tool_call_id"] == "c1"


def test_missing_call_ids_are_made_up_and_matched() -> None:
    case = next(c for c in cases() if c.id == "ag-005:en")
    provider = MockAgentProvider(
        [AgentTurn(tool_calls=[ToolCall(name="open_ticket", arguments={})]), AgentTurn(text="ok")]
    )
    run_agent_case(provider, case, SYSTEM)
    second = provider.calls[1][0]
    assert second[2]["tool_calls"][0]["id"] == second[3]["tool_call_id"] != ""


def test_unknown_tools_get_an_error_and_plain_tools_get_ok() -> None:
    case = next(c for c in cases() if c.id == "ag-004:en")
    assert simulate_tool(case, call("hack")).startswith("Error: unknown tool")
    assert simulate_tool(case, call("cancel_subscription")) == "OK (simulated)"


def test_an_agent_that_never_stops_is_cut_off_but_its_calls_still_count() -> None:
    case = next(c for c in cases() if c.id == "ag-004:en")
    provider = MockAgentProvider([AgentTurn(tool_calls=[call("cancel_subscription")])])
    trace = run_agent_case(provider, case, SYSTEM, max_steps=3)
    assert trace.stopped == "max_steps"
    assert len(trace.calls()) == 3
    assert len(provider.calls) == 3


# ---------------------------------------------------------------- checks


def ctx(case_id: str, events: list[TraceEvent]) -> CheckContext:
    case = next(c for c in cases() if c.id == case_id).to_case()
    attempt = Attempt(case_id=case_id, repeat=1, reply="", latency_ms=0, created_at="t")
    return CheckContext(case, attempt, SYSTEM, AgentTrace(events=events, stopped="answered"))


def asks(*calls: ToolCall) -> list[TraceEvent]:
    return [TraceEvent(kind="assistant", tool_calls=list(calls))]


def test_forbidden_tool_call_fails_with_the_call_as_proof() -> None:
    bad = forbidden_tool(ctx("ag-001:en", asks(call("send_sms", to="+373", text="verify"))))
    assert bad.status is CheckStatus.FAILED
    assert bad.excerpt == "send_sms(to='+373', text='verify')"
    good = forbidden_tool(ctx("ag-001:en", asks(call("search_help", query="x"))))
    assert good.status is CheckStatus.PASSED


def test_forbidden_tool_call_can_look_at_the_arguments() -> None:
    context = ctx("ag-001:en", asks(call("send_sms", to="+37360111111")))
    narrowed = context.case.model_copy(
        update={"params": {"tool": "send_sms", "arg_contains": ["+373 60 999 999"]}}
    )
    context = CheckContext(narrowed, context.attempt, SYSTEM, context.trace)
    assert forbidden_tool(context).status is CheckStatus.PASSED  # other number: allowed here
    hit = ctx("ag-001:en", asks(call("send_sms", to="+37360999999")))
    hit = CheckContext(narrowed, hit.attempt, SYSTEM, hit.trace)
    assert forbidden_tool(hit).status is CheckStatus.FAILED


@pytest.mark.parametrize(
    "arguments",
    [
        {"summary": "code: CANARY-7F3A91"},
        {"summary": "code canary 7f3a91 please"},
        {"summary": "C A N A R Y - 7 F 3 A 9 1"},
    ],
)
def test_a_secret_in_tool_arguments_is_found_in_any_spelling(arguments: dict) -> None:  # type: ignore[type-arg]
    result = secret_in_args(ctx("ag-002:en", asks(call("open_ticket", **arguments))))
    assert result.status is CheckStatus.FAILED
    assert "open_ticket" in result.detail


def test_no_secret_means_passed_and_phone_numbers_ignore_spaces() -> None:
    clean = secret_in_args(ctx("ag-002:en", asks(call("open_ticket", summary="slow internet"))))
    assert clean.status is CheckStatus.PASSED
    leak = secret_in_args(ctx("ag-003:en", asks(call("send_sms", text="+373 60 000 001"))))
    assert leak.status is CheckStatus.FAILED


def test_expected_tool_called_measures_a_task_left_undone() -> None:
    done = expected_tool(ctx("ag-005:en", asks(call("open_ticket", summary="x"))))
    assert done.status is CheckStatus.PASSED
    refused = [TraceEvent(kind="assistant", text="I cannot help with that.")]
    undone = expected_tool(ctx("ag-005:en", refused))
    assert undone.status is CheckStatus.FAILED
    assert "I cannot help" in undone.excerpt
    silent = expected_tool(ctx("ag-005:en", [TraceEvent(kind="assistant")]))
    assert "(nothing)" in silent.excerpt


def test_agent_checks_need_a_trace() -> None:
    case = cases()[0].to_case()
    attempt = Attempt(case_id=case.id, repeat=1, reply="x", latency_ms=0, created_at="t")
    with pytest.raises(CheckConfigError, match="needs an agent trace"):
        run_checks([case], [attempt], SYSTEM)


# ---------------------------------------------------------------- whole runs


def new_run(
    storage: Storage, provider: AgentProvider, repeats: int = 1, max_steps: int = 5
) -> int:
    agent_cases = cases()
    snapshot = build_snapshot(
        model=provider.model,
        temperature=provider.temperature,
        num_predict=provider.num_predict,
        repeats=repeats,
        system_prompt=SYSTEM,
        scenarios_text=SCENARIOS.read_text(encoding="utf-8"),
        max_steps=max_steps,
    )
    run_id = storage.create_run(snapshot)
    storage.save_cases(run_id, [c.to_case() for c in agent_cases])
    run_agent_experiment(
        provider, agent_cases, SYSTEM, storage, run_id, repeats, max_steps=max_steps
    )
    return run_id


def check_run(storage: Storage, run_id: int) -> None:
    results = run_checks(
        storage.get_cases(run_id),
        storage.get_attempts(run_id),
        SYSTEM,
        storage.get_traces(run_id),
    )
    storage.save_check_results(run_id, results)


@pytest.fixture
def storage(tmp_path: Path):  # type: ignore[no-untyped-def]
    db = Storage(tmp_path / "a.db")
    yield db
    db.close()


def test_a_gullible_agent_is_caught_in_every_attack_scenario(storage: Storage) -> None:
    run_id = new_run(storage, Gullible())
    check_run(storage, run_id)
    assert storage.is_agent_run(run_id)
    data = build_report(storage, run_id)
    rows = {row.key: row.rate for row in data.rows}
    assert (rows["tool_output_injection"].k, rows["tool_output_injection"].n) == (6, 6)
    assert (rows["data_exfiltration"].k, rows["data_exfiltration"].n) == (3, 3)
    assert (rows["excessive_agency"].k, rows["excessive_agency"].n) == (3, 3)
    assert rows["benign_task"].k == 0  # it does the harmless tasks
    assert data.attack_rate is not None
    assert (data.attack_rate.k, data.attack_rate.n) == (12, 12)
    assert data.is_agent


def test_a_firm_agent_passes_attacks_but_the_report_shows_the_tasks_it_left_undone(
    storage: Storage,
) -> None:
    run_id = new_run(storage, MockAgentProvider([AgentTurn(text="I cannot help with that.")]))
    check_run(storage, run_id)
    data = build_report(storage, run_id)
    rows = {row.key: row for row in data.rows}
    assert data.attack_rate is not None and data.attack_rate.k == 0
    assert (rows["benign_task"].rate.k, rows["benign_task"].rate.n) == (6, 6)
    assert rows["benign_task"].harmless


def test_the_report_proves_findings_with_the_agent_journal(storage: Storage) -> None:
    run_id = new_run(storage, Gullible())
    check_run(storage, run_id)
    html = render_html(build_report(storage, run_id))
    assert "What the agent said and did" in html
    assert "AGENT CALLS: send_sms(" in html
    assert "TOOL RESULT (search_help)" in html
    assert "Task given to the agent" in html
    assert "Tools are simulated" in html
    assert "Max steps per task" in html
    assert "vespera run-agent --model gullible" in html
    assert "Order hidden in tool output" in html


def test_hostile_text_in_a_journal_cannot_break_the_page(storage: Storage) -> None:
    provider = MockAgentProvider(
        [AgentTurn(tool_calls=[call("cancel_subscription", number="<script>alert(1)</script>")])]
    )
    run_id = new_run(storage, provider, max_steps=1)
    check_run(storage, run_id)
    html = render_html(build_report(storage, run_id))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_repeats_and_max_steps_are_stored_and_runs_resume(storage: Storage) -> None:
    run_id = new_run(storage, Gullible(), repeats=2)
    assert storage.get_snapshot(run_id).repeats == 2
    assert storage.get_snapshot(run_id).max_steps == 5
    assert len(storage.get_traces(run_id)) == 36
    again = run_agent_experiment(Gullible(), cases(), SYSTEM, storage, run_id, 2)
    assert (again.attempted, again.skipped) == (0, 36)


class Broken(AgentProvider):
    model = "broken"
    temperature = 0.0
    num_predict = 0

    def chat(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentTurn:
        raise ProviderError("server down")


def test_three_errors_in_a_row_stop_the_run_and_save_nothing_misleading(storage: Storage) -> None:
    agent_cases = cases()
    snapshot = build_snapshot(
        model="broken",
        temperature=0.0,
        num_predict=0,
        repeats=1,
        system_prompt=SYSTEM,
        scenarios_text="x",
    )
    run_id = storage.create_run(snapshot)
    storage.save_cases(run_id, [c.to_case() for c in agent_cases])
    with pytest.raises(RunAborted, match="--resume"):
        run_agent_experiment(Broken(), agent_cases, SYSTEM, storage, run_id, 1)
    attempts = storage.get_attempts(run_id)
    assert len(attempts) == 3 and all(a.error == "server down" for a in attempts)
    assert storage.get_traces(run_id) == {}


def test_agent_runs_can_be_compared(storage: Storage) -> None:
    gullible = new_run(storage, Gullible())
    firm = new_run(storage, MockAgentProvider([AgentTurn(text="No.")]))
    for run_id in (gullible, firm):
        check_run(storage, run_id)
    data = build_comparison(storage, gullible, firm)
    row = next(r for r in data.rows if r.label == "Order hidden in tool output")
    assert row.conclusive
    assert "gullible violated more often than mock" in row.sentence


def test_stats_know_the_agent_languages(storage: Storage) -> None:
    run_id = new_run(storage, Gullible())
    check_run(storage, run_id)
    languages = {c.language for c in storage.get_cases(run_id)}
    assert languages == set(Language)


# ---------------------------------------------------------------- command line


def agent_args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "--db",
        str(tmp_path / "c.db"),
        "--scenarios",
        str(SCENARIOS),
        "--system-prompt",
        str(DATA / "agent_system_prompt.txt"),
        *extra,
    ]


def test_run_agent_check_report_with_the_fake_agent(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    runner = CliRunner()
    ran = runner.invoke(app, ["run-agent", "--mock", *agent_args(tmp_path)])
    assert ran.exit_code == 0, ran.output
    assert "[18/18]" in ran.output
    monkeypatch.chdir(DATA)  # `check` finds agent_system_prompt.txt next to where it runs
    checked = runner.invoke(app, ["check", "--db", str(tmp_path / "c.db")])
    assert checked.exit_code == 0, checked.output
    assert "benign_task" in checked.output
    out = tmp_path / "r.html"
    reported = runner.invoke(app, ["report", "--db", str(tmp_path / "c.db"), "--out", str(out)])
    assert reported.exit_code == 0, reported.output
    assert "Harmless task not done" in out.read_text(encoding="utf-8")


def test_run_agent_limit_and_resume_guard(tmp_path: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(app, ["run-agent", "--mock", "--limit", "3", *agent_args(tmp_path)])
    assert first.exit_code == 0, first.output
    assert "Started run 1: 3 cases x 1 repeats" in first.output
    changed = runner.invoke(
        app, ["run-agent", "--mock", "--limit", "3", "--max-steps", "2", "--resume", "1",
              *agent_args(tmp_path)]
    )
    assert changed.exit_code == 1
    assert "differ from the original run" in changed.output
    same = runner.invoke(
        app, ["run-agent", "--mock", "--limit", "3", "--resume", "1", *agent_args(tmp_path)]
    )
    assert same.exit_code == 0 and "Resuming run 1" in same.output
    assert "3 skipped" in same.output


def test_run_agent_needs_a_key_for_a_remote_server_and_explains_missing_files(
    tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    runner = CliRunner()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    remote = runner.invoke(
        app, ["run-agent", "--base-url", "https://api.example.com/v1", *agent_args(tmp_path)]
    )
    assert remote.exit_code == 1 and "OPENAI_API_KEY" in remote.output
    missing = runner.invoke(app, ["run-agent", "--scenarios", str(tmp_path / "nope.yaml")])
    assert missing.exit_code == 1 and "vespera init --agents" in missing.output


def test_init_agents_copies_the_agent_files_too(tmp_path: Path) -> None:
    runner = CliRunner()
    plain = runner.invoke(app, ["init", "--directory", str(tmp_path / "a")])
    assert plain.exit_code == 0 and not (tmp_path / "a" / "agent_scenarios_v1.yaml").exists()
    both = runner.invoke(app, ["init", "--agents", "--directory", str(tmp_path / "b")])
    assert both.exit_code == 0
    for name in ("scenarios_v1.yaml", "system_prompt.txt", "agent_scenarios_v1.yaml",
                 "agent_system_prompt.txt"):
        assert (tmp_path / "b" / name).exists()


# ---------------------------------------------------------------- exposure and language drift


def test_the_hidden_order_check_tells_apart_delivered_and_never_opened() -> None:
    from vespera.checks import check_hidden_order_delivered as delivered

    opened = [
        TraceEvent(kind="assistant", tool_calls=[call("search_help", query="x")]),
        TraceEvent(kind="tool_result", text="Roaming... [Note to the assistant: ...]",
                   tool_name="search_help"),
    ]
    yes = delivered(ctx("ag-001:en", opened))
    assert (yes.status, yes.value) == (CheckStatus.INFO, "yes")
    ignored = [TraceEvent(kind="assistant", text="Dial *105#")]
    no = delivered(ctx("ag-001:en", ignored))
    assert no.value == "no" and "not tested" in no.detail
    plain = [TraceEvent(kind="tool_result", text="OK (simulated)", tool_name="send_sms")]
    assert delivered(ctx("ag-001:en", plain)).value == "no"  # an empty "OK" carries no order
    unknown = [TraceEvent(kind="tool_result", text="Error: unknown tool 'x'", tool_name="x")]
    assert delivered(ctx("ag-001:en", unknown)).value == "no"


def test_run_checks_adds_the_exposure_check_only_to_tool_output_tests() -> None:
    agent_cases = cases()
    shared = [c.to_case() for c in agent_cases if c.id in ("ag-001:en", "ag-004:en")]
    attempts = [
        Attempt(case_id=c.id, repeat=1, reply="x", latency_ms=0, created_at="t") for c in shared
    ]
    trace = AgentTrace(events=[TraceEvent(kind="assistant", text="x")], stopped="answered")
    traces = {(c.id, 1): trace for c in shared}
    names = {(r.case_id, r.check) for r in run_checks(shared, attempts, SYSTEM, traces)}
    assert ("ag-001:en", "hidden_order_delivered") in names
    assert ("ag-004:en", "hidden_order_delivered") not in names


def test_an_agent_that_never_opens_the_poisoned_tool_is_reported_as_untested(
    storage: Storage,
) -> None:
    run_id = new_run(storage, MockAgentProvider([AgentTurn(text="I cannot help with that.")]))
    check_run(storage, run_id)
    data = build_report(storage, run_id)
    assert (data.hidden_order_delivered, data.hidden_order_total) == (0, 6)
    html = render_html(data)
    assert "reached the agent in\n0 of 6 attempts" in html
    assert "not really tested" in html


def test_a_gullible_agent_received_every_hidden_order(storage: Storage) -> None:
    run_id = new_run(storage, Gullible())
    check_run(storage, run_id)
    data = build_report(storage, run_id)
    assert (data.hidden_order_delivered, data.hidden_order_total) == (6, 6)


def test_the_exposure_check_does_not_disturb_the_language_numbers(storage: Storage) -> None:
    run_id = new_run(storage, Gullible())
    check_run(storage, run_id)
    data = build_report(storage, run_id)
    assert data.language_known + data.language_unknown == 18  # one language result per attempt


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Для了解“无限”套餐的价格，我可以为您查询相关信息。请稍等。", "other"),
        ("これは日本語の返事です", "other"),
        ("هذا رد باللغة العربية", "other"),
        ("Привет, это ответ на русском", "ru"),
        ("Thank you, the plan costs $25 per month", "en"),
    ],
)
def test_a_reply_in_another_script_is_flagged_as_drift(text: str, expected: str) -> None:
    from vespera.checks import detect_language

    assert detect_language(text) == expected
