"""Model providers: unified interface between Vespera and any LLM."""

from abc import ABC, abstractmethod
from itertools import cycle

import httpx


class BaseProvider(ABC):
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
    ) -> None:
        self.model = model
        self.num_predict = num_predict
        self.temperature = temperature
        self._keep_alive = keep_alive
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)

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
            response = self._client.post("/api/chat", json=payload)
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
    ) -> None:
        self.model = model
        self.num_predict = num_predict
        self.temperature = temperature
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, headers=headers
        )

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
            response = self._client.post("/chat/completions", json=payload)
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


def _explain_http_error(model: str, status: int) -> str:
    if status in (401, 403):
        return f"the server refused the API key (HTTP {status}): check the key and its rights"
    if status == 404:
        return (
            f"model '{model}' or the address was not found (HTTP 404): "
            "check --model and --base-url"
        )
    if status == 429:
        return "too many requests or no credit left (HTTP 429): wait or check the account"
    return f"the server answered with an error (HTTP {status})"