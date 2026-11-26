"""Targets and the Playground (S4): saved connections, one test message, models in memory."""

import hashlib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from vespera.config import TargetConfig
from vespera.model_identity import LoadedModel, ollama_loaded
from vespera.providers import (
    HttpProvider,
    MockAgentProvider,
    MockProvider,
    OllamaProvider,
    OpenAICompatibleAgentProvider,
    OpenAICompatibleProvider,
    ProviderError,
)
from vespera.services.errors import TargetError
from vespera.services.targets import (
    MASKED,
    agent_provider,
    build_target,
    delete_target,
    get_target,
    list_targets,
    request_preview,
    save_target,
    text_provider,
    try_target,
)
from vespera.ui import app as ui_app
from vespera.ui.app import UiSettings, create_app
from vespera.workspace import Workspace, resolve_workspace

BASE = "http://127.0.0.1:8765"
SENTINEL = "sk-sentinel-must-never-show-0123456789"
STATIC = Path(ui_app.__file__).parent / "static"
HTMX_SHA256 = "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447"


@pytest.fixture
def space(tmp_path: Path) -> Workspace:
    return resolve_workspace(tmp_path / "ws").ensure()


def studio(space: Workspace, **extra: object) -> tuple[TestClient, dict[str, str]]:
    settings = UiSettings(
        workspace=space, environment_checks=lambda: [], loaded_models=lambda: None, **extra
    )  # type: ignore[arg-type]
    return TestClient(create_app(settings), base_url=BASE), {"x-csrf-token": settings.csrf_token}


# ---------------------------------------------------------------- vendored htmx


def test_the_shipped_htmx_is_the_verified_file() -> None:
    digest = hashlib.sha256((STATIC / "htmx.min.js").read_bytes()).hexdigest()
    assert digest == HTMX_SHA256
    assert HTMX_SHA256 in (STATIC.parent / "VENDOR.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- building and saving targets


def test_form_fields_become_a_target_with_numbers_and_json() -> None:
    target = build_target(
        {
            "type": "http",
            "url": "https://bot.example.com/chat",
            "reply_field": "data.text",
            "extra_body": '{"channel": "web"}',
            "timeout": "30",
            "max_retries": "5",
            "system_field": "  ",
        }
    )
    assert (target.timeout, target.max_retries, target.extra_body) == (30.0, 5, {"channel": "web"})
    assert target.system_field is None


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"type": "openai", "model": "m", "api_key_env": SENTINEL}, "NAME of an environment"),
        ({"type": "http", "url": "https://x", "reply_field": "a", "timeout": "soon"}, "number"),
        ({"type": "http", "url": "https://x", "reply_field": "a", "extra_body": "[1]"}, "JSON"),
        ({"type": "http", "url": "https://x", "reply_field": "a", "extra_body": "{"}, "JSON"),
        ({"type": "http", "url": "https://x"}, "not complete"),
    ],
)
def test_bad_form_fields_are_explained(fields: dict[str, str], message: str) -> None:
    with pytest.raises(TargetError, match=message):
        build_target(fields)


def test_saved_targets_round_trip_without_ever_storing_a_key(
    space: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOT_KEY", SENTINEL)
    config = build_target({"type": "openai", "model": "gpt-4o-mini", "api_key_env": "BOT_KEY"})
    save_target(space, "cloud", config)
    save_target(space, "local", build_target({"type": "ollama", "model": "qwen2.5:7b"}))
    assert [item.name for item in list_targets(space)] == ["cloud", "local"]
    assert get_target(space, "cloud").config == config
    stored = (space.root / "targets.yaml").read_text(encoding="utf-8")
    assert "BOT_KEY" in stored and SENTINEL not in stored
    delete_target(space, "cloud")
    assert [item.name for item in list_targets(space)] == ["local"]


def test_target_names_lookups_and_broken_files_are_errors(space: Workspace) -> None:
    mock = TargetConfig(type="mock")
    with pytest.raises(TargetError, match="small letters"):
        save_target(space, "My Bot", mock)
    with pytest.raises(TargetError, match="no saved target"):
        get_target(space, "ghost")
    with pytest.raises(TargetError, match="no saved target"):
        delete_target(space, "ghost")
    (space.root / "targets.yaml").write_text("targets: [1, 2]\n", encoding="utf-8")
    with pytest.raises(TargetError, match="mapping"):
        list_targets(space)
    (space.root / "targets.yaml").write_text("targets: {a: {type: ftp}}\n", encoding="utf-8")
    with pytest.raises(TargetError, match="cannot be read"):
        list_targets(space)


def test_saved_targets_know_their_address_and_key_variable() -> None:
    from vespera.services.targets import SavedTarget

    cases = {
        "mock": (TargetConfig(type="mock"), "fake model", None),
        "ollama": (TargetConfig(type="ollama", model="m"), "localhost:11434", None),
        "openai": (TargetConfig(type="openai", model="m"), "api.openai.com", "OPENAI_API_KEY"),
        "http": (TargetConfig(type="http", url="https://b", reply_field="a"), "https://b", None),
    }
    for name, (config, address, key) in cases.items():
        saved = SavedTarget(name, config)
        assert address in saved.address and saved.key_env == key


def test_the_factories_build_the_right_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert isinstance(text_provider(TargetConfig(type="mock")), MockProvider)
    assert isinstance(text_provider(TargetConfig(type="ollama", model="m")), OllamaProvider)
    openai = TargetConfig(type="openai", model="m")
    assert isinstance(text_provider(openai), OpenAICompatibleProvider)
    http = TargetConfig(type="http", url="https://b", reply_field="a")
    assert isinstance(text_provider(http), HttpProvider)
    assert isinstance(agent_provider(TargetConfig(type="mock")), MockAgentProvider)
    assert isinstance(agent_provider(openai), OpenAICompatibleAgentProvider)


# ---------------------------------------------------------------- the Playground


def test_the_request_preview_masks_the_key_and_shows_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_KEY", SENTINEL)
    http = TargetConfig(
        type="http", url="https://bot.example.com/chat", reply_field="answer",
        request_field="query", extra_body={"channel": "web"}, system_field="system",
        api_key_env="BOT_KEY", api_key_header="X-Api-Key", api_key_prefix="",
    )
    preview = request_preview(http, "hi", "be nice")
    assert preview["body"] == {"channel": "web", "query": "hi", "system": "be nice"}
    assert preview["headers"]["X-Api-Key"] == f"{MASKED} (from BOT_KEY)"
    assert SENTINEL not in str(preview)
    ollama = request_preview(TargetConfig(type="ollama", model="qwen2.5:7b"), "hi", "s")
    assert ollama["url"] == "http://localhost:11434/api/chat" and ollama["body"]["stream"] is False
    openai = request_preview(TargetConfig(type="openai", model="m"), "hi", "s")
    assert openai["url"].endswith("/chat/completions")
    assert request_preview(TargetConfig(type="mock"), "hi", "s")["method"] == "none"


def test_try_target_reports_reply_error_and_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = try_target(TargetConfig(type="mock"), "hi")
    assert ok.reply and ok.error is None and ok.retries == 0

    class Down(MockProvider):
        def generate(self, system_prompt: str, user_prompt: str) -> str:
            raise ProviderError("server down")

    failed = try_target(TargetConfig(type="mock"), "hi", make_provider=lambda t: Down(["x"]))
    assert (failed.reply, failed.error) == (None, "server down")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    no_key = try_target(TargetConfig(type="openai", model="m"), "hi")
    assert no_key.error == "set the environment variable OPENAI_API_KEY with your API key"


# ---------------------------------------------------------------- pages


def test_targets_pages_add_open_try_and_delete(space: Workspace) -> None:
    client, token = studio(space)
    assert "No targets yet" in client.get("/targets").text
    form = client.get("/targets/new?type=http").text
    assert 'name="url"' in form and 'name="reply_field"' in form
    assert client.get("/targets/new?type=ftp").status_code == 404

    created = client.post("/targets", data={"type": "mock", "name": "demo"}, headers=token)
    assert (created.status_code, created.headers["HX-Redirect"]) == (204, "/targets/demo")
    assert "demo" in client.get("/targets").text

    page = client.get("/targets/demo").text
    assert 'data-testid="playground"' in page and 'hx-post="/targets/demo/try"' in page
    tried = client.post("/targets/demo/try", data={"message": "hello"}, headers=token).text
    assert "I cannot help with that." in tried and "a fake model" in tried
    empty = client.post("/targets/demo/try", data={"message": "  "}, headers=token).text
    assert "write a message" in empty

    removed = client.post("/targets/demo/delete", headers=token)
    assert (removed.status_code, removed.headers["HX-Redirect"]) == (204, "/targets")
    assert client.get("/targets/demo").status_code == 404


def test_problems_come_back_as_messages_for_the_form(space: Workspace) -> None:
    client, token = studio(space)
    bad_name = client.post("/targets", data={"type": "mock", "name": "Bad Name"}, headers=token)
    assert bad_name.status_code == 200 and "small letters" in bad_name.text
    bad_type = client.post("/targets", data={"type": "ftp", "name": "x"}, headers=token)
    assert "unknown target type" in bad_type.text
    assert "no saved target" in client.post("/targets/ghost/try", headers=token).text
    assert "no saved target" in client.post("/targets/ghost/delete", headers=token).text
    assert client.post("/targets", data={"type": "mock", "name": "x"}).status_code == 403


def test_a_broken_targets_file_is_shown_not_hidden(space: Workspace) -> None:
    (space.root / "targets.yaml").write_text("targets: [1]\n", encoding="utf-8")
    client, _ = studio(space)
    assert 'data-testid="targets-problem"' in client.get("/targets").text


def test_the_playground_uses_the_injected_provider(space: Workspace) -> None:
    save_target(space, "bot", TargetConfig(type="mock"))
    client, token = studio(space, text_provider=lambda target: MockProvider(["<b>bold</b>"]))
    reply = client.post("/targets/bot/try", data={"message": "hi"}, headers=token).text
    assert "&lt;b&gt;bold&lt;/b&gt;" in reply  # a reply is hostile input: always escaped


# ---------------------------------------------------------------- models in memory


def test_settings_show_models_in_video_memory(space: Workspace) -> None:
    loaded = [LoadedModel("qwen2.5:7b", 5_000_000_000, 5_000_000_000),
              LoadedModel("big:14b", 10_000_000_000, 6_000_000_000)]
    settings = UiSettings(
        workspace=space, environment_checks=lambda: [], loaded_models=lambda: loaded
    )
    page = TestClient(create_app(settings), base_url=BASE).get("/settings").text
    assert "all of it" in page and "60% — the rest runs on the CPU" in page
    client, _ = studio(space)
    assert "Ollama is not running" in client.get("/settings").text


def test_ollama_loaded_reads_api_ps_and_skips_remote_servers() -> None:
    payload = {"models": [{"name": "qwen2.5:7b", "size": 4, "size_vram": 3}, {"size": 1}]}
    answer = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    client = httpx.Client(transport=answer)
    assert ollama_loaded(client, "http://localhost:11434") == [LoadedModel("qwen2.5:7b", 4, 3)]
    assert ollama_loaded(client, "https://api.example.com") is None
    broken = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert ollama_loaded(broken, "http://localhost:11434") is None
    assert LoadedModel("x", 0, 0).gpu_share == 0.0
