from pathlib import Path

import pytest
from typer.testing import CliRunner

from test_stats_report import ScriptedProvider, realistic_script
from vespera.checks import run_checks
from vespera.cli import app
from vespera.compare import build_comparison, render_comparison_html
from vespera.report import ReportError
from vespera.runner import run_experiment
from vespera.scenarios import expand_cases, load_scenarios
from vespera.snapshot import build_snapshot
from vespera.storage import Storage

REPO = Path(__file__).resolve().parents[1]
SYSTEM = (REPO / "system_prompt.txt").read_text(encoding="utf-8")
SCENARIOS = (REPO / "scenarios_v1.yaml").read_text(encoding="utf-8")


def store_run(
    storage: Storage,
    model: str,
    script: dict[tuple[str, int], str],
    system: str = SYSTEM,
    scenarios_text: str = SCENARIOS,
    check: bool = True,
    temperature: float = 0.7,
) -> int:
    cases = expand_cases(load_scenarios(REPO / "scenarios_v1.yaml"))
    snapshot = build_snapshot(
        model=model,
        temperature=temperature,
        num_predict=200,
        repeats=3,
        system_prompt=system,
        scenarios_text=scenarios_text,
    )
    run_id = storage.create_run(snapshot)
    storage.save_cases(run_id, cases)
    provider = ScriptedProvider(cases, script)
    run_experiment(provider, cases, SYSTEM, storage, run_id, repeats=3)
    if check:
        storage.save_check_results(run_id, run_checks(cases, storage.get_attempts(run_id), SYSTEM))
    return run_id


@pytest.fixture
def storage(tmp_path: Path):  # type: ignore[no-untyped-def]
    db = Storage(tmp_path / "c.db")
    yield db
    db.close()


def test_the_gullible_model_is_conclusively_worse_than_a_firm_one(storage: Storage) -> None:
    gullible = store_run(storage, "gullible", realistic_script())
    firm = store_run(storage, "firm", {})  # refuses everything except the benign question
    data = build_comparison(storage, gullible, firm)
    row = next(r for r in data.rows if r.label == "Direct prompt injection")
    assert (row.a.k, row.a.n) == (18, 18)
    assert row.b.k == 0
    assert row.conclusive
    assert "gullible violated more often than firm" in row.sentence
    assert data.attack_conclusive
    assert data.attack_a.k > data.attack_b.k


def test_identical_behaviour_is_not_conclusive_and_says_so(storage: Storage) -> None:
    first = store_run(storage, "same", realistic_script())
    second = store_run(storage, "same", realistic_script())
    data = build_comparison(storage, first, second)
    assert not any(r.conclusive for r in data.rows)
    assert not data.attack_conclusive
    assert any("same model" in n for n in data.notes)


def test_per_scenario_counts_are_pooled_over_languages_and_repeats(storage: Storage) -> None:
    gullible = store_run(storage, "gullible", realistic_script())
    firm = store_run(storage, "firm", {})
    data = build_comparison(storage, gullible, firm)
    inj1 = next(s for s in data.scenarios if s.scenario_id == "inj-001")
    assert (inj1.a.k, inj1.a.n) == (9, 9)  # 3 languages x 3 repeats
    assert inj1.b.k == 0


def test_different_system_prompt_or_scenarios_is_refused(storage: Storage) -> None:
    base = store_run(storage, "a", realistic_script())
    other_prompt = store_run(storage, "b", {}, system=SYSTEM + " extra")
    other_scenarios = store_run(storage, "c", {}, scenarios_text="changed")
    with pytest.raises(ReportError, match="system prompts"):
        build_comparison(storage, base, other_prompt)
    with pytest.raises(ReportError, match="scenario files"):
        build_comparison(storage, base, other_scenarios)


def test_needs_two_existing_checked_runs(storage: Storage) -> None:
    checked = store_run(storage, "a", realistic_script())
    unchecked = store_run(storage, "b", {}, check=False)
    with pytest.raises(ReportError, match="different runs"):
        build_comparison(storage, checked, checked)
    with pytest.raises(ReportError, match="no run with id 99"):
        build_comparison(storage, checked, 99)
    with pytest.raises(ReportError, match="vespera check"):
        build_comparison(storage, checked, unchecked)


def test_setting_differences_are_flagged(storage: Storage) -> None:
    first = store_run(storage, "a", realistic_script())
    second = store_run(storage, "b", {}, temperature=0.2)
    data = build_comparison(storage, first, second)
    assert any("Temperature" in n for n in data.notes)


def test_html_shows_both_models_and_escapes_names(storage: Storage) -> None:
    first = store_run(storage, "<b>evil</b>", realistic_script())
    second = store_run(storage, "firm", {})
    html = render_comparison_html(build_comparison(storage, first, second))
    assert "&lt;b&gt;evil&lt;/b&gt;" in html
    assert "<b>evil</b>" not in html
    assert "Conclusive" in html
    assert "inj-001" in html


def test_compare_command(tmp_path: Path) -> None:
    db = tmp_path / "c.db"
    runner = CliRunner()
    base = [
        "--db",
        str(db),
        "--scenarios",
        str(REPO / "scenarios_v1.yaml"),
        "--system-prompt",
        str(REPO / "system_prompt.txt"),
    ]
    for _ in range(2):
        assert runner.invoke(app, ["run", "--mock", "--repeats", "2", *base]).exit_code == 0
        check_args = ["check", "--db", str(db), "--system-prompt", str(REPO / "system_prompt.txt")]
        assert runner.invoke(app, check_args).exit_code == 0
    out = tmp_path / "cmp.html"
    result = runner.invoke(app, ["compare", "1", "2", "--db", str(db), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "Vespera comparison" in out.read_text(encoding="utf-8")
    default = runner.invoke(app, ["compare", "1", "2", "--db", str(db)])
    assert (tmp_path / "compare-run-1-vs-2.html").exists(), default.output
    failed = runner.invoke(app, ["compare", "1", "1", "--db", str(db)])
    assert failed.exit_code == 1
    assert "[FAIL]" in failed.output