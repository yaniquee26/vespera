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