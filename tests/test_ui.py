"""Vespera Studio: pages, security rules and the `vespera ui` command (docs/adr/0003, 0004)."""

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from vespera.bundled import read_bundled
from vespera.catalog import PACKS, TARGET_KINDS
from vespera.cli import app as cli_app
from vespera.doctor import CheckStatus
from vespera.providers import MockProvider
from vespera.report_template import TEMPLATE
from vespera.services.runs import check_saved_run, execute_text_run
from vespera.storage import Storage
from vespera.ui import server
from vespera.ui.app import UiSettings, create_app
from vespera.ui.security import SECURITY_HEADERS, allowed_origins, request_problem
from vespera.ui.views import key_variables, workspace_view
from vespera.workspace import Workspace, resolve_workspace

BASE = "http://127.0.0.1:8765"
SENTINEL = "sk-sentinel-must-never-show-0123456789"
CHECKS = [
    CheckStatus("python", True, "Python 3.13", True),
    CheckStatus("ollama", False, "not reachable at http://localhost:11434", False),
]


@pytest.fixture
def space(tmp_path: Path) -> Workspace:
    return resolve_workspace(tmp_path / "ws").ensure()


@pytest.fixture
def settings(space: Workspace) -> UiSettings:
    return UiSettings(
        workspace=space, environment_checks=lambda: CHECKS, loaded_models=lambda: None
    )


@pytest.fixture
def client(settings: UiSettings) -> TestClient:
    return TestClient(create_app(settings), base_url=BASE)


def seed_checked_run(space: Workspace, tmp_path: Path) -> None:
    for name in ("scenarios_v1.yaml", "system_prompt.txt"):
        (tmp_path / name).write_text(read_bundled(name), encoding="utf-8")
    run_id, _ = execute_text_run(
        lambda: MockProvider(["I cannot help with that."]),
        tmp_path / "scenarios_v1.yaml",
        tmp_path / "system_prompt.txt",
        1,
        4,
        space.database,
        None,
    )
    with Storage(space.database) as storage:
        check_saved_run(storage, run_id, tmp_path / "system_prompt.txt")


# ---------------------------------------------------------------- pages


def test_overview_shows_the_catalog_and_the_menu(client: TestClient, settings: UiSettings) -> None:
    page = client.get("/")
    assert page.status_code == 200
    for kind in TARGET_KINDS:
        assert kind.title in page.text
    for item in PACKS:
        assert item.title in page.text
    assert 'data-testid="kpi-runs">0<' in page.text
    assert f'<meta name="csrf-token" content="{settings.csrf_token}">' in page.text
    assert 'aria-current="page" data-testid="nav-overview"' in page.text


def test_overview_counts_the_runs_of_the_workspace(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    seed_checked_run(space, tmp_path)
    assert 'data-testid="kpi-runs">1<' in client.get("/").text


def test_settings_show_the_workspace_checks_and_keys_but_never_a_key_value(
    client: TestClient, space: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    page = client.get("/settings").text
    assert str(space.root) in page and "the --workspace option" in page
    assert "not reachable at http://localhost:11434" in page
    assert "OPENAI_API_KEY" in page and ">set<" in page and ">not set<" in page
    assert SENTINEL not in page


def test_an_unknown_page_is_a_friendly_404(client: TestClient) -> None:
    page = client.get("/no/such/page")
    assert page.status_code == 404
    assert "Page not found" in page.text


def test_static_files_are_served(client: TestClient) -> None:
    assert client.get("/static/vespera.css").headers["content-type"].startswith("text/css")
    assert client.get("/static/logo.svg").headers["content-type"] == "image/svg+xml"


@pytest.mark.parametrize("path", ["/", "/settings"])
def test_pages_have_no_inline_script_or_style_so_the_csp_holds(
    client: TestClient, path: str
) -> None:
    html = client.get(path).text
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", html, flags=re.S)
    assert scripts and all(body.strip() == "" for body in scripts)  # only files from /static
    assert re.findall(r'<script\b[^>]*\bsrc="(/static/[^"]+)"', html) == [
        "/static/htmx.min.js",
        "/static/app.js",
    ]
    assert "style=" not in html and "<style" not in html


# ---------------------------------------------------------------- security


@pytest.mark.parametrize("path", ["/", "/settings", "/static/vespera.css", "/missing"])
def test_every_response_carries_the_security_headers(client: TestClient, path: str) -> None:
    headers = client.get(path).headers
    for name, value in SECURITY_HEADERS.items():
        assert headers[name] == value


def test_a_foreign_host_is_refused_with_the_same_headers(settings: UiSettings) -> None:
    rebinding = TestClient(create_app(settings), base_url="http://attacker.example:8765")
    refused = rebinding.get("/")
    assert refused.status_code == 400
    assert refused.headers["Content-Security-Policy"] == SECURITY_HEADERS[
        "Content-Security-Policy"
    ]


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 403),
        ({"x-csrf-token": "wrong"}, 403),
        ({"x-csrf-token": "TOKEN", "origin": "http://evil.example"}, 403),
        ({"x-csrf-token": "TOKEN", "sec-fetch-site": "cross-site"}, 403),
        ({"x-csrf-token": "TOKEN", "origin": BASE, "sec-fetch-site": "same-origin"}, 405),
        ({"x-csrf-token": "TOKEN"}, 405),  # passes the guard; / simply has no POST handler
    ],
)
def test_state_changing_requests_need_the_token_and_studios_own_origin(
    client: TestClient, settings: UiSettings, headers: dict[str, str], status: int
) -> None:
    token = settings.csrf_token
    sent = {name: token if value == "TOKEN" else value for name, value in headers.items()}
    assert client.post("/", headers=sent).status_code == status


def test_reading_never_needs_a_token() -> None:
    origins = allowed_origins(8765)
    assert request_problem("GET", {}, "t", origins) is None
    assert request_problem("head", {"origin": "http://evil.example"}, "t", origins) is None
    assert origins == {"http://127.0.0.1:8765", "http://localhost:8765"}


# ---------------------------------------------------------------- views and design


def test_reading_the_workspace_never_creates_the_database(space: Workspace) -> None:
    view = workspace_view(space)
    assert (view.runs, view.database_exists) == (0, False)
    assert not space.database.exists()


def test_key_variables_cover_the_services_that_need_a_key() -> None:
    names = {key.name: key.used_by for key in key_variables()}
    assert names == {"OPENAI_API_KEY": "openai", "OPENROUTER_API_KEY": "openrouter"}


def _tokens(css: str, block: int) -> dict[str, str]:
    blocks = re.findall(r":root[^{]*\{([^}]*)\}", css)
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", blocks[block]))


def test_studio_uses_the_same_colours_as_the_report() -> None:
    css = (Path(server.__file__).parent / "static" / "vespera.css").read_text(encoding="utf-8")
    for block in (0, 1):  # light, then dark
        report, studio = _tokens(TEMPLATE, block), _tokens(css, block)
        assert report and {name: studio[name] for name in report} == report


# ---------------------------------------------------------------- starting Studio


def test_serve_binds_to_this_computer_only_and_opens_the_browser(space: Workspace) -> None:
    calls: dict[str, object] = {}
    opened: list[str] = []
    server.serve(
        space,
        8800,
        True,
        run=lambda app, **options: calls.update(options),
        later=lambda delay, action: action(),
        opener=opened.append,
    )
    assert calls["host"] == "127.0.0.1" and calls["port"] == 8800
    assert calls["proxy_headers"] is False
    assert opened == ["http://127.0.0.1:8800/"]


def test_the_ui_command_prepares_the_workspace_and_starts_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[tuple[Path, int, bool]] = []
    monkeypatch.setattr(server, "serve", lambda space, port, open_it: started.append(
        (space.root, port, open_it)
    ))
    result = CliRunner().invoke(
        cli_app, ["ui", "--workspace", str(tmp_path / "studio"), "--port", "8801", "--no-open"]
    )
    assert result.exit_code == 0, result.output
    assert "Vespera Studio: http://127.0.0.1:8801/" in result.output
    assert started == [((tmp_path / "studio").resolve(), 8801, False)]
    assert (tmp_path / "studio" / "runs").is_dir()


def test_the_ui_command_explains_the_missing_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "vespera.ui.server", None)
    result = CliRunner().invoke(cli_app, ["ui", "--no-open"])
    assert result.exit_code == 1
    assert "Vespera Studio needs extra packages" in result.output
