"""Targets: how Vespera talks to a bot or model, the targets saved in the workspace, and a single
test message for the Playground.

An API key is only ever read from an environment variable at the moment a request is sent. It is
never stored, never put in a page and never written to targets.yaml: only the variable's name.
"""

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from vespera.agent_contracts import AgentTurn
from vespera.config import TargetConfig
from vespera.demo import DemoProvider
from vespera.providers import (
    AgentProvider,
    BaseProvider,
    HttpProvider,
    MockAgentProvider,
    MockProvider,
    OllamaProvider,
    OpenAICompatibleAgentProvider,
    OpenAICompatibleProvider,
    ProviderError,
    RequestPolicy,
)
from vespera.services.errors import MissingApiKey, TargetError
from vespera.workspace import Workspace

OLLAMA_ADDRESS = "http://localhost:11434"
OPENAI_ADDRESS = "https://api.openai.com/v1"
MOCK_REPLY = "I cannot help with that."
DEMO_AGENT_PROBLEM = "the demo target has text mode only"
DEMO_AGENT_TIP = "Tip: for a first look at agent mode use `vespera test mock --mode agent`."
TARGET_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
TARGETS_FILE = "targets.yaml"
MASKED = "••••••••"


# ---------------------------------------------------------------- keys


def api_key_from_env(name: str | None) -> str | None:
    """The key in the variable `name`. No name means the target needs no key."""
    if name is None:
        return None
    key = os.environ.get(name)
    if not key:
        raise MissingApiKey(name)
    return key


def api_key_for(address: str, api_key_env: str) -> str | None:
    """The key for an API address. A server on this computer needs none, any other server does."""
    key = os.environ.get(api_key_env)
    if not key and "localhost" not in address and "127.0.0.1" not in address:
        raise MissingApiKey(api_key_env)
    return key


def env_var_is_set(name: str) -> bool:
    """Whether a variable has a value. Callers show only yes or no, never the value itself."""
    return bool(os.environ.get(name))


# ---------------------------------------------------------------- providers


def request_policy(target: TargetConfig) -> RequestPolicy:
    """Pacing and retries for a target's HTTP requests."""
    return RequestPolicy(request_interval=target.request_interval, max_retries=target.max_retries)


def text_provider(target: TargetConfig, mock: bool = False) -> BaseProvider:
    """The provider that asks a target questions in text mode."""
    if mock or target.type == "mock":
        return MockProvider([MOCK_REPLY])
    if target.type == "demo":
        return DemoProvider()
    if target.type == "http":
        assert target.url is not None and target.reply_field is not None  # config validated
        return HttpProvider(
            target.url,
            reply_field=target.reply_field,
            request_field=target.request_field,
            extra_body=target.extra_body,
            system_field=target.system_field,
            api_key=api_key_from_env(target.api_key_env),
            api_key_header=target.api_key_header,
            api_key_prefix=target.api_key_prefix,
            timeout=target.timeout,
            policy=request_policy(target),
        )
    assert target.model is not None  # config validated
    if target.type == "openai":
        address = target.base_url or OPENAI_ADDRESS
        return OpenAICompatibleProvider(
            model=target.model,
            base_url=address,
            api_key=api_key_for(address, target.api_key_env or "OPENAI_API_KEY"),
            policy=request_policy(target),
        )
    return OllamaProvider(
        model=target.model,
        base_url=target.base_url or OLLAMA_ADDRESS,
        policy=request_policy(target),
    )


def agent_provider(target: TargetConfig, mock: bool = False) -> AgentProvider:
    """The provider that gives a target tasks with tools (agent mode)."""
    if mock or target.type == "mock":
        return MockAgentProvider([AgentTurn(text=MOCK_REPLY)])
    if target.type == "demo":
        raise TargetError(DEMO_AGENT_PROBLEM, tip=DEMO_AGENT_TIP)
    assert target.model is not None  # config validated
    default = OPENAI_ADDRESS if target.type == "openai" else f"{OLLAMA_ADDRESS}/v1"
    address = target.base_url or default
    return OpenAICompatibleAgentProvider(
        model=target.model,
        base_url=address,
        api_key=api_key_for(address, target.api_key_env or "OPENAI_API_KEY"),
        policy=request_policy(target),
    )


# ---------------------------------------------------------------- saved targets


@dataclass(frozen=True)
class SavedTarget:
    name: str
    config: TargetConfig

    @property
    def key_env(self) -> str | None:
        if self.config.api_key_env:
            return self.config.api_key_env
        return "OPENAI_API_KEY" if self.config.type == "openai" else None

    @property
    def address(self) -> str:
        """Where requests go, in words people recognise."""
        config = self.config
        if config.type == "http":
            return config.url or ""
        if config.type == "openai":
            return config.base_url or OPENAI_ADDRESS
        if config.type == "ollama":
            return config.base_url or OLLAMA_ADDRESS
        return "no network: a fake model"


def _targets_path(space: Workspace) -> Path:
    return space.root / TARGETS_FILE


def list_targets(space: Workspace) -> list[SavedTarget]:
    """The targets saved in the workspace, by name. A broken file is reported, not ignored."""
    path = _targets_path(space)
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = raw.get("targets", {}) if isinstance(raw, dict) else None
        if not isinstance(items, dict):
            raise TargetError(f"{path} must hold a 'targets' mapping")
        return [
            SavedTarget(name, TargetConfig.model_validate(items[name])) for name in sorted(items)
        ]
    except (yaml.YAMLError, ValidationError) as exc:
        raise TargetError(f"{path} cannot be read: {exc}") from exc


def get_target(space: Workspace, name: str) -> SavedTarget:
    for item in list_targets(space):
        if item.name == name:
            return item
    raise TargetError(f"no saved target called {name}")


def _write_targets(space: Workspace, targets: list[SavedTarget]) -> None:
    payload = {
        "targets": {
            item.name: item.config.model_dump(mode="json", exclude_defaults=True)
            | {"type": item.config.type}
            for item in targets
        }
    }
    header = "# Targets saved by Vespera Studio. Keys are never stored: only variable names.\n"
    path = _targets_path(space)
    space.ensure()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(header + yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def build_target(fields: dict[str, str]) -> TargetConfig:
    """A target from form fields. Empty fields are left out; numbers and JSON are converted."""
    data: dict[str, Any] = {}
    for key, value in fields.items():
        text = value.strip()
        if not text:
            continue
        if key in ("timeout", "request_interval"):
            data[key] = _number(key, text, float)
        elif key == "max_retries":
            data[key] = _number(key, text, int)
        elif key == "extra_body":
            data[key] = _json_object(text)
        else:
            data[key] = text
    env_name = data.get("api_key_env")
    if env_name is not None and not ENV_NAME.match(env_name):
        raise TargetError(
            "api_key_env takes the NAME of an environment variable, such as OPENAI_API_KEY, "
            "never the key itself"
        )
    try:
        return TargetConfig.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(str(problem["msg"]) for problem in exc.errors())
        raise TargetError(f"the target is not complete: {problems}") from exc


def _number(key: str, text: str, kind: type[float] | type[int]) -> float | int:
    try:
        return kind(text)
    except ValueError as exc:
        raise TargetError(f"{key} must be a number") from exc


def _json_object(text: str) -> dict[str, Any]:
    problem = 'extra_body must be a JSON object, for example {"channel": "web"}'
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TargetError(problem) from exc
    if not isinstance(value, dict):
        raise TargetError(problem)
    return value


def save_target(space: Workspace, name: str, config: TargetConfig) -> SavedTarget:
    if not TARGET_NAME.match(name):
        raise TargetError(
            "a target name uses small letters, digits and dashes, up to 40 characters"
        )
    others = [item for item in list_targets(space) if item.name != name]
    saved = SavedTarget(name, config)
    _write_targets(space, [*others, saved])
    return saved


def delete_target(space: Workspace, name: str) -> None:
    remaining = [item for item in list_targets(space) if item.name != name]
    if len(remaining) == len(list_targets(space)):
        raise TargetError(f"no saved target called {name}")
    _write_targets(space, remaining)


# ---------------------------------------------------------------- the Playground


def request_preview(target: TargetConfig, message: str, system_prompt: str) -> dict[str, Any]:
    """What Vespera sends for one question, as people read it. The key is always masked."""
    key_env = target.api_key_env or ("OPENAI_API_KEY" if target.type == "openai" else None)
    headers = {"Content-Type": "application/json"}
    if key_env and env_var_is_set(key_env):
        header = target.api_key_header if target.type == "http" else "Authorization"
        prefix = target.api_key_prefix if target.type == "http" else "Bearer "
        headers[header] = f"{prefix}{MASKED} (from {key_env})"
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": message}]
    if target.type in ("mock", "demo"):
        return {
            "method": "none",
            "url": "(a fake model: nothing is sent)",
            "body": {"message": message},
        }
    if target.type == "http":
        body: dict[str, Any] = dict(target.extra_body)
        body[target.request_field] = message
        if target.system_field:
            body[target.system_field] = system_prompt
        return {"method": "POST", "url": target.url, "headers": headers, "body": body}
    if target.type == "openai":
        url = f"{(target.base_url or OPENAI_ADDRESS).rstrip('/')}/chat/completions"
        return {
            "method": "POST",
            "url": url,
            "headers": headers,
            "body": {"model": target.model, "messages": messages},
        }
    url = f"{(target.base_url or OLLAMA_ADDRESS).rstrip('/')}/api/chat"
    return {
        "method": "POST",
        "url": url,
        "headers": headers,
        "body": {"model": target.model, "messages": messages, "stream": False},
    }


@dataclass(frozen=True)
class TryResult:
    request: dict[str, Any]
    reply: str | None
    error: str | None
    latency_ms: int
    retries: int


def try_target(
    target: TargetConfig,
    message: str,
    system_prompt: str = "You are a helpful assistant.",
    make_provider: Callable[[TargetConfig], BaseProvider] | None = None,
) -> TryResult:
    """Send one message to a target and report what happened. Problems are results, not crashes."""
    preview = request_preview(target, message, system_prompt)
    factory = make_provider or text_provider
    try:
        provider = factory(target)
    except MissingApiKey as exc:
        return TryResult(preview, None, str(exc), 0, 0)
    started = time.perf_counter()
    try:
        reply = provider.generate(system_prompt, message)
        error = None
    except ProviderError as exc:
        reply, error = None, str(exc)
    elapsed = time.perf_counter() - started - provider.waited
    return TryResult(preview, reply, error, max(0, round(elapsed * 1000)), provider.retries)
