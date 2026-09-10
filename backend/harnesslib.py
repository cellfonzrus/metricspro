"""Shared source-reading helpers for the static (frontend-asserting) harnesses.

NAMED SO THE SUITE RUNNER DOES NOT COUNT IT. CI and the local sweep run `for f in harness_*.py`; a
module that merely imports cleanly would pass trivially and inflate "N harnesses green" by one. This
is a library, not a proof, so it sits outside that glob.

WHY THIS EXISTS. A harness that proves something about a TSX/TS file has to read that file as TEXT —
neither tsc nor a build can see a screen that renders a wrong WORD. But the comment right above the
fixed line almost always QUOTES THE DEFECT ("this used to read `need > 0 ? … : 'on track'`"), because
that is what a good comment does. A raw substring search then matches the explanation and the harness
fails on a file that is correct.

That has now happened three separate times in this repo (harness_pickup_entered_work's `setSel({})`,
harness_cash_pickup's 11s/11t, harness_target_state's 'on track'), and each time the fix was another
private copy of the same six-line stripper. Three copies of one idea is the duplicate this house calls
a defect, so it lives here once.

A GUARD THAT A TRUTHFUL COMMENT CAN BREAK IS A GUARD THAT GETS DELETED. Read code; let comments say
whatever they need to say.
"""
import re


def js_code_only(js: str) -> str:
    """JS/TS/TSX source with `//` line comments and `/* … */` blocks removed.

    Deliberately simple and deliberately CONSERVATIVE: it strips a `//` comment only when it starts
    the line (leading whitespace allowed), so a `https://…` inside a string literal survives and no
    assertion silently loses the code it meant to read. JSX `{/* … */}` comments are covered by the
    block rule, which is what most of these files use.
    """
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)
