"""Runs: read the inputs, start or resume a run, play every case, check it, list saved runs.

Nothing here prints. Callers pass `on_start` and `on_progress` to show what happens: the command
line prints lines, the web app updates a progress bar.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from vespera.agent_runner import run_agent_experiment
from vespera.agent_scenarios import expand_agent_cases, load_agent_scenarios
from vespera.checks import CheckConfigError, run_checks
from vespera.contracts import Case, CheckResult, Snapshot
from vespera.providers import AgentProvider, BaseProvider
from vespera.report import ReportError, build_report
from vespera.runner import ProgressCallback, RunAborted, RunSummary, run_experiment
from vespera.scenarios import ScenarioFileError, expand_cases, load_scenarios
from vespera.services.errors import (
    CheckError,
    InputFileError,
    ResumeMismatch,
    RunNotFound,
    RunStopped,
)
from vespera.snapshot import build_snapshot, sha256_text
from vespera.stats import Rate
from vespera.storage import Storage
from vespera.summary import Summary, summarize

# Called once the run exists: (run id, resumed?, number of cases, repeats).
StartCallback = Callable[[int, bool, int, int], None]

AGENT_FILES_TIP = "Tip: `vespera init --agents` copies the example agent files here."
FILES_TIP = "Tip: `vespera init` copies the example files here."
RUNS_TIP = "Tip: list the runs with `vespera runs`; start one with `vespera test`."
RESUME_TIP = (
    "Tip: resume with the same model, repeats and files as the original run, "
    "or leave out --resume to start a new run."
)
PROMPT_TIP = (
    "Tip: pass the system prompt file this run used with --system-prompt "
    "(the run stored its hash, not its text), or start a new run."
)


def read_inputs[T](
    scenarios: Path, system_prompt: Path, load: Callable[[Path], T], tip: str | None = FILES_TIP
) -> tuple[T, str, str]:
    """The loaded scenario file and the text of both input files.

    The tip is attached to the error only when one of the files does not exist.
    """
    try:
        loaded = load(scenarios)
        system_text = system_prompt.read_text(encoding="utf-8")
        scenarios_text = scenarios.read_text(encoding="utf-8")
    except (ScenarioFileError, OSError) as exc:
        missing = not scenarios.exists() or not system_prompt.exists()
        raise InputFileError(str(exc), tip=tip if missing else None) from exc
    return loaded, system_text, scenarios_text


def begin_run(
    storage: Storage, db: Path, snapshot: Snapshot, cases: list[Case], resume: int | None
) -> tuple[int, bool]:
    """A new run, or an old one to continue if nothing that matters has changed.

    Returns (run id, resumed?).
    """
    if resume is None:
        run_id = storage.create_run(snapshot)
        storage.save_cases(run_id, cases)
        return run_id, False
    if not storage.run_exists(resume):
        raise RunNotFound(f"no run with id {resume} in {db}", tip=RUNS_TIP)
    old = storage.get_snapshot(resume)
    same = (
        old.model == snapshot.model
        and old.system_prompt_sha256 == snapshot.system_prompt_sha256
        and old.scenarios_sha256 == snapshot.scenarios_sha256
        and old.repeats == snapshot.repeats
        and old.max_steps == snapshot.max_steps
    )
    if not same:
        raise ResumeMismatch(
            "model, repeats or input files differ from the original run", tip=RESUME_TIP
        )
    before, now = old.weights_digest, snapshot.weights_digest
    if before and now and before != now:  # unknown on either side (older run, remote API): allowed
        raise ResumeMismatch(
            f"the weights of {snapshot.model} changed since the original run "
            f"({before[:12]} -> {now[:12]}): start a new run"
        )
    _refuse_other_cases(storage, resume, cases)
    return resume, True


def _refuse_other_cases(storage: Storage, run_id: int, cases: list[Case]) -> None:
    """A resume must ask exactly the cases the run was started with, in the same order.

    The cases are saved once, when the run starts, and every attempt points to one of them by id.
    Other cases (another --limit, for example) would leave attempts the run has no case for. The
    limit itself is not compared: another limit that gives the same list is fine. Nothing has been
    written when this refuses.
    """
    saved = [case.id for case in storage.get_cases(run_id)]
    asked = [case.id for case in cases]
    if saved == asked:
        return
    if sorted(saved) == sorted(asked):
        problem = "the same cases in another order"
    else:
        problem = f"{len(saved)} saved, {len(asked)} asked now"
    raise ResumeMismatch(
        f"the cases differ from the original run ({problem})",
        tip=f"Tip: use the same --limit as the original run ({len(saved)} cases), "
        "or leave out --resume to start a new run.",
    )


def snapshot_for(
    provider: BaseProvider | AgentProvider,
    repeats: int,
    system_text: str,
    scenarios_text: str,
    max_steps: int | None = None,
) -> Snapshot:
    """The conditions of a run, including the weights behind the model name when known."""
    identity = provider.identity()
    return build_snapshot(
        model=provider.model,
        temperature=provider.temperature,
        num_predict=provider.num_predict,
        repeats=repeats,
        system_prompt=system_text,
        scenarios_text=scenarios_text,
        max_steps=max_steps,
        weights_digest=identity.digest if identity else None,
        weights_quantization=identity.quantization if identity else None,
    )


def run_and_save(
    db: Path,
    snapshot: Snapshot,
    cases: list[Case],
    resume: int | None,
    execute: Callable[[Storage, int], RunSummary],
    on_start: StartCallback | None = None,
) -> tuple[int, RunSummary]:
    """Open the database, start or resume the run and execute it."""
    with Storage(db) as storage:
        run_id, resumed = begin_run(storage, db, snapshot, cases, resume)
        if on_start is not None:
            on_start(run_id, resumed, len(cases), snapshot.repeats)
        try:
            summary = execute(storage, run_id)
        except RunAborted as exc:
            raise RunStopped(str(exc)) from exc
    return run_id, summary


def execute_text_run(
    make_provider: Callable[[], BaseProvider],
    scenarios: Path,
    system_prompt: Path,
    repeats: int,
    limit: int | None,
    db: Path,
    resume: int | None,
    *,
    on_start: StartCallback | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[int, RunSummary]:
    """Ask the model every case of a question file and save every answer."""
    loaded, system_text, scenarios_text = read_inputs(scenarios, system_prompt, load_scenarios)
    cases = expand_cases(loaded)[:limit]
    provider = make_provider()
    snapshot = snapshot_for(provider, repeats, system_text, scenarios_text)
    return run_and_save(
        db,
        snapshot,
        cases,
        resume,
        lambda storage, run_id: run_experiment(
            provider, cases, system_text, storage, run_id, repeats, on_progress=on_progress
        ),
        on_start,
    )


def execute_agent_run(
    make_provider: Callable[[], AgentProvider],
    scenarios: Path,
    system_prompt: Path,
    repeats: int,
    limit: int | None,
    max_steps: int,
    db: Path,
    resume: int | None,
    *,
    on_start: StartCallback | None = None,
    on_progress: ProgressCallback | None = None,
) -> tuple[int, RunSummary]:
    """Give an agent every task of an agent file with simulated tools and save what it did."""
    loaded, system_text, scenarios_text = read_inputs(
        scenarios, system_prompt, load_agent_scenarios, tip=AGENT_FILES_TIP
    )
    cases = expand_agent_cases(loaded)[:limit]
    provider = make_provider()
    snapshot = snapshot_for(provider, repeats, system_text, scenarios_text, max_steps)
    return run_and_save(
        db,
        snapshot,
        [case.to_case() for case in cases],
        resume,
        lambda storage, run_id: run_agent_experiment(
            provider,
            cases,
            system_text,
            storage,
            run_id,
            repeats,
            max_steps=max_steps,
            on_progress=on_progress,
        ),
        on_start,
    )


def choose_run(storage: Storage, run_id: int | None) -> int:
    """The run asked for, or the latest one when none is given."""
    chosen = run_id if run_id is not None else storage.latest_run_id()
    if chosen is None or not storage.run_exists(chosen):
        raise RunNotFound("no such run", tip=RUNS_TIP)
    return chosen


def default_prompt_file(storage: Storage, run_id: int) -> Path:
    """The system prompt file that fits this kind of run, in the current folder."""
    return Path("agent_system_prompt.txt" if storage.is_agent_run(run_id) else "system_prompt.txt")


@dataclass(frozen=True)
class CheckOutcome:
    run_id: int
    snapshot: Snapshot
    cases: list[Case]
    results: list[CheckResult]
    summary: Summary


def check_saved_run(storage: Storage, run_id: int, prompt_path: Path | None) -> CheckOutcome:
    """Judge every saved attempt of a run with the rules and save the verdicts.

    The system prompt must be the one the run used: its hash is compared with the snapshot.
    """
    path = prompt_path or default_prompt_file(storage, run_id)
    try:
        system_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckError(f"cannot read {path}: {exc}") from exc
    snapshot = storage.get_snapshot(run_id)
    if sha256_text(system_text) != snapshot.system_prompt_sha256:
        raise CheckError(
            "the system prompt differs from the one used in this run", tip=PROMPT_TIP
        )
    cases = storage.get_cases(run_id)
    try:
        results = run_checks(
            cases, storage.get_attempts(run_id), system_text, storage.get_traces(run_id)
        )
    except CheckConfigError as exc:
        raise CheckError(str(exc)) from exc
    storage.save_check_results(run_id, results)
    return CheckOutcome(run_id, snapshot, cases, results, summarize(cases, results))


@dataclass(frozen=True)
class RunRow:
    """One line of the list of runs."""

    run_id: int
    started: datetime  # local time
    model: str
    mode: str  # text or agent
    cases: int
    repeats: int
    attempts: int
    checked: bool
    attack_rate: Rate | None  # None when nothing was checked or no attack was judged
    status: str = "finished"  # running | finished; a run cut short stays "running"

    @property
    def expected(self) -> int:
        return self.cases * self.repeats


def list_runs(storage: Storage) -> list[RunRow]:
    """Every saved run with its size and, once checked, its attack success rate."""
    rows = []
    for info in storage.list_runs():
        snapshot = info.snapshot
        rate = None
        if info.checked:
            try:
                rate = build_report(storage, info.run_id).attack_rate
            except ReportError:
                rate = None
        rows.append(
            RunRow(
                run_id=info.run_id,
                started=datetime.fromisoformat(info.started_at).astimezone(),
                model=snapshot.model,
                mode="agent" if snapshot.max_steps is not None else "text",
                cases=info.cases,
                repeats=snapshot.repeats,
                attempts=info.attempts,
                checked=info.checked,
                attack_rate=rate,
                status=info.status,
            )
        )
    return rows
