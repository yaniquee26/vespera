"""The weights behind a model name: read from a local Ollama, kept in the snapshot (S2c)."""

import json
from pathlib import Path

import httpx
import pytest

from vespera.bundled import read_bundled
from vespera.model_identity import ModelIdentity, ollama_identity, server_root
from vespera.providers import (
    MockProvider,
    OllamaProvider,
    OpenAICompatibleAgentProvider,
    OpenAICompatibleProvider,
)
from vespera.report import build_report, render_html
from vespera.services.errors import ResumeMismatch
from vespera.services.results import evaluate, results_json
from vespera.services.runs import check_saved_run, execute_text_run
from vespera.storage import Storage

QWEN = "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"
TAGS = {
    "models": [
        {
            "name": "qwen2.5:7b",
            "model": "qwen2.5:7b",
            "digest": QWEN,
            "details": {"quantization_level": "Q4_K_M", "parameter_size": "7.6B"},
        },
        {"name": "llama3.1:latest", "model": "llama3.1:latest", "digest": "abc123"},
    ]
}


def ollama(
    payload: object = TAGS, status: int = 200, seen: list[str] | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_identity_of_an_installed_model() -> None:
    identity = ollama_identity(ollama(), "http://localhost:11434", "qwen2.5:7b")
    assert identity == ModelIdentity(QWEN, "Q4_K_M", "7.6B")


def test_a_name_without_a_tag_means_latest_and_missing_details_are_none() -> None:
    identity = ollama_identity(ollama(), "http://127.0.0.1:11434", "llama3.1")
    assert identity == ModelIdentity("abc123", None, None)


@pytest.mark.parametrize(
    ("client", "model"),
    [
        (ollama(), "mistral:7b"),  # not installed
        (ollama(status=404), "qwen2.5:7b"),  # not an Ollama server
        (ollama("not json"), "qwen2.5:7b"),
        (ollama({"models": "nope"}), "qwen2.5:7b"),
        (ollama([1, 2]), "qwen2.5:7b"),
    ],
)
def test_anything_unexpected_gives_no_identity(client: httpx.Client, model: str) -> None:
    assert ollama_identity(client, "http://localhost:11434", model) is None


def test_a_server_that_is_down_gives_no_identity() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    assert ollama_identity(client, "http://localhost:11434", "qwen2.5:7b") is None


def test_a_remote_api_is_never_asked() -> None:
    seen: list[str] = []
    assert ollama_identity(ollama(seen=seen), "https://api.openai.com/v1", "gpt-4o-mini") is None
    assert seen == []


def test_the_openai_style_address_of_ollama_is_asked_at_its_root() -> None:
    seen: list[str] = []
    provider = OpenAICompatibleAgentProvider(
        "qwen2.5:7b", "http://localhost:11434/v1", client=ollama(seen=seen)
    )
    assert provider.identity() == ModelIdentity(QWEN, "Q4_K_M", "7.6B")
    assert seen == ["http://localhost:11434/api/tags"]
    assert server_root("http://localhost:11434/v1/") == "http://localhost:11434"


def test_every_provider_answers_the_question() -> None:
    assert OllamaProvider("qwen2.5:7b", client=ollama()).identity() is not None
    remote = OpenAICompatibleProvider("gpt-4o-mini", api_key="k", client=ollama())
    assert remote.identity() is None
    assert MockProvider(["x"]).identity() is None


# ---------------------------------------------------------------- snapshot, resume, report


class Weighted(MockProvider):
    """A fake model whose server reports the given weights."""

    def __init__(self, digest: str) -> None:
        super().__init__(["I cannot help with that."])
        self.digest = digest

    def identity(self) -> ModelIdentity | None:
        return ModelIdentity(self.digest, "Q4_K_M", "7.6B")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    for name in ("scenarios_v1.yaml", "system_prompt.txt"):
        (tmp_path / name).write_text(read_bundled(name), encoding="utf-8")
    return tmp_path


def run(project: Path, provider: MockProvider, resume: int | None = None) -> int:
    run_id, _ = execute_text_run(
        lambda: provider,
        project / "scenarios_v1.yaml",
        project / "system_prompt.txt",
        1,
        3,
        project / "v.db",
        resume,
    )
    return run_id


def test_the_snapshot_keeps_the_weights_and_resume_refuses_other_weights(project: Path) -> None:
    run_id = run(project, Weighted(QWEN))
    with Storage(project / "v.db") as storage:
        snapshot = storage.get_snapshot(run_id)
    assert (snapshot.weights_digest, snapshot.weights_quantization) == (QWEN, "Q4_K_M")
    assert run(project, Weighted(QWEN), resume=run_id) == run_id
    with pytest.raises(ResumeMismatch, match="weights of mock changed"):
        run(project, Weighted("0" * 64), resume=run_id)


def test_unknown_weights_on_either_side_do_not_block_a_resume(project: Path) -> None:
    older = run(project, MockProvider(["I cannot help with that."]))  # no identity, like old runs
    assert run(project, Weighted(QWEN), resume=older) == older


def test_the_report_and_results_json_show_the_weights(project: Path) -> None:
    run_id = run(project, Weighted(QWEN))
    with Storage(project / "v.db") as storage:
        check_saved_run(storage, run_id, project / "system_prompt.txt")
        html = render_html(build_report(storage, run_id))
        data, gate = evaluate(storage, run_id, None)
        payload = json.loads(results_json(storage, run_id, data, gate, reveal=False))
    assert QWEN[:16] in html and "Q4_K_M" in html
    assert payload["run"]["weights_digest"] == QWEN
