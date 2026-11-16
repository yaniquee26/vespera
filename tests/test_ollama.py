import json

import httpx
import pytest
from typer.testing import CliRunner

from vespera import cli
from vespera.cli import app
from vespera.providers import BaseProvider, OllamaProvider, ProviderError


def make_provider(handler: httpx.MockTransport) -> OllamaProvider:
    client = httpx.Client(base_url="http://test", transport=handler)
    return OllamaProvider(model="gemma3:4b", client=client)


def test_generate_returns_model_text_and_sends_expected_request() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "Hi!"}})

    provider = make_provider(httpx.MockTransport(handler))
    assert provider.generate("be brief", "hello") == "Hi!"
    assert seen["path"] == "/api/chat"
    body = seen["body"]
    assert body["model"] == "gemma3:4b"
    assert body["stream"] is False
    assert body["options"]["num_predict"] == 200
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
    ]


def test_ollama_provider_is_a_provider() -> None:
    assert issubclass(OllamaProvider, BaseProvider)


def test_connection_error_becomes_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    provider = make_provider(httpx.MockTransport(handler))
    with pytest.raises(ProviderError):
        provider.generate("s", "u")


def test_server_error_becomes_provider_error() -> None:
    provider = make_provider(httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ProviderError):
        provider.generate("s", "u")


def test_missing_model_tells_the_user_how_to_install_it() -> None:
    provider = make_provider(httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(ProviderError, match="ollama pull gemma3:4b"):
        provider.generate("s", "u")


def test_unexpected_reply_shape_becomes_provider_error() -> None:
    provider = make_provider(httpx.MockTransport(lambda r: httpx.Response(200, json={"oops": 1})))
    with pytest.raises(ProviderError):
        provider.generate("s", "u")


def test_non_text_content_becomes_provider_error() -> None:
    reply = {"message": {"content": 42}}
    provider = make_provider(httpx.MockTransport(lambda r: httpx.Response(200, json=reply)))
    with pytest.raises(ProviderError):
        provider.generate("s", "u")


class FakeProvider:
    def __init__(self, model: str) -> None:
        self.model = model

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        return f"echo: {user_prompt}"


class BrokenProvider:
    def __init__(self, model: str) -> None:
        pass

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise ProviderError("server down")


def test_ask_prints_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "OllamaProvider", FakeProvider)
    result = CliRunner().invoke(app, ["ask", "hello"])
    assert result.exit_code == 0
    assert "echo: hello" in result.output


def test_ask_reports_failure_with_exit_code_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "OllamaProvider", BrokenProvider)
    result = CliRunner().invoke(app, ["ask", "hello"])
    assert result.exit_code == 1
    assert "[FAIL] server down" in result.output


# ------------------------------------------------ OpenAI-compatible provider


def make_openai(handler, **kwargs):  # type: ignore[no-untyped-def]
    from vespera.providers import OpenAICompatibleProvider

    client = httpx.Client(base_url="http://test/v1", transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(model="car-bot", client=client, **kwargs)


def test_openai_provider_sends_expected_request_and_returns_text() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hello"}}]})

    assert make_openai(handler).generate("sys", "hi") == "Hello"
    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"]["max_tokens"] == 200
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


@pytest.mark.parametrize(
    ("status", "words"),
    [(401, "API key"), (403, "API key"), (404, "--base-url"), (429, "too many"), (500, "HTTP 500")],
)
def test_openai_provider_explains_http_errors(status: int, words: str) -> None:
    provider = make_openai(lambda request: httpx.Response(status, json={}))
    with pytest.raises(ProviderError, match=words):
        provider.generate("s", "u")


@pytest.mark.parametrize(
    "body", [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]}, "not json"]
)
def test_openai_provider_rejects_strange_replies(body) -> None:  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        if body == "not json":
            return httpx.Response(200, content=b"<html>")
        return httpx.Response(200, json=body)

    with pytest.raises(ProviderError):
        make_openai(handler).generate("s", "u")


def test_openai_provider_wraps_network_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(ProviderError, match="failed"):
        make_openai(handler).generate("s", "u")


def test_openai_key_is_sent_as_bearer_header_but_never_stored() -> None:
    from vespera.providers import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(model="m", base_url="http://x/v1", api_key="sk-secret")
    assert provider._client.headers["Authorization"] == "Bearer sk-secret"
    assert "sk-secret" not in repr(vars(provider).get("model"))


def test_run_with_openai_needs_a_key_unless_local(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = CliRunner().invoke(
        app, ["run", "--provider", "openai", "--limit", "1", "--db", str(tmp_path / "x.db")]
    )
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output


def test_run_rejects_unknown_provider(tmp_path) -> None:  # type: ignore[no-untyped-def]
    result = CliRunner().invoke(
        app, ["run", "--provider", "banana", "--db", str(tmp_path / "x.db")]
    )
    assert result.exit_code == 1
    assert "ollama or openai" in result.output