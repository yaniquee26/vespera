"""Model providers: unified interface between Vespera and any LLM."""
 
from abc import ABC, abstractmethod
from itertools import cycle
 
import httpx
 
 
class BaseProvider(ABC):
    """Abstract contract that every LLM adapter must follow."""
 
    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Send system and user prompts to a model and return its text reply."""
 
 
class MockProvider(BaseProvider):
    """Deterministic fake provider for CI pipelines and unit tests."""
 
    def __init__(self, replies: list[str]) -> None:
        if not replies:
            raise ValueError("MockProvider requires at least one reply")
        self._replies = cycle(tuple(replies))
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
        self._num_predict = num_predict
        self._temperature = temperature
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
            "options": {"num_predict": self._num_predict, "temperature": self._temperature},
        }
        try:
            response = self._client.post("/api/chat", json=payload)
            response.raise_for_status()
            content = response.json()["message"]["content"]
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama request failed: {exc}") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("Ollama returned an unexpected reply") from exc
        if not isinstance(content, str):
            raise ProviderError("Ollama returned an unexpected reply")
        return content