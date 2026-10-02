#!/usr/bin/env python3
"""Management Envelope Receipt — WHICH cash, and WHO counted (index §47).

TWO OWNER ASKS, 2026-10-01, verbatim:
  1. "Management Envelope Receipt should have both reports epay and store cash , use a radio button
     or select box to choose"
  2. "yes include countedby"  — on the gap reported with §44/§45: `counted_by` was the literal string
     "management", so a count recorded THAT management counted and never WHO, while the pickup side
     named the person.

WHY A BASIS, AND WHY IT IS NOT A NEW FORMULA. "The cash figure a basis reconciles against" already had
exactly ONE home — `deposit_recon.cash_for_basis` — with these three formulas:
    total_cash        = t_cash                            the whole drawer   (the historical default)
    store_cash        = max(t_cash - epay_on_cash, 0)      register cash, bill-pay excluded
    bill_payment_cash = epay_on_cash                       the ePay cash only
`envelope_report.expected_cash` now DEREFERENCES that function instead of keeping its own
`t_cash or store_cash` rule, so the receipt and the deposit recon can never disagree about what a
basis means. On the owner's own row (B-559 2026-09-06: t_cash 100 of which epay_on_cash 100) the three
read 100 / 0 / 100 — which is the answer to the "store cash 100 and epay 100, so is it 200?" question:
it was never 200, and store-cash-net is 0.

WHY `counted_by` IS A UUID AND NOT A NAME. §19.34: actor identity is the signed-in uid or NULL, never a
sentinel, from the ONE home `_caller_uid`. Storing a name would reintroduce the sentinel class. But
§19.34 never gave anyone a way to DISPLAY it — every surface renders the raw value through
`actorLabel`, which returns the string unchanged, so actor columns show a RAW UUID
(`/commcalc/daily-commission` "Recorded by", `plan-installments` "by …", `ingest-guard`,
`commission-discrepancy`). `app/core/actors.py` is the missing join, once: uid -> person from
`storeops.app_users`, org-scoped, best-effort, name-only.

AND WHY THE SELECTOR STILL READ ZERO (owner bug 2026-10-02, index §47.8). All of the above shipped
correct, and the receipt showed bill-payment cash 0.00 on every line with store cash carrying the whole
drawer — because BOTH callers of `expected_cash` hand-spelled a `daily_closing` column list that omitted
`epay_on_cash`, the split's one input. `.get()` cannot tell "0 dollars" from "never fetched", so nothing
failed. The class is §19.18's again in its quietest form: the unwired thing was a SELECT LIST. The
columns a pure reader dereferences now live beside it (`envelope_report.CLOSING_COLUMNS`) and every
caller selects that declaration — sections H1-H3 below.

WHAT FAILS THE BUILD HERE: a second basis formula; a basis word or label spelled on the screen or in
the router; the default drifting off the historical figure; an unknown basis folding to 'manual' (which
would render a whole receipt as zeros); the legacy store_cash fallback being lost; the count math
leaving `count_fields`; `counted_by` carrying a sentinel again or not using the one home; a second
uid->name resolver; `core.actors` reading a column it has no business reading, dropping its org scope,
or raising; a caller hand-spelling a daily_closing column list again; the pure module reading a closing
column the contract does not declare; the counted basis not reaching the save handler; a basis without
a column on the screen.

Stdlib only — the pure modules are import-free and exec'd directly; the router is read as text/AST.
"""
import ast
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROUTER = os.path.join(HERE, "app", "modules", "closing", "router.py")
PAGE = os.path.join(os.path.dirname(HERE), "frontend", "src", "app", "(platform)",
                    "closing", "envelope-report", "page.tsx")

_p = _f = 0


def check(name, got, want=None):
    global _p, _f
    ok = bool(got) if want is None else (got == want)
    if ok:
        _p += 1
        print("  PASS  %s" % name)
    else:
        _f += 1
        print("  ✗ %s" % name)
        if want is not None:
            print("        want: %r" % (want,))
        print("        got : %r" % (got,))
    return ok


def read(p):
    with open(p, "r", encoding="utf-8") as fh:
        return fh.read()


def main():
    # DB-free: stub the one I/O import core.actors needs, then import the pure modules for real.
    m = types.ModuleType("app.core.database")
    m.get_supabase = lambda: (_ for _ in ()).throw(RuntimeError("no db in a harness"))
    sys.modules["app.core.database"] = m
    from app.modules.closing import envelope_report as ER
    from app.modules.closing import deposit_recon as DR
    from app.core import actors as A

    rsrc = read(ROUTER)
    page = read(PAGE)
    # Comments stripped for the "must not appear" rules: this change's own comments legitimately NAME
    # the retired sentinel and the basis keys while explaining them, and a rule that cannot tell prose
    # from code would force the explanation out of the codebase. (Same posture as
    # harness_screen_link_guard's `_CC`.) Rules about what the code must DO read the full source.
    rcode = re.sub(r"(?m)^\s*#.*$", "", rsrc)
    pcode = re.sub(r"(?m)^\s*(//|\*|/\*).*$", "", page)
    pcode = re.sub(r"\{/\*.*?\*/\}", "", pcode, flags=re.S)

    print("=" * 96)
    print("MANAGEMENT ENVELOPE RECEIPT — WHICH CASH, AND WHO COUNTED (index §47)")
    print("=" * 96)

    # ── A. the basis is DEREFERENCED, never a second formula ─────────────────────────────────────
    print("\nA. the basis vocabulary has one home and the receipt dereferences it")
    esrc = read(os.path.join(HERE, "app", "modules", "closing", "envelope_report.py"))
    # Bound the body at the NEXT top-level def — not at the first blank line, which falls inside the
    # docstring and truncated the very call this rule exists to find (caught by this harness's own
    # run, 2026-10-01).
    i = esrc.index("def expected_cash(")
    _m = re.search(r"\n(?=def )", esrc[i + 10:])
    body = esrc[i:i + 10 + _m.start()]
    check("expected_cash calls deposit_recon.cash_for_basis", "cash_for_basis(" in body)
    check("...and spells no formula of its own (no bare t_cash - epay arithmetic)",
          re.search(r"t_cash.*-.*epay", body) is None)
    check("every offered basis is one deposit_recon already defines",
          all(b in DR.BASIS_VALUES for b in ER.ENVELOPE_BASES))
    check("'manual' is NOT offered (it has no formula; cash_for_basis returns 0.0 for it)",
          "manual" not in ER.ENVELOPE_BASES)

    # ── B. the owner's own numbers ───────────────────────────────────────────────────────────────
    print("\nB. the owner's live rows")
    row0906 = {"t_cash": 100.0, "store_cash": 100.0, "epay_on_cash": 100.0}
    check("B-559 09/06 total_cash  = 100 (the whole drawer)",
          ER.expected_cash(row0906, "total_cash"), 100.0)
    check("B-559 09/06 store_cash  = 0   (all of it was bill-pay cash)",
          ER.expected_cash(row0906, "store_cash"), 0.0)
    check("B-559 09/06 bill_payment_cash = 100",
          ER.expected_cash(row0906, "bill_payment_cash"), 100.0)
    row0907 = {"t_cash": 1002.0, "store_cash": 1002.0, "epay_on_cash": 230.0}
    check("B-559 09/07 store_cash = 772 — the equip/acc figure the DM actually collected",
          ER.expected_cash(row0907, "store_cash"), 772.0)
    check("...and the three bases reconcile: store + billpay == total",
          round(ER.expected_cash(row0907, "store_cash")
                + ER.expected_cash(row0907, "bill_payment_cash"), 2),
          ER.expected_cash(row0907, "total_cash"))

    # ── C/D/E. the default, the degrade, the legacy fallback ─────────────────────────────────────
    print("\nC. the default is the historical figure, and a bad basis degrades to it")
    check("no basis == total_cash == the pre-selector behaviour",
          ER.expected_cash(row0907), ER.expected_cash(row0907, "total_cash"))
    check("the module's declared default is total_cash", ER.ENVELOPE_BASIS_DEFAULT, "total_cash")
    for bad in ("", None, "manual", "nonsense", "STORE CASH"):
        check("an unrecognised basis %r degrades to the DEFAULT, not to 0" % (bad,),
              ER.expected_cash(row0907, bad), 1002.0)
    check("CONTRAST: deposit_recon's own normalizer would have folded it to 'manual'",
          DR._normalize_basis("nonsense"), "manual")
    check("...which would have rendered the receipt as zeros",
          DR.cash_for_basis(1002.0, 230.0, "nonsense"), 0.0)
    legacy = {"store_cash": 250.0, "epay_on_cash": 40.0}
    check("a legacy row with no t_cash keeps the store_cash fallback (total)",
          ER.expected_cash(legacy, "total_cash"), 250.0)
    check("...and the fallback feeds the other bases too (store)",
          ER.expected_cash(legacy, "store_cash"), 210.0)

    # ── F. the short/over math stays in ONE place ────────────────────────────────────────────────
    print("\nD. the short/over math follows the basis from one place")
    rr = ER.report_row({"id": "r", "close_date": "2026-09-07", "store_code": "B-559",
                        **row0907}, None, None, None, "NJ", basis="store_cash")
    check("report_row reports the basis it used", rr["basis"], "store_cash")
    check("declared_cash follows the basis", rr["declared_cash"], 772.0)
    check("both components are reported beside it (so 'why 0?' is answerable)",
          (rr["declared_total_cash"], rr["declared_billpay_cash"]), (1002.0, 230.0))
    cf = ER.count_fields(ER.expected_cash(row0907, "store_cash"), 772.0)
    check("counting 772 on the store_cash basis is a MATCH, not a $230 shortage",
          (cf["variance"], cf["status"]), (0.0, "match"))
    cf2 = ER.count_fields(ER.expected_cash(row0907, "total_cash"), 772.0)
    check("...while on the whole drawer it reads short by exactly the bill-pay cash",
          (cf2["variance"], cf2["status"]), (-230.0, "short"))

    # ── G. the screen and the router spell no basis word ────────────────────────────────────────
    print("\nE. no basis word or label is spelled outside the pure module")
    check("the endpoint normalizes through the pure module",
          "envelope_report_mod.normalize_envelope_basis(basis)" in rsrc)
    check("the endpoint serves the option list from the pure module, built there",
          "envelope_report_mod.basis_options(" in rsrc)
    check("...and never indexes the label dict itself (the wording stays in ONE place)",
          "ENVELOPE_BASIS_LABELS[" not in rsrc)

    # ── THE PROCESSOR'S NAME IS RESOLVED, NEVER SPELLED (harness_carrier_vocab_guard's class) ────
    # The first cut of this selector wrote 'ePay' into the label, so a Total-side reader would have been
    # told their drawer held "Bill payments (ePay)". The brand has ONE home — the report_labels
    # `processor` term (mig 953) — and these rules keep it there.
    brands = set()
    for mg in sorted(os.listdir(os.path.join(os.path.dirname(HERE), "database", "migrations"))):
        if "_carrier_vocab" in mg or mg.startswith("953"):
            txt = open(os.path.join(os.path.dirname(HERE), "database", "migrations", mg),
                       encoding="utf-8").read()
            brands |= {m.group(1) for m in re.finditer(r"'report_term:\w+',\s*'\w+',\s*'([^']+)'", txt)}
    check("the mig-953 brand list was actually derived (not an empty set that can never fail)",
          len(brands) >= 3 and any(b for b in brands))
    lbl_src = " ".join(ER.ENVELOPE_BASIS_LABELS.values())
    check("no carrier brand is spelled in the pure module's basis labels",
          [b for b in sorted(brands) if b.lower() in lbl_src.lower()], [])
    check("the bill-payment label carries the {processor} SLOT instead", "{processor}" in lbl_src)
    check("the term key is the registry's own, not a new one", ER.ENVELOPE_BASIS_TERM_KEY, "processor")
    check("a resolved term fills the slot",
          ER.basis_label("bill_payment_cash", "ePay"), "Bill payments (ePay) cash only")
    check("...an unresolved term DROPS the parenthetical, never leaving a raw slot",
          ER.basis_label("bill_payment_cash", ""), "Bill payments cash only")
    check("...and never guesses a brand",
          [b for b in sorted(brands) if b.lower() in ER.basis_label("bill_payment_cash", "").lower()], [])
    check("basis_options covers every basis, in order",
          [o["key"] for o in ER.basis_options("ePay")], list(ER.ENVELOPE_BASES))
    check("the endpoint resolves the term through the ONE home",
          "_carrier_term(client, org_id, envelope_report_mod.ENVELOPE_BASIS_TERM_KEY)" in rsrc)
    ct = rsrc[rsrc.index("def _carrier_term("):]
    ct = ct[:ct.index("\n\n\n")] if "\n\n\n" in ct else ct[:600]
    check("..._carrier_term dereferences report_labels.carrier_term, not a hand-rolled lookup",
          "report_labels.carrier_term(client, org_id, key)" in ct.replace("_report_labels.", "report_labels."))
    check("the screen spells no carrier brand at all",
          [b for b in sorted(brands) if b.lower() in pcode.lower()], [])
    for word in ("total_cash", "store_cash", "bill_payment_cash"):
        check("the screen does not hardcode the basis key %r" % word, word not in pcode)
    check("the screen renders the SERVER's option list", "data?.basis_options" in page)

    # ── H. counted_by ───────────────────────────────────────────────────────────────────────────
    print("\nF. counted_by is the real actor, never a sentinel")
    j = rsrc.index("def save_envelope_count(")
    hbody = rsrc[j:rsrc.index("\n@router", j)]
    check("the handler takes the Authorization header", "authorization: str = Header" in hbody)
    check("it stamps the ONE home _caller_uid", "_caller_uid(authorization)" in hbody)
    check("...imported, not re-implemented",
          "from app.modules.commcalc.router import _caller_uid" in hbody)
    check("it never writes the old 'management' sentinel",
          '"management"' not in re.sub(r"(?m)^\s*#.*$", "", hbody))
    check("an unresolved actor is None (the database's own unknown), not a word",
          re.search(r'"counted_by":\s*\(_caller_uid\(authorization\)\s*or\s*prior\.get\("counted_by"\)\s*or\s*None\)', hbody) is not None)
    check("a prior counter is preserved when this save cannot resolve one",
          'prior.get("counted_by")' in hbody)

    print("\nG. uid -> person has ONE home, and it is safe")
    class FC:
        def __init__(s, rows, raise_it=False):
            s.rows, s.raise_it, s.seen = rows, raise_it, {}
        def schema(s, x): s.seen["schema"] = x; return s
        def table(s, x): s.seen["table"] = x; return s
        def select(s, x): s.seen["select"] = x; return s
        def eq(s, k, v): s.seen[k] = v; return s
        def in_(s, k, v): s.seen["in_" + k] = v; return s
        def execute(s):
            if s.raise_it:
                raise RuntimeError("boom")
            org = s.seen.get("org_id")
            keep = [r for r in s.rows if r.get("org_id") == org]
            return types.SimpleNamespace(data=[{k: v for k, v in r.items() if k != "org_id"} for r in keep])

    U1 = "11111111-1111-1111-1111-111111111111"
    U2 = "22222222-2222-2222-2222-222222222222"
    U3 = "33333333-3333-3333-3333-333333333333"
    rows = [{"org_id": "A", "auth_id": U1, "full_name": "A Person", "email": "a@x.com"},
            {"org_id": "A", "auth_id": U2, "full_name": "", "email": "noname@x.com"},
            {"org_id": "B", "auth_id": U3, "full_name": "Other Org", "email": "o@y.com"}]
    c = FC(rows)
    got = A.actor_names([U1, U2, U3], "A", client=c)
    check("the name wins", got.get(U1), "A Person")
    check("email is the fallback only when there is no name", got.get(U2), "noname@x.com")
    check("ANOTHER ORG's uid never resolves (org-scoped)", U3 in got, False)
    check("only the asked uids are read (no table scan)",
          sorted(c.seen.get("in_auth_id") or []), sorted([U1, U2, U3]))
    check("it reads ONLY the name columns", c.seen.get("select"), "auth_id,full_name,email")
    check("from storeops.app_users", (c.seen.get("schema"), c.seen.get("table")),
          ("storeops", "app_users"))
    check("no uids / no org short-circuits without a read",
          A.actor_names([], "A", client=c) == {} and A.actor_names([U1], "", client=c) == {})
    check("a failed read degrades to {} and never raises",
          A.actor_names([U1], "A", client=FC(rows, raise_it=True)), {})
    check("name_for falls back to the uid rather than printing None",
          (A.name_for(U1, got), A.name_for(U3, got), A.name_for(None, got)),
          ("A Person", U3, None))
    others = [p for p in os.listdir(os.path.join(HERE, "app", "core")) if p.endswith(".py")]
    dupes = [p for p in others if p != "actors.py"
             and "def actor_names(" in read(os.path.join(HERE, "app", "core", p))]
    check("no second uid->name resolver under app/core: %s" % (dupes or "none"), dupes, [])

    # ── THE WIRING (owner bug 2026-10-02) ───────────────────────────────────────────────────────
    # The selector above shipped CORRECT and read 0 anyway: `expected_cash` dereferences
    # `epay_on_cash`, and BOTH of its callers hand-spelled a daily_closing column list that did not
    # include it. `.get()` cannot tell "0 dollars" from "never fetched", so the receipt showed
    # bill-payment cash 0.00 on every line and store cash as the whole drawer, and nothing failed.
    # The class is §19.18's again, in its quietest form: the unwired thing was a SELECT LIST. So the
    # columns a pure reader dereferences are DECLARED beside it and every caller selects that
    # declaration — a reader that starts reading a new column fails this build until the tuple names
    # it, and every caller then fetches it.
    print("\nH1. the columns the basis split reads are DECLARED, and every caller selects them")
    check("CLOSING_COLUMNS is the one column contract, and declares the split's whole input",
          ("epay_on_cash" in ER.CLOSING_COLUMNS and "t_cash" in ER.CLOSING_COLUMNS
           and "store_cash" in ER.CLOSING_COLUMNS), True)
    check("CLOSING_SELECT is DERIVED from it, never a second hand-written list",
          ER.CLOSING_SELECT, ",".join(ER.CLOSING_COLUMNS))
    # THE LOCK: every closing-row key the pure module reads must be in the tuple. `r` is bound to
    # `closing_row or {}` in exactly the functions that take a closing row, so the scan is exact.
    etree = ast.parse(read(os.path.join(HERE, "app", "modules", "closing", "envelope_report.py")))
    row_fns = [n for n in ast.walk(etree)
               if isinstance(n, ast.FunctionDef) and any(a.arg == "closing_row" for a in n.args.args)]
    check("the closing-row readers are discoverable by their `closing_row` parameter",
          {"declared_total_cash", "expected_cash", "report_row"} <= {f.name for f in row_fns}, True)
    undeclared = sorted({
        c.args[0].value
        for f in row_fns for c in ast.walk(f)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "get"
        and isinstance(c.func.value, ast.Name) and c.func.value.id in ("r", "closing_row")
        and c.args and isinstance(c.args[0], ast.Constant) and isinstance(c.args[0].value, str)
        and c.args[0].value not in ER.CLOSING_COLUMNS})
    check("no closing-row column is read without being declared (add it to CLOSING_COLUMNS): %s"
          % (undeclared or "none"), undeclared, [])
    # THE LOCK: no caller hand-spells a column list for the rows it feeds to those readers.
    rtree = ast.parse(rsrc)
    callers = [n for n in ast.walk(rtree)
               if isinstance(n, ast.FunctionDef)
               and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                       and c.func.attr in ("report_row", "expected_cash", "declared_components")
                       and isinstance(c.func.value, ast.Name)
                       and c.func.value.id == "envelope_report_mod"
                       for c in ast.walk(n))]
    check("the router's envelope-receipt handlers are discoverable",
          {"envelope_report", "save_envelope_count"} <= {f.name for f in callers}, True)
    for f in callers:
        body = ast.get_source_segment(rsrc, f) or ""
        if 'table("daily_closing")' not in body:
            continue        # a handler that reads no closing row has no column list to get wrong
        check("`%s` selects envelope_report.CLOSING_SELECT" % f.name,
              "envelope_report_mod.CLOSING_SELECT" in body, True)
        rest = body.replace("envelope_report_mod.CLOSING_SELECT", "")
        check("`%s` spells no daily_closing money column of its own" % f.name,
              [c for c in ("t_cash", "epay_on_cash", "store_cash") if '"%s,' % c in rest
               or ',%s"' % c in rest or ',%s,' % c in rest], [])
    # The SIBLING consumer of the same formulas must fetch the same column — one of the two paths
    # fixed and the other not is the same defect wearing a hat.
    check("deposit_recon's own query carries the split's input too",
          'select("store_code,close_date,t_cash,store_cash,epay_on_cash")'
          in read(os.path.join(HERE, "app", "modules", "closing", "deposit_recon.py")), True)

    print("\nH2. a count is scored on the basis the counter was looking at")
    # Counting the bill-payment or store basis against the whole drawer reads as a huge shortage —
    # and a shortage can become a CHARGEBACK against the rep. The basis has to reach the handler.
    check("EnvelopeCountIn accepts a basis",
          re.search(r"class EnvelopeCountIn\(LaxModel\):(.|\n)*?\n\n", rsrc).group(0).count("basis") >= 1, True)
    check("the save handler normalizes the incoming basis through the pure module",
          "normalize_envelope_basis(payload.basis)" in hbody)
    check("...and passes it to expected_cash instead of taking the bare default",
          "expected_cash(crow, _basis)" in hbody)
    check("an absent basis still means the historical default (every stored count keeps its meaning)",
          ER.expected_cash(row0907, None), ER.expected_cash(row0907, "total_cash"))
    check("the screen sends the basis it is showing",
          "basis," in page.split("api('/api/v1/closing/envelope-count'")[1][:900], True)

    print("\nH3. every basis stands beside the chosen one, on screen and in the export")
    rr3 = ER.report_row({"id": "r", **row0907}, None, None, None, "NJ", basis="bill_payment_cash")
    check("report_row carries a figure per basis, keyed by basis",
          rr3["declared"], {"total_cash": 1002.0, "store_cash": 772.0, "bill_payment_cash": 230.0})
    check("the flat keys the payload has carried since 2026-10-01 still answer",
          (rr3["declared_total_cash"], rr3["declared_billpay_cash"]), (1002.0, 230.0))
    check("basis_options carries the column-header form of the same words",
          [o["short"] for o in ER.basis_options("ePay")],
          ["Total cash", "Store cash", "Bill payments (ePay)"])
    check("...with the same slot rule — an unresolved term drops the parenthetical",
          [o["short"] for o in ER.basis_options("")][2], "Bill payments")
    check("the screen renders a column per SERVER option, spelling no basis key",
          "basisCols" in pcode and "r.declared?.[b.key]" in pcode, True)

    # ── THE CASH IS NAMED FOR WHAT IT IS (owner 2026-10-02, index §47.9) ───────────────────────
    # Owner: "Store cash in DM Verify is the total cash in the store, need one more field which shows
    # the store cash - which is total store cash - epay cash as declared by the users". The day-1
    # `store_cash` column holds the WHOLE drawer for a mig103+ row and EXCLUDES the bill-payment cash
    # for a pre-mig103 one — one column, two meanings — while the rest of the platform already defines
    # store cash as the NET figure. A surface that has to show both must not be the place that decides
    # what either one is, so the SPLIT has one home beside the formulas and every surface reads it.
    print("\nI1. the cash split has ONE home, and every surface dereferences it")
    check("deposit_recon.cash_components is the split, and it IS cash_for_basis",
          all(DR.cash_components(1002.0, 230.0)[b] == DR.cash_for_basis(1002.0, 230.0, b)
              for b in DR.DERIVED_BASES), True)
    check("...covering every basis that has a formula, and no 'manual'",
          sorted(DR.cash_components(1002.0, 230.0)), sorted(DR.DERIVED_BASES))
    check("the envelope receipt dereferences it (not its own loop over expected_cash)",
          "deposit_recon.cash_components(" in read(
              os.path.join(HERE, "app", "modules", "closing", "envelope_report.py")), True)
    check("/closing/summary dereferences it too — one derivation, two screens",
          rsrc.count("deposit_recon.cash_components(") >= 2, True)
    # THE LOCK: nobody re-derives the net. An AST scan for "a cash total MINUS something ePay" —
    # comments and docstrings legitimately explain the formula (that is where the one home is named),
    # so the scan is over expressions, not text. The bill-pay recon's own `declared - processor`
    # variances are a different question and do not match (their LEFT side is the ePay figure).
    def net_derivations_in(src):
        out = []
        for n in ast.walk(ast.parse(src)):
            if not (isinstance(n, ast.BinOp) and isinstance(n.op, ast.Sub)):
                continue
            left = (ast.get_source_segment(src, n.left) or "").lower()
            right = (ast.get_source_segment(src, n.right) or "").lower()
            if "epay" in right and any(w in left for w in ("t_cash", "store_cash", "total", "drawer")):
                out.append(ast.get_source_segment(src, n))
        return out

    def net_derivations(path):
        return net_derivations_in(read(path))

    _closing = os.path.join(HERE, "app", "modules", "closing")
    check("the ONE home derives the net, and it is cash_for_basis",
          net_derivations(os.path.join(_closing, "deposit_recon.py")), ["_f(t_cash) - _f(epay_cash)"])
    for mod in ("router.py", "envelope_report.py", "verified_overlay.py", "pickup_actual.py"):
        check("`closing/%s` does not derive the net itself" % mod,
              net_derivations(os.path.join(_closing, mod)), [])
    check("...and money_recon reads the named drawer instead of repeating its expression",
          'closing_cash = totals["total_store_cash"]' in rsrc, True)

    print("\nI2. DM Verify names the drawer and shows the net beside it")
    dmv = read(os.path.join(os.path.dirname(HERE), "frontend", "src", "components",
                            "DailyClosingVerify.tsx"))
    dmv_code = re.sub(r"(?m)^\s*(//|\*|/\*).*$", "", dmv)
    dmv_code = re.sub(r"\{/\*.*?\*/\}", "", dmv_code, flags=re.S)
    # THE RULE, stated as what must NOT be on the screen: the raw `store_cash` field is the column
    # with two meanings (the drawer for a modern row, the net for a legacy one), so DM Verify must
    # never RENDER it — every figure it shows comes from the server's named keys. Spelled as the
    # render expressions themselves so the rule cannot be satisfied by the phrase existing elsewhere
    # in the file (which is exactly how a looser first cut of this check failed to bite).
    _raw_renders = [x for x in ("fmt(t.store_cash)", "fmt(r.store_cash)", "=> r.store_cash ",
                                "r.totals?.store_cash ", "r.totals_original.store_cash",
                                "totals_original ? r.totals_original.store_cash")
                    if x in dmv_code]
    check("DM Verify renders the two-meaning `store_cash` column NOWHERE: %s" % (_raw_renders or "none"),
          _raw_renders, [])
    check("the drawer is labelled as a TOTAL and read from the named key",
          '"Total store cash" value={fmt(t.total_store_cash)}' in dmv_code, True)
    check("the net figure has its own column, read from the server's named key",
          '"Store cash" value={fmt(t.store_cash_net)}' in dmv_code, True)
    check("the per-rep table reads the server's split, not the raw column",
          "_cash_split?.total_cash" in dmv_code and "_cash_split?.store_cash" in dmv_code, True)
    check("the screen derives NEITHER figure itself",
          [x for x in ("- t.epay_on_cash", "- r.epay_on_cash", "-t.epay_on_cash")
           if x in dmv_code.replace(" ", " ")], [])
    check("the DM's correction field is named for what it corrects (the cash TOTAL)",
          'Lbl t="Total store cash"' in dmv_code, True)
    check("...and prefills from the drawer, so a legacy store's DM is not shown the net as a total",
          "t.total_store_cash ?? t.store_cash" in dmv_code, True)

    print("\nI3. the live-form mirror cannot drift, and is the only copy on the frontend")
    # A half-filled SUBMIT form has no server figure to read, so the net is computed in the browser
    # (owner 2026-10-02: "add the store cash box on the daily closing also, which will be calculated
    # and greyed out"). That is the ONE legitimate frontend derivation, so it has one home and this
    # rule keeps it honest against deposit_recon's.
    FE = os.path.join(os.path.dirname(HERE), "frontend", "src")
    mirror = read(os.path.join(FE, "lib", "cash-basis.ts"))
    check("the mirror names the backend home it mirrors",
          "deposit_recon.cash_components" in mirror, True)
    check("the mirror's bases are deposit_recon's, exactly",
          sorted(re.findall(r"^\s{4}(\w+):", mirror, re.M)), sorted(DR.DERIVED_BASES))
    check("the mirror's net is max(total - billPay, 0) — the same formula, floored the same way",
          "Math.max(total - billPay, 0)" in mirror, True)
    check("...and the total / bill-payment legs are pass-throughs, as they are in the one home",
          "total_cash: total" in mirror and "bill_payment_cash: billPay" in mirror, True)
    check("the mirror rounds to cents like the backend's _f",
          "Math.round(" in mirror and "100) / 100" in mirror, True)
    # THE LOCK: no SECOND copy of this arithmetic anywhere under frontend/src. Comments may explain it.
    second_copies = []
    for root, _dirs, files in os.walk(FE):
        for fn in files:
            if not fn.endswith((".ts", ".tsx")) or fn == "cash-basis.ts":
                continue
            fp = os.path.join(root, fn)
            body = re.sub(r"(?m)^\s*(//|\*|/\*).*$", "", read(fp))
            body = re.sub(r"\{/\*.*?\*/\}", "", body, flags=re.S)
            # The operand char class allows parens/spaces so `enteredCash - (parseFloat(x) || 0)`
            # matches too — a first cut stopped at the `(` and the rule missed exactly that shape.
            for mm in re.finditer(r"(?:t_cash|total_cash|enteredCash|total_store_cash)"
                                  r"[\w.?\[\]'\"() |]*\s-\s[\w.?\[\]'\"() |]*epay", body):
                second_copies.append("%s: %s" % (os.path.relpath(fp, FE), mm.group(0)))
    check("no second copy of the net subtraction under frontend/src: %s" % (second_copies or "none"),
          second_copies, [])
    submit = read(os.path.join(FE, "components", "ClosingSubmitForm.tsx"))
    check("the daily-closing form READS the mirror",
          "from '@/lib/cash-basis'" in submit and "storeCashNet(" in submit, True)
    check("...and shows it read-only (calculated, greyed — never a field the rep can type into)",
          re.search(r"Store cash \(total cash less bill payments[^<]*<div style=\{\{ \.\.\.inp,"
                    r" background: 'var\(--surface2\)'", submit, re.S) is not None, True)
    check("...and never SUBMITS it (the server recomputes from t_cash and epay_on_cash)",
          "store_cash:" not in re.sub(r"(?m)^\s*//.*$", "", submit), True)

    print("\nI4. a row rebuilt from ONLY the fetched columns still answers all three bases")
    # The behavioural form of H1's rule, and the one that would have caught the 2026-10-02 defect on
    # its own: take the column contract, build a row from NOTHING ELSE, and the bases must differ.
    _fetched_row = {c: {"t_cash": 1002.0, "epay_on_cash": 230.0}.get(c, None) for c in ER.CLOSING_COLUMNS}
    check("a row carrying only CLOSING_COLUMNS yields three DIFFERENT, reconciling bases",
          ER.declared_components(_fetched_row),
          {"total_cash": 1002.0, "store_cash": 772.0, "bill_payment_cash": 230.0})
    check("...and nothing the contract fetches is unread by the module",
          [c for c in ER.CLOSING_COLUMNS
           if c not in read(os.path.join(HERE, "app", "modules", "closing", "envelope_report.py"))], [])

    # ── I. armed negative controls ──────────────────────────────────────────────────────────────
    print("\nJ. CONTROLS — each rule goes RED with the defect patched back in")
    check("CONTROL: the pre-fix handler's sentinel would be caught",
          '"management"' in '"counted_by": (payload.counted_by or prior or "management"),')
    check("CONTROL: a hand-written basis formula instead of the dereference → RED",
          re.search(r"t_cash.*-.*epay", "    return max(_f(r.get('t_cash')) - _f(r.get('epay_on_cash')), 0)") is not None)
    check("CONTROL: a basis key hardcoded on the screen → RED",
          "store_cash" in '<option value="store_cash">Store cash</option>')
    check("CONTROL: folding an unknown basis to deposit_recon's 'manual' would zero a receipt",
          DR.cash_for_basis(1002.0, 230.0, DR._normalize_basis("typo")), 0.0)
    check("CONTROL: a resolver that ignored org_id would leak another org's person",
          [r for r in rows if r["auth_id"] == U3][0]["full_name"], "Other Org")
    # The brand rules, armed: the label as it SHIPPED (and as the vocab guard caught it) must trip them.
    shipped = "Bill payments (ePay) cash only"
    check("CONTROL: the brand written into the label → RED",
          [b for b in sorted(brands) if b.lower() in shipped.lower()] != [])
    check("CONTROL: a label that kept the slot unfilled → RED (a raw {processor} reaches the reader)",
          "{processor}" in ER.ENVELOPE_BASIS_LABELS["bill_payment_cash"].replace("{processor}", "{processor}")
          and "{processor}" not in ER.basis_label("bill_payment_cash", "ePay"))
    check("CONTROL: the endpoint going back to indexing the label dict → RED",
          "ENVELOPE_BASIS_LABELS[" in 'x = envelope_report_mod.ENVELOPE_BASIS_LABELS[b]')
    # The WIRING rules, armed with the select list exactly as it shipped on 2026-10-01.
    shipped_select = ('.select("id,close_date,store_code,store_name,store_address,employee_name,"\n'
                      '        "t_cash,store_cash,envelope_picture,remarks")')
    check("CONTROL: the hand-spelled select list → RED",
          "envelope_report_mod.CLOSING_SELECT" not in shipped_select
          and [c for c in ("t_cash", "epay_on_cash", "store_cash")
               if '"%s,' % c in shipped_select or ',%s,' % c in shipped_select] != [])
    check("CONTROL: and it is exactly what the owner saw — bill-pay 0, store cash = the whole drawer",
          (ER.expected_cash({"t_cash": 1002.0}, "bill_payment_cash"),
           ER.expected_cash({"t_cash": 1002.0}, "store_cash")), (0.0, 1002.0))
    check("CONTROL: a reader that starts reading an undeclared column → RED",
          "dm_epay_cash" not in ER.CLOSING_COLUMNS)
    # The naming rules, armed with the label and the derivation exactly as they stood on 2026-10-01.
    # The naming rules, armed against the screen exactly as it stood before this change.
    _shipped_tile = '<Stat label="Store cash" value={fmt(t.store_cash)} />'
    check("CONTROL: the shipped tile rendered the two-meaning column → RED",
          [x for x in ("fmt(t.store_cash)",) if x in _shipped_tile] != [])
    check("CONTROL: ...and it is not the named drawer the rule requires",
          '"Total store cash" value={fmt(t.total_store_cash)}' not in _shipped_tile)
    check("CONTROL: a screen deriving the net itself → RED",
          [x for x in ("- t.epay_on_cash",) if x in "{fmt(t.store_cash - t.epay_on_cash)}"] != [])
    check("CONTROL: the scan SEES a caller that sneaks the subtraction back in",
          net_derivations_in('x = totals["store_cash"] - totals["epay_on_cash"]'),
          ['totals["store_cash"] - totals["epay_on_cash"]'])
    check("CONTROL: ...and leaves the bill-pay recon's own declared-vs-processor variance alone",
          net_derivations_in("v = closing_epay - pos_billpay"), [])
    check("CONTROL: a form inlining the subtraction instead of the mirror → RED",
          [x for x in (re.search(r"(?:t_cash|total_cash|enteredCash|total_store_cash)"
                                 r"[\w.?\[\]'\"() |]*\s-\s[\w.?\[\]'\"() |]*epay",
                                 "{fmt(enteredCash - (parseFloat(f.epay_on_cash) || 0))}"),)
           if x] != [])
    check("CONTROL: a row built WITHOUT the contract collapses the bases (the shipped defect)",
          ER.declared_components({"t_cash": 1002.0}),
          {"total_cash": 1002.0, "store_cash": 1002.0, "bill_payment_cash": 0.0})
    check("CONTROL: the legacy column read as a total is WRONG for a legacy row, and the split says so",
          DR.cash_components(round(80.0 + 30.0, 2), 30.0),
          {"total_cash": 110.0, "store_cash": 80.0, "bill_payment_cash": 30.0})

    print("\n" + "=" * 96)
    print("RESULT: %d passed, %d failed" % (_p, _f))
    print("=" * 96)
    if _f:
        print("FAIL  the receipt's basis or its counted-by attribution has regressed")
        return 1
    print("OK  one basis vocabulary, dereferenced; counted_by is the real actor, shown as a person")
    return 0


if __name__ == "__main__":
    sys.exit(main())
