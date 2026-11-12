"""Model providers: unified interface between Vespera and any LLM."""
 
from abc import ABC, abstractmethod
from itertools import cycle
 
 
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
 