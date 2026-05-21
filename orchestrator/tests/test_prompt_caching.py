"""
Spec §5.2 — system prompt is cacheable static content.

When PROMPT_CACHING_ENABLED, the system parameter is passed as a single-element
list with cache_control: ephemeral so the Anthropic API caches it. Without
this, repeat executions of the same agent pay the full system-prompt input
cost every call.
"""

from executor import _build_system_param


class _Cfg:
    def __init__(self, caching: bool):
        self.PROMPT_CACHING_ENABLED = caching


def test_caching_off_returns_plain_string():
    """When caching is off, the system parameter is passed as a string
    (matches the Anthropic SDK's simple form)."""
    result = _build_system_param("You are SG.", _Cfg(False))
    assert result == "You are SG."


def test_caching_on_wraps_with_cache_control():
    """When caching is on, the system parameter is a list with one text block
    carrying cache_control: ephemeral."""
    result = _build_system_param("You are SG.", _Cfg(True))
    assert isinstance(result, list)
    assert len(result) == 1
    block = result[0]
    assert block["type"] == "text"
    assert block["text"] == "You are SG."
    assert block["cache_control"] == {"type": "ephemeral"}


def test_caching_default_is_off_when_attr_missing():
    """Defensive — if config doesn't have the attribute, treat as off."""
    class _Bare:
        pass
    result = _build_system_param("You are SG.", _Bare())
    assert result == "You are SG."


def test_empty_prompt_returns_string_even_with_caching_on():
    """Edge — empty system prompt shouldn't be wrapped in a cache_control'd
    block (the API would reject the empty text). Let the SDK error surface
    naturally with the empty string."""
    result = _build_system_param("", _Cfg(True))
    assert result == ""
