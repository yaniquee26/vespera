"""Model providers: unified interface between Vespera and any LLM."""

import json
import math
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from itertools import cycle
from typing import Any
from urllib.parse import urlparse

import httpx

from vespera.agent_contracts import AgentTurn, ToolCall, ToolSpec

# HTTP 429 (too many requests) and 503 (busy) say "not now": the same request may work later.
RETRY_STATUSES = frozenset({429, 503})


def retry_after_seconds(response: httpx.Response) -> float | None:
    """The wait a server asks for in `Retry-After`, in seconds. None if absent or a date."""
    try:
        seconds = float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


class RequestPolicy:
    """How an HTTP provider paces its requests and retries when the server is busy.

    Requests start at least `request_interval` seconds apart. An answer with HTTP 429 or 503 is
    sent again, at most `max_retries` times: after the wait the server asks for in `Retry-After`,
    otherwise after an exponential pause with jitter (about 1, 2, 4 seconds, each cut by up to a
    half at random). A server that asks for a wait longer than `max_wait` is not waited for. The
    last answer is returned as it is, so the provider reports it like any other HTTP error.
    Retries and pauses are counted: a run shows the retries and keeps the pauses out of latency.
    """

    def __init__(
        self,
        *,
        request_interval: float = 0.0,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_wait: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.request_interval = request_interval
        self.max_retries = max_retries
        self._base_delay = base_delay
        self._max_wait = max_wait
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        self._last_start: float | None = None
        self.retries = 0  # requests sent again
        self.waited = 0.0  # seconds spent pausing, for pacing and before retries

    def post(self, client: httpx.Client, url: str, **options: Any) -> httpx.Response:
        """POST with pacing and retries. Network errors are raised as they are, not retried."""
        retry = 0
        while True:
            self._keep_interval()
            response = client.post(url, **options)
            if response.status_code not in RETRY_STATUSES or retry >= self.max_retries:
                return response
            delay = self._delay(response, retry)
            if delay is None:
                return response
            retry += 1
            self.retries += 1
            self._pause(delay)

    def _keep_interval(self) -> None:
        now = self._clock()
        if self._last_start is not None:
            gap = self.request_interval - (now - self._last_start)
            if gap > 0:
                self._pause(gap)
                now = self._clock()
        self._last_start = now

    def _delay(self, response: httpx.Response, retry: int) -> float | None:
        asked = retry_after_seconds(response)
        if asked is not None:
            return asked if asked <= self._max_wait else None
        step = min(self._max_wait, self._base_delay * 2**retry)
        return step * (1 - self._jitter() / 2)

    def _pause(self, seconds: float) -> None:
        self.waited += seconds
        self._sleep(seconds)


class _RequestCounters:
    """What a run reads from any provider. Only providers that talk HTTP make these move."""

    _requests: RequestPolicy | None = None

    @property
    def retries(self) -> int:
        """Requests sent again because the server was busy (HTTP 429 or 503)."""
        return self._requests.retries if self._requests else 0

    @property
    def waited(self) -> float:
        """Seconds spent pausing before requests: time of the rate limit, not of the model."""
        return self._requests.waited if self._requests else 0.0


class BaseProvider(_RequestCounters, ABC):
    """Abstract contract that every LLM adapter must follow."""

    model: str
    temperature: float
    num_predict: int

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Send system and user prompts to a model and return its text reply."""


class MockProvider(BaseProvider):
    """Deterministic fake provider for CI pipelines and unit tests."""

    def __init__(self, replies: list[str]) -> None:
        if not replies:
            raise ValueError("MockProvider requires at least one reply")
        self._replies = cycle(tuple(replies))
        self.model = "mock"
        self.temperature = 0.0
        self.num_predict = 0
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return next(self._replies)


class ProviderError(Exception):
    """A model could not give an answer (server down, bad reply, timeout)."""


class OllamaProvider(BaseProvider):
    """Talks to a local Ollama server. One request at a time, capped answer length."""

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        *,
        num_predict: int = 200,
        temperature: float = 0.7,
        timeout: float = 120.0,
        keep_alive: str = "5m",
        client: httpx.Client | None = None,
        policy: RequestPolicy | None = None,
    ) -> None:
        self.model = model
        self.num_predict = num_predict
        self.temperature = temperature
        self._keep_alive = keep_alive
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)
        self._requests: RequestPolicy = policy or RequestPolicy()

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {"num_predict": self.num_predict, "temperature": self.temperature},
        }
        try:
            response = self._requests.post(self._client, "/api/chat", json=payload)
            response.raise_for_status()
            content = response.json()["message"]["content"]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ProviderError(
                    f"model '{self.model}' is not installed in Ollama: "
                    f"run `ollama pull {self.model}`"
                ) from exc
            raise ProviderError(f"Ollama request failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama request failed: {exc}") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("Ollama returned an unexpected reply") from exc
        if not isinstance(content, str):
            raise ProviderError("Ollama returned an unexpected reply")
        return content


class OpenAICompatibleProvider(BaseProvider):
    """Talks to any server with the OpenAI chat API: OpenAI, OpenRouter, LM Studio, vLLM, Ollama.

    The API key is passed in, never stored: it does not reach the database or the report.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        *,
        api_key: str | None = None,
        num_predict: int = 200,
        temperature: float = 0.7,
        timeout: float = 120.0,
        client: httpx.Client | None = None,
        policy: RequestPolicy | None = None,
    ) -> None:
        self.model = model
        self.num_predict = num_predict
        self.temperature = temperature
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, headers=headers
        )
        self._requests: RequestPolicy = policy or RequestPolicy()

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.num_predict,
            "stream": False,
        }
        try:
            response = self._requests.post(self._client, "/chat/completions", json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            raise ProviderError(_explain_http_error(self.model, exc.response.status_code)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"request to the model server failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("the server returned an unexpected reply") from exc
        if not isinstance(content, str):
            raise ProviderError("the server returned an empty or unexpected reply")
        return content


def dig(data: Any, path: str) -> Any:
    """Follow a dotted path through JSON: 'choices.0.message.content'. Raises KeyError if absent."""
    current = data
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise KeyError(path) from exc
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(path)
    return current


def put(body: dict[str, Any], path: str, value: Any) -> None:
    """Set a value at a dotted path, creating objects on the way: 'input.text'."""
    *parents, last = path.split(".")
    current = body
    for part in parents:
        nested = current.setdefault(part, {})
        if not isinstance(nested, dict):
            raise ValueError(f"'{path}' collides with a fixed value in extra_body")
        current = nested
    current[last] = value


class HttpProvider(BaseProvider):
    """Talks to any bot behind a plain JSON-over-HTTP endpoint.

    Each question is sent alone, as one POST. Where the question goes in the request and where the
    answer sits in the response are set by dotted paths, so no code is needed for a new bot.
    Vespera does not control such a bot's own settings, so temperature and length are not set here.
    The key is passed in and never stored; error messages never repeat the address.
    """

    def __init__(
        self,
        url: str,
        *,
        reply_field: str,
        request_field: str = "message",
        extra_body: dict[str, Any] | None = None,
        system_field: str | None = None,
        api_key: str | None = None,
        api_key_header: str = "Authorization",
        api_key_prefix: str = "Bearer ",
        timeout: float = 60.0,
        client: httpx.Client | None = None,
        policy: RequestPolicy | None = None,
    ) -> None:
        parts = urlparse(url)
        self.model = f"{parts.netloc}{parts.path}".rstrip("/") or url  # no query: it may hold a key
        self.temperature = 0.0
        self.num_predict = 0
        self._url = url
        self._reply_field = reply_field
        self._request_field = request_field
        self._extra_body = extra_body or {}
        self._system_field = system_field
        headers = {api_key_header: f"{api_key_prefix}{api_key}"} if api_key else {}
        self._client = client or httpx.Client(timeout=timeout, headers=headers)
        self._requests: RequestPolicy = policy or RequestPolicy()

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        body = json.loads(json.dumps(self._extra_body))  # a private copy for every request
        try:
            put(body, self._request_field, user_prompt)
            if self._system_field:
                put(body, self._system_field, system_prompt)
        except ValueError as exc:
            raise ProviderError(str(exc)) from exc
        try:
            response = self._requests.post(self._client, self._url, json=body)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise ProviderError(_explain_http_error(self.model, status, "target.url")) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"request to the bot failed ({type(exc).__name__})") from exc
        except ValueError as exc:
            raise ProviderError("the bot did not answer with JSON") from exc
        try:
            content = dig(data, self._reply_field)
        except KeyError as exc:
            raise ProviderError(
                f"no reply found at '{self._reply_field}' in the bot's answer: check reply_field"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(f"the value at '{self._reply_field}' is empty or not text")
        return content


def _explain_http_error(model: str, status: int, where: str = "--model and --base-url") -> str:
    if status in (401, 403):
        return f"the server refused the API key (HTTP {status}): check the key and its rights"
    if status == 404:
        return (
            f"model '{model}' or the address was not found (HTTP 404): check {where}"
        )
    if status == 429:
        return (
            "too many requests or no credit left (HTTP 429): "
            "slow down with target.request_interval or check the account"
        )
    return f"the server answered with an error (HTTP {status})"


class AgentProvider(_RequestCounters, ABC):
    """A model that can ask for tools. Messages use the OpenAI chat format."""

    model: str
    temperature: float
    num_predict: int

    @abstractmethod
    def chat(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentTurn:
        """Send the conversation so far and return the model's next turn."""


class MockAgentProvider(AgentProvider):
    """Plays a fixed list of turns, over and over. For tests and for trying out without a GPU."""

    def __init__(self, turns: list[AgentTurn]) -> None:
        if not turns:
            raise ValueError("MockAgentProvider requires at least one turn")
        self._turns = cycle(tuple(turns))
        self.model = "mock"
        self.temperature = 0.0
        self.num_predict = 0
        self.calls: list[tuple[list[dict[str, Any]], list[ToolSpec]]] = []

    def chat(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentTurn:
        self.calls.append(([dict(m) for m in messages], list(tools)))
        return next(self._turns)


def tool_schema(tool: ToolSpec) -> dict[str, Any]:
    properties = {
        name: {"type": "string", "description": meaning}
        for name, meaning in tool.parameters.items()
    }
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(tool.parameters),
            },
        },
    }


def parse_agent_turn(data: Any) -> AgentTurn:
    """Read one OpenAI-style reply. Raises KeyError, TypeError or ValueError when malformed."""
    message = data["choices"][0]["message"]
    content = message.get("content") or ""
    if not isinstance(content, str):
        raise TypeError("content is not text")
    calls: list[ToolCall] = []
    for index, raw in enumerate(message.get("tool_calls") or []):
        function = raw["function"]
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {"_raw": arguments}
        if not isinstance(arguments, dict):
            arguments = {"_raw": str(arguments)}
        calls.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{index}"),
                name=function["name"],
                arguments=arguments,
            )
        )
    return AgentTurn(text=content, tool_calls=calls)


class OpenAICompatibleAgentProvider(AgentProvider):
    """Agent conversations with any OpenAI-style server that supports tools (also Ollama /v1)."""

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434/v1",
        *,
        api_key: str | None = None,
        num_predict: int = 300,
        temperature: float = 0.7,
        timeout: float = 180.0,
        client: httpx.Client | None = None,
        policy: RequestPolicy | None = None,
    ) -> None:
        self.model = model
        self.num_predict = num_predict
        self.temperature = temperature
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, headers=headers
        )
        self._requests: RequestPolicy = policy or RequestPolicy()

    def chat(self, messages: list[dict[str, Any]], tools: list[ToolSpec]) -> AgentTurn:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": [tool_schema(tool) for tool in tools],
            "temperature": self.temperature,
            "max_tokens": self.num_predict,
            "stream": False,
        }
        try:
            response = self._requests.post(self._client, "/chat/completions", json=payload)
            response.raise_for_status()
            return parse_agent_turn(response.json())
        except httpx.HTTPStatusError as exc:
            if "does not support tools" in exc.response.text:
                raise ProviderError(
                    f"model '{self.model}' does not support tools: use a model that can call "
                    "tools, for example qwen2.5:7b"
                ) from exc
            raise ProviderError(_explain_http_error(self.model, exc.response.status_code)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"request to the model server failed: {exc}") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("the server returned an unexpected reply") from exc
