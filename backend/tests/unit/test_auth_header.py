"""An authorization argument read as a string must BE a string.

WRITTEN RED FIRST, 2026-11-04. `person_write_allowed` landed in #645 doing `(authorization or
"").strip()`. FastAPI hands a real request a `str` or `None`, so every HTTP caller was fine — but a
caller that invokes the endpoint function directly gets the DEFAULT, a `fastapi.Header` object,
which is truthy and has no `.strip()`. Three proof harnesses crashed on it the moment they were
wired into CI (`harness_intake_partial_save`, `harness_payroll_row_merge`,
`harness_timeoff_reschedule`), and nothing had been running them.

The fact "what this authorization argument is, as a string" now has ONE home,
`app.modules.core.auth_header.auth_str`. Twelve other modules still carry their own copy of the
same `isinstance(authorization, str)` line — that is stated, not fixed, in this change.
"""
import pytest

pytestmark = pytest.mark.unit


def test_a_string_passes_through():
    from app.modules.core.auth_header import auth_str
    assert auth_str("Bearer abc") == "Bearer abc"


def test_none_becomes_empty():
    from app.modules.core.auth_header import auth_str
    assert auth_str(None) == ""


def test_a_fastapi_header_default_becomes_empty_not_a_crash():
    """THE REGRESSION. A `Header` object is TRUTHY, so `authorization or ""` keeps it and the next
    `.strip()` raises AttributeError — a 500, not a refusal."""
    from fastapi import Header
    from app.modules.core.auth_header import auth_str
    sentinel = Header(None)
    assert bool(sentinel) is True, "if this is falsy the bug cannot happen and this test is moot"
    assert auth_str(sentinel) == ""


def test_any_other_non_string_becomes_empty():
    from app.modules.core.auth_header import auth_str
    for value in (0, 1, [], {}, object()):
        assert auth_str(value) == ""


def test_whitespace_is_not_trimmed_away_silently():
    """auth_str answers WHAT IT IS, not whether it is blank; callers strip when they mean to."""
    from app.modules.core.auth_header import auth_str
    assert auth_str("  Bearer x  ") == "  Bearer x  "


def test_person_write_allowed_does_not_crash_on_the_header_default():
    """The crash site itself, through the real function."""
    from fastapi import Header
    from app.modules.storeops.router import person_write_allowed
    result = person_write_allowed(Header(None), "00000000-0000-0000-0000-000000000001", None)
    assert isinstance(result, bool)
