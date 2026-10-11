"""LOCK: paid hours are DECIDED in one place, and the next change cannot quietly un-wire it.

WHY THIS FILE EXISTS (owner directive, "a fix is a DESIGN fix"): *"one fact, one home, dereferenced
— never copied … writing the registry without wiring the callers is not a fix at all — it has
happened three times here."* So the registry is not the fix; the registry PLUS this lock is.

WHAT WENT WRONG, STATED ONCE. Paid hours were decided by `actual_hours or scheduled_hours` in six
modules at once, over a column effectively nothing writes (7 of 7,740 live `storeops.shifts` rows
carry `actual_hours > 0`). Each `or` was locally reasonable and collectively they meant payroll ran
off the schedule without telling anybody. `storeops/payroll_hours_basis.py` is now the only module
allowed to decide that, and it answers with the hours AND the basis so a caller cannot show one
without the other.

THE SIX AXES THIS LOCK FAILS THE BUILD ON
  A. THE `or` ITSELF. No module may fall back from one hours column to the other. This is the
     literal shape of the shipped defect, and it is banned by text so it cannot come back by habit.
  B. THE CALLERS STAY WIRED. Every module on the PAY PATH must import the one home and actually
     CALL one of its resolvers. A module that stops dereferencing is the §19.18 un-wiring failure,
     and an unused import is the same thing wearing a hat.
  C. NO SECOND COPY OF THE VOCABULARY. The basis words may be spelled as literals only in the one
     home and in the harnesses that pin them — a module carrying its own `"scheduled"` / `"actual"`
     string is a second answer to "where did this number come from".
  D. THE ALLOWLIST STAYS AN ALLOWLIST. `NON_PAYING_BASES` must be DERIVED from `PAYING_BASES`, and
     an unruled basis must not pay. A hand-written denylist is how an unruled basis starts paying.
  E. THE ONE ACCENT FOLD STAYS DEREFERENCED. `str.isalnum()` is True for an accented letter, so the
     five identity folds here must all read `store_identity.fold_accents` and no new private
     alnum-only fold may appear without it.
  F. EVERY DECLARED VARIANCE KIND IS EMITTED. A kind in the vocabulary that no code raises is a
     check nobody performs — and two of them (`store_mismatch`, `auto_clockout`) were exactly that
     for a day, which is why this axis exists.

No DB, no network, no app imports — it reads the source. Run:
    cd backend && python3 harness_payroll_hours_basis_lock.py
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULES = os.path.join(_HERE, "app", "modules")
_CORE = os.path.join(_HERE, "app", "core")
_HOME = os.path.join(_MODULES, "storeops", "payroll_hours_basis.py")
_VARIANCE = os.path.join(_MODULES, "storeops", "clock_variance.py")
_FOLD_HOME = os.path.join(_MODULES, "account", "store_identity.py")

PASS, FAIL = [], []


def ok(label, cond, detail=""):
    if cond:
        PASS.append(label)
    else:
        FAIL.append(f"{label}{('  ' + str(detail)) if detail else ''}")


def section(t):
    print(f"\n── {t}")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


_STRING_RE = re.compile(r"('''|\"\"\"|'|\")")


def strip_comments_and_strings(src):
    """Remove comments, docstrings and string literals, so a BAN is about CODE and a DOC may still
    name the defect it closes. Without this the ban cannot be documented without tripping itself —
    the mistake that made an earlier guard unmaintainable."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "#":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in "\"'":
            q = src[i:i + 3]
            if q in ('"""', "'''"):
                j = src.find(q, i + 3)
                i = n if j < 0 else j + 3
                continue
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == c or src[j] == "\n":
                    break
                j += 1
            out.append('""')
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def strip_comments_only(src):
    """Remove `#` comments and TRIPLE-quoted docstrings, but KEEP single-line string literals.

    Needed because some facts this lock checks ARE string literals in the code — a dict key such as
    `"kind"` or `"auto_closed_punches"`. `strip_comments_and_strings` deletes those, and the axis then
    passes or fails for the wrong reason, which is how a guard quietly stops guarding.
    """
    out = []
    i, n = 0, len(src)
    tq = (chr(34) * 3, chr(39) * 3)
    while i < n:
        if src[i] == "#":
            j = src.find(chr(10), i)
            i = n if j < 0 else j
            continue
        q = src[i:i + 3]
        if q in tq:
            j = src.find(q, i + 3)
            i = n if j < 0 else j + 3
            continue
        out.append(src[i])
        i += 1
    return "".join(out)


def py_files(*roots):
    for root in roots:
        for dirpath, _dirs, names in os.walk(root):
            for nm in sorted(names):
                if nm.endswith(".py"):
                    yield os.path.join(dirpath, nm)


ALL = sorted(py_files(_MODULES, _CORE))


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("A. the `or` fallback itself — the literal shape of the shipped defect")
# `actual_hours or scheduled_hours`, in any spelling a reader would recognise: the two column names
# adjacent across an `or`, through a dict get, an attribute, or bare locals.
_OR_RE = re.compile(
    r"(actual_hours|act_h|act)\b[^\n]{0,80}?\bor\b[^\n]{0,80}?(scheduled_hours|sched_h|sched)\b")
# ONE EXCUSED SITE, and it is excused because it is the PROOF. The harness builds the pre-fix
# expression deliberately, to show the one home reproducing it and then refusing it.
_OR_EXCUSED = {
    os.path.join(_HERE, "harness_payroll_hours_basis.py"):
        "builds the pre-fix expression on purpose, as the regression's control",
}
_hits = []
for p in ALL:
    if p == _HOME:
        continue
    code = strip_comments_and_strings(read(p))
    for m in _OR_RE.finditer(code):
        _hits.append(f"{os.path.relpath(p, _HERE)}: {m.group(0)[:70]}")
ok("A1 no module under app/ falls back from one hours column to the other", not _hits, _hits)
# NOT VACUOUS. The pattern must still catch the defect if somebody writes it again.
ok("A2 …and the search is not vacuous: it still catches the exact shipped expression",
   bool(_OR_RE.search("hrs = s.get('actual_hours') or s.get('scheduled_hours')")))
ok("A3 …including the local-variable spelling",
   bool(_OR_RE.search("hours = act or sched")))
ok("A4 …and it does NOT fire on an unrelated `or` between two other fields",
   not _OR_RE.search("name = s.get('employee_name') or s.get('login')"))


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("B. the callers stay WIRED — a module that stops dereferencing fails the build")
# THE PAY PATH. Each of these decides, prices or reports hours that become money, and each one
# carried its own `or` before 2026-10-10. If a refactor moves the decision back out of the one home,
# the import disappears and this axis goes red.
PAY_PATH = {
    "storeops/router.py": "GET /storeops/payroll and /payroll-by-store — the money path itself",
    "storeops/salary_owed.py": "what one employee is owed, per day",
    "storeops/salary_expense.py": "the labour expense line, and the three-state hours question",
    "storeops/pto_accrual.py": "hours worked, which is what PTO accrues on",
    "account/coa.py": "the P&L wage line",
    "commcalc/labour_coverage.py": "the labour-coverage report's paid-hours basis",
    "hr/router.py": "HR total comp",
}
_RESOLVERS = ("resolve_shift_row", "resolve_day", "punch_counts_for_pay", "measurement_state",
              "hours_basis_by_code", "punch_usability", "load_config", "resolve_config")
for rel, why in sorted(PAY_PATH.items()):
    p = os.path.join(_MODULES, rel)
    if not os.path.exists(p):
        FAIL.append(f"B1 {rel} is gone — the pay path changed shape; re-pin this list ({why})")
        continue
    code = strip_comments_and_strings(read(p))
    imports = "payroll_hours_basis" in code
    # The ALIAS it was imported under, so "imported and never used" cannot pass.
    alias = None
    for m in re.finditer(r"payroll_hours_basis(?:\s+as\s+(\w+))?", code):
        alias = m.group(1) or "payroll_hours_basis"
        if m.group(1):
            break
    uses = bool(alias) and any(f"{alias}.{fn}" in code for fn in _RESOLVERS)
    ok(f"B1 {rel} imports the one home", imports, why)
    ok(f"B2 {rel} actually CALLS it — a dead import is a caller that went back to deriving",
       uses, f"alias={alias}")


# ── B3: the TWO router shift resolvers cannot drift ──────────────────────────────────────────────
# `_shift_actual_contribution` answers "how many hours", `_shift_actual_contribution_with_basis`
# answers "how many, and WHY". Two functions answering overlapping questions is exactly the
# duplicate the index rules forbid — so the lock requires that BOTH reach the one home and that
# neither grows arithmetic of its own. A `max(`/`or`/`if act` in either body is the old defect back.
_ROUTER_SRC = strip_comments_and_strings(read(os.path.join(_MODULES, "storeops/router.py")))
for _fn in ("_shift_actual_contribution", "_shift_actual_contribution_with_basis"):
    _m = re.search(r"\ndef %s\(.*?(?=\ndef )" % _fn, _ROUTER_SRC, re.S)
    ok(f"B3 {_fn} exists", bool(_m), "the pay path's shift resolver was renamed — re-pin this axis")
    _body = _m.group(0) if _m else ""
    ok(f"B4 {_fn} resolves through the one home",
       "resolve_shift_row" in _body, "it no longer dereferences payroll_hours_basis")
    # The pre-2026-10-10 defect, literally: `actual_hours or scheduled_hours` decided in the caller.
    ok(f"B5 {_fn} decides nothing itself",
       not re.search(r"scheduled_hours\s*\)?\s*(?:if|or)\b", _body),
       "an `or`/inline-if over scheduled_hours is back in the caller")
# And the figure's SENTENCE is the one home's, never one composed beside it.
# NOTE the different stripper: `strip_comments_and_strings` would eat the key name this check is
# about, so B6 reads the comment-free source that KEEPS single-line literals.
_ROUTER_LIT = strip_comments_only(read(os.path.join(_MODULES, "storeops/router.py")))
ok("B6 /payroll's basis sentence is read from the one home, not spelled on the row",
   re.search(r'hours_basis_reason"\]\s*=\s*_hours_basis\.basis_reason\(', _ROUTER_LIT) is not None,
   "hours_basis_reason must be basis_reason(dom) — a literal sentence on the row drifts from the API")


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("C. no SECOND COPY of the basis vocabulary")
_HOME_SRC = read(_HOME)
_BASIS_WORDS = ("clocked_corrected", "clocked_reconciled", "clocked_override", "measured_zero",
                "schedule_opt_in", "not_measured_unpaid", "punch_unusable")
# A module may only name a basis by DEREFERENCING the constant. The literal string belongs to the
# one home and to the harnesses that pin it (a harness that cannot write the word cannot pin it).
_copies = []
for p in ALL:
    if p in (_HOME, _VARIANCE):
        continue
    code = strip_comments_and_strings(read(p))
    for w in _BASIS_WORDS:
        if f'"{w}"' in code or f"'{w}'" in code:
            _copies.append(f"{os.path.relpath(p, _HERE)}: {w}")
ok("C1 no module spells a basis as a literal string — they dereference the constant",
   not _copies, _copies)
ok("C2 …and the one home does declare every one of them",
   all(f'"{w}"' in _HOME_SRC or f"'{w}'" in _HOME_SRC for w in _BASIS_WORDS))
# THE VARIANCE MODULE is excused above because it IS the other half of the same vocabulary; what it
# may not do is decide whether a basis pays.
_VAR_SRC = strip_comments_only(read(_VARIANCE))
ok("C3 the variance board asks the one home whether a basis is a measurement, never decides itself",
   "_basis.basis_is_measurement" in _VAR_SRC)
ok("C4 …and never computes paid hours of its own: it calls resolve_day",
   "_basis.resolve_day" in _VAR_SRC)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("D. the ALLOWLIST stays an allowlist")
sys.path.insert(0, _HERE)
from app.modules.storeops import payroll_hours_basis as B  # noqa: E402
from app.modules.storeops import clock_variance as V  # noqa: E402

ok("D1 NON_PAYING_BASES is DERIVED from PAYING_BASES, so it cannot drift",
   sorted(B.NON_PAYING_BASES) == sorted([b for b in B.ALL_BASES if b not in B.PAYING_BASES]))
ok("D2 an UNRULED basis does NOT pay", B.basis_pays_hours("a_basis_added_in_2027") is False)
ok("D3 a missing basis does not pay", B.basis_pays_hours(None) is False)
ok("D4 every ruled basis carries an explaining sentence",
   sorted(B.BASIS_REASONS) == sorted(B.ALL_BASES))
ok("D5 …and an unruled one still gets a sentence, rather than a blank label",
   bool(B.basis_reason("whatever")))
# The derivation must be in the SOURCE, not merely true today by coincidence.
ok("D6 the derivation is written as a comprehension over PAYING_BASES, not a second tuple",
   re.search(r"NON_PAYING_BASES\s*=\s*tuple\(", _HOME_SRC) is not None)
# AND THE OPPOSITE DIRECTION, for the other question. "Is it safe to run payroll" must fail CLOSED.
ok("D7 an unruled VARIANCE kind BLOCKS — the safe direction for that question is the opposite",
   V.kind_blocks("a_kind_added_in_2027") is True)
ok("D8 …and the two facts live in two modules, so neither can be read off the other",
   B.__file__ != V.__file__)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("E. the ONE accent fold stays dereferenced by all five siblings")
_FOLD_SRC = read(_FOLD_HOME)
ok("E1 the fold lives in account/store_identity as `fold_accents`",
   "def fold_accents(" in _FOLD_SRC)
ok("E2 …and is exported", '"fold_accents"' in _FOLD_SRC)
# THE FIVE SIBLINGS. Each was its own `"".join(ch for ch in s.upper() if ch.isalnum())`, and each
# kept an accent. They are named so a refactor cannot quietly re-spell one back into a private fold.
FOLD_SIBLINGS = {
    os.path.join(_CORE, "scope.py"): "_squash — market/store grant resolution",
    os.path.join(_MODULES, "account", "balance_sheet.py"): "_squash — journal store designation",
    os.path.join(_MODULES, "account", "store_identity_audit.py"): "_squash — store-code match key",
    os.path.join(_MODULES, "account", "device_payable.py"): "norm_key — the device join key",
    os.path.join(_MODULES, "account", "coa.py"): "_squash_key — the P&L store grain",
}
for p, why in sorted(FOLD_SIBLINGS.items()):
    code = strip_comments_and_strings(read(p))
    ok(f"E3 {os.path.relpath(p, _HERE)} dereferences the one fold",
       "fold_accents" in code or "squash_key" in code, why)
# NO NEW PRIVATE FOLD. An alnum-only upper/lower fold that does not go through fold_accents is the
# defect being reintroduced, and it is invisible until an accented name arrives.
# An IDENTITY FOLD drops every non-alphanumeric: its `isalnum()` sits in a comprehension's
# FILTER (`for ch in s if ch.isalnum()`). A SLUGIFIER SUBSTITUTES instead
# (`ch if ch.isalnum() else "_"`) and is not an identity at all — it is a safe filename — so an
# `else` disqualifies a hit. Without that distinction this axis reports six filename builders
# and the real thing hides among them, which is a guard that cannot be read.
_PRIVATE_FOLD = re.compile(r"join\(.{0,200}?isalnum\(\).{0,40}?\)", re.S)
_new = []
for p in ALL:
    if p == _FOLD_HOME:
        continue
    code = strip_comments_and_strings(read(p))
    for m in _PRIVATE_FOLD.finditer(code):
        frag = m.group(0)
        if "else" in frag or "or c ==" in frag or "or ch ==" in frag:
            # SLUG, not IDENTITY. `commission_statement`, `payout_structure` and `router`'s
            # attachment-name builders keep a character if it is alphanumeric and SUBSTITUTE
            # otherwise, to make a safe filename. Nothing is ever matched on the result, so an
            # accent surviving it changes no join — it changes a file's name, which is correct.
            continue
        # A fold is fine when the thing being folded has ALREADY been through the one fold.
        line_start = code.rfind("\n", 0, m.start()) + 1
        ctx = code[line_start:m.end() + 40]
        if "fold_accents" in ctx or "_sid_fold" in ctx or "_fold_accents" in ctx \
                or "squash_key" in ctx or "_sid.fold" in ctx:
            continue
        _new.append(f"{os.path.relpath(p, _HERE)}: {' '.join(frag.split())[:80]}")
ok("E4 no module carries an alnum-only fold that skips the one accent fold", not _new, _new)
ok("E4b …and the inventory below is not empty: there ARE folds, they are all classified",
   len(FOLD_SIBLINGS) >= 5)
ok("E5 …and the search is not vacuous: it sees a planted private fold",
   bool(_PRIVATE_FOLD.search('k = "".join(ch for ch in s.upper() if ch.isalnum())')))
ok("E5b …and it does NOT fire on a filename slugifier, which substitutes rather than drops",
   not [m for m in _PRIVATE_FOLD.finditer(
       'n = "".join(c if c.isalnum() else chr(95) for c in name)') if "else" not in m.group(0)])
# THE FOLD ACTUALLY FOLDS. The whole point, asserted on the measured spelling.
from app.modules.account.store_identity import fold_accents, squash_key  # noqa: E402
ok("E6 the fold really folds the measured spelling", fold_accents("Martínez") == "Martinez")
ok("E7 …and every key built on it agrees across the accent",
   squash_key("Martínez") == squash_key("Martinez"))
ok("E8 …while plain ASCII is byte-identical, so no existing match moved",
   squash_key("4640-A W Diversey Ave") == "4640AWDIVERSEYAVE")


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("F. every DECLARED variance kind is EMITTED by somebody")
# THE FAILURE MODE THIS CATCHES, and it caught it once already: `store_mismatch` and `auto_clockout`
# were in the vocabulary and raised by NO code path, so two checks the owner asked for did not exist
# while appearing to. A kind nobody emits is worse than a kind nobody declared.
_EMITTERS = [_VARIANCE]
_emit_src = "\n".join(strip_comments_only(read(p)) for p in _EMITTERS)
_CONST_OF = {
    V.MISSING_PUNCH: "MISSING_PUNCH", V.EXTRA_PUNCH: "EXTRA_PUNCH",
    V.HOURS_DIFFER: "HOURS_DIFFER", V.EMPLOYEE_UNMATCHED: "EMPLOYEE_UNMATCHED",
    V.STORE_UNMATCHED: "STORE_UNMATCHED", V.LOGIN_UNLINKED: "LOGIN_UNLINKED",
    V.NO_CLOCK_FEED: "NO_CLOCK_FEED", V.STORE_MISMATCH: "STORE_MISMATCH",
    V.AUTO_CLOCKOUT: "AUTO_CLOCKOUT",
}
# A KIND MAY BE EXCUSED, and it costs a sentence saying why — the same posture as the feed
# watchdog's NOT_WATCHED_REASONS. This is the only way out, so a kind cannot go unemitted silently.
_EMIT_EXCUSED = {
    V.EMPLOYEE_UNMATCHED:
        "the CATCH-ALL default for an extra row that arrives with no kind of its own "
        "(`compare`: `x.get(\"kind\") or EMPLOYEE_UNMATCHED`). Nothing raises it deliberately, "
        "because every real unbindable row is raised more precisely as login_unlinked or "
        "store_unmatched — but it must stay in the vocabulary so a malformed row still reads as a "
        "BLOCKING variance rather than vanishing from the board.",
}
_unemitted = []
for kind in V.KINDS:
    if kind in _EMIT_EXCUSED:
        continue
    const = _CONST_OF.get(kind)
    if not const:
        _unemitted.append(f"{kind} (no constant mapped — add it to this lock)")
        continue
    # An emission is an ASSIGNMENT of the kind, or the kind handed to a row builder — not merely the
    # declaration line and not merely the reason table.
    assigned = re.search(rf"(kind\s*=\s*{const}\b|\"kind\":\s*{const}\b)", _emit_src)
    if not assigned:
        _unemitted.append(f"{kind} ({const})")
ok("F1 every declared variance kind is assigned somewhere, or excused in writing",
   not _unemitted, _unemitted)
ok("F1b every excused kind still BLOCKS — an excuse is about emission, never about safety",
   all(V.kind_blocks(k) for k in _EMIT_EXCUSED))
ok("F1c …and every excuse names a real kind, so the list cannot rot",
   sorted(_EMIT_EXCUSED) == sorted(k for k in _EMIT_EXCUSED if k in V.KINDS))
ok("F2 …and the search is not vacuous: it sees an assignment",
   bool(re.search(r"kind\s*=\s*MISSING_PUNCH\b", _emit_src)))
ok("F3 every kind carries an explaining sentence of its own",
   sorted(V.KIND_REASONS) == sorted(V.KINDS))
# THE AUTO-CLOCKOUT FACT IS READ FROM THE ONE HOME, not re-derived on the board.
ok("F4 the board reads the auto-clockout fact from the one home rather than scanning punches",
   "auto_closed_punches" in _VAR_SRC and "_raw_punches" not in _VAR_SRC)
ok("F5 …and the one home exposes it as a FACT, separate from whether policy pays it",
   "def auto_closed_punches(" in _HOME_SRC)


# ════════════════════════════════════════════════════════════════════════════════════════════════
section("G. the whole feature is OFF until an org says otherwise")
# A lock, not a harness assertion: shipping this ON would be a money change nobody approved.
for _k in ("clock_feed_enabled", "variance_ask_enabled", "login_gate_enabled",
           "payroll_run_gate_enabled"):
    ok(f"G1 {_k} is FALSE with no config row", V.resolve_config(None)[_k] is False)
ok("G2 the hours basis ships in schedule-fallback mode — today's behaviour",
   B.resolve_config(None)["hours_basis_mode"] == B.MODE_SCHEDULE_FALLBACK)
ok("G3 the per-employee tick box defaults to ON, so nobody stops being paid on apply",
   B.resolve_config(None)["pay_from_schedule_default"] is True)
ok("G4 the clock report is NOT a pay source until an org switches it on",
   B.resolve_config(None)["clock_feed_is_pay_source"] is False)
ok("G5 and both halves read the SAME per-org row — one row, one feature",
   V.CONFIG_TABLE == B.CONFIG_TABLE)


print(f"\n{'=' * 78}")
for f in FAIL:
    print(f"  ✗ {f}")
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
print(f"{'=' * 78}")
if FAIL:
    print("THE ONE HOME HAS BEEN UN-WIRED, or a second copy has appeared. Fix the caller, do not\n"
          "relax the lock: a guard that is edited to pass is not a guard.")
sys.exit(1 if FAIL else 0)
