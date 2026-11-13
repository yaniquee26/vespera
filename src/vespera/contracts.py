"""Data contracts: the only shapes of data that Vespera accepts.

Anything that does not match these models is rejected at the door with a
ValidationError, so the rest of the code can trust what it receives.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Language(StrEnum):
    EN = "en"
    RU = "ru"
    RO = "ro"


class Category(StrEnum):
    DIRECT_INJECTION = "direct_injection"
    INDIRECT_INJECTION = "indirect_injection"
    SYSTEM_PROMPT_LEAK = "system_prompt_leak"
    PII_LEAK = "pii_leak"
    JAILBREAK = "jailbreak"
    BENIGN = "benign"


class Scenario(BaseModel):
    """One test idea, written once in every supported language."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    category: Category
    origin: str = Field(min_length=1)
    checks: list[str] = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    prompts: dict[Language, str]

    @field_validator("prompts")
    @classmethod
    def _all_languages_present(cls, value: dict[Language, str]) -> dict[Language, str]:
        missing = set(Language) - set(value)
        if missing:
            names = ", ".join(sorted(lang.value for lang in missing))
            raise ValueError(f"missing prompts for languages: {names}")
        return value


class Case(BaseModel):
    """One scenario in one language: the smallest thing we send to a model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)  # "{scenario_id}:{language}", e.g. "inj-001:ru"
    scenario_id: str = Field(min_length=1)
    language: Language
    category: Category
    prompt: str = Field(min_length=1)
    checks: list[str] = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class Snapshot(BaseModel):
    """Everything needed to repeat an experiment: model, settings, input fingerprints."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vespera_version: str
    python_version: str
    model: str
    temperature: float
    num_predict: int
    repeats: int = Field(ge=1)
    system_prompt_sha256: str
    scenarios_sha256: str
    created_at: str


class Attempt(BaseModel):
    """One question asked to the model once, and what came back."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    repeat: int = Field(ge=1)
    reply: str
    error: str | None = None
    latency_ms: int = Field(ge=0)
    created_at: str


class Evidence(BaseModel):
    """A piece of raw proof: a fragment of a real model answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str = Field(min_length=1)
    check: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)


class Finding(BaseModel):
    """A conclusion. It cannot exist without at least one piece of evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)