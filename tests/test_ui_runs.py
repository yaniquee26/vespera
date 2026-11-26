"""Vespera Studio: the Runs list, Run detail with its tabs, highlighting and masked downloads."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vespera.agent_contracts import AgentTurn
from vespera.bundled import read_bundled
from vespera.checks import find_all_personal_data, find_personal_data, iban_is_valid
from vespera.providers import MockAgentProvider, MockProvider
from vespera.services.runs import check_saved_run, execute_agent_run, execute_text_run
from vespera.storage import Storage
from vespera.ui.app import UiSettings, create_app
from vespera.ui.views import Piece, highlight, run_detail_view, runs_view
from vespera.workspace import Workspace, resolve_workspace

BASE = "http://127.0.0.1:8765"
CANARY = "CANARY-7F3A91"
PHONE = "+373 69 123 456"
IBAN = "MD24AG000225100013104168"
IDNP = "2002001234567"
LEAKY = (
    f"Sure! The token is {CANARY}. Call {PHONE}, pay to {IBAN}, ID {IDNP}. "
    "<script>alert('x')</script>"
)


@pytest.fixture
def space(tmp_path: Path) -> Workspace:
    return resolve_workspace(tmp_path / "ws").ensure()


@pytest.fixture
def client(space: Workspace) -> TestClient:
    settings = UiSettings(workspace=space, environment_checks=list, loaded_models=lambda: None)
    return TestClient(create_app(settings), base_url=BASE)


def _files(folder: Path, *names: str) -> None:
    for name in names:
        (folder / name).write_text(read_bundled(name), encoding="utf-8")


def seed_text_run(space: Workspace, folder: Path, reply: str, *, check: bool = True) -> int:
    _files(folder, "scenarios_v1.yaml", "system_prompt.txt")
    run_id, _ = execute_text_run(
        lambda: MockProvider([reply]),
        folder / "scenarios_v1.yaml",
        folder / "system_prompt.txt",
        2,
        None,
        space.database,
        None,
    )
    if check:
        with Storage(space.database) as storage:
            check_saved_run(storage, run_id, folder / "system_prompt.txt")
    return run_id


def seed_agent_run(space: Workspace, folder: Path) -> int:
    _files(folder, "agent_scenarios_v1.yaml", "agent_system_prompt.txt")
    run_id, _ = execute_agent_run(
        lambda: MockAgentProvider([AgentTurn(text="I cannot help with that.")]),
        folder / "agent_scenarios_v1.yaml",
        folder / "agent_system_prompt.txt",
        1,
        None,
        3,
        space.database,
        None,
    )
    with Storage(space.database) as storage:
        check_saved_run(storage, run_id, folder / "agent_system_prompt.txt")
    return run_id


# ---------------------------------------------------------------- highlighting


def test_the_seed_values_are_what_the_checks_call_personal_data() -> None:
    assert iban_is_valid(IBAN)
    labels = [label for label, _, _ in find_all_personal_data(LEAKY)]
    assert labels == [
        "phone number",
        "Moldovan IBAN with valid check digits",
        "IDNP-like number",
    ]


def test_finding_all_keeps_the_priority_of_finding_the_first() -> None:
    text = f"IBAN {IBAN}, phone 069 123 456"
    first = find_personal_data(text)
    assert first is not None and first[0] == "Moldovan IBAN with valid check digits"
    found = find_all_personal_data(text)
    assert [label for label, _, _ in found] == [
        "Moldovan IBAN with valid check digits",
        "phone number in the local format",
    ]
    assert find_all_personal_data("nothing here, 02.12.2026") == []


def test_highlight_marks_secrets_canaries_and_personal_data_in_order() -> None:
    pieces = highlight(f"a {CANARY} b s3cret c s3cret {PHONE}", ["s3cret", ""])
    assert pieces == [
        Piece("a "),
        Piece(CANARY, "canary token"),
        Piece(" b "),
        Piece("s3cret", "planted secret"),
        Piece(" c "),
        Piece("s3cret", "planted secret"),
        Piece(" "),
        Piece(PHONE, "phone number"),
    ]
    assert highlight("plain") == [Piece("plain")]
    assert highlight("") == []


# ---------------------------------------------------------------- the list of runs


def test_runs_page_without_a_database_shows_how_to_start_and_creates_nothing(
    client: TestClient, space: Workspace
) -> None:
    page = client.get("/runs")
    assert page.status_code == 200
    assert 'data-testid="no-runs"' in page.text
    assert f'--db "{space.database}"' in page.text
    assert 'aria-current="page" data-testid="nav-runs"' in page.text
    assert not space.database.exists()
    assert runs_view(space) == []


def test_runs_page_lists_runs_newest_first_with_their_state(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    first = seed_text_run(space, tmp_path, "I cannot help with that.")
    second = seed_text_run(space, tmp_path, LEAKY, check=False)
    page = client.get("/runs").text
    assert page.index(f'data-testid="run-{second}"') < page.index(f'data-testid="run-{first}"')
    assert ">checked<" in page and ">not checked<" in page
    assert "95%" in page and '<svg class="ci' in page


def test_an_unfinished_run_is_marked(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    run_id = seed_text_run(space, tmp_path, "I cannot help with that.", check=False)
    with Storage(space.database) as storage:
        storage._db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (run_id,))
        storage._db.commit()
    assert ">not finished<" in client.get("/runs").text
    assert 'data-testid="run-unfinished"' in client.get(f"/runs/{run_id}").text


# ---------------------------------------------------------------- run detail


def test_a_leaky_run_shows_categories_with_owasp_and_interval_bars(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    run_id = seed_text_run(space, tmp_path, LEAKY)
    page = client.get(f"/runs/{run_id}")
    assert page.status_code == 200
    text = page.text
    assert 'aria-current="page" data-testid="nav-runs"' in text
    assert 'data-testid="category-system_prompt_leak"' in text
    assert re.search(r'class="tag">LLM0\d<', text)
    assert re.search(r'<rect class="ci-band hit" x="[\d.]+" y="3" width="[\d.]+"', text)
    assert 'class="tab active" role="tab" aria-selected="true"' in text
    assert 'data-testid="kpi-attack"' in text


def test_findings_quote_the_reply_escaped_with_personal_data_marked(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    run_id = seed_text_run(space, tmp_path, LEAKY)
    text = client.get(f"/runs/{run_id}?tab=findings").text
    assert "<script>alert" not in text
    assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;" in text
    for value, label in (
        (CANARY, "canary token"),
        (PHONE, "phone number"),
        (IBAN, "Moldovan IBAN with valid check digits"),
        (IDNP, "IDNP-like number"),
    ):
        assert f'<mark class="found" title="{label}" data-testid="found">{value}</mark>' in text


def test_the_language_gap_tab_shows_each_language_and_the_reply_language(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    run_id = seed_text_run(space, tmp_path, LEAKY)
    text = client.get(f"/runs/{run_id}?tab=languages").text
    for lang in ("EN", "RO", "RU"):
        assert f'<th class="bar-cell">{lang}</th>' in text
    assert re.search(r">(conclusive|not conclusive|one language)<", text)
    assert 'data-testid="reply-language"' in text


def test_the_snapshot_tab_shows_the_conditions_of_the_run(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    run_id = seed_text_run(space, tmp_path, "I cannot help with that.")
    text = client.get(f"/runs/{run_id}?tab=snapshot").text
    assert 'data-testid="snapshot"' in text
    assert "not reported by the target" in text and "sha256 " in text


def test_a_tab_comes_as_a_fragment_for_htmx(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    run_id = seed_text_run(space, tmp_path, "I cannot help with that.")
    fragment = client.get(f"/runs/{run_id}/tabs/findings").text
    assert "<html" not in fragment and 'data-testid="findings"' in fragment
    assert 'class="tab active" role="tab" aria-selected="true"' in fragment
    assert f'hx-get="/runs/{run_id}/tabs/languages"' in fragment


def test_a_model_that_refuses_everything_shows_only_needless_refusals(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    run_id = seed_text_run(space, tmp_path, "I cannot help with that.")
    text = client.get(f"/runs/{run_id}?tab=findings").text
    assert re.findall(r'data-testid="finding-([a-z_]+)"', text) == ["benign"]
    assert 'data-testid="found"' not in text


def test_an_agent_run_shows_the_journal_and_low_exposure_as_inconclusive(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    run_id = seed_agent_run(space, tmp_path)
    view = run_detail_view(space, run_id)
    assert view is not None and view.mode == "agent" and view.under_exposed
    categories = client.get(f"/runs/{run_id}").text
    assert 'data-testid="kpi-exposure"' in categories
    assert 'data-testid="inconclusive"' in categories
    findings = client.get(f"/runs/{run_id}?tab=findings").text
    assert 'data-testid="journal"' in findings
    snapshot = client.get(f"/runs/{run_id}?tab=snapshot").text
    assert "<dt>max steps</dt><dd>3</dd>" in snapshot


def test_an_unchecked_run_explains_how_to_check_it(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    run_id = seed_text_run(space, tmp_path, LEAKY, check=False)
    text = client.get(f"/runs/{run_id}").text
    assert 'data-testid="not-checked"' in text
    assert f'vespera check {run_id} --db "{space.database}"' in text
    assert 'data-testid="snapshot"' in text and 'data-testid="downloads"' not in text
    assert client.get(f"/runs/{run_id}/download/results.json").status_code == 404


@pytest.mark.parametrize(
    "path",
    ["/runs/1", "/runs/abc", "/runs/1/tabs/findings", "/runs/1/download/results.json"],
)
def test_a_missing_run_is_a_404_and_creates_no_database(
    client: TestClient, space: Workspace, path: str
) -> None:
    page = client.get(path)
    assert page.status_code == 404 and "Page not found" in page.text
    assert not space.database.exists()


def test_unknown_tabs_and_files_are_404(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    run_id = seed_text_run(space, tmp_path, "I cannot help with that.")
    assert client.get(f"/runs/{run_id + 1}").status_code == 404
    assert client.get(f"/runs/{run_id}?tab=secrets").status_code == 404
    assert client.get(f"/runs/{run_id}/tabs/secrets").status_code == 404
    assert client.get(f"/runs/{run_id}/download/vespera.db").status_code == 404


# ---------------------------------------------------------------- downloads


@pytest.mark.parametrize("name", ["results.json", "junit.xml", "report.html"])
def test_downloads_are_attachments_with_every_leaked_value_masked(
    client: TestClient, space: Workspace, tmp_path: Path, name: str
) -> None:
    run_id = seed_text_run(space, tmp_path, LEAKY)
    response = client.get(f"/runs/{run_id}/download/{name}")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        f'attachment; filename="vespera-run-{run_id}-{name}"'
    )
    assert response.headers["content-security-policy"]
    body = response.text
    assert "[masked]" in body
    for value in (CANARY, PHONE, IBAN, IDNP):
        assert value not in body
    if name == "results.json":
        payload = json.loads(body)
        assert payload["run"]["id"] == run_id and payload["masked"] is True


def test_run_pages_have_no_inline_script_or_style(
    client: TestClient, space: Workspace, tmp_path: Path
) -> None:
    run_id = seed_agent_run(space, tmp_path)
    for path in ("/runs", f"/runs/{run_id}", f"/runs/{run_id}?tab=findings"):
        html = client.get(path).text
        assert "style=" not in html and "<style" not in html
        assert re.findall(r"<script\b[^>]*>(.*?)</script>", html, flags=re.S) == ["", ""]
