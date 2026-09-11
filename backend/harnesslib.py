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


# ── COA NO-MOVEMENT: what a "coa.py is byte-identical" guard was really protecting ────────────────
# Several proof harnesses pinned `account/coa.py` byte-identical to the branch point. That was a
# PROXY for the claim that actually matters — *this work did not re-attribute booked money* — and it
# held only while nothing else legitimately edited the file. On 2026-09-11 something did: the owner
# ruled a distributor chargeback is an expense ("159106.76 is an expense"), which books in
# `build_inputs`. A proxy that has become wrong gets switched off or ignored, which is the one thing
# a money guard must never be, so it is re-expressed here as the real claim and made STRICTER in the
# part that counts: the resolver and attribution functions must be byte-identical, and the set of
# functions that changed at all must be exactly the sanctioned set. Nothing unexplained may move.
COA_ATTRIBUTION_FUNCS = (
    "store_resolver", "build_company_matcher", "company_assignment", "store_company_map",
    "org_companies", "store_code_to_address", "_norm_store", "_squash_key", "_lead_num_key",
)


def function_sources(src):
    """{qualified name: exact source text} for every function in a Python module source."""
    import ast
    out, lines = {}, src.split("\n")

    def walk(node, prefix=""):
        for child in getattr(node, "body", []):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                seg = "\n".join(lines[child.lineno - 1:child.end_lineno])
                out[name] = seg
                walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")

    walk(ast.parse(src))
    return out


def coa_movement(base_src, now_src, sanctioned=()):
    """Compare two revisions of coa.py. Returns (removed, changed_outside_sanction, attribution_moved).

    All three must be empty for the no-movement claim to hold. `sanctioned` names the functions a
    given package is allowed to have edited — everything else, including every resolver, must be
    identical."""
    base, now = function_sources(base_src), function_sources(now_src)
    removed = sorted(set(base) - set(now))
    changed = {k for k in set(base) & set(now) if base[k] != now[k]}
    outside = sorted(changed - set(sanctioned))
    moved = sorted(f for f in COA_ATTRIBUTION_FUNCS
                   if f in base and f in now and base[f] != now[f])
    return removed, outside, moved
