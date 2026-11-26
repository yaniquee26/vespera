"""Runs in the service layer, the storage modes and the workspace folder."""

import threading
from pathlib import Path

import pytest

from vespera.agent_contracts import AgentTurn
from vespera.bundled import read_bundled
from vespera.contracts import Language
from vespera.providers import MockAgentProvider, MockProvider, ProviderError
from vespera.scenarios import load_scenarios
from vespera.services.errors import (
    CheckError,
    InputFileError,
    ResumeMismatch,
    RunNotFound,
    RunStopped,
)
from vespera.services.runs import (
    AGENT_FILES_TIP,
    check_saved_run,
    choose_run,
    execute_agent_run,
    execute_text_run,
    list_runs,
    read_inputs,
)
from vespera.storage import Storage
from vespera.workspace import ENV_VAR, human_size, resolve_workspace

FILES = (
    "scenarios_v1.yaml",
    "system_prompt.txt",
    "agent_scenarios_v1.yaml",
    "agent_system_prompt.txt",
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    for name in FILES:
        (tmp_path / name).write_text(read_bundled(name), encoding="utf-8")
    return tmp_path


class Broken(MockProvider):
    """A model that never answers, as when a server keeps returning errors."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise ProviderError("server error")


def text_run(project: Path, **kwargs: object) -> int:
    started: list[tuple[int, bool, int, int]] = []
    progress: list[tuple[int, int]] = []
    run_id, summary = execute_text_run(
        lambda: MockProvider(["I cannot help with that."]),
        project / "scenarios_v1.yaml",
        project / "system_prompt.txt",
        int(kwargs.get("repeats", 1)),  # type: ignore[call-overload]
        kwargs.get("limit", 3),  # type: ignore[arg-type]
        project / "runs" / "v.db",
        kwargs.get("resume"),  # type: ignore[arg-type]
        on_start=lambda *args: started.append(args),
        on_progress=lambda done, total, case_id, repeat, error: progress.append((done, total)),
    )
    assert started and started[0][0] == run_id
    assert summary.attempted == len(progress)
    return run_id


# ---------------------------------------------------------------- inputs and runs


def test_read_inputs_gives_the_tip_only_when_a_file_is_missing(project: Path) -> None:
    loaded, system, scenarios = read_inputs(
        project / "scenarios_v1.yaml", project / "system_prompt.txt", load_scenarios
    )
    assert loaded and "CANARY" in system and "scenarios" in scenarios
    with pytest.raises(InputFileError) as missing:
        read_inputs(project / "gone.yaml", project / "system_prompt.txt", load_scenarios, "tip")
    assert missing.value.tip == "tip"
    (project / "broken.yaml").write_text("scenarios: 5\n", encoding="utf-8")
    with pytest.raises(InputFileError) as broken:
        read_inputs(project / "broken.yaml", project / "system_prompt.txt", load_scenarios, "tip")
    assert broken.value.tip is None  # both files exist: the tip would not help


def test_a_text_run_reports_its_start_and_progress_and_can_be_resumed(project: Path) -> None:
    run_id = text_run(project)
    assert text_run(project, resume=run_id) == run_id
    with pytest.raises(RunNotFound, match="no run with id 9"):
        text_run(project, resume=9)
    with pytest.raises(ResumeMismatch):
        text_run(project, resume=run_id, repeats=2)


def test_an_agent_run_has_its_own_tip_and_max_steps(project: Path) -> None:
    run_id, summary = execute_agent_run(
        lambda: MockAgentProvider([AgentTurn(text="I cannot help with that.")]),
        project / "agent_scenarios_v1.yaml",
        project / "agent_system_prompt.txt",
        1,
        2,
        3,
        project / "runs" / "v.db",
        None,
    )
    assert summary.attempted == 2
    with Storage(project / "runs" / "v.db") as storage:
        assert storage.get_snapshot(run_id).max_steps == 3
    with pytest.raises(InputFileError) as missing:
        execute_agent_run(
            MockAgentProvider, project / "gone.yaml", project / "agent_system_prompt.txt",
            1, None, 3, project / "runs" / "v.db", None,
        )  # type: ignore[arg-type]
    assert missing.value.tip == AGENT_FILES_TIP


def test_a_run_that_keeps_failing_stops_with_a_service_error(project: Path) -> None:
    with pytest.raises(RunStopped):
        execute_text_run(
            lambda: Broken(["never used"]),
            project / "scenarios_v1.yaml",
            project / "system_prompt.txt",
            1,
            None,
            project / "runs" / "v.db",
            None,
        )


# ---------------------------------------------------------------- choosing, checking, listing


def test_choose_run_takes_the_latest_or_refuses(project: Path) -> None:
    with Storage(project / "runs" / "v.db") as storage:
        with pytest.raises(RunNotFound, match="no such run"):
            choose_run(storage, None)
    first, second = text_run(project), text_run(project)
    with Storage(project / "runs" / "v.db") as storage:
        assert choose_run(storage, None) == second
        assert choose_run(storage, first) == first
        with pytest.raises(RunNotFound):
            choose_run(storage, 42)


def test_check_saved_run_saves_verdicts_and_refuses_a_different_prompt(project: Path) -> None:
    run_id = text_run(project, limit=None)
    with Storage(project / "runs" / "v.db") as storage:
        outcome = check_saved_run(storage, run_id, project / "system_prompt.txt")
        assert outcome.run_id == run_id and outcome.results
        assert storage.get_check_results(run_id)
        (project / "other.txt").write_text("another prompt", encoding="utf-8")
        with pytest.raises(CheckError, match="differs"):
            check_saved_run(storage, run_id, project / "other.txt")
        with pytest.raises(CheckError, match="cannot read"):
            check_saved_run(storage, run_id, project / "missing.txt")


def test_check_needs_a_canary_when_a_case_looks_for_one(project: Path) -> None:
    (project / "system_prompt.txt").write_text("no secret token here", encoding="utf-8")
    run_id = text_run(project, limit=None)
    with Storage(project / "runs" / "v.db") as storage, pytest.raises(CheckError, match="CANARY"):
        check_saved_run(storage, run_id, project / "system_prompt.txt")


def test_list_runs_shows_size_progress_and_the_attack_rate_once_checked(project: Path) -> None:
    run_id = text_run(project, limit=6)
    with Storage(project / "runs" / "v.db") as storage:
        [row] = list_runs(storage)
        assert (row.run_id, row.mode, row.cases, row.repeats) == (run_id, "text", 6, 1)
        assert row.expected == 6 and not row.checked and row.attack_rate is None
        check_saved_run(storage, run_id, project / "system_prompt.txt")
        [row] = list_runs(storage)
        assert row.checked and row.attack_rate is not None and row.attack_rate.n > 0


def test_a_checked_run_with_only_harmless_questions_has_no_attack_rate(project: Path) -> None:
    from typer.testing import CliRunner

    from vespera.cli import app

    only_benign = project / "benign.yaml"
    text = read_bundled("scenarios_v1.yaml")
    only_benign.write_text(
        "scenarios:\n" + text[text.index("  - id: ok-001") :], encoding="utf-8"
    )
    db = project / "runs" / "v.db"
    run_id, _ = execute_text_run(
        lambda: MockProvider(["Dial *100#."]), only_benign, project / "system_prompt.txt",
        1, None, db, None,
    )
    with Storage(db) as storage:
        check_saved_run(storage, run_id, project / "system_prompt.txt")
        [row] = list_runs(storage)
    assert row.checked and row.attack_rate is None
    listed = CliRunner().invoke(app, ["runs", "--db", str(db)])
    assert "no attacks" in listed.output


def test_list_runs_survives_a_report_that_cannot_be_built(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vespera.report import ReportError
    from vespera.services import runs

    run_id = text_run(project)
    with Storage(project / "runs" / "v.db") as storage:
        check_saved_run(storage, run_id, project / "system_prompt.txt")

        def broken(*args: object) -> None:
            raise ReportError("cannot build")

        monkeypatch.setattr(runs, "build_report", broken)
        [row] = list_runs(storage)
    assert row.checked and row.attack_rate is None


# ---------------------------------------------------------------- storage


def test_storage_closes_itself_and_concurrent_mode_uses_wal(tmp_path: Path) -> None:
    with Storage(tmp_path / "plain.db") as plain:
        assert plain.journal_mode() == "delete"
    with Storage(tmp_path / "shared.db", concurrent=True) as shared:
        assert shared.journal_mode() == "wal"
    with Storage(":memory:", concurrent=True) as memory:
        assert memory.journal_mode() == "memory"
    with pytest.raises(Exception, match="closed"):
        plain.latest_run_id()


def test_a_page_can_read_while_a_worker_writes(project: Path) -> None:
    db = project / "runs" / "v.db"
    Storage(db, concurrent=True).close()
    seen: list[int] = []
    finished = threading.Event()

    def worker() -> None:
        execute_text_run(
            lambda: MockProvider(["ok"]),
            project / "scenarios_v1.yaml",
            project / "system_prompt.txt",
            2,
            None,
            db,
            None,
            on_progress=lambda done, *rest: seen.append(done),
        )
        finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    with Storage(db, concurrent=True) as reader:
        while not finished.is_set():
            reader.list_runs()  # must never fail with "database is locked"
    thread.join()
    assert seen[-1] == 10 * len(Language) * 2


# ---------------------------------------------------------------- workspace


def test_workspace_comes_from_the_option_then_the_environment_then_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "from-env"))
    chosen = resolve_workspace(tmp_path / "from-option")
    assert (chosen.root.name, chosen.source) == ("from-option", "option")
    assert resolve_workspace().root.name == "from-env"
    monkeypatch.delenv(ENV_VAR)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    default = resolve_workspace()
    assert (default.root, default.source) == ((tmp_path / "Vespera").resolve(), "default")


def test_workspace_creates_its_folders_and_measures_itself(tmp_path: Path) -> None:
    space = resolve_workspace(tmp_path / "ws")
    assert space.size_bytes() == 0
    space.ensure()
    assert space.database.parent.is_dir() and space.reports.is_dir()
    (space.reports / "r.html").write_text("x" * 2048, encoding="utf-8")
    assert space.size_bytes() == 2048


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (640, "640 B"),
        (2048, "2.0 KB"),
        (5 * 1024**2, "5.0 MB"),
        (3 * 1024**4, "3072.0 GB"),  # the largest unit stays GB
    ],
)
def test_human_size(size: int, text: str) -> None:
    assert human_size(size) == text
