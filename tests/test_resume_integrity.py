"""A run is continued only with the very cases it was started with, and a refusal changes nothing.

The cases of a run are saved once, when it starts. Attempts are saved per case id. If a resume
asked other cases, attempts would point to cases the run does not have and the run could no
longer be checked, so a resume compares the ordered list of case ids before anything is written.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vespera import cli
from vespera.agent_contracts import AgentTurn
from vespera.bundled import read_bundled
from vespera.contracts import Case
from vespera.providers import MockAgentProvider, MockProvider
from vespera.scenarios import expand_cases, load_scenarios
from vespera.services.errors import ResumeMismatch
from vespera.services.runs import begin_run, execute_agent_run, execute_text_run
from vespera.storage import Storage

runner = CliRunner()
FILES = ("scenarios_v1.yaml", "system_prompt.txt", "agent_scenarios_v1.yaml",
         "agent_system_prompt.txt")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    for name in FILES:
        (tmp_path / name).write_text(read_bundled(name), encoding="utf-8")
    return tmp_path


def database_dump(db: Path) -> list[str]:
    """Every table and row of the database as SQL text, to prove that nothing changed."""
    with closing(sqlite3.connect(db)) as connection:
        return list(connection.iterdump())


def text_run(project: Path, limit: int | None, resume: int | None = None) -> int:
    run_id, _ = execute_text_run(
        lambda: MockProvider(["Here is the answer."]),
        project / "scenarios_v1.yaml",
        project / "system_prompt.txt",
        1,
        limit,
        project / "v.db",
        resume,
    )
    return run_id


def all_cases(project: Path) -> list[Case]:
    return expand_cases(load_scenarios(project / "scenarios_v1.yaml"))


def refused_resume(project: Path, run_id: int, cases: list[Case]) -> ResumeMismatch:
    """Resume the run with other cases (same snapshot) and return the refusal."""
    db = project / "v.db"
    with Storage(db) as storage:
        snapshot = storage.get_snapshot(run_id)
        with pytest.raises(ResumeMismatch) as refusal:
            begin_run(storage, db, snapshot, cases, run_id)
    return refusal.value


# ---------------------------------------------------------------- refused


def test_a_different_limit_is_refused_and_the_database_is_unchanged(project: Path) -> None:
    run_id = text_run(project, limit=2)
    before = database_dump(project / "v.db")
    with pytest.raises(ResumeMismatch) as refusal:
        text_run(project, limit=5, resume=run_id)
    assert str(refusal.value) == "the cases differ from the original run (2 saved, 5 asked now)"
    assert refusal.value.tip is not None and "--limit" in refusal.value.tip
    assert "2 cases" in refusal.value.tip
    assert database_dump(project / "v.db") == before


def test_a_smaller_limit_is_refused_too(project: Path) -> None:
    run_id = text_run(project, limit=4)
    before = database_dump(project / "v.db")
    with pytest.raises(ResumeMismatch, match=r"\(4 saved, 2 asked now\)"):
        text_run(project, limit=2, resume=run_id)
    assert database_dump(project / "v.db") == before


def test_other_cases_of_the_same_number_are_refused(project: Path) -> None:
    cases = all_cases(project)
    run_id = text_run(project, limit=3)
    before = database_dump(project / "v.db")
    other = [cases[0], cases[1], cases[5]]  # same size, one different case
    refusal = refused_resume(project, run_id, other)
    assert "(3 saved, 3 asked now)" in str(refusal)
    assert database_dump(project / "v.db") == before


def test_the_same_cases_in_another_order_are_refused(project: Path) -> None:
    cases = all_cases(project)
    run_id = text_run(project, limit=3)
    before = database_dump(project / "v.db")
    refusal = refused_resume(project, run_id, [cases[1], cases[0], cases[2]])
    assert "the same cases in another order" in str(refusal)
    assert database_dump(project / "v.db") == before


def test_an_agent_run_with_another_limit_is_refused(project: Path) -> None:
    def agent_run(limit: int, resume: int | None = None) -> int:
        run_id, _ = execute_agent_run(
            lambda: MockAgentProvider([AgentTurn(text="Done.")]),
            project / "agent_scenarios_v1.yaml",
            project / "agent_system_prompt.txt",
            1,
            limit,
            3,
            project / "v.db",
            resume,
        )
        return run_id

    run_id = agent_run(limit=1)
    before = database_dump(project / "v.db")
    with pytest.raises(ResumeMismatch, match=r"\(1 saved, 3 asked now\)"):
        agent_run(limit=3, resume=run_id)
    assert database_dump(project / "v.db") == before


# ---------------------------------------------------------------- allowed


def test_a_compatible_resume_continues_the_run(project: Path) -> None:
    run_id = text_run(project, limit=3)
    assert text_run(project, limit=3, resume=run_id) == run_id
    with Storage(project / "v.db") as storage:
        assert [case.id for case in storage.get_cases(run_id)] == [
            case.id for case in all_cases(project)[:3]
        ]
        assert len(storage.get_attempts(run_id)) == 3


def test_another_limit_that_gives_the_same_cases_is_allowed(project: Path) -> None:
    total = len(all_cases(project))
    run_id = text_run(project, limit=None)  # every case
    assert text_run(project, limit=total + 100, resume=run_id) == run_id  # still every case


def test_a_run_saved_before_this_rule_can_still_be_resumed(project: Path) -> None:
    """A run written directly with the storage calls of 1.0.0 (cases saved once at the start)."""
    db = project / "v.db"
    first = text_run(project, limit=2)
    with Storage(db) as storage:
        snapshot = storage.get_snapshot(first)
        cases = all_cases(project)[:2]
        old = storage.create_run(snapshot)
        storage.save_cases(old, cases)
        assert begin_run(storage, db, snapshot, cases, old) == (old, True)


# ---------------------------------------------------------------- the command line


def test_the_command_line_refuses_and_then_resumes_with_the_original_limit() -> None:
    assert runner.invoke(cli.app, ["init", "--no-interactive"]).exit_code == 0
    base = ["test", "mock", "--no-config", "--repeats", "1"]
    assert runner.invoke(cli.app, [*base, "--limit", "2"]).exit_code == 0
    before = database_dump(Path("runs/vespera.db"))

    refused = runner.invoke(cli.app, [*base, "--limit", "5", "--resume", "1"])
    assert refused.exit_code == 1
    assert "[FAIL] the cases differ from the original run (2 saved, 5 asked now)" in refused.output
    assert "Tip: use the same --limit as the original run (2 cases)" in refused.output
    assert "Resuming run 1" not in refused.output
    assert database_dump(Path("runs/vespera.db")) == before

    resumed = runner.invoke(cli.app, [*base, "--limit", "2", "--resume", "1"])
    assert resumed.exit_code == 0, resumed.output
    assert "Resuming run 1" in resumed.output and "Checked run 1" in resumed.output
