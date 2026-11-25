from pathlib import Path

from typer.testing import CliRunner

from vespera.bundled import AGENT_FILES, BUNDLED_FILES, read_bundled
from vespera.cli import app

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"


def test_bundled_examples_match_the_files_in_examples() -> None:
    for name in (*BUNDLED_FILES, *AGENT_FILES):
        assert read_bundled(name) == (EXAMPLES / name).read_text(encoding="utf-8"), name


def test_the_repo_root_holds_no_stray_example_files() -> None:
    for name in (*BUNDLED_FILES, *AGENT_FILES):
        assert not (REPO / name).exists(), name


def test_unknown_bundled_file_is_refused() -> None:
    import pytest

    with pytest.raises(ValueError):
        read_bundled("secrets.txt")


def test_init_writes_examples_and_does_not_overwrite_without_force(tmp_path: Path) -> None:
    runner = CliRunner()
    target = tmp_path / "bot"
    first = runner.invoke(app, ["init", "--directory", str(target)])
    assert first.exit_code == 0, first.output
    assert (target / "scenarios_v1.yaml").exists()
    assert (target / "system_prompt.txt").exists()
    (target / "system_prompt.txt").write_text("my own prompt", encoding="utf-8")
    second = runner.invoke(app, ["init", "--directory", str(target)])
    assert second.exit_code == 1
    assert (target / "system_prompt.txt").read_text(encoding="utf-8") == "my own prompt"
    forced = runner.invoke(app, ["init", "--directory", str(target), "--force"])
    assert forced.exit_code == 0
    assert (target / "system_prompt.txt").read_text(encoding="utf-8") != "my own prompt"


def test_files_from_init_are_valid_input_for_validate(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["init", "--directory", str(tmp_path)])
    result = runner.invoke(app, ["validate", str(tmp_path / "scenarios_v1.yaml")])
    assert result.exit_code == 0, result.output


def mock_test_args(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "test",
        "--mock",
        "--repeats",
        "2",
        "--db",
        str(tmp_path / "t.db"),
        "--out",
        str(tmp_path / "r.html"),
        "--scenarios",
        str(EXAMPLES / "scenarios_v1.yaml"),
        "--system-prompt",
        str(EXAMPLES / "system_prompt.txt"),
        *extra,
    ]


def test_test_command_runs_checks_and_writes_the_report(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, mock_test_args(tmp_path))
    assert result.exit_code == 0, result.output
    assert "[OK] report written" in result.output
    assert (tmp_path / "r.html").exists()


def test_the_gate_passes_when_the_bot_holds_and_fails_when_it_does_not(tmp_path: Path) -> None:
    runner = CliRunner()
    # the mock always refuses: no attack succeeds, so even a strict limit passes
    ok = runner.invoke(app, mock_test_args(tmp_path, "--max-attack-rate", "0.05"))
    assert ok.exit_code == 0, ok.output
    assert "within the allowed 5%" in ok.output


def test_the_gate_exits_with_code_2_above_the_limit(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from vespera import cli
    from vespera.providers import MockProvider

    monkeypatch.setattr(
        cli, "MockProvider", lambda replies: MockProvider(["PWNED CANARY-7F3A91 HACKED"])
    )
    result = CliRunner().invoke(app, mock_test_args(tmp_path, "--max-attack-rate", "0.1"))
    assert result.exit_code == 2, result.output
    assert "above the allowed 10%" in result.output


def test_the_gate_rejects_values_outside_zero_to_one(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, mock_test_args(tmp_path, "--max-attack-rate", "5"))
    assert result.exit_code != 0