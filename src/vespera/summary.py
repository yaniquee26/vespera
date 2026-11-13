"""Turns raw check results into the numbers a human wants to read."""

from dataclasses import dataclass

from vespera.contracts import Case, Category, CheckResult, CheckStatus


@dataclass(frozen=True)
class CategorySummary:
    category: Category
    attempts: int
    violations: int


@dataclass(frozen=True)
class Violation:
    case_id: str
    repeat: int
    check: str
    detail: str
    excerpt: str


@dataclass(frozen=True)
class Summary:
    categories: list[CategorySummary]
    violations: list[Violation]
    language_matches: int
    language_known: int
    language_unknown: int


def summarize(cases: list[Case], results: list[CheckResult]) -> Summary:
    by_id = {case.id: case for case in cases}
    judged: dict[Category, set[tuple[str, int]]] = {}
    violated: dict[Category, set[tuple[str, int]]] = {}
    violations: list[Violation] = []
    matches = known = unknown = 0

    for result in results:
        case = by_id[result.case_id]
        key = (result.case_id, result.repeat)
        if result.status is CheckStatus.INFO:
            if result.value == "unknown":
                unknown += 1
            else:
                known += 1
                matches += result.value == case.language.value
            continue
        judged.setdefault(case.category, set()).add(key)
        if result.status is CheckStatus.FAILED:
            violated.setdefault(case.category, set()).add(key)
            violations.append(
                Violation(
                    result.case_id,
                    result.repeat,
                    result.check,
                    result.detail,
                    result.excerpt,
                )
            )

    categories = [
        CategorySummary(
            category=category,
            attempts=len(judged.get(category, ())),
            violations=len(violated.get(category, ())),
        )
        for category in Category
        if judged.get(category)
    ]
    return Summary(categories, violations, matches, known, unknown)