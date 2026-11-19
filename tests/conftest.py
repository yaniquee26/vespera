"""Every test starts in an empty folder.

Vespera reads `vespera.yaml` from the current folder. Without this, a stray project file in the
repository root (for example one made by `vespera init` while trying the tool) would silently
change what the command-line tests do. Tests that need files give their paths explicitly or
change folder themselves.
"""

import pytest


@pytest.fixture(autouse=True)
def _empty_working_folder(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
