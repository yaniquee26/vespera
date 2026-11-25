"""The registries must describe everything the code can do, and nothing it cannot."""

import typing

import pytest

from vespera.agent_contracts import AGENT_CHECKS, AgentCategory
from vespera.bundled import read_bundled
from vespera.catalog import (
    ATTACKS,
    CHECK_CATALOG,
    FIELD_HELP,
    PACKS,
    TARGET_KINDS,
    attack_guide,
    check_info,
    pack,
    pack_overview,
    target_kind,
)
from vespera.checks import CHECKS, find_canary
from vespera.config import TargetConfig
from vespera.contracts import KNOWN_CHECKS, Category, Language

MEASUREMENTS = {"answer_language", "hidden_order_delivered", "tool_call_as_text"}


def test_every_check_in_the_code_has_exactly_one_description() -> None:
    names = [info.name for info in CHECK_CATALOG]
    assert len(names) == len(set(names))
    assert set(names) == set(CHECKS)


def test_checks_are_filed_under_the_right_mode_and_kind() -> None:
    for info in CHECK_CATALOG:
        expected_mode = "agent" if info.name in AGENT_CHECKS else "text"
        assert info.mode == expected_mode, info.name
        assert info.name in KNOWN_CHECKS | AGENT_CHECKS
        assert info.verdict is (info.name not in MEASUREMENTS), info.name
        assert all((info.catches, info.decides, info.false_alarms, info.misses)), info.name


def test_every_category_has_a_guide_with_the_right_mode() -> None:
    assert [guide.category for guide in ATTACKS] == list(Category)
    agent_values = {category.value for category in AgentCategory}
    for guide in ATTACKS:
        assert guide.mode == ("agent" if guide.category.value in agent_values else "text")
        assert guide.attack and guide.failure


def test_target_kinds_match_the_target_types_and_fields_of_the_config() -> None:
    types = typing.get_args(TargetConfig.model_fields["type"].annotation)
    assert [kind.key for kind in TARGET_KINDS] == list(types)
    for kind in TARGET_KINDS:
        assert set(kind.fields) <= set(TargetConfig.model_fields), kind.key
        assert set(kind.fields) <= set(FIELD_HELP), kind.key
    assert set(FIELD_HELP) <= set(TargetConfig.model_fields)


def test_only_the_http_target_cannot_run_agent_tests() -> None:
    assert [kind.key for kind in TARGET_KINDS if not kind.agent_mode] == ["http"]


@pytest.mark.parametrize("item", PACKS, ids=lambda item: item.key)
def test_every_pack_loads_and_its_prompt_has_a_canary(item: object) -> None:
    overview = pack_overview(item)  # type: ignore[arg-type]
    assert overview.scenarios > 0
    assert overview.cases == overview.scenarios * len(Language)
    assert sum(overview.categories.values()) == overview.scenarios
    prompt = read_bundled(overview.pack.system_prompt_file)
    assert find_canary(prompt) is not None


def test_pack_numbers_match_their_descriptions() -> None:
    text, agent = pack_overview(pack("core-text-v1")), pack_overview(pack("core-agent-v1"))
    assert (text.scenarios, text.cases) == (10, 30)
    assert "Ten traps" in text.pack.summary
    assert (agent.scenarios, agent.cases) == (6, 18)
    assert "Six tasks" in agent.pack.summary
    assert agent.categories[Category.TOOL_OUTPUT_INJECTION] == 2


def test_lookups_find_entries_and_refuse_unknown_names() -> None:
    assert target_kind("http").agent_mode is False
    assert check_info("pii_not_leaked").verdict
    assert attack_guide(Category.PII_LEAK).mode == "text"
    assert pack("core-agent-v1").mode == "agent"
    for lookup, name in (
        (target_kind, "ftp"),
        (check_info, "telepathy"),
        (pack, "nope"),
    ):
        with pytest.raises(KeyError):
            lookup(name)  # type: ignore[operator]
