from typer.testing import CliRunner

from vespera import __version__, cli
from vespera.cli import app
from vespera.doctor import CheckStatus, all_required_ok, check_ollama, check_python

runner = CliRunner()


def test_python_new_enough_is_ok() -> None:
    assert check_python((3, 12)).ok


def test_python_too_old_fails() -> None:
    result = check_python((3, 10))
    assert not result.ok
    assert "3.12" in result.detail


def test_ollama_unreachable_is_not_ok_and_not_required() -> None:
    result = check_ollama(url="http://127.0.0.1:9", timeout=0.2)
    assert not result.ok
    assert not result.required


def test_missing_optional_check_does_not_break_required_ok() -> None:
    results = [
        CheckStatus("python", True, "ok", required=True),
        CheckStatus("ollama", False, "down", required=False),
    ]
    assert all_required_ok(results)


def test_failed_required_check_breaks_required_ok() -> None:
    results = [CheckStatus("python", False, "old", required=True)]
    assert not all_required_ok(results)


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_doctor_exit_code_zero_when_only_optional_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "run_all",
        lambda: [
            CheckStatus("python", True, "Python 3.13", required=True),
            CheckStatus("ollama", False, "down", required=False),
        ],
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "[OK] python" in result.output
    assert "[WARN] ollama" in result.output


def test_doctor_exit_code_one_when_required_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        cli, "run_all", lambda: [CheckStatus("python", False, "old", required=True)]
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] python" in result.output