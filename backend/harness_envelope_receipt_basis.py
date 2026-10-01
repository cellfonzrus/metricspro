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

WHAT FAILS THE BUILD HERE: a second basis formula; a basis word or label spelled on the screen or in
the router; the default drifting off the historical figure; an unknown basis folding to 'manual' (which
would render a whole receipt as zeros); the legacy store_cash fallback being lost; the count math
leaving `count_fields`; `counted_by` carrying a sentinel again or not using the one home; a second
uid->name resolver; `core.actors` reading a column it has no business reading, dropping its org scope,
or raising.

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
    check("the endpoint serves the option list from the pure module's labels",
          "ENVELOPE_BASIS_LABELS" in rsrc and "basis_options" in rsrc)
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

    # ── I. armed negative controls ──────────────────────────────────────────────────────────────
    print("\nH. CONTROLS — each rule goes RED with the defect patched back in")
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
