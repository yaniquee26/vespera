"""Which weights a model name stands for.

A tag such as `qwen2.5:7b` can point to new weights after `ollama pull`, so the name alone does
not make a run reproducible. Ollama lists every installed model with the digest of its weights
and their quantization (`GET /api/tags`); the snapshot of a run keeps both.

Only servers on this computer are asked: a remote API gets no extra request.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
PROBE_TIMEOUT = 5.0  # seconds; the identity is a nice-to-have, never worth a long wait


@dataclass(frozen=True)
class ModelIdentity:
    digest: str  # the digest of the weights, as Ollama reports it (hex)
    quantization: str | None  # for example Q4_K_M
    parameter_size: str | None  # for example 7.6B


def is_local(address: str) -> bool:
    return urlsplit(address).hostname in LOCAL_HOSTS


def server_root(address: str) -> str:
    """The server address without an OpenAI-style `/v1` suffix: Ollama serves both."""
    root = address.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def _matches(entry: dict[str, Any], model: str) -> bool:
    names = {entry.get("name"), entry.get("model")}
    return model in names or (":" not in model and f"{model}:latest" in names)


def ollama_identity(client: httpx.Client, address: str, model: str) -> ModelIdentity | None:
    """The identity of `model` on the Ollama server at `address`, or None if it cannot be told.

    Any problem (the server is not Ollama, is down, does not list the model) gives None: a run
    never fails because its weights could not be fingerprinted.
    """
    if not is_local(address):
        return None
    try:
        response = client.get(f"{server_root(address)}/api/tags", timeout=PROBE_TIMEOUT)
        response.raise_for_status()
        entries = response.json().get("models", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and _matches(entry, model) and entry.get("digest"):
            raw = entry.get("details")
            details: dict[str, Any] = raw if isinstance(raw, dict) else {}
            return ModelIdentity(
                digest=str(entry["digest"]),
                quantization=details.get("quantization_level"),
                parameter_size=details.get("parameter_size"),
            )
    return None
