"""The report: one self-contained HTML page for one run.

Every number comes from stored attempts and verdicts, and every finding links
to the raw reply that proves it.
"""

from dataclasses import dataclass

from jinja2 import Environment, select_autoescape

from vespera.contracts import Category, CheckStatus, Language, Snapshot
from vespera.findings import build_findings, split_attempt_id
from vespera.report_template import TEMPLATE
from vespera.stats import (
    Rate,
    Stability,
    attempt_verdicts,
    language_gap_is_conclusive,
    make_rate,
    rates_by_category,
    rates_by_language,
    stability,
)
from vespera.storage import Storage

LABELS = {
    Category.DIRECT_INJECTION: "Direct prompt injection",
    Category.INDIRECT_INJECTION: "Indirect prompt injection",
    Category.SYSTEM_PROMPT_LEAK: "System prompt leak",
    Category.PII_LEAK: "Personal data leak",
    Category.JAILBREAK: "Jailbreak (role-play)",
    Category.BENIGN: "Needless refusal (harmless question)",
}


class ReportError(Exception):
    """The report cannot be built, for example because checks were not run yet."""


@dataclass(frozen=True)
class CategoryRow:
    key: str
    label: str
    harmless: bool  # True for the benign category: a violation means a needless refusal
    rate: Rate
    stability: Stability | None
    by_language: dict[str, Rate]
    language_gap: bool | None  # None when fewer than two languages were judged


@dataclass(frozen=True)
class EvidenceView:
    attempt_id: str
    language: str
    check: str
    detail: str
    excerpt: str
    prompt: str
    reply: str


@dataclass(frozen=True)
class FindingView:
    key: str
    label: str
    statement: str
    evidence: list[EvidenceView]


@dataclass(frozen=True)
class ReportData:
    run_id: int
    status: str
    snapshot: Snapshot
    attack_rate: Rate | None
    rows: list[CategoryRow]
    findings: list[FindingView]
    clean: list[tuple[str, Rate]]
    technical_errors: int
    flaky_cases: int
    repeated_cases: int
    language_matches: int
    language_known: int
    language_unknown: int


def build_report(storage: Storage, run_id: int) -> ReportData:
    if not storage.run_exists(run_id):
        raise ReportError(f"no run with id {run_id}")
    results = storage.get_check_results(run_id)
    if not results:
        raise ReportError("this run has no verdicts yet: run `vespera check` first")

    cases = storage.get_cases(run_id)
    attempts = storage.get_attempts(run_id)
    case_by_id = {case.id: case for case in cases}
    attempt_by_key = {(a.case_id, a.repeat): a for a in attempts}
    detail_by_key = {(r.case_id, r.repeat, r.check): r.detail for r in results}

    verdicts = attempt_verdicts(cases, results)
    category_rates = rates_by_category(verdicts)
    stable = stability(verdicts)

    rows: list[CategoryRow] = []
    for category, rate in category_rates.items():
        languages = rates_by_language(verdicts, category)
        rows.append(
            CategoryRow(
                key=category.value,
                label=LABELS[category],
                harmless=category is Category.BENIGN,
                rate=rate,
                stability=stable.get(category),
                by_language={lang.value: r for lang, r in languages.items()},
                language_gap=language_gap_is_conclusive(languages) if len(languages) > 1 else None,
            )
        )

    attack = [v for v in verdicts if v.category is not Category.BENIGN]
    attack_rate = make_rate(sum(v.violated for v in attack), len(attack)) if attack else None

    findings: list[FindingView] = []
    for finding in build_findings(cases, results):
        key, _, statement = finding.statement.partition(": ")
        category = Category(key)
        views: list[EvidenceView] = []
        for item in finding.evidence:
            case_id, repeat = split_attempt_id(item.attempt_id)
            case = case_by_id[case_id]
            attempt = attempt_by_key[(case_id, repeat)]
            views.append(
                EvidenceView(
                    attempt_id=item.attempt_id,
                    language=case.language.value.upper(),
                    check=item.check,
                    detail=detail_by_key.get((case_id, repeat, item.check), ""),
                    excerpt=item.excerpt,
                    prompt=case.prompt,
                    reply=attempt.reply or "(empty reply)",
                )
            )
        findings.append(
            FindingView(key=key, label=LABELS[category], statement=statement, evidence=views)
        )

    finding_keys = {f.key for f in findings}
    clean = [(row.label, row.rate) for row in rows if row.key not in finding_keys]

    matches = known = unknown = 0
    for result in results:
        if result.status is CheckStatus.INFO:
            if result.value == "unknown":
                unknown += 1
            else:
                known += 1
                matches += result.value == case_by_id[result.case_id].language.value

    return ReportData(
        run_id=run_id,
        status=storage.get_status(run_id),
        snapshot=storage.get_snapshot(run_id),
        attack_rate=attack_rate,
        rows=rows,
        findings=findings,
        clean=clean,
        technical_errors=sum(1 for a in attempts if a.error is not None),
        flaky_cases=sum(s.flaky_cases for s in stable.values()),
        repeated_cases=sum(s.judged_cases for s in stable.values()),
        language_matches=matches,
        language_known=known,
        language_unknown=unknown,
    )


def _percent(value: float) -> str:
    return f"{round(value * 100)}%"


def _width(value: float) -> str:
    return f"{value * 100:.1f}"


def render_html(data: ReportData) -> str:
    environment = Environment(autoescape=select_autoescape(default=True))
    environment.filters["pct"] = _percent
    environment.filters["w"] = _width
    template = environment.from_string(TEMPLATE)
    return template.render(d=data, languages=[lang.value for lang in Language])