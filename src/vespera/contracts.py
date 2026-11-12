"""Data contracts: the only shapes of data that Vespera accepts.

Anything that does not match these models is rejected at the door with a
ValidationError, so the rest of the code can trust what it receives.
"""

from enum import StrEnum

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
    prompts: dict[Language, str]

    @field_validator("prompts")
    @classmethod
    def _all_languages_present(cls, value: dict[Language, str]) -> dict[Language, str]:
        missing = set(Language) - set(value)
        if missing:
            names = ", ".join(sorted(lang.value for lang in missing))
            raise ValueError(f"missing prompts for languages: {names}")
        return value


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