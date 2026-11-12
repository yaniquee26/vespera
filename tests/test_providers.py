import pytest
 
from vespera.providers import BaseProvider, MockProvider
 
 
def test_mock_returns_replies_in_order() -> None:
    provider = MockProvider(replies=["first", "second"])
    assert provider.generate("sys", "hello") == "first"
    assert provider.generate("sys", "hello") == "second"
 
 
def test_mock_cycles_when_replies_run_out() -> None:
    provider = MockProvider(replies=["a", "b"])
    assert provider.generate("sys", "1") == "a"
    assert provider.generate("sys", "2") == "b"
    assert provider.generate("sys", "3") == "a"
 
 
def test_mock_without_replies_raises_error() -> None:
    # Without replies, generate() would fail with IndexError or loop endlessly
    # during a test run instead of failing fast at setup.
    with pytest.raises(ValueError):
        MockProvider(replies=[])
 
 
def test_base_provider_cannot_be_created_directly() -> None:
    with pytest.raises(TypeError):
        BaseProvider()  # type: ignore[abstract]
 
 
def test_mock_records_calls() -> None:
    provider = MockProvider(replies=["ok"])
    provider.generate("sys", "hello")
    assert provider.calls == [("sys", "hello")]
 
 
def test_mock_is_not_affected_by_later_changes_to_the_list() -> None:
    replies = ["a", "b"]
    provider = MockProvider(replies=replies)
    replies.append("c")
    assert [provider.generate("s", "u") for _ in range(3)] == ["a", "b", "a"]
 