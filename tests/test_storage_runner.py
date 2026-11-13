from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vespera.cli import app
from vespera.contracts import Attempt, Case, Category, Language
from vespera.providers import BaseProvider, MockProvider, ProviderError
from vespera.runner import RunAborted, run_experiment
from vespera.snapshot import build_snapshot
from vespera.storage import Storage


@pytest.fixture
def storage() -> Iterator[Storage]:
    db = Storage(":memory:")
    yield db
    db.close()


def snap(repeats: int = 1, system: str = "sys", scenarios: str = "sc"):
    return build_snapshot(
        model="mock",
        temperature=0.0,
        num_predict=0,
        repeats=repeats,
        system_prompt=system,
        scenarios_text=scenarios,
    )


def make_cases(n: int) -> list[Case]:
    return [
        Case(
            id=f"c-{i}:en",
            scenario_id=f"c-{i}",
            language=Language.EN,
            category=Category.BENIGN,
            prompt=f"question {i}",
            checks=["not_refused"],
        )
        for i in range(n)
    ]


def attempt(case_id: str, repeat: int = 1, error: str | None = None) -> Attempt:
    return Attempt(
        case_id=case_id,
        repeat=repeat,
        reply="" if error else "answer",
        error=error,
        latency_ms=5,
        created_at="2026-01-01T00:00:00+00:00",
    )


class FlakyProvider(BaseProvider):
    """Fails on the given call numbers (1-based), otherwise answers."""

    def __init__(self, fail_on: set[int]) -> None:
        self.model = "flaky"
        self.temperature = 0.0
        self.num_predict = 0
        self.fail_on = fail_on
        self.count = 0

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.count += 1
        if self.count in self.fail_on:
            raise ProviderError("boom")
        return "fine"


# ---------- snapshot ----------

def test_snapshot_fingerprints_change_with_input() -> None:
    assert snap(system="a").system_prompt_sha256 != snap(system="b").system_prompt_sha256
    assert snap(scenarios="a").scenarios_sha256 != snap(scenarios="b").scenarios_sha256
    assert snap(system="a").system_prompt_sha256 == snap(system="a").system_prompt_sha256


# ---------- storage ----------

def test_storage_roundtrip_of_snapshot_and_attempts(storage: Storage) -> None:
    run_id = storage.create_run(snap(repeats=3))
    storage.add_attempt(run_id, attempt("c-0:en"))
    assert storage.get_snapshot(run_id).repeats == 3
    assert [a.case_id for a in storage.get_attempts(run_id)] == ["c-0:en"]
    assert storage.get_status(run_id) == "running"
    storage.finish_run(run_id)
    assert storage.get_status(run_id) == "finished"


def test_failed_attempts_are_not_counted_as_done(storage: Storage) -> None:
    run_id = storage.create_run(snap())
    storage.add_attempt(run_id, attempt("c-0:en"))
    storage.add_attempt(run_id, attempt("c-1:en", error="boom"))
    assert storage.done_keys(run_id) == {("c-0:en", 1)}


def test_retry_replaces_the_failed_attempt(storage: Storage) -> None:
    run_id = storage.create_run(snap())
    storage.add_attempt(run_id, attempt("c-0:en", error="boom"))
    storage.add_attempt(run_id, attempt("c-0:en"))
    attempts = storage.get_attempts(run_id)
    assert len(attempts) == 1
    assert attempts[0].error is None


def test_latest_run_and_missing_run(storage: Storage) -> None:
    assert storage.latest_run_id() is None
    first = storage.create_run(snap())
    second = storage.create_run(snap())
    assert storage.latest_run_id() == second > first
    assert not storage.run_exists(999)
    with pytest.raises(KeyError):
        storage.get_snapshot(999)


def test_storage_creates_missing_folder_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "runs.db"
    first = Storage(path)
    run_id = first.create_run(snap())
    first.save_cases(run_id, make_cases(2))
    first.close()
    reopened = Storage(path)
    assert reopened.run_exists(run_id)
    reopened.close()


# ---------- runner ----------

def test_runner_asks_every_case_every_repeat(storage: Storage) -> None:
    run_id = storage.create_run(snap(repeats=2))
    provider = MockProvider(["ok"])
    summary = run_experiment(provider, make_cases(3), "sys", storage, run_id, repeats=2)
    assert summary.total == 6
    assert summary.attempted == 6
    assert len(provider.calls) == 6
    assert len(storage.get_attempts(run_id)) == 6
    assert storage.get_status(run_id) == "finished"
    assert provider.calls[0] == ("sys", "question 0")


def test_resume_skips_what_is_already_done(storage: Storage) -> None:
    run_id = storage.create_run(snap())
    storage.add_attempt(run_id, attempt("c-0:en"))
    provider = MockProvider(["ok"])
    summary = run_experiment(provider, make_cases(3), "sys", storage, run_id, repeats=1)
    assert summary.skipped == 1
    assert summary.attempted == 2
    assert [call[1] for call in provider.calls] == ["question 1", "question 2"]


def test_single_error_is_recorded_and_run_continues(storage: Storage) -> None:
    run_id = storage.create_run(snap())
    provider = FlakyProvider(fail_on={2})
    summary = run_experiment(provider, make_cases(3), "sys", storage, run_id, repeats=1)
    assert summary.errors == 1
    assert summary.attempted == 3
    errored = [a for a in storage.get_attempts(run_id) if a.error]
    assert [a.case_id for a in errored] == ["c-1:en"]


def test_errors_are_retried_on_resume(storage: Storage) -> None:
    run_id = storage.create_run(snap())
    run_experiment(FlakyProvider({2}), make_cases(3), "sys", storage, run_id, repeats=1)
    provider = MockProvider(["ok"])
    summary = run_experiment(provider, make_cases(3), "sys", storage, run_id, repeats=1)
    assert [call[1] for call in provider.calls] == ["question 1"]
    assert summary.errors == 0
    assert all(a.error is None for a in storage.get_attempts(run_id))


def test_run_aborts_after_consecutive_errors(storage: Storage) -> None:
    run_id = storage.create_run(snap())
    provider = FlakyProvider(fail_on={1, 2, 3, 4, 5})
    with pytest.raises(RunAborted, match="--resume"):
        run_experiment(provider, make_cases(10), "sys", storage, run_id, repeats=1)
    assert provider.count == 3  # it stopped, it did not keep hammering the server
    assert storage.get_status(run_id) == "running"


def test_success_resets_the_error_streak(storage: Storage) -> None:
    run_id = storage.create_run(snap())
    provider = FlakyProvider(fail_on={1, 2, 4, 5})
    summary = run_experiment(provider, make_cases(6), "sys", storage, run_id, repeats=1)
    assert summary.errors == 4


def test_progress_callback_is_called(storage: Storage) -> None:
    run_id = storage.create_run(snap())
    seen: list[tuple[int, int, str]] = []
    run_experiment(
        MockProvider(["ok"]),
        make_cases(2),
        "sys",
        storage,
        run_id,
        repeats=1,
        on_progress=lambda done, total, case_id, repeat, error: seen.append((done, total, case_id)),
    )
    assert seen == [(1, 2, "c-0:en"), (2, 2, "c-1:en")]


# ---------- CLI ----------

REPO = Path(__file__).resolve().parents[1]


def cli_args(db: Path, *extra: str) -> list[str]:
    return [
        "run",
        "--mock",
        "--db",
        str(db),
        "--scenarios",
        str(REPO / "scenarios_v1.yaml"),
        "--system-prompt",
        str(REPO / "system_prompt.txt"),
        *extra,
    ]


def test_run_command_with_mock_saves_everything(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    result = CliRunner().invoke(app, cli_args(db))
    assert result.exit_code == 0, result.output
    assert "30 attempts, 0 errors" in result.output
    shown = CliRunner().invoke(app, ["show", "--db", str(db)])
    assert shown.exit_code == 0
    assert "inj-001:ru #1" in shown.output
    assert "I cannot help with that." in shown.output


def test_run_command_limit_and_repeats(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, cli_args(tmp_path / "r.db", "--limit", "4", "--repeats", "2"))
    assert result.exit_code == 0
    assert "8 attempts" in result.output


def test_resume_of_finished_run_does_nothing(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    CliRunner().invoke(app, cli_args(db, "--limit", "3"))
    result = CliRunner().invoke(app, cli_args(db, "--limit", "3", "--resume", "1"))
    assert result.exit_code == 0
    assert "0 attempts" in result.output
    assert "3 skipped" in result.output


def test_resume_refuses_when_inputs_changed(tmp_path: Path) -> None:
    db = tmp_path / "r.db"
    CliRunner().invoke(app, cli_args(db, "--limit", "3"))
    args = cli_args(db, "--limit", "3", "--repeats", "2", "--resume", "1")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "differ" in result.output


def test_resume_unknown_run_fails(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, cli_args(tmp_path / "r.db", "--resume", "42"))
    assert result.exit_code == 1
    assert "no run with id 42" in result.output


def test_run_command_missing_system_prompt_fails(tmp_path: Path) -> None:
    args = cli_args(tmp_path / "r.db") + ["--system-prompt", str(tmp_path / "nope.txt")]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "[FAIL]" in result.output


def test_show_without_runs_fails(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["show", "--db", str(tmp_path / "empty.db")])
    assert result.exit_code == 1