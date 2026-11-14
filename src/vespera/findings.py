"""Findings: conclusions that are always tied to the raw proof behind them."""

from vespera.contracts import Case, CheckResult, CheckStatus, Evidence, Finding
from vespera.stats import attempt_verdicts, rates_by_category


def evidence_attempt_id(case_id: str, repeat: int) -> str:
    return f"{case_id}#{repeat}"


def split_attempt_id(attempt_id: str) -> tuple[str, int]:
    case_id, _, repeat = attempt_id.rpartition("#")
    return case_id, int(repeat)


def build_findings(cases: list[Case], results: list[CheckResult]) -> list[Finding]:
    """One finding per category that had violations. No violations, no finding.

    A category without violations is not proof of safety, so it is reported
    separately as an upper bound, never as a 'finding'.
    """
    by_id = {case.id: case for case in cases}
    rates = rates_by_category(attempt_verdicts(cases, results))
    evidence: dict[str, list[Evidence]] = {}
    for result in sorted(results, key=lambda r: (r.case_id, r.repeat, r.check)):
        if result.status is not CheckStatus.FAILED:
            continue
        category = by_id[result.case_id].category
        evidence.setdefault(category.value, []).append(
            Evidence(
                attempt_id=evidence_attempt_id(result.case_id, result.repeat),
                check=result.check,
                excerpt=result.excerpt or "(empty reply)",
            )
        )
    findings: list[Finding] = []
    for category, rate in rates.items():
        proof = evidence.get(category.value)
        if not proof:
            continue
        statement = (
            f"{category.value}: {rate.k} of {rate.n} attempts violated the rule "
            f"({rate.p:.0%}, 95% CI {rate.low:.0%} to {rate.high:.0%})"
        )
        findings.append(Finding(statement=statement, evidence=proof))
    return findings