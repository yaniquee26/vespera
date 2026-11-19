"""Risk labels: which known risk a category belongs to, and how serious it is.

The OWASP ids follow the OWASP Top 10 for LLM Applications, 2026 edition. The severity is a
convention, a sensible default that a team can disagree with. It is not a measurement.
"""

from dataclasses import dataclass
from enum import IntEnum

from vespera.contracts import Category


class Severity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @property
    def label(self) -> str:
        return self.name.capitalize()


OWASP_EDITION = "2026"

# Ids whose meaning changed between the 2025 and 2026 lists, with the 2026 id to use instead.
RENUMBERED_2025 = {
    "LLM06": ("excessive agency", "LLM03"),
    "LLM07": ("system prompt leakage", "LLM08"),
}


@dataclass(frozen=True)
class Risk:
    owasp: str | None  # None: a quality problem, not a security risk (a needless refusal)
    title: str
    severity: Severity


RISKS: dict[Category, Risk] = {
    Category.DIRECT_INJECTION: Risk("LLM01", "Prompt injection", Severity.HIGH),
    Category.INDIRECT_INJECTION: Risk("LLM01", "Prompt injection", Severity.HIGH),
    Category.JAILBREAK: Risk("LLM01", "Prompt injection", Severity.MEDIUM),
    Category.SYSTEM_PROMPT_LEAK: Risk("LLM08", "Hidden context exposure", Severity.HIGH),
    Category.PII_LEAK: Risk("LLM02", "Sensitive information disclosure", Severity.CRITICAL),
    Category.TOOL_OUTPUT_INJECTION: Risk("LLM01", "Prompt injection", Severity.CRITICAL),
    Category.DATA_EXFILTRATION: Risk(
        "LLM02", "Sensitive information disclosure", Severity.CRITICAL
    ),
    Category.EXCESSIVE_AGENCY: Risk("LLM03", "Excessive agency", Severity.HIGH),
    Category.BENIGN: Risk(None, "Needless refusal", Severity.MEDIUM),
    Category.BENIGN_TASK: Risk(None, "Harmless task not done", Severity.MEDIUM),
}


def risk_of(category: Category) -> Risk:
    return RISKS[category]


def matches_selector(category: Category, selector: str) -> bool:
    """A selector is an OWASP id ('LLM02', any case) or a category name ('pii_leak')."""
    wanted = selector.strip()
    risk = RISKS[category]
    return wanted.lower() == category.value or (
        risk.owasp is not None and wanted.upper() == risk.owasp
    )
