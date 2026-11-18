"""Data contracts for testing agents: bots that call tools, not only answer in text."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vespera.contracts import Case, Category, Language

AGENT_CHECKS = frozenset(
    {
        "forbidden_tool_call",
        "secret_not_in_tool_args",
        "expected_tool_called",
        "hidden_order_delivered",
        "tool_call_as_text",
    }
)

DEFAULT_TOOL_RESULT = "OK (simulated)"  # what a tool without a scripted answer returns
UNKNOWN_TOOL_PREFIX = "Error: unknown tool"

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

    def to_case(self) -> Case:
        """The same case in the shape the shared pipeline (checks, stats, report) understands."""
        return Case(
            id=self.id,
            scenario_id=self.scenario_id,
            language=self.language,
            category=Category(self.category.value),
            prompt=self.task,
            checks=self.checks,
            params={**self.params, "tools": [tool.name for tool in self.tools]},
        )


class ToolCall(BaseModel):
    """The agent asked to use a tool."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = ""
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)

    def describe(self) -> str:
        args = ", ".join(f"{key}={value!r}" for key, value in self.arguments.items())
        return f"{self.name}({args})"


class AgentTurn(BaseModel):
    """One answer of the model: some text, and maybe requests to use tools."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)


class TraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["assistant", "tool_result"]
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)  # only for "assistant"
    tool_name: str | None = None  # only for "tool_result"


class AgentTrace(BaseModel):
    """Everything that happened in one attempt. This is the proof for agent findings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    events: list[TraceEvent]
    stopped: Literal["answered", "max_steps"]

    def calls(self) -> list[ToolCall]:
        return [call for event in self.events for call in event.tool_calls]

    def final_reply(self) -> str:
        for event in reversed(self.events):
            if event.kind == "assistant":
                return event.text
        return ""

    def describe(self) -> str:
        lines: list[str] = []
        for event in self.events:
            if event.kind == "assistant":
                if event.text.strip():
                    lines.append(f"AGENT SAYS: {event.text.strip()}")
                lines.extend(f"AGENT CALLS: {call.describe()}" for call in event.tool_calls)
            else:
                lines.append(f"TOOL RESULT ({event.tool_name}): {event.text}")
        if self.stopped == "max_steps":
            lines.append("(stopped: too many steps)")
        return "\n".join(lines) or "(the agent did nothing)"
