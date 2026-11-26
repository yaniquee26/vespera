"""Rate limits: pacing between requests and retries after HTTP 429/503, without real waiting."""

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from vespera import cli
from vespera.agent_runner import run_agent_experiment
from vespera.agent_scenarios import expand_agent_cases, load_agent_scenarios
from vespera.config import ConfigError, TargetConfig, load_config
from vespera.contracts import Case, Category, Language
from vespera.providers import (
    HttpProvider,
    MockProvider,
    OllamaProvider,
    OpenAICompatibleAgentProvider,
    OpenAICompatibleProvider,
    ProviderError,
    RequestPolicy,
    retry_after_seconds,
)
from vespera.runner import RunSummary, latency_ms, run_experiment
from vespera.snapshot import build_snapshot
from vespera.storage import Storage

DATA = Path(__file__).resolve().parents[1] / "src" / "vespera" / "data"
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
OLLAMA_OK = {"message": {"content": "Hi"}}
OPENAI_OK = {"choices": [{"message": {"content": "Hi"}}]}
LOCAL_API = "http://localhost:1234/v1"  # a server on this computer needs no key


class FakeTime:
    """A clock that moves only when someone sleeps or a request takes time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now

    def policy(self, **options: Any) -> RequestPolicy:
        return RequestPolicy(sleep=self.sleep, clock=self.clock, jitter=lambda: 0.0, **options)


def server(*responses: httpx.Response) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Answers with the given responses in order, then repeats the last one."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen), len(responses)) - 1]

    return httpx.MockTransport(handler), seen


def busy(status: int = 429, after: str | None = None) -> httpx.Response:
    return httpx.Response(status, headers={"Retry-After": after} if after is not None else {})


def ollama(transport: httpx.MockTransport, policy: RequestPolicy) -> OllamaProvider:
    client = httpx.Client(base_url="http://test", transport=transport)
    return OllamaProvider(model="m", client=client, policy=policy)


def openai(transport: httpx.MockTransport, policy: RequestPolicy) -> OpenAICompatibleProvider:
    client = httpx.Client(base_url="http://test/v1", transport=transport)
    return OpenAICompatibleProvider(model="m", client=client, policy=policy)


def http_bot(transport: httpx.MockTransport, policy: RequestPolicy) -> HttpProvider:
    client = httpx.Client(transport=transport)
    return HttpProvider("https://bot.test/chat", reply_field="a", client=client, policy=policy)


# ---------------------------------------------------------------- Retry-After


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("2", 2.0),
        ("0", 0.0),
        ("1.5", 1.5),
        (None, None),
        ("", None),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),  # a date: back off instead
        ("-1", None),
        ("nan", None),
        ("inf", None),
    ],
)
def test_retry_after_is_read_in_seconds_and_anything_else_is_ignored(
    value: str | None, seconds: float | None
) -> None:
    assert retry_after_seconds(busy(after=value)) == seconds


# ---------------------------------------------------------------- retries


def test_a_429_with_retry_after_is_waited_for_as_asked_and_then_answered() -> None:
    fake = FakeTime()
    transport, seen = server(busy(429, "2"), httpx.Response(200, json=OLLAMA_OK))
    provider = ollama(transport, fake.policy())
    assert provider.generate("s", "u") == "Hi"
    assert len(seen) == 2
    assert fake.sleeps == [2.0]
    assert (provider.retries, provider.waited) == (1, 2.0)
    assert json.loads(seen[0].content) == json.loads(seen[1].content)  # the same request again


@pytest.mark.parametrize(("jitter", "pauses"), [(0.0, [1.0, 2.0, 4.0]), (0.5, [0.75, 1.5, 3.0])])
def test_a_503_without_retry_after_backs_off_exponentially_with_jitter(
    jitter: float, pauses: list[float]
) -> None:
    fake = FakeTime()
    policy = RequestPolicy(sleep=fake.sleep, clock=fake.clock, jitter=lambda: jitter)
    transport, seen = server(busy(503), busy(503), busy(503), httpx.Response(200, json=OPENAI_OK))
    provider = openai(transport, policy)
    assert provider.generate("s", "u") == "Hi"
    assert len(seen) == 4
    assert fake.sleeps == pauses
    assert provider.retries == 3


def test_the_backoff_never_waits_longer_than_the_cap() -> None:
    fake = FakeTime()
    policy = RequestPolicy(
        max_retries=5, base_delay=10.0, max_wait=15.0, sleep=fake.sleep, jitter=lambda: 0.0
    )
    transport, _ = server(busy(503))
    with pytest.raises(ProviderError):
        openai(transport, policy).generate("s", "u")
    assert fake.sleeps == [10.0, 15.0, 15.0, 15.0, 15.0]


def test_when_retries_run_out_the_attempt_is_an_ordinary_error() -> None:
    fake = FakeTime()
    transport, seen = server(busy(429, "1"))
    provider = http_bot(transport, fake.policy(max_retries=2))
    with pytest.raises(ProviderError, match=r"too many requests .*request_interval"):
        provider.generate("s", "u")
    assert len(seen) == 3  # the first request and two retries
    assert fake.sleeps == [1.0, 1.0]
    assert provider.retries == 2


def test_no_retries_at_all_when_max_retries_is_zero() -> None:
    fake = FakeTime()
    transport, seen = server(busy(503))
    with pytest.raises(ProviderError, match="HTTP 503"):
        http_bot(transport, fake.policy(max_retries=0)).generate("s", "u")
    assert len(seen) == 1 and fake.sleeps == []


def test_a_server_that_asks_for_a_very_long_wait_is_not_waited_for() -> None:
    fake = FakeTime()
    transport, seen = server(busy(429, "3600"))
    provider = openai(transport, fake.policy())
    with pytest.raises(ProviderError, match="HTTP 429"):
        provider.generate("s", "u")
    assert len(seen) == 1 and fake.sleeps == [] and provider.retries == 0


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 500, 502])
def test_other_errors_are_not_retried(status: int) -> None:
    fake = FakeTime()
    transport, seen = server(httpx.Response(status, json={}))
    provider = http_bot(transport, fake.policy())
    with pytest.raises(ProviderError):
        provider.generate("s", "u")
    assert len(seen) == 1 and fake.sleeps == [] and provider.retries == 0


def test_network_errors_are_not_retried() -> None:
    calls: list[httpx.Request] = []

    def down(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("refused")

    fake = FakeTime()
    with pytest.raises(ProviderError):
        ollama(httpx.MockTransport(down), fake.policy()).generate("s", "u")
    assert len(calls) == 1 and fake.sleeps == []


def test_the_agent_provider_retries_too() -> None:
    fake = FakeTime()
    transport, seen = server(busy(429, "0"), httpx.Response(200, json=OPENAI_OK))
    client = httpx.Client(base_url="http://test/v1", transport=transport)
    agent = OpenAICompatibleAgentProvider(model="m", client=client, policy=fake.policy())
    assert agent.chat([{"role": "user", "content": "hi"}], []).text == "Hi"
    assert len(seen) == 2 and agent.retries == 1


def test_providers_without_http_never_retry_or_wait() -> None:
    mock = MockProvider(["ok"])
    assert (mock.retries, mock.waited) == (0, 0.0)


# ---------------------------------------------------------------- pacing


def test_requests_start_at_least_request_interval_apart() -> None:
    fake = FakeTime()

    def slow_model(request: httpx.Request) -> httpx.Response:
        fake.now += 0.5  # the model thinks for half a second
        return httpx.Response(200, json={"a": "ok"})

    provider = http_bot(httpx.MockTransport(slow_model), fake.policy(request_interval=2.0))
    for _ in range(3):
        provider.generate("s", "u")
    assert fake.sleeps == [1.5, 1.5]  # the first request does not wait
    assert provider.waited == 3.0 and provider.retries == 0


def test_no_pause_when_the_answer_took_longer_than_the_interval() -> None:
    fake = FakeTime()

    def slower_model(request: httpx.Request) -> httpx.Response:
        fake.now += 3.0
        return httpx.Response(200, json={"a": "ok"})

    provider = http_bot(httpx.MockTransport(slower_model), fake.policy(request_interval=2.0))
    provider.generate("s", "u")
    provider.generate("s", "u")
    assert fake.sleeps == []


def test_a_retry_after_a_long_pause_does_not_also_wait_for_the_interval() -> None:
    fake = FakeTime()
    transport, seen = server(busy(429, "5"), httpx.Response(200, json={"a": "ok"}))
    provider = http_bot(transport, fake.policy(request_interval=2.0))
    provider.generate("s", "u")
    assert fake.sleeps == [5.0] and len(seen) == 2


def test_no_pacing_by_default() -> None:
    fake = FakeTime()
    transport, _ = server(httpx.Response(200, json={"a": "ok"}))
    provider = http_bot(transport, RequestPolicy(sleep=fake.sleep, clock=fake.clock))
    for _ in range(3):
        provider.generate("s", "u")
    assert fake.sleeps == []


# ---------------------------------------------------------------- runs


@pytest.fixture
def storage() -> Iterator[Storage]:
    db = Storage(":memory:")
    yield db
    db.close()


def snap(model: str, max_steps: int | None = None):  # type: ignore[no-untyped-def]
    return build_snapshot(
        model=model,
        temperature=0.0,
        num_predict=0,
        repeats=1,
        system_prompt="sys",
        scenarios_text="sc",
        max_steps=max_steps,
    )


def cases(n: int) -> list[Case]:
    return [
        Case(
            id=f"c-{i}:en",
            scenario_id=f"c-{i}",
            language=Language.EN,
            category=Category.BENIGN,
            prompt=f"question {i}",
            checks=["not_refused"],
        )
        for i in range(n)
    ]


def test_a_run_counts_retries_and_never_as_model_failures(storage: Storage) -> None:
    fake = FakeTime()
    ok = httpx.Response(200, json={"a": "ok"})
    transport, _ = server(busy(503, "1"), busy(429, "1"), ok, ok)
    provider = http_bot(transport, fake.policy())
    run_id = storage.create_run(snap(provider.model))
    summary = run_experiment(provider, cases(2), "sys", storage, run_id, repeats=1)
    assert summary == RunSummary(total=2, attempted=2, skipped=0, errors=0, retries=2)
    assert all(attempt.error is None for attempt in storage.get_attempts(run_id))


def test_an_agent_run_counts_retries_of_every_turn(storage: Storage) -> None:
    fake = FakeTime()
    transport, _ = server(busy(429, "0"), httpx.Response(200, json=OPENAI_OK))
    client = httpx.Client(base_url="http://test/v1", transport=transport)
    agent = OpenAICompatibleAgentProvider(model="m", client=client, policy=fake.policy())
    agent_cases = expand_agent_cases(load_agent_scenarios(DATA / "agent_scenarios_v1.yaml"))[:2]
    run_id = storage.create_run(snap("m", max_steps=5))
    summary = run_agent_experiment(agent, agent_cases, "sys", storage, run_id, repeats=1)
    assert (summary.attempted, summary.errors, summary.retries) == (2, 0, 1)


def test_latency_leaves_out_the_time_spent_waiting() -> None:
    started = time.perf_counter() - 5.0
    assert 1000 <= latency_ms(started, waited=4.0) < 2000
    assert latency_ms(time.perf_counter(), waited=9.0) == 0  # never negative


# ---------------------------------------------------------------- settings


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_the_settings_have_safe_defaults_and_refuse_nonsense(tmp_path: Path) -> None:
    target = TargetConfig(type="ollama", model="m")
    assert (target.request_interval, target.max_retries) == (0.0, 3)
    for bad in ("request_interval: -1", "max_retries: -1", "max_retries: 11"):
        text = f"target: {{type: ollama, model: m, {bad}}}\n"
        with pytest.raises(ConfigError, match=bad.split(":")[0]):
            load_config(write(tmp_path / "v.yaml", text))


@pytest.mark.parametrize(
    ("target", "make"),
    [
        (TargetConfig(type="ollama", model="m"), cli._target_provider),
        (TargetConfig(type="openai", model="m", base_url=LOCAL_API), cli._target_provider),
        (TargetConfig(type="http", url="https://b.test/c", reply_field="a"), cli._target_provider),
        (TargetConfig(type="ollama", model="m"), cli._agent_target_provider),
    ],
)
def test_the_target_settings_reach_every_http_provider(
    target: TargetConfig, make: Callable[[TargetConfig], Any]
) -> None:
    tuned = target.model_copy(update={"request_interval": 1.5, "max_retries": 7})
    policy = make(tuned)._requests
    assert (policy.request_interval, policy.max_retries) == (1.5, 7)


def test_vespera_test_shows_retries_only_when_there_were_some(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeTime()
    ok = httpx.Response(200, json={"answer": "Sorry, I cannot help with that."})
    transport, _ = server(busy(429, "1"), busy(503), ok)
    used: list[RequestPolicy] = []
    real = HttpProvider

    def with_fake_server(url: str, **options: Any) -> HttpProvider:
        configured = options.pop("policy")
        used.append(configured)
        policy = fake.policy(
            request_interval=configured.request_interval, max_retries=configured.max_retries
        )
        return real(url, client=httpx.Client(transport=transport), policy=policy, **options)

    monkeypatch.setattr(cli, "HttpProvider", with_fake_server)
    write(
        tmp_path / "vespera.yaml",
        f"""
target:
  type: http
  url: https://bot.example.com/chat
  reply_field: answer
  request_interval: 0.5
  max_retries: 4
repeats: 1
limit: 3
scenarios: {EXAMPLES / "scenarios_v1.yaml"}
system_prompt: {EXAMPLES / "system_prompt.txt"}
report: {tmp_path / "r.html"}
""",
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.app, ["test", "--db", str(tmp_path / "c.db")])
    assert result.exit_code == 0, result.output
    assert (used[0].request_interval, used[0].max_retries) == (0.5, 4)
    assert "3 attempts, 0 errors, 0 skipped, 2 requests retried after HTTP 429/503." in (
        result.output
    )
    assert fake.sleeps == [1.0, 2.0, 0.5, 0.5]  # Retry-After, backoff, then pacing


def test_the_saved_line_says_request_in_the_singular(capsys: pytest.CaptureFixture[str]) -> None:
    cli._print_saved(4, RunSummary(total=1, attempted=1, skipped=0, errors=0, retries=1), Path("d"))
    line = capsys.readouterr().out
    assert line.endswith(", 0 skipped, 1 request retried after HTTP 429/503. Saved to d\n")
    cli._print_saved(4, RunSummary(total=1, attempted=1, skipped=0, errors=0), Path("d"))
    assert capsys.readouterr().out == "[OK] run 4: 1 attempts, 0 errors, 0 skipped. Saved to d\n"
