"""The exit-code contract that CI relies on.

0 success or gate passed · 1 the command was used wrongly or could not run · 2 gate failed, and
nothing else · 3 gate inconclusive · 130 stopped with Ctrl+C during a run.
"""

import pytest
import typer
from typer.testing import CliRunner

from vespera import cli
from vespera.doctor import CheckStatus
from vespera.providers import MockProvider
from vespera.services import targets

app = cli.app
runner = CliRunner()
SMALL = ["--no-config", "--repeats", "1", "--limit", "3"]


def _project() -> None:
    assert runner.invoke(app, ["init", "--agents", "--no-interactive"]).exit_code == 0


# ---------------------------------------------------------------- usage errors: 1
#
# The wording of these errors belongs to Click and changes between versions, so the tests check
# the kind of error and the parameter it names, never the sentence.

USAGE_CASES = [
    (["nosuchcommand"], None, None),  # UsageError, or its subclass NoSuchCommand in Click 8.5
    (["test", "--bogus"], "NoSuchOption", None),
    (["gate", "notanumber"], "BadParameter", "run_id"),
    (["gate", "--max-attack-rate", "2"], "BadParameter", "max_attack_rate"),
    (["compare"], "MissingParameter", "run_a"),
    (["compare", "1"], "MissingParameter", "run_b"),
]


@pytest.mark.parametrize(("args", "kind", "param"), USAGE_CASES)
def test_a_usage_error_has_its_kind_and_exit_code_1(
    args: list[str], kind: str | None, param: str | None
) -> None:
    command = typer.main.get_command(app)
    with pytest.raises(cli.USAGE_ERROR) as caught:
        command.main(args, prog_name="vespera", standalone_mode=False)
    error = caught.value
    if kind is not None:
        assert type(error).__name__ == kind
    assert error.exit_code == 1  # type: ignore[attr-defined]
    if param is not None:
        assert error.param.name == param  # type: ignore[attr-defined]
    if kind == "NoSuchOption":
        assert error.option_name == "--bogus"  # type: ignore[attr-defined]


@pytest.mark.parametrize(("args", "kind", "param"), USAGE_CASES)
def test_a_usage_error_exits_with_1_and_prints_the_usage(
    args: list[str], kind: str | None, param: str | None
) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 1, result.output
    assert "Usage: " in result.output


def test_no_command_shows_the_help_and_exits_with_1() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "Evidence-based reliability testing for LLMs." in result.output
    assert "Commands" in result.output and "test" in result.output


@pytest.mark.parametrize("args", [["--help"], ["test", "--help"], ["version"]])
def test_help_and_version_still_exit_with_0(args: list[str]) -> None:
    assert runner.invoke(app, args).exit_code == 0


def test_the_usage_error_class_is_the_one_the_parser_raises() -> None:
    # public in every Typer version: BadParameter derives from the parser's UsageError
    assert issubclass(typer.BadParameter, cli.USAGE_ERROR)
    assert cli.USAGE_ERROR.__name__ == "UsageError"


def test_an_old_click_help_exit_for_a_bare_call_becomes_1() -> None:
    """Click before 8.2 printed the help for a bare call and exited with 0 instead of raising."""

    class OldClickGroup(cli._VesperaGroup):  # noqa: SLF001
        def parse_args(self, ctx: object, args: list[str]) -> list[str]:
            if not args:
                raise typer.Exit(0)
            return super().parse_args(ctx, args)  # type: ignore[arg-type]

    group = OldClickGroup(name="vespera")
    with pytest.raises(typer.Exit) as bare:
        group.make_context("vespera", [])
    assert bare.value.exit_code == 1


def test_an_old_click_help_exit_after_options_stays_0() -> None:
    class HelpGroup(cli._VesperaGroup):  # noqa: SLF001
        def parse_args(self, ctx: object, args: list[str]) -> list[str]:
            args.clear()  # the parser consumes the list, as Click does
            raise typer.Exit(0)

    with pytest.raises(typer.Exit) as helped:
        HelpGroup(name="vespera").make_context("vespera", ["--help"])
    assert helped.value.exit_code == 0


# ---------------------------------------------------------------- Vespera's own errors: 1


@pytest.mark.parametrize(
    "args",
    [
        ["gate", "--no-config"],  # no rule
        ["test", "mock", "--no-config", "--mode", "bogus"],
        ["test", "mock", "--no-config"],  # files missing
        ["gate", "--max-attack-rate", "0.3", "--no-config"],  # no such run
    ],
)
def test_a_fail_line_exits_with_1(args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "[FAIL] " in result.output


def test_doctor_with_a_failed_required_check_exits_with_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli, "run_all", lambda: [CheckStatus("python", False, "3.11 is too old", True)]
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] python: 3.11 is too old" in result.output


# ---------------------------------------------------------------- the gate: 0, 2, 3


def test_passed_and_inconclusive_keep_their_codes() -> None:
    _project()
    passed = runner.invoke(app, ["test", "mock", *SMALL, "--max-attack-rate", "0.3"])
    assert passed.exit_code == 0 and "Gate: passed" in passed.output
    inconclusive = runner.invoke(
        app, ["test", "mock", *SMALL, "--mode", "agent", "--max-attack-rate", "0.3"]
    )
    assert inconclusive.exit_code == 3 and "Gate: inconclusive" in inconclusive.output


def test_a_failed_gate_exits_with_2(monkeypatch: pytest.MonkeyPatch) -> None:
    _project()
    monkeypatch.setattr(
        targets, "MockProvider", lambda replies: MockProvider(["PWNED CANARY-7F3A91 HACKED"])
    )
    result = runner.invoke(app, ["test", "mock", *SMALL, "--max-attack-rate", "0.1"])
    assert result.exit_code == 2, result.output
    assert "Gate: failed" in result.output
    again = runner.invoke(app, ["gate", "1", "--no-config", "--zero-tolerance", "LLM01"])
    assert again.exit_code == 2, again.output  # the same decision from `vespera gate`


# ---------------------------------------------------------------- Ctrl+C: 130


class Interrupted(MockProvider):
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        raise KeyboardInterrupt


def test_ctrl_c_during_a_run_exits_with_130(monkeypatch: pytest.MonkeyPatch) -> None:
    _project()
    monkeypatch.setattr(targets, "MockProvider", Interrupted)
    result = runner.invoke(app, ["test", "mock", *SMALL])
    assert result.exit_code == 130
    listed = runner.invoke(app, ["runs"])
    assert "(0/3 done)" in listed.output  # the run stays unfinished; resume can fill it
