"""The project file `vespera.yaml`: what to test, with which tests, and which result is acceptable.

One small file replaces a long command line. Unknown keys are refused, so a typo is reported
instead of silently ignored. Secrets are never written here: only the NAME of the environment
variable that holds a key.
"""

from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ConfigError(Exception):
    """vespera.yaml cannot be used."""


class TargetConfig(BaseModel):
    """The thing under test: a local model, an OpenAI-style server, or any bot behind HTTP."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["ollama", "openai", "http", "mock", "demo"] = "ollama"
    model: str | None = None  # ollama and openai
    base_url: str | None = None  # openai (and agent runs): the API address
    api_key_env: str | None = None  # NAME of the environment variable with the key
    # type http: a bot behind a plain JSON endpoint
    url: str | None = None
    request_field: str = "message"  # where the question goes in the JSON body (dotted path)
    reply_field: str | None = None  # where the answer sits in the JSON reply (dotted path)
    extra_body: dict[str, Any] = Field(default_factory=dict)  # fixed fields sent every time
    system_field: str | None = None  # send the system prompt here (only for your own models)
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "
    timeout: float = Field(default=60.0, gt=0)
    # any HTTP target: for servers with a rate limit
    request_interval: float = Field(default=0.0, ge=0)  # seconds between two requests
    max_retries: int = Field(default=3, ge=0, le=10)  # repeats of a request after HTTP 429/503

    @model_validator(mode="after")
    def _needs(self) -> Self:
        if self.type in ("ollama", "openai") and not self.model:
            raise ValueError(f"target.model is required for type {self.type}")
        if self.type == "http":
            if not self.url:
                raise ValueError("target.url is required for type http")
            if not self.reply_field:
                raise ValueError(
                    "target.reply_field is required for type http "
                    "(the dotted path of the answer in the JSON reply, e.g. 'answer')"
                )
        return self


# Short names for common services: prefix -> (API address, environment variable with the key).
# A key is None for a local server, which needs none.
SERVICES: dict[str, tuple[str, str | None]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "lmstudio": ("http://localhost:1234/v1", None),
}

TARGET_EXAMPLES = (
    "qwen2.5:7b (local Ollama), ollama:llama3.1:8b, openai:gpt-4o-mini, "
    "openrouter:meta-llama/llama-3.1-8b-instruct, lmstudio:MODEL, mock, demo"
)


def parse_target(spec: str) -> TargetConfig:
    """A target written as one string, for example 'openai:gpt-4o-mini' or 'qwen2.5:7b'.

    A known prefix picks the service; anything else is a local Ollama model name (Ollama names
    contain a colon themselves, like 'qwen2.5:7b'). A bot behind plain HTTP needs more settings
    than fit in one string, so it is described in vespera.yaml instead.
    """
    text = spec.strip()
    if text == "mock":
        return TargetConfig(type="mock")
    if text == "demo":
        return TargetConfig(type="demo")
    if text.startswith(("http://", "https://")):
        raise ConfigError(
            "a web address needs more settings (where the answer is in the reply): "
            "describe the bot in vespera.yaml, `vespera init` asks for them"
        )
    prefix, _, rest = text.partition(":")
    if prefix == "ollama":
        text, prefix = rest, ""
    elif prefix in SERVICES:
        if not rest:
            raise ConfigError(f"give a model after '{prefix}:', for example {prefix}:MODEL")
        base_url, key_env = SERVICES[prefix]
        return TargetConfig(type="openai", model=rest, base_url=base_url, api_key_env=key_env)
    if not text:
        raise ConfigError(f"empty target; examples: {TARGET_EXAMPLES}")
    return TargetConfig(type="ollama", model=text)


class GateConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attack_rate: float | None = Field(default=None, ge=0, le=1)
    zero_tolerance: list[str] = Field(default_factory=list)  # OWASP ids or category names
    min_exposure: float = Field(default=0.5, ge=0, le=1)


class ExportsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    results_json: Path | None = None
    junit_xml: Path | None = None


class ProjectConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target: TargetConfig
    mode: Literal["text", "agent"] = "text"
    repeats: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=None, ge=1)
    max_steps: int | None = Field(default=None, ge=1, le=20)  # agent mode
    scenarios: Path | None = None
    system_prompt: Path | None = None
    report: Path | None = None
    gate: GateConfig = Field(default_factory=GateConfig)
    exports: ExportsConfig = Field(default_factory=ExportsConfig)

    @model_validator(mode="after")
    def _agent_needs_a_tool_calling_target(self) -> Self:
        if self.mode == "agent" and self.target.type == "http":
            raise ValueError(
                "mode agent needs a target with tool calling (type openai or ollama), "
                "not a plain http bot"
            )
        return self


def load_config(path: Path) -> ProjectConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping with at least a 'target' section")
    try:
        return ProjectConfig.model_validate(raw)
    except ValidationError as exc:
        lines = []
        for problem in exc.errors():
            place = ".".join(str(part) for part in problem["loc"]) or "(top level)"
            lines.append(f"  {place}: {problem['msg']}")
        raise ConfigError(f"{path} has problems:\n" + "\n".join(lines)) from exc
