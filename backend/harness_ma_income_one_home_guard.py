#!/usr/bin/env python3
"""BUILD GUARD — a reported MA/VidaPay income figure may be derived in ONE home.

Owner bug report 2026-09-21 (LuxeLink): "why does the gross profit report and the p&l entries dont
match, the m1 commision is different in both and also the gross profit shows company level
commission it shoudl show store level as it is paid on store level".

THE CLASS, NOT THE INSTANCE. The instance was "GP says M1 = $23,271.90, the P&L says $2,300.40".
The class is: **two code paths independently summing the same raw_ma_* money columns into a
reported income figure.** They had drifted on three axes at once, and every one of the three facts
already had a home in `account/ma_store_pnl.py` that the second path simply did not read:

  · TIMING BASIS — the sheet's `spiff_m1..m6` columns (earned at activation) vs the daily-tx cash
    rows (received in the month paid). `commission_org_config.pl_ma_month_spiff_source` says which
    the org books; the GP report did not read it.
  · WHAT COUNTS AS COMMISSION — the GP column carried $251,946.31 of device-purchase rebate and
    −$43,445.63 of wallet funding, both of which the owner had already ruled out of the P&L
    (2026-09-08 rebate, mig 992; 2026-08-10 wallet funding → balance sheet). One report was fixed,
    the other was not. `ma_store_pnl.commission_received_lines()` is the ruling.
  · STORE GRAIN — one company-wide row vs the mig-314 account→store index, which resolves 20/20
    LuxeLink accounts on the GP report's OWN source feed (`ma_store_pnl.canonical_store_index`).

So: **only `account/ma_store_pnl.py` and `account/residual_subs.py` may turn the MA money columns
into a reported income total.** Every other caller dereferences them. This guard fails the build
when a new caller starts summing its own again.

Run: python3 backend/harness_ma_income_one_home_guard.py     (stdlib only, no DB, no network)
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")

HOMES = (
    os.path.join("app", "modules", "account", "ma_store_pnl.py"),
    os.path.join("app", "modules", "account", "residual_subs.py"),
)

# ── CHECK 1 — an income total built from the MA money columns, outside the homes ─────────────────
# (a) the shared component list aggregated directly;
# (b) a sign-flipped spiff total (the flip is the signature of "money the dealer RECEIVES");
# (c) the component-sum dict totalled (`-sum(comps…)`).
PATTERNS = (
    (re.compile(r"\bsum\(.*(?:_MA_COMPONENTS|_MA_LEG_COMPONENTS|_MACOMP)"
                r"|(?:_MA_COMPONENTS|_MA_LEG_COMPONENTS|_MACOMP).*\bsum\("),
     "aggregates the shared MA money-component list"),
    (re.compile(r"-\s*sum\(.*spiff_m"), "sign-flipped spiff total (an income figure)"),
    (re.compile(r"-\s*sum\(\s*comps"), "sign-flipped total of the MA component sums"),
)

# ── CHECK 2 — the surfaces that were re-derived must KEEP dereferencing the home ─────────────────
# Anti-unwiring. Writing the registry without wiring the callers to it is not a fix (CLAUDE.md);
# neither is wiring them and letting a later edit quietly unwire them.
MUST_DEREFERENCE = {
    ("app/modules/commcalc/router.py", "_compute_gp"):
        "the Gross Profit report's MA carrier income",
    ("app/modules/commcalc/router.py", "_ma_summary_legs"):
        "the MA roll-up's 1st-month / M2-M12 legs",
    ("app/modules/commcalc/router.py", "ma_commission_summary"):
        "the MA roll-up's per-store rows and month ladder",
}
DEREFERENCE_TOKENS = ("gp_carrier_income", "ma_earned_month_ladder", "ma_received_month_ladder",
                      "ma_sheet_component_total", "commission_received_lines",
                      "canonical_store_index")

# ── CHECK 2b — "which month-of-life leg is this row" also has one home ──────────────────────────
# Owner report 2026-09-21 ("the m1 commission cannot be 2300"). `parse_payment_month` answers only
# "does this label SAY a month". The LEG question has a second form — a label that is an activation
# commission and carries no token — and asking the token parser instead dropped 1,218 rows and
# $19,292.54 of M1 into the unlabelled bucket, reading August's M1 as $2,300.40 against $6,049.96.
# `commission_ledger.month_leg_of` is the leg's one home; the token parser stays private to its own
# module. A caller that goes back to the token parser for a leg fails the build here.
MONTH_LEG_HOME = "app/modules/commcalc/commission_ledger.py"
TOKEN_PARSER = "parse_payment_month"
LEG_RESOLVER = "month_leg_of"

# ── CHECK 3 — INVENTORY (reported, not a build gate) ─────────────────────────────────────────────
# The commission SHEET's export genuinely carries six spiff columns, so a surface that reconciles to
# that export, or reads one device's spiff evidence, legitimately walks six. Those are named here so
# the limitation is on the record rather than rediscovered: none of them is an income figure for a
# report, and none of them can show M7-M12 — which is exactly why the GP/P&L month ladder now comes
# from the daily-tx feed, where M7..M12+ exist.
SIX_COLUMN_SHEET_SITES = {
    "app/modules/commcalc/commission_drilldown.py":
        "per-DEVICE spiff evidence in the commission drill-down — one row's six sheet columns",
    "app/modules/commcalc/imei_rebate_report.py":
        "per-IMEI rebate/spiff report — reconciles line by line to the sheet's own export",
    "app/modules/commcalc/ma_overview.py":
        "portal-tile reconciliation — compares us to the portal's OWN six-column Overview export",
    "app/modules/commcalc/whatif.py":
        "money-vs-identifier column guard (mig 083) — names the columns, sums none of them",
    "app/modules/commcalc/router.py":
        "the upload mapper ('1st Month Spiff' header -> spiff_m1) — ingest, not an income figure",
    "app/modules/commcalc/column_mapping.py":
        "the upload wizard's header vocabulary — names the columns for mapping, sums none of them",
    "app/modules/commcalc/agency.py":
        "the agency holdback's COMMISSION_COMPONENTS vocabulary — a pick-list of component names a "
        "holdback rule may scope to, never an amount",
    "app/modules/commcalc/device_history.py":
        "one DEVICE's spiff history (_MA_SPIFF_KEYS) — per-device evidence, not a report income line",
    "app/modules/commcalc/sale_installment_engine.py":
        "the installment gate's paid-proof index (_MA_NUMERIC_COLS) — asks whether a month's leg was "
        "PAID for one device; it pays installments, it reports no income line",
}
SIX_COLUMN = re.compile(r"spiff_m\{i\}|spiff_m1[\"'],\s*[\"']spiff_m2")


def _py_files():
    for base, _dirs, files in os.walk(APP):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


def _rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def _code_lines(src):
    """(line_no, text) for lines that are not whole-line comments, so a rule DOCUMENTED in a note or
    a docstring example is never reported as a live derivation."""
    out, in_doc, delim = [], False, None
    for i, ln in enumerate(src.splitlines(), 1):
        s = ln.strip()
        if in_doc:
            if delim in s:
                in_doc = False
            continue
        if s.startswith(('"""', "'''")):
            delim = s[:3]
            if not (len(s) > 3 and s.endswith(delim)):
                in_doc = True
            continue
        if s.startswith("#"):
            continue
        out.append((i, ln))
    return out


def _function_source(src, name):
    """The source text of a top-level `def name(` up to the next top-level def/@/class. '' if absent."""
    lines = src.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("def %s(" % name) or ln.startswith("async def %s(" % name):
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if ln and not ln[0].isspace() and (ln.startswith(("def ", "async def ", "class ", "@"))):
            end = j
            break
    return "\n".join(lines[start:end])


def main():
    sources = {}
    for path in sorted(_py_files()):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            sources[_rel(path)] = fh.read()

    print("MA INCOME — ONE HOME GUARD")
    print("=" * 78)
    print("homes: %s" % ", ".join(HOMES))
    print()

    failures = []

    # CHECK 1
    derivations = []
    for rel, src in sources.items():
        if rel.replace("/", os.sep) in HOMES or rel in [h.replace(os.sep, "/") for h in HOMES]:
            continue
        for lineno, text in _code_lines(src):
            for pat, why in PATTERNS:
                if pat.search(text):
                    derivations.append((rel, lineno, why, text.strip()[:110]))
                    break
    print("CHECK 1 — MA income totals derived outside the homes")
    if derivations:
        for rel, lineno, why, text in derivations:
            print("   FAIL %s:%d — %s" % (rel, lineno, why))
            print("        %s" % text)
        failures.append("%d site(s) derive an MA income total outside the homes" % len(derivations))
    else:
        print("   PASS — none.")
    print()

    # CHECK 2
    print("CHECK 2 — the re-derived surfaces still dereference the home")
    for (rel, fn), what in sorted(MUST_DEREFERENCE.items()):
        body = _function_source(sources.get(rel, ""), fn)
        if not body:
            print("   FAIL %s:%s — function not found" % (rel, fn))
            failures.append("%s:%s missing" % (rel, fn))
            continue
        hit = [t for t in DEREFERENCE_TOKENS if t in body]
        if hit:
            print("   PASS %s:%s (%s) -> %s" % (rel, fn, what, ", ".join(sorted(hit))))
        else:
            print("   FAIL %s:%s (%s) dereferences nothing from ma_store_pnl" % (rel, fn, what))
            failures.append("%s:%s unwired" % (rel, fn))
    print()

    # CHECK 2b
    print("CHECK 2b — the month-of-life LEG question has one home")
    leg_offenders = []
    for rel, src in sources.items():
        if rel == MONTH_LEG_HOME:
            continue
        for lineno, text in _code_lines(src):
            if (TOKEN_PARSER + "(") in text:
                leg_offenders.append((rel, lineno, text.strip()[:100]))
    if leg_offenders:
        for rel, lineno, text in leg_offenders:
            print("   FAIL %s:%d asks the TOKEN parser a leg question" % (rel, lineno))
            print("        %s" % text)
            print("        -> use commission_ledger.%s()" % LEG_RESOLVER)
        failures.append("%d caller(s) ask %s for a leg" % (len(leg_offenders), TOKEN_PARSER))
    else:
        print("   PASS — %s is private to %s; every caller asks %s()"
              % (TOKEN_PARSER, MONTH_LEG_HOME, LEG_RESOLVER))
    print()

    # CHECK 3 — inventory, on the record, never a build gate
    print("CHECK 3 — six-column SHEET sites (reported, not a gate: the export really has six)")
    seen = set()
    for rel, src in sources.items():
        if rel.replace("/", os.sep) in HOMES:
            continue
        for _lineno, text in _code_lines(src):
            if SIX_COLUMN.search(text):
                seen.add(rel)
                break
    for rel in sorted(seen):
        print("   %-52s %s" % (rel, SIX_COLUMN_SHEET_SITES.get(rel, "UNDOCUMENTED — name it here")))
    undocumented = sorted(r for r in seen if r not in SIX_COLUMN_SHEET_SITES)
    if undocumented:
        failures.append("undocumented six-column site(s): %s" % ", ".join(undocumented))
    print()

    print("=" * 78)
    if failures:
        print("FAIL — %d problem(s):" % len(failures))
        for f in failures:
            print("   · %s" % f)
        print()
        print("Dereference ma_store_pnl instead of deriving:")
        print("   · a report's MA income figure -> gp_carrier_income(...)")
        print("   · a sheet-basis month ladder  -> ma_earned_month_ladder(...)")
        print("   · a cash-basis month ladder   -> ma_received_month_ladder(...)")
        print("   · the sheet's payable total   -> ma_sheet_component_total(...)")
        print("   · 'is this line commission?'  -> commission_received_lines()")
        print("   · account -> store            -> canonical_store_index(...)")
        return 1
    print("PASS — one home, every caller dereferencing it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
