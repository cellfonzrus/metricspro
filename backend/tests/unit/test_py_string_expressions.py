"""Unit: a source scanner sees F-STRINGS, on whatever Python runs it (§CORE-32).

WHAT WAS WRONG, and it is not about one check. `harness_carrier_vocab_guard` reads every string
expression of a backend source and asks whether a customer could be shown our own setup internals
("is migration 1093 applied?"). It found those strings by walking `tokenize.STRING` tokens.

**PEP 701 (Python 3.12) stopped making an f-string one STRING token.** It is now
`FSTRING_START` / `FSTRING_MIDDLE` / `FSTRING_END` with the interpolations tokenised in between. So
from 3.12 that scanner saw NO f-string at all — and worse than merely failing, it silently lost the
interpolated half of an implicit concatenation, which is the single most common shape a setup hint
is written in:

    raise HTTPException(500, "could not save (is migration "
                             f"{MIG} applied?): {e}")

CI pins 3.11, so the suite stayed green and nobody would have found out until the interpreter moved
— at which point a guard over customer-facing copy would have been reading past every f-string in
the codebase and reporting nothing. A guard that goes quiet is worse than one that goes red.

THE CLASS: **a static reader of Python strings must be told that f-strings exist, and must prove it
on the interpreter it is actually running on.** The reader now has ONE home
(`harnesslib.py_string_expressions`) and these tests run it under whichever Python the suite uses,
so the next tokenisation change fails here rather than going unnoticed.
"""
import sys

from harnesslib import py_string_expressions


def _texts(src):
    return [text for _ln, text in py_string_expressions(src)]


# ── the shape the defect was found in ────────────────────────────────────────────────────────────
def test_an_implicit_concatenation_of_a_literal_and_an_fstring_joins_into_one_text():
    src = ('raise HTTPException(500, "could not save (is migration "\n'
           '                         f"{MIG} applied?): {e}")\n')
    got = _texts(src)
    assert got == ["could not save (is migration 000 applied?): 000"], got


def test_a_bare_fstring_is_seen_at_all():
    """The half of the defect that is pure blindness: on 3.12+ this used to come back empty."""
    assert _texts('msg = f"run migration {MIG} first"') == ["run migration 000 first"]


def test_an_fstring_with_a_format_spec_and_nesting_is_still_one_text():
    src = 'msg = f"migration {n:03d} of {total!r} pending"'
    assert _texts(src) == ["migration 000 of 000 pending"]


def test_the_interpolation_becomes_a_placeholder_and_never_the_expression_text():
    """`000` stands in for the value, so `migration {x}` reads as a migration id — and a variable
    NAME can never be mistaken for copy the customer sees."""
    got = _texts('msg = f"see {SUPABASE_PROJECT_REF} dashboard"')
    assert got == ["see 000 dashboard"]
    assert "SUPABASE_PROJECT_REF" not in got[0]


# ── everything the pre-PEP-701 reader already promised, unchanged ────────────────────────────────
def test_two_plain_literals_still_join():
    src = 'x = ("one part "\n     "and the next")\n'
    assert _texts(src) == ["one part and the next"]


def test_separate_expressions_stay_separate():
    src = 'a = "first thing"\nb = "second thing"\n'
    assert _texts(src) == ["first thing", "second thing"]


def test_a_module_docstring_is_not_an_expression_a_customer_sees():
    src = '"""The module\'s own prose about migration 071."""\nx = "real copy here"\n'
    assert _texts(src) == ["real copy here"]


def test_a_log_line_is_skipped_because_nobody_is_shown_it():
    src = 'print("run migration 071 — operator log")\nx = "shown to a person"\n'
    assert _texts(src) == ["shown to a person"]


def test_a_log_line_written_as_an_fstring_is_skipped_too():
    """The skip is decided from the LINE, so it must hold for the new token shape as well —
    otherwise fixing the blindness would make every f-string log line a false finding."""
    src = 'print(f"run migration {MIG} — operator log")\nx = "shown to a person"\n'
    assert _texts(src) == ["shown to a person"]


def test_a_percent_placeholder_reads_as_a_value_too():
    assert _texts('msg = "run migration %s first" % MIG') == ["run migration 000 first"]


def test_a_comment_is_not_a_string_expression():
    src = '# run migration 068_x.sql first\nx = "copy"\n'
    assert _texts(src) == ["copy"]


def test_unparseable_source_yields_nothing_rather_than_raising():
    """A scanner that raises takes the whole guard down with it; an empty read is the honest answer
    for source that is not Python at all."""
    assert py_string_expressions("def (: this is not python") == []


def test_the_line_number_is_where_the_expression_starts():
    src = 'a = 1\nb = ("hint "\n     f"{MIG}")\n'
    assert [ln for ln, _t in py_string_expressions(src)] == [2]


# ── the version this runs on, stated ─────────────────────────────────────────────────────────────
def test_it_works_on_the_interpreter_running_the_suite():
    """Declared rather than assumed: whichever Python this is, the reader sees an f-string on it.
    Before the fix this test failed on 3.12+ and passed on 3.11, which is exactly the trap —
    CI pins 3.11, so the blindness could not surface there."""
    assert _texts('x = f"migration {MIG}"') == ["migration 000"], (
        f"f-strings are invisible to the reader on Python {sys.version_info.major}."
        f"{sys.version_info.minor} — the PEP 701 token shape is not handled")
