"""The service layer: the same logic the command line and the web app call, without any I/O."""

import json
from pathlib import Path

import pytest
import typer

from test_agent_runner import Gullible
from test_gate_export import Careful, checked_run
from vespera.cli import _reports_service_errors
from vespera.config import ProjectConfig, TargetConfig
from vespera.gate import GatePolicy, GateStatus
from vespera.services.errors import (
    MissingApiKey,
    ProjectFileError,
    ResultError,
    ServiceError,
    TargetError,
)
from vespera.services.project import (
    DEFAULT_TARGET,
    build_policy,
    export_paths,
    has_rules,
    load_project,
    resolve_target,
)
from vespera.services.results import evaluate, junit_xml, results_json
from vespera.services.targets import api_key_for, api_key_from_env, env_var_is_set
from vespera.storage import Storage

PROJECT = ProjectConfig.model_validate(
    {
        "target": {"type": "openai", "model": "m", "base_url": "https://x/v1"},
        "gate": {"max_attack_rate": 0.2, "zero_tolerance": ["LLM02"], "min_exposure": 0.7},
        "exports": {"results_json": "out/r.json", "junit_xml": "out/j.xml"},
    }
)


# ---------------------------------------------------------------- project


def test_no_project_file_is_fine_and_no_config_ignores_one(tmp_path: Path) -> None:
    assert load_project(None, no_config=False) is None  # the test folder has no vespera.yaml
    (tmp_path / "vespera.yaml").write_text("target: {type: mock}\n", encoding="utf-8")
    assert load_project(tmp_path / "vespera.yaml", no_config=True) is None


def test_a_project_file_is_loaded_and_a_broken_one_is_a_project_error(tmp_path: Path) -> None:
    good = tmp_path / "good.yaml"
    good.write_text("target: {type: mock}\nrepeats: 2\n", encoding="utf-8")
    loaded = load_project(good, no_config=False)
    assert loaded is not None and loaded.repeats == 2
    with pytest.raises(ProjectFileError, match="cannot read"):
        load_project(tmp_path / "missing.yaml", no_config=False)


def test_target_comes_from_the_spec_then_the_project_then_the_default() -> None:
    assert resolve_target(None, None, None, None, None, "mock").type == "mock"
    assert resolve_target(PROJECT, None, None, None, None).model == "m"
    assert resolve_target(None, None, None, None, None) == DEFAULT_TARGET


def test_flags_are_laid_over_the_target() -> None:
    target = resolve_target(PROJECT, "other", None, None, "MY_KEY")
    assert (target.model, target.api_key_env, target.base_url) == ("other", "MY_KEY", "https://x/v1")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"spec": "https://bot.example.com"}, "a web address needs more settings"),
        ({"provider_kind": "http"}, "--provider must be ollama or openai"),
        ({"spec": "mock", "provider_kind": "openai"}, "the flags do not fit the target"),
    ],
)
def test_impossible_targets_are_target_errors(kwargs: dict[str, str], message: str) -> None:
    args = {"model": None, "provider_kind": None, "base_url": None, "api_key_env": None}
    with pytest.raises(TargetError, match=message):
        resolve_target(None, **{**args, **kwargs})  # type: ignore[arg-type]


def test_gate_rules_flags_win_over_the_file_and_the_file_over_defaults() -> None:
    from_file = build_policy(None, None, None, PROJECT)
    assert from_file == GatePolicy(max_attack_rate=0.2, zero_tolerance=("LLM02",), min_exposure=0.7)
    from_flags = build_policy(0.5, "LLM01, pii_leak", 0.1, PROJECT)
    assert from_flags == GatePolicy(0.5, ("LLM01", "pii_leak"), 0.1)
    assert build_policy(None, None, None) == GatePolicy(None, (), 0.5)
    assert has_rules(from_file) and not has_rules(GatePolicy())


def test_export_paths_flags_win_over_the_exports_section() -> None:
    assert export_paths(None, None, Path("j.xml")) == (None, Path("j.xml"))
    assert export_paths(PROJECT, Path("mine.json"), None) == (Path("mine.json"), Path("out/j.xml"))


# ---------------------------------------------------------------- keys


def test_a_key_is_read_from_the_environment_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VESPERA_TEST_KEY", "secret-value")
    assert api_key_from_env(None) is None
    assert api_key_from_env("VESPERA_TEST_KEY") == "secret-value"
    monkeypatch.delenv("VESPERA_TEST_KEY")
    with pytest.raises(MissingApiKey) as error:
        api_key_from_env("VESPERA_TEST_KEY")
    assert error.value.env_name == "VESPERA_TEST_KEY"
    assert str(error.value) == "set the environment variable VESPERA_TEST_KEY with your API key"


def test_a_server_on_this_computer_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VESPERA_TEST_KEY", raising=False)
    assert api_key_for("http://localhost:1234/v1", "VESPERA_TEST_KEY") is None
    assert api_key_for("http://127.0.0.1:11434/v1", "VESPERA_TEST_KEY") is None
    with pytest.raises(MissingApiKey):
        api_key_for("https://api.example.com/v1", "VESPERA_TEST_KEY")


def test_env_var_is_set_says_yes_or_no(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VESPERA_TEST_KEY", "x")
    assert env_var_is_set("VESPERA_TEST_KEY")
    monkeypatch.setenv("VESPERA_TEST_KEY", "")
    assert not env_var_is_set("VESPERA_TEST_KEY")


# ---------------------------------------------------------------- results


@pytest.fixture
def storage(tmp_path: Path):  # type: ignore[no-untyped-def]
    db = Storage(tmp_path / "s.db")
    yield db
    db.close()


def test_evaluate_gives_the_report_data_and_the_gate_decision(storage: Storage) -> None:
    run_id = checked_run(storage, Gullible())
    data, gate = evaluate(storage, run_id, GatePolicy(zero_tolerance=("LLM02",)))
    assert data.run_id == run_id
    assert gate is not None and gate.status is GateStatus.FAILED
    assert evaluate(storage, run_id, None)[1] is None


@pytest.mark.parametrize(
    ("policy", "message"),
    [(None, "no run with id 99"), (GatePolicy(zero_tolerance=("LLM06",)), "LLM06")],
)
def test_a_missing_run_or_a_bad_rule_is_a_result_error(
    storage: Storage, policy: GatePolicy | None, message: str
) -> None:
    run_id = checked_run(storage, Careful()) if policy else 99
    with pytest.raises(ResultError, match=message):
        evaluate(storage, run_id, policy)


def test_exports_are_masked_unless_revealed(storage: Storage) -> None:
    run_id = checked_run(storage, Gullible())
    data, gate = evaluate(storage, run_id, GatePolicy(max_attack_rate=0.5))
    masked = results_json(storage, run_id, data, gate, reveal=False)
    revealed = results_json(storage, run_id, data, gate, reveal=True)
    leaked_phone = "+373 60 000 001"  # what the gullible agent puts into the SMS
    assert json.loads(masked)["schema"] == "vespera.results/1"
    assert leaked_phone not in masked and leaked_phone in revealed
    assert leaked_phone not in junit_xml(storage, run_id, reveal=False)
    assert leaked_phone in junit_xml(storage, run_id, reveal=True)


# ---------------------------------------------------------------- the command line boundary


def test_a_service_error_becomes_a_fail_line_its_tip_and_exit_code_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    @_reports_service_errors
    def command() -> None:
        raise ServiceError("something is wrong", tip="Tip: do this instead.")

    with pytest.raises(typer.Exit) as stop:
        command()
    assert stop.value.exit_code == 1
    assert capsys.readouterr().out == "[FAIL] something is wrong\nTip: do this instead.\n"


def test_the_wrapper_keeps_the_signature_typer_reads() -> None:
    @_reports_service_errors
    def command(name: str, count: int = 2) -> str:
        return name * count

    assert command("ab") == "abab"
    assert command.__name__ == "command"
    assert command.__wrapped__.__annotations__ == {"name": str, "count": int, "return": str}  # type: ignore[attr-defined]


def test_the_default_target_is_a_local_model() -> None:
    assert DEFAULT_TARGET == TargetConfig(type="ollama", model="qwen2.5:7b")
