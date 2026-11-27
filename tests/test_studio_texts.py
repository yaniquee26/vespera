"""Studio and the report never promise what does not exist, and every command they show works.

Each command printed on a page is run here exactly as written, from the place the page says.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from vespera import cli
from vespera.ui import server
from vespera.ui.app import UiSettings, create_app
from vespera.workspace import Workspace, resolve_workspace

runner = CliRunner()
BASE = "http://127.0.0.1:8765"


@pytest.fixture
def space(tmp_path: Path) -> Workspace:
    return resolve_workspace(tmp_path / "ws").ensure()


@pytest.fixture
def client(space: Workspace) -> TestClient:
    settings = UiSettings(workspace=space, environment_checks=list, loaded_models=lambda: None)
    return TestClient(create_app(settings), base_url=BASE)


def snippet(page: str, testid: str) -> str:
    start = page.index(f'data-testid="{testid}">') + len(f'data-testid="{testid}">')
    return page[start : page.index("</code>", start)]


# ---------------------------------------------------------------- Runs, empty


def test_the_first_run_commands_on_an_empty_runs_page_work_as_written(
    client: TestClient, space: Workspace
) -> None:
    page = client.get("/runs").text
    assert snippet(page, "first-run-init") == "vespera init --no-interactive"
    assert snippet(page, "first-run") == f'vespera test demo --db "{space.database}"'
    # run them in an empty folder (each test starts in one), exactly as shown
    assert runner.invoke(cli.app, ["init", "--no-interactive"]).exit_code == 0
    demo = runner.invoke(cli.app, ["test", "demo", "--db", str(space.database)])
    assert demo.exit_code == 2, demo.output  # the demo bot fails the gate of vespera.yaml
    listed = client.get("/runs").text
    assert 'data-testid="run-1"' in listed and "vespera-demo" in listed


def test_the_runs_page_says_which_database_it_reads_and_how_to_see_a_project(
    client: TestClient, space: Workspace
) -> None:
    page = client.get("/runs").text
    hint = page[page.index('data-testid="workspace-hint"') :]
    assert str(space.database) in hint and "vespera ui --workspace ." in hint


def test_a_project_folder_opened_as_the_workspace_shows_its_runs(tmp_path: Path) -> None:
    """`vespera ui --workspace .` in a project folder reads the database the CLI wrote there."""
    assert runner.invoke(cli.app, ["init", "--no-interactive"]).exit_code == 0
    runner.invoke(cli.app, ["test", "demo", "--repeats", "1"])
    project = resolve_workspace(Path("."))
    assert project.database == (Path("runs") / "vespera.db").resolve()
    settings = UiSettings(workspace=project, environment_checks=list, loaded_models=lambda: None)
    page = TestClient(create_app(settings), base_url=BASE).get("/runs").text
    assert 'data-testid="run-1"' in page


# ---------------------------------------------------------------- Overview


def test_the_overview_promises_nothing_that_is_already_there_or_missing(
    client: TestClient,
) -> None:
    page = client.get("/").text
    assert "come next" not in page and "comes next" not in page
    assert "vespera test demo" in page and "vespera ui --workspace ." in page
    assert 'href="/runs"' in page


# ---------------------------------------------------------------- Run detail, not checked


@pytest.mark.parametrize("mode", ["text", "agent"])
def test_the_check_command_of_an_unchecked_run_works_in_its_project_folder(
    client: TestClient, space: Workspace, mode: str
) -> None:
    init = ["init", "--no-interactive"] + (["--agents"] if mode == "agent" else [])
    assert runner.invoke(cli.app, init).exit_code == 0
    command = "run-agent" if mode == "agent" else "run"
    saved = runner.invoke(cli.app, [command, "--mock", "--limit", "2", "--db", str(space.database)])
    assert saved.exit_code == 0, saved.output
    page = client.get("/runs/1").text
    prompt = "agent_system_prompt.txt" if mode == "agent" else "system_prompt.txt"
    shown = snippet(page, "check-command")
    assert shown == f'vespera check 1 --db "{space.database}" --system-prompt {prompt}'
    checked = runner.invoke(
        cli.app, ["check", "1", "--db", str(space.database), "--system-prompt", prompt]
    )
    assert checked.exit_code == 0, checked.output
    assert 'data-testid="not-checked"' not in client.get("/runs/1").text


# ---------------------------------------------------------------- `vespera ui`


def test_vespera_ui_points_to_the_runs_of_the_current_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server, "serve", lambda space, port, open_it: None)
    monkeypatch.setenv("VESPERA_HOME", str(tmp_path / "home"))
    quiet = runner.invoke(cli.app, ["ui", "--no-open"])
    assert quiet.exit_code == 0 and "its own runs" not in quiet.output  # no runs here yet

    assert runner.invoke(cli.app, ["init", "--no-interactive"]).exit_code == 0
    runner.invoke(cli.app, ["test", "demo", "--repeats", "1", "--limit", "1"])
    hinted = runner.invoke(cli.app, ["ui", "--no-open"])
    assert "This folder has its own runs" in hinted.output
    assert "vespera ui --workspace ." in hinted.output
    here = runner.invoke(cli.app, ["ui", "--no-open", "--workspace", "."])
    assert "its own runs" not in here.output  # already showing them


# ---------------------------------------------------------------- the report


def test_the_report_says_that_repeats_are_not_independent() -> None:
    assert runner.invoke(cli.app, ["init", "--no-interactive"]).exit_code == 0
    runner.invoke(cli.app, ["test", "mock", "--no-config", "--repeats", "1", "--limit", "2"])
    html = Path("runs/report-run-1.html").read_text(encoding="utf-8")
    assert "Repeats of the same question are not independent" in html
