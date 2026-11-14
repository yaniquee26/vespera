"""Statistics: how far can a count of violations be trusted?"""

from collections import defaultdict
from dataclasses import dataclass
from math import sqrt

from vespera.contracts import Case, Category, CheckResult, CheckStatus, Language

Z95 = 1.96


def wilson_interval(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """95% Wilson score interval for k successes out of n tries.

    Works for small n and for 0 or n successes, where the simple formula fails.
    With no tries at all nothing is known, so the interval is the whole 0..1 range.
    """
    if n < 0 or k < 0 or k > n:
        raise ValueError(f"need 0 <= k <= n, got k={k}, n={n}")
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    # Mathematically the interval always contains p; rounding can break that by 1e-16.
    return (max(0.0, min(centre - half, p)), min(1.0, max(centre + half, p)))


@dataclass(frozen=True)
class Rate:
    """k violations out of n judged attempts, with its 95% interval."""

    k: int
    n: int
    low: float
    high: float

    @property
    def p(self) -> float:
        return self.k / self.n if self.n else 0.0


def make_rate(k: int, n: int) -> Rate:
    low, high = wilson_interval(k, n)
    return Rate(k=k, n=n, low=low, high=high)


def intervals_overlap(a: Rate, b: Rate) -> bool:
    return a.low <= b.high and b.low <= a.high


@dataclass(frozen=True)
class AttemptVerdict:
    """One judged attempt: did it violate at least one rule?"""

    case_id: str
    repeat: int
    category: Category
    language: Language
    violated: bool


def attempt_verdicts(cases: list[Case], results: list[CheckResult]) -> list[AttemptVerdict]:
    """Collapse check results to one verdict per attempt. Info checks do not judge."""
    by_id = {case.id: case for case in cases}
    violated: dict[tuple[str, int], bool] = {}
    for result in results:
        if result.status is CheckStatus.INFO:
            continue
        key = (result.case_id, result.repeat)
        violated[key] = violated.get(key, False) or result.status is CheckStatus.FAILED
    return [
        AttemptVerdict(
            case_id=case_id,
            repeat=repeat,
            category=by_id[case_id].category,
            language=by_id[case_id].language,
            violated=is_violated,
        )
        for (case_id, repeat), is_violated in sorted(violated.items())
    ]


def rate_of(verdicts: list[AttemptVerdict]) -> Rate:
    return make_rate(sum(v.violated for v in verdicts), len(verdicts))


def rates_by_category(verdicts: list[AttemptVerdict]) -> dict[Category, Rate]:
    grouped: dict[Category, list[AttemptVerdict]] = defaultdict(list)
    for verdict in verdicts:
        grouped[verdict.category].append(verdict)
    return {category: rate_of(grouped[category]) for category in Category if grouped[category]}


def rates_by_language(verdicts: list[AttemptVerdict], category: Category) -> dict[Language, Rate]:
    grouped: dict[Language, list[AttemptVerdict]] = defaultdict(list)
    for verdict in verdicts:
        if verdict.category is category:
            grouped[verdict.language].append(verdict)
    return {language: rate_of(grouped[language]) for language in Language if grouped[language]}


def language_gap_is_conclusive(by_language: dict[Language, Rate]) -> bool:
    """True only if two languages have intervals that do not even touch."""
    rates = list(by_language.values())
    return any(
        not intervals_overlap(a, b) for i, a in enumerate(rates) for b in rates[i + 1 :]
    )


@dataclass(frozen=True)
class Stability:
    judged_cases: int  # cases asked at least twice
    flaky_cases: int  # same case, same model, different verdict between repeats


def stability(verdicts: list[AttemptVerdict]) -> dict[Category, Stability]:
    per_case: dict[str, list[AttemptVerdict]] = defaultdict(list)
    for verdict in verdicts:
        per_case[verdict.case_id].append(verdict)
    judged: dict[Category, int] = defaultdict(int)
    flaky: dict[Category, int] = defaultdict(int)
    for attempts in per_case.values():
        if len(attempts) < 2:
            continue
        category = attempts[0].category
        judged[category] += 1
        outcomes = {a.violated for a in attempts}
        if len(outcomes) > 1:
            flaky[category] += 1
    return {c: Stability(judged[c], flaky[c]) for c in Category if judged[c]}