"""What each Studio page shows, built from the services and the catalog. No HTML here."""

import platform
from collections.abc import Callable
from dataclasses import dataclass

from vespera import __version__
from vespera.catalog import ATTACKS, CHECK_CATALOG, PACKS, TARGET_KINDS, PackOverview, TargetKind
from vespera.catalog import pack_overview as load_pack_overview
from vespera.config import SERVICES
from vespera.doctor import CheckStatus
from vespera.services.targets import env_var_is_set
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
    space: Workspace, port: int, environment_checks: Callable[[], list[CheckStatus]]
) -> SettingsView:
    return SettingsView(
        version=__version__,
        python=platform.python_version(),
        port=port,
        workspace=workspace_view(space),
        checks=environment_checks(),
        keys=key_variables(),
    )
