"""The workspace: the folder where Vespera Studio keeps its database, reports and settings.

The command line keeps working in the current project folder (`runs/`). Studio is started from
anywhere, so it needs one fixed place. The location is chosen in this order:

1. the `--workspace` option of `vespera ui`;
2. the environment variable VESPERA_HOME;
3. a `Vespera` folder in the user's home folder.

The path belongs to the computer, not to a test, so it is not part of vespera.yaml: the same
project file then works on every machine and in CI.
"""

import os
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "VESPERA_HOME"
DEFAULT_NAME = "Vespera"


@dataclass(frozen=True)
class Workspace:
    root: Path
    source: str  # where the location came from: option, environment or default

    @property
    def database(self) -> Path:
        return self.root / "runs" / "vespera.db"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    def ensure(self) -> "Workspace":
        """Create the folders if they do not exist yet."""
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)
        return self

    def size_bytes(self) -> int:
        """Total size of the files in the workspace."""
        if not self.root.exists():
            return 0
        return sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())


def resolve_workspace(option: Path | None = None) -> Workspace:
    """The workspace location: the option, else the environment variable, else the default."""
    if option is not None:
        return Workspace(option.expanduser().resolve(), "option")
    from_env = os.environ.get(ENV_VAR, "").strip()
    if from_env:
        return Workspace(Path(from_env).expanduser().resolve(), "environment")
    return Workspace((Path.home() / DEFAULT_NAME).resolve(), "default")


def human_size(size: int) -> str:
    """A size in bytes, written the way people read it: 640 B, 12.5 KB, 3.0 MB."""
    units = ("B", "KB", "MB", "GB")
    value, index = float(size), 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.0f} B" if index == 0 else f"{value:.1f} {units[index]}"
