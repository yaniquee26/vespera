"""Compare two runs of two models on the same questions.

A difference is called conclusive only when the 95% intervals of the two models do not
overlap. That is a cautious rule: it may miss small real differences, but it does not
announce a winner on noise.
"""

from collections import defaultdict
from dataclasses import dataclass

from jinja2 import Environment, select_autoescape

from vespera.compare_template import COMPARE_TEMPLATE
from vespera.contracts import Category, Snapshot
from vespera.report import LABELS, ReportError, _percent, _width
from vespera.stats import (
    AttemptVerdict,
    Rate,
    attempt_verdicts,
    intervals_overlap,
    make_rate,
    rate_of,
    rates_by_category,
)
from vespera.storage import Storage


@dataclass(frozen=True)
class CompareRow:
    label: str
    harmless: bool
    a: Rate
    b: Rate
    conclusive: bool
    sentence: str  # plain-language reading of this row


@dataclass(frozen=True)
class ScenarioRow:
    scenario_id: str
    category_label: str
    a: Rate
    b: Rate


@dataclass(frozen=True)
class CompareData:
    run_a: int
    run_b: int
    snapshot_a: Snapshot
    snapshot_b: Snapshot
    attack_a: Rate
    attack_b: Rate
    attack_conclusive: bool
    attack_sentence: str
    rows: list[CompareRow]
    scenarios: list[ScenarioRow]
    notes: list[str]


def _sentence(name_a: str, name_b: str, a: Rate, b: Rate) -> tuple[bool, str]:
    if intervals_overlap(a, b):
        return (
            False,
            "Not conclusive: the intervals overlap, so the difference may be chance."
            if a.p != b.p
            else "No difference seen. With this much data, small differences stay hidden.",
        )
    worse, better = (name_a, name_b) if a.p > b.p else (name_b, name_a)
    return True, (
        f"Conclusive: {worse} violated more often than {better} (intervals do not overlap)."
    )


def _judged(storage: Storage, run_id: int) -> list[AttemptVerdict]:
    results = storage.get_check_results(run_id)
    if not results:
        raise ReportError(f"run {run_id} has no verdicts yet: run `vespera check {run_id}` first")
    return attempt_verdicts(storage.get_cases(run_id), results)


def build_comparison(storage: Storage, run_a: int, run_b: int) -> CompareData:
    if run_a == run_b:
        raise ReportError("choose two different runs to compare")
    for run_id in (run_a, run_b):
        if not storage.run_exists(run_id):
            raise ReportError(f"no run with id {run_id}")
    snap_a, snap_b = storage.get_snapshot(run_a), storage.get_snapshot(run_b)
    if snap_a.system_prompt_sha256 != snap_b.system_prompt_sha256:
        raise ReportError("the runs used different system prompts: not a fair comparison")
    if snap_a.scenarios_sha256 != snap_b.scenarios_sha256:
        raise ReportError("the runs used different scenario files: not a fair comparison")

    verdicts_a, verdicts_b = _judged(storage, run_a), _judged(storage, run_b)
    name_a, name_b = snap_a.model, snap_b.model

    notes: list[str] = []
    if name_a == name_b:
        notes.append(f"Both runs use the same model ({name_a}): this shows run-to-run noise.")
    if snap_a.temperature != snap_b.temperature or snap_a.num_predict != snap_b.num_predict:
        notes.append("Temperature or reply length differ between the runs.")
    if snap_a.repeats != snap_b.repeats:
        notes.append(f"Repeats differ: {snap_a.repeats} vs {snap_b.repeats}.")

    rates_a, rates_b = rates_by_category(verdicts_a), rates_by_category(verdicts_b)
    rows: list[CompareRow] = []
    for category in Category:
        if category not in rates_a or category not in rates_b:
            continue
        conclusive, sentence = _sentence(name_a, name_b, rates_a[category], rates_b[category])
        rows.append(
            CompareRow(
                label=LABELS[category],
                harmless=category is Category.BENIGN,
                a=rates_a[category],
                b=rates_b[category],
                conclusive=conclusive,
                sentence=sentence,
            )
        )

    def attack_rate(verdicts: list[AttemptVerdict]) -> Rate:
        attack = [v for v in verdicts if v.category is not Category.BENIGN]
        return rate_of(attack) if attack else make_rate(0, 0)

    attack_a, attack_b = attack_rate(verdicts_a), attack_rate(verdicts_b)
    attack_conclusive, attack_sentence = _sentence(name_a, name_b, attack_a, attack_b)

    cases = {case.id: case for case in storage.get_cases(run_a)}

    def per_scenario(verdicts: list[AttemptVerdict]) -> dict[str, list[AttemptVerdict]]:
        grouped: dict[str, list[AttemptVerdict]] = defaultdict(list)
        for verdict in verdicts:
            grouped[cases[verdict.case_id].scenario_id].append(verdict)
        return grouped

    grouped_a, grouped_b = per_scenario(verdicts_a), per_scenario(verdicts_b)
    scenario_rows = [
        ScenarioRow(
            scenario_id=scenario_id,
            category_label=LABELS[grouped_a[scenario_id][0].category],
            a=rate_of(grouped_a[scenario_id]),
            b=rate_of(grouped_b[scenario_id]),
        )
        for scenario_id in sorted(grouped_a)
        if scenario_id in grouped_b
    ]

    return CompareData(
        run_a=run_a,
        run_b=run_b,
        snapshot_a=snap_a,
        snapshot_b=snap_b,
        attack_a=attack_a,
        attack_b=attack_b,
        attack_conclusive=attack_conclusive,
        attack_sentence=attack_sentence,
        rows=rows,
        scenarios=scenario_rows,
        notes=notes,
    )


def render_comparison_html(data: CompareData) -> str:
    environment = Environment(autoescape=select_autoescape(default=True))
    environment.filters["pct"] = _percent
    environment.filters["w"] = _width
    return environment.from_string(COMPARE_TEMPLATE).render(d=data)