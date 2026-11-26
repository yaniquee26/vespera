"""The decision on a checked run and its machine-readable results, without writing any file."""

from vespera.export import build_junit, build_results, mask_report, render_json, secrets_of
from vespera.gate import GateConfigError, GatePolicy, GateResult, evaluate_gate
from vespera.report import ReportData, ReportError, build_report, render_html
from vespera.services.errors import ResultError
from vespera.storage import Storage


def evaluate(
    storage: Storage, run_id: int, policy: GatePolicy | None
) -> tuple[ReportData, GateResult | None]:
    """The report data of a checked run and, if there is a policy, the gate decision."""
    try:
        data = build_report(storage, run_id)
        return data, (evaluate_gate(data, policy) if policy is not None else None)
    except (ReportError, GateConfigError) as exc:
        raise ResultError(str(exc)) from exc


def results_json(
    storage: Storage, run_id: int, data: ReportData, gate: GateResult | None, *, reveal: bool
) -> str:
    """results.json text. Secrets and long numbers are masked unless `reveal` is set."""
    payload = build_results(
        data, gate, mask_values=not reveal, secrets=secrets_of(storage.get_cases(run_id))
    )
    return render_json(payload)


def report_html(storage: Storage, run_id: int, data: ReportData, *, reveal: bool) -> str:
    """The HTML report as a file to keep or send. Masked like results.json."""
    shown = data if reveal else mask_report(data, secrets_of(storage.get_cases(run_id)))
    return render_html(shown)


def junit_xml(storage: Storage, run_id: int, *, reveal: bool) -> str:
    """JUnit XML text, one test case per case. Masked like results.json."""
    cases = storage.get_cases(run_id)
    return build_junit(
        run_id,
        storage.get_snapshot(run_id),
        cases,
        storage.get_attempts(run_id),
        storage.get_check_results(run_id),
        mask_values=not reveal,
        secrets=secrets_of(cases),
    )
