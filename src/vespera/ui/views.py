"""What each Studio page shows, built from the services and the catalog. No HTML here."""

import platform
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from vespera import __version__
from vespera.catalog import ATTACKS, CHECK_CATALOG, PACKS, TARGET_KINDS, PackOverview, TargetKind
from vespera.catalog import pack_overview as load_pack_overview
from vespera.checks import CANARY_RE, find_all_personal_data
from vespera.config import SERVICES
from vespera.contracts import Category, Language, Snapshot
from vespera.demo import DEMO_MODEL
from vespera.doctor import CheckStatus
from vespera.export import secrets_of
from vespera.gate import GatePolicy
from vespera.model_identity import LoadedModel, ollama_loaded
from vespera.report import CategoryRow, ReportData, ReportError, build_report
from vespera.services.errors import TargetError
from vespera.services.runs import RunRow, list_runs
from vespera.services.targets import OLLAMA_ADDRESS, SavedTarget, env_var_is_set, list_targets
from vespera.storage import Storage
from vespera.workspace import ENV_VAR, Workspace, human_size

SOURCE_LABELS = {
    "option": "the --workspace option",
    "environment": f"the {ENV_VAR} environment variable",
    "default": "the default location",
}


@dataclass(frozen=True)
class WorkspaceView:
    path: str
    chosen_by: str
    size: str
    database_exists: bool
    runs: int


def workspace_view(space: Workspace) -> WorkspaceView:
    """The workspace as people read it. Reading never creates the database file."""
    runs = 0
    if space.database.exists():
        with Storage(space.database) as storage:
            runs = len(storage.list_runs())
    return WorkspaceView(
        path=str(space.root),
        chosen_by=SOURCE_LABELS[space.source],
        size=human_size(space.size_bytes()),
        database_exists=space.database.exists(),
        runs=runs,
    )


@dataclass(frozen=True)
class OverviewView:
    workspace: WorkspaceView
    targets: tuple[TargetKind, ...]
    packs: list[PackOverview]
    attacks: int
    verdict_checks: int
    measurements: int


def overview_view(space: Workspace) -> OverviewView:
    return OverviewView(
        workspace=workspace_view(space),
        targets=TARGET_KINDS,
        packs=[load_pack_overview(item) for item in PACKS],
        attacks=len(ATTACKS),
        verdict_checks=sum(1 for info in CHECK_CATALOG if info.verdict),
        measurements=sum(1 for info in CHECK_CATALOG if not info.verdict),
    )


@dataclass(frozen=True)
class KeyVariable:
    name: str
    used_by: str
    is_set: bool  # the value itself is never read into a page


@dataclass(frozen=True)
class SettingsView:
    version: str
    python: str
    port: int
    workspace: WorkspaceView
    checks: list[CheckStatus]
    keys: list[KeyVariable]
    loaded: list[LoadedModel] | None = None  # None: Ollama could not be asked


def local_ollama_models() -> list[LoadedModel] | None:
    """What the local Ollama holds in memory now, with its share in video memory."""
    with httpx.Client() as client:
        return ollama_loaded(client, OLLAMA_ADDRESS)


@dataclass(frozen=True)
class TargetRow:
    target: SavedTarget
    key_set: bool


def targets_view(space: Workspace) -> tuple[list[TargetRow], str | None]:
    """The saved targets, and a problem to show if the file cannot be read."""
    try:
        saved = list_targets(space)
    except TargetError as exc:
        return [], str(exc)
    rows = [TargetRow(item, bool(item.key_env and env_var_is_set(item.key_env))) for item in saved]
    return rows, None


def target_settings(target: SavedTarget) -> list[tuple[str, str]]:
    """The settings of a target that differ from the defaults, key variable shown separately."""
    data = target.config.model_dump(mode="json", exclude_defaults=True)
    hidden = ("type", "api_key_env")
    return [(name, str(value)) for name, value in data.items() if name not in hidden]


PLACEHOLDERS = {
    "model": "qwen2.5:7b",
    "base_url": "http://localhost:11434",
    "api_key_env": "OPENAI_API_KEY",
    "url": "https://bot.example.com/chat",
    "reply_field": "answer",
    "request_field": "message",
    "timeout": "60",
    "request_interval": "0",
    "max_retries": "3",
}


# ---------------------------------------------------------------- runs

MIN_EXPOSURE = GatePolicy().min_exposure
TABS = (
    ("categories", "Categories"),
    ("languages", "Language gap"),
    ("findings", "Findings"),
    ("snapshot", "Snapshot"),
)


def runs_view(space: Workspace) -> list[RunRow]:
    """Every saved run, newest first. Reading never creates the database file."""
    if not space.database.exists():
        return []
    with Storage(space.database, concurrent=True) as storage:
        return list(reversed(list_runs(storage)))


@dataclass(frozen=True)
class RunDetailView:
    run_id: int
    status: str
    mode: str  # text or agent
    snapshot: Snapshot
    attempts: int
    report: ReportData | None  # None until the run is checked
    problem: str | None  # why there is no report yet
    secrets: list[str]  # planted secrets, highlighted wherever they show up
    languages: list[str]

    @property
    def exposure(self) -> float | None:
        """Share of tool-output attempts in which the hidden order reached the agent."""
        if self.report is None or not self.report.hidden_order_total:
            return None
        return self.report.hidden_order_delivered / self.report.hidden_order_total

    @property
    def is_demo(self) -> bool:
        """A run of the demo bot, built to leak: Studio says so next to the model name."""
        return self.snapshot.model == DEMO_MODEL

    @property
    def under_exposed(self) -> bool:
        exposure = self.exposure
        return exposure is not None and exposure < MIN_EXPOSURE

    def inconclusive(self, row: CategoryRow) -> bool:
        """A category that passed without the attack really reaching the agent proves nothing."""
        return (
            self.under_exposed
            and row.key == Category.TOOL_OUTPUT_INJECTION.value
            and row.rate.k == 0
        )


def run_detail_view(space: Workspace, run_id: int) -> RunDetailView | None:
    """One run with its report, or None when there is no such run."""
    if not space.database.exists():
        return None
    with Storage(space.database, concurrent=True) as storage:
        if not storage.run_exists(run_id):
            return None
        snapshot = storage.get_snapshot(run_id)
        cases = storage.get_cases(run_id)
        try:
            report: ReportData | None = build_report(storage, run_id)
            problem = None
        except ReportError as exc:
            report, problem = None, str(exc)
        return RunDetailView(
            run_id=run_id,
            status=storage.get_status(run_id),
            mode="agent" if snapshot.max_steps is not None else "text",
            snapshot=snapshot,
            attempts=len(storage.get_attempts(run_id)),
            report=report,
            problem=problem,
            secrets=secrets_of(cases),
            languages=[lang.value for lang in Language],
        )


@dataclass(frozen=True)
class Piece:
    """A stretch of quoted text; `label` names what was found in it, None for plain text."""

    text: str
    label: str | None = None


def highlight(text: str, secrets: list[str] | None = None) -> list[Piece]:
    """Split text so that personal data, canary tokens and planted secrets can be marked.

    Uses the same patterns as the checks (IDNP, +373 and local phones, IBAN MD), so what a
    check reports is what the page marks. Where two finds overlap, the first one found wins.
    """
    spans: list[tuple[int, int, str]] = []

    def add(start: int, end: int, label: str) -> None:
        if start < end and all(end <= s or start >= e for s, e, _ in spans):
            spans.append((start, end, label))

    for secret in secrets or []:
        start = text.find(secret) if secret else -1
        while start >= 0:
            add(start, start + len(secret), "planted secret")
            start = text.find(secret, start + len(secret))
    for match in CANARY_RE.finditer(text):
        add(*match.span(), "canary token")
    for label, start, end in find_all_personal_data(text):
        add(start, end, label)

    pieces: list[Piece] = []
    position = 0
    for start, end, label in sorted(spans):
        if start > position:
            pieces.append(Piece(text[position:start]))
        pieces.append(Piece(text[start:end], label))
        position = end
    if position < len(text):
        pieces.append(Piece(text[position:]))
    return pieces


def key_variables() -> list[KeyVariable]:
    """The environment variables the built-in services read their keys from."""
    found: dict[str, list[str]] = {}
    for prefix, (_, env_name) in SERVICES.items():
        if env_name is not None:
            found.setdefault(env_name, []).append(prefix)
    return [
        KeyVariable(name, ", ".join(users), env_var_is_set(name))
        for name, users in sorted(found.items())
    ]


def settings_view(
    space: Workspace,
    port: int,
    environment_checks: Callable[[], list[CheckStatus]],
    loaded_models: Callable[[], list[LoadedModel] | None] = local_ollama_models,
) -> SettingsView:
    return SettingsView(
        version=__version__,
        python=platform.python_version(),
        port=port,
        workspace=workspace_view(space),
        checks=environment_checks(),
        keys=key_variables(),
        loaded=loaded_models(),
    )
