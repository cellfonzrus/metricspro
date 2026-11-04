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


def py_code_only(src: str) -> str:
    """Python source with `#` comments and every docstring / triple-quoted block removed.

    THE SAME REASON `js_code_only` exists, on the Python side, and it bit immediately: a lock asserting
    "the `else 0` expression is gone from the sweep" failed on the replacement function's own docstring,
    which QUOTES the retired expression so a reader knows what was fixed. A guard a truthful comment can
    break is a guard that gets deleted — so locks read CODE.

    Conservative, like its JS sibling: triple-quoted blocks go first (that covers docstrings wherever
    they sit), then `#` comments, and only when the `#` starts the line so a `#` inside a surviving
    string literal cannot silently remove real code.
    """
    src = re.sub(re.escape('"""') + r".*?" + re.escape('"""'), "", src, flags=re.S)
    src = re.sub(re.escape("'''") + r".*?" + re.escape("'''"), "", src, flags=re.S)
    return re.sub(r"^\s*#.*$", "", src, flags=re.M)


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


# THE §64 SANCTION (owner directive 2026-10-09, "chase trhew street number matching"). The store
# RESOLUTION CHAIN moved OUT of coa.py into its one home, `app/modules/account/store_identity.py`:
# `store_resolver` is now only the I/O wrapper that reads `store_mapping` + `store_aliases` and
# hands them to `build_store_resolver`, and `_squash_key` / `_lead_num_key` delegate instead of
# holding copies. The two nested helpers inside the old `store_resolver` are therefore gone.
#
# A caller that includes this sanction is saying: "the chain moving to its home does not move MY
# number." That claim was MEASURED before it was made — old chain vs new, over every distinct live
# store string in raw_sales / raw_payment_detail / raw_comp_report / rep_commissions /
# store_mapping / store_aliases / store_expenses: RE-MEASURED live 2026-10-09 at merge time, house
# org 1 divergence of 92 — the reported relocation defect, `'2778 Mt Ephraim Ave Camden, NJ 08104'`
# resolving to `'1598 Mount Ephraim Ave'` instead of to itself — and the other three tenants 0 of 43.
# (An earlier run read 1 of 71 and 0 of 49: the feeds carry more spellings now, the divergence set is
# the same single string.) What the chain now IS, and that coa keeps no second copy of it,
# is locked by `harness_store_identity_lock.py`.
COA_STORE_IDENTITY_SANCTION = (
    "store_resolver", "_squash_key", "_lead_num_key",
    "store_resolver._num_key", "store_resolver.resolve",      # nested, removed with the chain
    # EXTENDED 2026-10-10 (§70). The identity I/O twin that answers "may these two addresses be one
    # store" for the per-device reconciliation. `ambiguous_store_keys` was its first shape and
    # tested the wrong fact — a per-ADDRESS ambiguity flag, which withheld 23 true findings because
    # the resolver already folds both codes of one address onto one key — so it is REMOVED and
    # `shared_store_identity_pairs` (a PAIR relation, derived in the one identity home) replaces it.
    # Both names are sanctioned here because this is the §64 chain moving again, in the same module,
    # for the same reason. Every resolver and attribution function stays pinned byte-identical.
    "ambiguous_store_keys", "shared_store_identity_pairs",
)

#: SANCTIONED 2026-10-12 (index §14y.1, mig 1076, owner ruling *"Payroll from clack in should be
#: from Oct 4 th onwards which would be the new cycle"*). THE P&L WAGE LINE NOW READS THE PUNCHES.
#:
#: Why these three functions changed, and why it is a FIX and not a drift. `wages_by_store` called
#: `payroll_hours_basis.resolve_shift_row` with NO evidence, so the one home could only answer from
#: `shifts.actual_hours` — the dead column (7 of 7,740 live rows). Under the house org's live
#: `clocked_only` + `pay_from_schedule_default = false` config, every shift row therefore resolved
#: `not_measured_unpaid` = ZERO and the wage line booked $374,733.02 -> $0.00 across Jun–Oct 2026
#: (measured on live data 2026-10-12) while the punches sat unread in `storeops.timelog`.
#:
#: `wages_by_store` now reads the period's punches + corrections through the ONE pay-path read
#: (`storeops/payroll_evidence.period_evidence`), consults `payroll_hours_basis.interlock`, and
#: RAISES `EvidenceRefused` rather than returning a dict of zeros when the period may not be paid
#: from. `derive_wage_cells` / `derive_wage_cells_with_basis` take that evidence and ask the one
#: home for the DAY's hours, allocated back across that day's shift rows in the proportions those
#: rows already had.
#:
#: ★ EVERY STORE AND COMPANY RESOLVER IN coa.py IS STILL PINNED BYTE-IDENTICAL. ★ Nothing here
#: changes WHICH store or WHICH company money books to — only HOW MANY HOURS a day is worth, which
#: is `payroll_hours_basis`'s decision and not coa's. Locked by
#: `backend/harness_payroll_hours_basis_lock.py` §H (a caller that stops passing evidence, or stops
#: consulting the interlock, fails the build) and proven by `harness_payroll_hours_basis.py` §P/§Q.
COA_PAID_HOURS_EVIDENCE_SANCTION = (
    "wages_by_store", "derive_wage_cells", "derive_wage_cells_with_basis",
)


def coa_movement(base_src, now_src, sanctioned=()):
    """Compare two revisions of coa.py. Returns (removed, changed_outside_sanction, attribution_moved).

    All three must be empty for the no-movement claim to hold. `sanctioned` names the functions a
    given package is allowed to have edited (or, since the §64 chain move, to have REMOVED when the
    name is in that package's sanction) — everything else, including every resolver, must be
    identical."""
    base, now = function_sources(base_src), function_sources(now_src)
    sanctioned = set(sanctioned)
    removed = sorted((set(base) - set(now)) - sanctioned)
    changed = {k for k in set(base) & set(now) if base[k] != now[k]}
    outside = sorted(changed - sanctioned)
    moved = sorted(f for f in COA_ATTRIBUTION_FUNCS
                   if f not in sanctioned and f in base and f in now and base[f] != now[f])
    return removed, outside, moved


# ── THE STRINGS OF A PYTHON SOURCE — ONE HOME, F-STRINGS INCLUDED (§CORE-32) ─────────────────────
# WHY THIS IS HERE. Two readers of "the string expressions of a Python source" lived in
# `harness_carrier_vocab_guard.py`, 500 lines apart. **PEP 701 (Python 3.12) stopped making an
# f-string one `tokenize.STRING` token** — it is `FSTRING_START` / `FSTRING_MIDDLE` / `FSTRING_END`
# with the interpolations tokenised between them. One of those two readers had been taught the new
# shape; the other had not, so on 3.12+ it saw NO f-string at all, and silently dropped the
# interpolated half of the commonest shape a setup hint takes:
#
#     raise HTTPException(500, "could not save (is migration "
#                              f"{MIG} applied?): {e}")
#
# CI pins 3.11, so the suite stayed green: a guard over customer-facing copy was one interpreter
# upgrade away from reading past every f-string in the codebase and reporting nothing. One fact, one
# home, dereferenced — and the same fact had already been FIXED in one of the two copies, which is
# the house's own §19.18 defect in its most expensive direction.
#
# VERSION-INDEPENDENT BY CONTRACT: an f-string yields ONE text on every supported interpreter, its
# interpolations reduced to `{}` (`py_string_parts`) or to the `placeholder` (`py_string_expressions`).
# A caller never branches on the Python version, and `tests/unit/test_py_string_expressions.py`
# proves the reader on whichever interpreter runs the suite.

_LOG_CALL = re.compile(r"\bprint\s*\(|\blog(?:ger)?\.(?:info|warning|warn|error|debug|exception)\s*\(")
_BRACE_OR_PERCENT = re.compile(r"\{[^{}]*\}|%[sdr]")


def strip_interpolations(text: str) -> str:
    """`'{a} vs {f((b or {}))}'` → `'{} vs {}'` — brace-depth aware, so a nested expression is
    dropped whole and a variable NAME can never be mistaken for copy a customer is shown."""
    out, depth = [], 0
    for ch in text:
        if ch == "{":
            if depth == 0:
                out.append("{")
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                out.append("}")
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _string_runs(src):
    """[[(line, text), …], …] — one inner list per STRING EXPRESSION, its implicitly concatenated
    parts in order. Docstrings and the strings of `print()` / logger calls are left out: operator
    output is not copy anybody is shown. An f-string is ONE part with `{}` where its interpolations
    were, on every interpreter. Source that does not parse yields nothing rather than raising, so a
    guard can never be taken down by the file it is reading."""
    import io
    import tokenize
    try:
        toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline)
                if t.type not in (tokenize.COMMENT, tokenize.NL)]
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return []
    lines = src.split("\n")
    FS_START = getattr(tokenize, "FSTRING_START", None)
    FS_MID = getattr(tokenize, "FSTRING_MIDDLE", None)
    FS_END = getattr(tokenize, "FSTRING_END", None)

    runs, run, prev_sig = [], [], None
    fs = None          # the f-string being assembled under PEP 701: {"line", "parts", "depth"}

    def flush():
        if run:
            runs.append(list(run))
        run.clear()

    for t in toks:
        # ── PEP 701 (3.12+): an f-string arrives as START … MIDDLE/code … END ──
        if fs is not None:
            if FS_MID is not None and t.type == FS_MID:
                # Literal text only. Inside an interpolation (`depth` > 0) a MIDDLE token is the
                # FORMAT SPEC — `03d` of `{n:03d}` — which is code the reader must not read as copy.
                if not fs["depth"]:
                    fs["parts"].append(t.string)
                continue
            if t.type == tokenize.OP and t.string == "{":
                fs["depth"] += 1
                if fs["depth"] == 1:
                    fs["parts"].append("{}")
                continue
            if t.type == tokenize.OP and t.string == "}" and fs["depth"]:
                fs["depth"] -= 1
                continue
            if FS_END is not None and t.type == FS_END and not fs["depth"]:
                text = "".join(fs["parts"])
                if not fs["skip"]:
                    run.append((fs["line"], text))
                fs = None
                prev_sig = t
                continue
            continue        # the interpolated EXPRESSION is code, not copy
        if FS_START is not None and t.type == FS_START:
            ln = lines[t.start[0] - 1] if t.start[0] - 1 < len(lines) else ""
            fs = {"line": t.start[0], "parts": [], "depth": 0, "skip": bool(_LOG_CALL.search(ln))}
            if fs["skip"]:
                flush()
            continue
        if t.type == tokenize.STRING:
            body = t.string.lstrip("rRbBuUfF")
            triple = body[:3] in ('"""', "'''")
            docstring = prev_sig is None and not run and triple
            text = body[3:-3] if triple else body[1:-1]
            if "f" in t.string[:2].lower():          # pre-3.12: the whole f-string is one STRING
                text = strip_interpolations(text)
            ln = lines[t.start[0] - 1] if t.start[0] - 1 < len(lines) else ""
            if docstring or _LOG_CALL.search(ln):
                flush()
                prev_sig = t
                continue
            run.append((t.start[0], text))
            prev_sig = t
            continue
        flush()
        prev_sig = None if t.type in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT) else t
    flush()
    return runs


def py_string_parts(src):
    """[(line, text)] per string LITERAL of a Python source — the parts, unjoined. An f-string is one
    part carrying `{}` where each interpolation was. Docstrings and log lines are left out."""
    return [(ln, text) for run in _string_runs(src) for ln, text in run]


def py_string_expressions(src, placeholder="000"):
    """[(line, text)] per string EXPRESSION of a Python source: implicitly concatenated parts JOINED
    (the text a client actually receives), each `{…}` and `%s` read as `placeholder` — because the
    value interpolated after the word "migration" is a migration id or file name, and a guard that
    reads only the half before it sees nothing to object to. The line is where the expression starts."""
    out = []
    for run in _string_runs(src):
        text = "".join(t for _ln, t in run)
        out.append((run[0][0], _BRACE_OR_PERCENT.sub(placeholder, text)))
    return out
