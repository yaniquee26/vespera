"""Data contracts for testing agents: bots that call tools, not only answer in text."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vespera.contracts import Language

AGENT_CHECKS = frozenset({"forbidden_tool_call", "secret_not_in_tool_args", "expected_tool_called"})

TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]*$"


class AgentCategory(StrEnum):
    TOOL_OUTPUT_INJECTION = "tool_output_injection"  # a hidden order inside data a tool returns
    DATA_EXFILTRATION = "data_exfiltration"  # secret or personal data leaves through a tool
    EXCESSIVE_AGENCY = "excessive_agency"  # the agent acts where it should not
    BENIGN_TASK = "benign_task"  # a harmless task that must still get done


class ToolSpec(BaseModel):
    """A tool the agent may call. Tools in tests are harmless stand-ins."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str = Field(min_length=1)
    parameters: dict[str, str] = Field(default_factory=dict)  # parameter name -> meaning


def _all_languages(value: dict[Language, str], what: str) -> dict[Language, str]:
    missing = set(Language) - set(value)
    if missing:
        names = ", ".join(sorted(lang.value for lang in missing))
        raise ValueError(f"missing {what} for languages: {names}")
    if any(not text.strip() for text in value.values()):
        raise ValueError(f"empty {what}")
    return value


class AgentScenario(BaseModel):
    """One agent test idea: a task, the tools, what tools will answer, the rules to check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    category: AgentCategory
    origin: str = Field(min_length=1)
    tools: list[ToolSpec] = Field(min_length=1)
    task: dict[Language, str]
    tool_results: dict[str, dict[Language, str]] = Field(default_factory=dict)
    checks: list[str] = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task")
    @classmethod
    def _task_in_every_language(cls, value: dict[Language, str]) -> dict[Language, str]:
        return _all_languages(value, "task")

    @field_validator("tool_results")
    @classmethod
    def _results_in_every_language(
        cls, value: dict[str, dict[Language, str]]
    ) -> dict[str, dict[Language, str]]:
        for tool, texts in value.items():
            _all_languages(texts, f"result of {tool}")
        return value


class AgentCase(BaseModel):
    """One agent scenario in one language, with tool answers already picked for that language."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)  # "{scenario_id}:{language}"
    scenario_id: str = Field(min_length=1)
    language: Language
    category: AgentCategory
    task: str = Field(min_length=1)
    tools: list[ToolSpec] = Field(min_length=1)
    tool_results: dict[str, str] = Field(default_factory=dict)
    checks: list[str] = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)