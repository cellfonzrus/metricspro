"""BUILD GATE — what a customer still owes us, and how late (index §POS-19).

OWNER ASK 2026-10-31. Having applied migrations 1106 and 1107 the owner asked for the two reports
the Terms tender and the wholesale W-9 imply: *"build them"* — the open terms balances, and the
W-9s about to lapse.

WHAT THIS GATE HOLDS, AND WHY EACH PART OF IT IS A CHECK AND NOT A SENTENCE IN A DOC
───────────────────────────────────────────────────────────────────────────────────
  §A  ONE HOME. The receivable question is answered in `pos/receivable.py` and the report
      dereferences it. No page and no endpoint subtracts a balance or ages a row.
  §B  A SALE WITH NO DEFERRED LEG IS NOT A RECEIVABLE OF $0.00 — it is not a receivable. 4,603 of
      the 4,605 live sales carry no payment row (measured 2026-10-31); this is the property that
      keeps them off a collections queue.
  §C  NOT YET DUE IS NOT LATE, AND IT IS NOT NOTHING.
  §D  AN AGE THAT CANNOT BE MEASURED IS NEVER THE NEWEST BAND.
  §E  THE AGING LADDER IS DEREFERENCED, never respelled. A second list of what "61-90 days" means
      is the duplicate defect the index rules forbid.
  §F  THE SIBLING §POS-17 SHARES. A terms leg is not money the till has — proved here as well as in
      `harness_pos_sale_settlement.py` §M, because this module is the other caller of that fact and
      the two must not be able to disagree.
  §G  WHAT IS NOT BUILT IS SAID. Taking a payment against an open balance does not exist, so the
      payload carries that sentence and the page shows it. A report that looked complete would have
      somebody chasing customers who had already paid.
  §H  THE W-9 LAPSE DATE HAS ONE HOME and the verdict a sale is blocked on reads it, so a manager
      cannot be told a certificate is good on a day the register refuses it.
  §I  THE FEATURE DEGRADES UNTIL ITS MIGRATION LANDS. Both reports answer `available: false` by
      name; an empty table would read as "nobody owes us anything" and "no certificate is missing".
  §J  EVERY TENANT. No tenant's vocabulary and no org id appears in the module, the span is checked
      before it is used, and nothing declared is said in words rather than shown as all-clear.
  §K  CONTROLS — each of the above really fails when the property is removed.

Run: `cd backend && python3 harness_pos_receivable.py`
"""
import ast
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.pos import receivable as R                                     # noqa: E402
from app.modules.pos import sale_settlement as S                                # noqa: E402
from app.modules.pos import wholesale_w9 as W                                   # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_passed = _failed = 0


def ok(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}" + (f"  [{detail}]" if detail else ""))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


MOD = _read("backend/app/modules/pos/receivable.py")
ROUTER = _read("backend/app/modules/pos/router.py")
W9MOD = _read("backend/app/modules/pos/wholesale_w9.py")
PAGE = _read("frontend/src/app/(platform)/pos/open-balances/page.tsx")
W9PAGE = _read("frontend/src/app/(platform)/pos/w9-status/page.tsx")
INDEX = _read("docs/SYSTEM_DATA_FLOW_INDEX.md")


def _code_only(text: str) -> str:
    """The module with every comment and docstring removed, so a lock asserting "this word appears
    in no branch" reads code rather than prose. Same filter `harness_pos_sale_settlement` uses, and
    for the same reason it was written: a lock that matches its own explanation holds nothing
    ([[harness-locks-can-pass-on-prose]])."""
    import io
    import tokenize as _tk
    docs = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:                                         # pragma: no cover
        return text
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docs.add(d)
    out = []
    try:
        for tok in _tk.generate_tokens(io.StringIO(text).readline):
            if tok.type == _tk.COMMENT:
                continue
            if tok.type == _tk.STRING and tok.string.strip("rbuf")[1:-1] in docs:
                continue
            if tok.type == _tk.STRING:
                try:
                    if ast.literal_eval(tok.string) in docs:
                        continue
                except (ValueError, SyntaxError):
                    pass
            out.append(tok.string)
            if tok.type in (_tk.NEWLINE, _tk.NL):
                out.append("\n")
            else:
                out.append(" ")
    except _tk.TokenError:                                      # pragma: no cover
        return text
    return "".join(out)


CODE = _code_only(MOD)

#: The endpoint regions, each bounded BY ITS OWN END (`\n@router.`) rather than by whatever endpoint
#: happens to be written next — the brittleness §POS-17's A3 paid for on 2026-10-31.
def _region(decorator: str) -> str:
    parts = ROUTER.split(decorator, 1)
    return re.split(r"\n@router\.", parts[1], maxsplit=1)[0] if len(parts) == 2 else ""


BAL = _region('@router.get("/reports/open-balances")')
W9R = _region('@router.get("/reports/w9-status")')

TODAY = dt.date(2026, 10, 31)


def sale(**kw):
    return {"id": "s1", "total": 100.0, "balance": 0.0, "status": "completed", **kw}


def terms(amount=100.0, days=30, due="2026-11-30"):
    return {"payment_method": "terms", "amount": amount, "terms_days": days, "due_date": due}


def cash(amount=100.0):
    return {"payment_method": "cash", "amount": amount}


# ── §A one home ──────────────────────────────────────────────────────────────────────────────────
print("\n§A  one home for the receivable, dereferenced by the report")
ok("A1. the module answers the question at all",
   R.assess(sale(balance=100.0), [terms()], as_of=TODAY)["receivable"] is True)
ok("A2. both regions exist and are bounded by their own end",
   0 < len(BAL) < 9000 and 0 < len(W9R) < 9000, f"{len(BAL)}/{len(W9R)}")
ok("A3. the open-balance report dereferences the one home", "_recv.assess(" in BAL)
ok("A4. ...for the rollup too, rather than totalling the rows itself", "_recv.summarise(" in BAL)
ok("A5. ...and for the sentence on each row", "_recv.sentence(" in BAL)
ok("A6. the W-9 report dereferences the verdict the REGISTER is blocked by",
   "_w9.may_sell(" in W9R)
ok("A7. ...and the lapse date from the same home", "_w9.expiry_date(" in W9R)
# The screen must hold no arithmetic. These are the shapes a page would reach for.
_SUBTRACTS = re.compile(r"(balance|outstanding|total)\s*-\s*(balance|outstanding|total|paid)")
#: The days-between idiom. A page that divides a millisecond difference by a day is aging a row in
#: the browser, which is the one arithmetic this screen must never do: the server resolved `as_of`
#: so that a report and a regression test see the same day. (The default date RANGE is computed
#: from the clock and that is fine — it chooses what to ask for, it does not answer anything.)
_AGES = re.compile(r"/\s*86_?400_?000|/\s*\(\s*1000\s*\*\s*60\s*\*\s*60\s*\*\s*24")
ok("A8. the open-balance PAGE subtracts no money and ages no row against the browser clock",
   not _SUBTRACTS.search(PAGE) and not _AGES.search(PAGE))
ok("A9. ...and spells no aging band of its own — it renders the server's list",
   not re.search(r"'0-7'|\"0-7\"|'31-60'|\"31-60\"|'90\+'|\"90\+\"", PAGE)
   and "data.bands.map" in PAGE)
ok("A10. the two endpoints are the ONLY places the report figures are built",
   ROUTER.count("_recv.assess(") == 1)


# ── §B a sale with no deferred leg is not a receivable of $0.00 ──────────────────────────────────
print("\n§B  absence is never zero — the 4,603-row property, in its second home")
_cashonly = R.assess(sale(), [cash()], as_of=TODAY)
ok("B1. a sale paid at the till is NOT a receivable", _cashonly["receivable"] is False)
ok("B2. ...with state not_receivable, not a $0.00 debt",
   _cashonly["state"] == R.NOT_RECEIVABLE and _cashonly["outstanding"] == 0.0)
ok("B3. ...and a reason a screen can show", bool(_cashonly["reason"]))
_none = R.assess(sale(), [], as_of=TODAY)
ok("B4. a sale with NO payment row is not a receivable either",
   _none["receivable"] is False and _none["state"] == R.NOT_RECEIVABLE)
ok("B5. ...and its reason is the absence, not 'everything settled'",
   _none["reason"] == R.NOT_RECEIVABLE_REASONS["tender_unrecorded"])
# A balance with no terms leg is the trap a `balance > 0` filter would fall into.
_bal_no_leg = R.assess(sale(balance=60.0), [cash(40.0)], as_of=TODAY)
ok("B6. REGRESSION — a part-tendered CASH sale carrying a balance is not a terms debt",
   _bal_no_leg["receivable"] is False)
ok("B7. the rollup adds nothing for a sale that is not a receivable",
   R.summarise([_cashonly, _none, _bal_no_leg])["open_total"] == 0.0)
ok("B8. ...and counts them honestly rather than dropping them",
   R.summarise([_cashonly, _none, _bal_no_leg])["not_receivable"] == 3)
ok("B9. the ENDPOINT drops them from the rows rather than listing $0.00 debts",
   "_recv.NOT_RECEIVABLE" in BAL and "continue" in BAL)
_big = [_none] * 4603
ok("B10. 4,603 unrecorded sales add $0.00 and 0 receivables",
   R.summarise(_big)["open_total"] == 0.0 and R.summarise(_big)["receivables"] == 0)


# ── §C not yet due is owed, and is not late ──────────────────────────────────────────────────────
print("\n§C  not yet due is not late, and it is not nothing")
_future = R.assess(sale(balance=100.0), [terms(due="2026-11-30")], as_of=TODAY)
ok("C1. a sale inside its terms is a receivable", _future["receivable"] is True)
ok("C2. ...in its own state", _future["state"] == R.OPEN_NOT_DUE)
ok("C3. ...carrying no age band, because it has no age", _future["band"] is None)
ok("C4. ...and counted as owed but not overdue",
   R.summarise([_future])["open_total"] == 100.0
   and R.summarise([_future])["not_due_total"] == 100.0
   and R.summarise([_future])["past_due_total"] == 0.0
   and R.summarise([_future])["past_due_count"] == 0)
_due_today = R.assess(sale(balance=100.0), [terms(due=TODAY.isoformat())], as_of=TODAY)
ok("C5. due TODAY is not yet late — a customer has the day they were given",
   _due_today["state"] == R.OPEN_NOT_DUE and _due_today["days_past_due"] == 0)
_late = R.assess(sale(balance=100.0), [terms(due="2026-10-01")], as_of=TODAY)
ok("C6. a day past the due date is past due", _late["state"] == R.OPEN_PAST_DUE)
ok("C7. ...by the right number of days", _late["days_past_due"] == 30)
ok("C8. the sentence states the figure and the lateness, and claims nothing else",
   R.sentence(_late) == "$100.00 is owed on this sale and is 30 days past due.")
ok("C9. a cleared balance is neither owed nor overdue",
   R.assess(sale(balance=0.0), [terms()], as_of=TODAY)["state"] == R.CLEARED)
ok("C10. a VOIDED terms sale is shown but never chased",
   R.assess(sale(balance=100.0, status="voided"), [terms()], as_of=TODAY)["state"] == R.VOIDED)
ok("C11. ...and adds nothing to what is owed",
   R.summarise([R.assess(sale(balance=100.0, status="voided"), [terms()],
                         as_of=TODAY)])["open_total"] == 0.0)


# ── §D an age that cannot be measured ────────────────────────────────────────────────────────────
print("\n§D  an age we cannot measure is never a fresh age")
_nodue = R.assess(sale(balance=100.0), [{"payment_method": "terms", "amount": 100.0}], as_of=TODAY)
ok("D1. a deferred leg with no due date is still a receivable", _nodue["receivable"] is True)
ok("D2. ...and its band is UNKNOWN, not the newest one", _nodue["band"] == R.BAND_UNKNOWN)
ok("D3. ...and it carries no day count somebody could sort on",
   _nodue["days_past_due"] is None)
ok("D4. ...and the reason says the age could not be measured", "cannot be measured" in _nodue["reason"])
ok("D5. the unknown band is in the rollup as a band, so the money is not lost",
   R.summarise([_nodue])["bands"][R.BAND_UNKNOWN] == 100.0)
ok("D6. the PAGE shows no day count for a row that is not past due",
   "r.state === 'past_due' ? r.days_past_due : null" in PAGE)
ok("D7. the unknown answer is the house one, dereferenced",
   R.BAND_UNKNOWN == __import__("app.modules.commcalc.manager_followup",
                                fromlist=["x"]).BAND_UNKNOWN)


# ── §E the aging ladder is dereferenced, never respelled ─────────────────────────────────────────
print("\n§E  one aging ladder, dereferenced")
from app.modules.commcalc import manager_followup as _age                       # noqa: E402
ok("E1. the bands ARE the house bands, by identity", R.BANDS is _age.AGE_BANDS)
ok("E2. the labels follow from them rather than being typed again",
   R.BAND_LABELS == tuple(lbl for (lbl, _l, _h) in _age.AGE_BANDS))
ok("E3. no band boundary is spelled in this module's CODE",
   not re.search(r"\b(0-7|8-30|31-60|61-90|90\+)\b", CODE))
ok("E4. ...and no day threshold literal stands in for one",
   not re.search(r"\b(?:7|30|60|90)\b\s*(?:<=|<|>=|>)", CODE))
ok("E5. the band a row gets is the house function's answer",
   R.assess(sale(balance=1.0), [terms(due="2026-08-01")], as_of=TODAY)["band"]
   == _age.age_band((TODAY - dt.date(2026, 8, 1)).days))
ok("E6. every house band is a key in the rollup, so a band with nothing in it is a zero",
   set(R.BAND_LABELS) <= set(R.summarise([])["bands"]))


# ── §F the sibling §POS-17 shares ───────────────────────────────────────────────────────────────
print("\n§F  a terms leg is not money the till has — the fact both homes read")
ok("F1. the settled total excludes a deferred leg", S.tendered([terms()])["total"] == 0.0)
ok("F2. ...and the deferred one is carried beside it",
   S.tendered([terms()])["deferred_total"] == 100.0)
ok("F3. REGRESSION — a sale wholly on terms is not a refund owed to the customer",
   S.outstanding(sale(balance=100.0), [terms()])["mismatch"] is False)
# CODE is the tokenized view, so spacing is normalised — match tokens, not source spelling.
ok("F4. this module asks that home rather than reading payment methods itself",
   bool(re.search(r"_settle\s*\.\s*settles_now\s*\(", CODE))
   and bool(re.search(r"_settle\s*\.\s*tendered\s*\(", CODE)))
ok("F5. ...so no tender word is spelled in this module's CODE",
   not re.search(r"(cash|credit_card|debit_card|card_external|\bterms\b)", CODE))
ok("F6. the two homes agree about what is deferred, by construction",
   R.assess(sale(balance=100.0), [terms()], as_of=TODAY)["deferred"]
   == S.tendered([terms()])["deferred_total"])
ok("F7. the ENDPOINT reads payment rows through the shared chunked read, not a loop of its own",
   "_sale_payments_by_sale(" in BAL and ROUTER.count("def _sale_payments_by_sale(") == 1)
ok("F8. ...and so does the mismatch log, so the two cannot page differently",
   ROUTER.count("_sale_payments_by_sale(") >= 3)


# ── §G what is not built is said ─────────────────────────────────────────────────────────────────
print("\n§G  the gap is reported, never papered over")
ok("G1. the module names the missing capability", bool(R.COLLECTION_NOT_WIRED))
ok("G2. ...in words, and not as a migration number a tenant cannot act on",
   not re.search(r"\b1\d{3}\b", R.COLLECTION_NOT_WIRED))
ok("G3. the endpoint carries it in the payload", '"collection_note": _recv.COLLECTION_NOT_WIRED' in BAL)
ok("G4. ...on the unavailable path too, so it is not lost when the migration is missing",
   BAL.count("_recv.COLLECTION_NOT_WIRED") == 2)
ok("G5. the page shows it rather than deciding it is not worth saying",
   "data.collection_note" in PAGE)
ok("G6. NO PAYMENT IS INFERRED FROM AN OVER-TENDER — the module reads the recorded balance",
   '"balance"' in CODE and "variance" not in CODE)


# ── §H the W-9 lapse date has one home ──────────────────────────────────────────────────────────
print("\n§H  the day a certificate lapses, and the verdict, cannot disagree")
_cfg = {}
_doc = {"status": "received", "received_at": "2026-01-15"}
ok("H1. the lapse date is a function of the home", callable(W.expiry_date))
ok("H2. the verdict READS it rather than re-deriving the date",
   "expiry_date(doc, cfg)" in W9MOD.split("def w9_state", 1)[1].split("\ndef ", 1)[0])
ok("H3. a certificate's OWN expiry wins over the house refresh period",
   W.expiry_date({**_doc, "expires_at": "2026-11-10"}, _cfg)["basis"] == "own")
ok("H4. ...and the declared period answers when it carries none",
   W.expiry_date(_doc, _cfg)["basis"] == "declared")
ok("H5. A DATE WITHOUT A BASIS IS NEVER RETURNED — the fallback-basis rule",
   all((e["date"] is None) == (e["basis"] is None)
       for e in (W.expiry_date(_doc, _cfg), W.expiry_date(None, _cfg),
                 W.expiry_date(_doc, {"w9_valid_months": 0}),
                 W.expiry_date({"status": "requested"}, _cfg))))
ok("H6. no lapse date carries a REASON instead, never a date a reader could sort on",
   W.expiry_date(None, _cfg)["reason"] in W.NO_EXPIRY_REASONS.values()
   and W.expiry_date(_doc, {"w9_valid_months": 0})["reason"] in W.NO_EXPIRY_REASONS.values())
ok("H7. days-left is None when there is no lapse date, never 0",
   W.days_until_expiry(None, _cfg, now=TODAY) is None
   and W.days_until_expiry(_doc, {"w9_valid_months": 0}, now=TODAY) is None)
ok("H8. ...and negative once it has lapsed, so an overdue one sorts first",
   W.days_until_expiry({"status": "received", "received_at": "2020-01-15"},
                       _cfg, now=TODAY) < 0)
ok("H9. the verdict and the date agree about a certificate on its last day",
   W.w9_state({**_doc, "expires_at": TODAY.isoformat()}, _cfg, now=TODAY) == W.CURRENT
   and W.days_until_expiry({**_doc, "expires_at": TODAY.isoformat()}, _cfg, now=TODAY) == 0)
ok("H10. ...and about the day after it",
   W.w9_state({**_doc, "expires_at": "2026-10-30"}, _cfg, now=TODAY) == W.EXPIRED
   and W.days_until_expiry({**_doc, "expires_at": "2026-10-30"}, _cfg, now=TODAY) == -1)
ok("H11. the rollup counts a wholesale customer with no certificate as one, never as zero",
   W.summarise([{"w9_status": W.MISSING}])["wholesale"] == 1)
ok("H12. the PAGE shows no days-left figure it was not given",
   "get: (r: Row) => r.days_until_expiry }" in W9PAGE)


# ── §I the feature degrades until its migration lands ───────────────────────────────────────────
print("\n§I  no report claims a clean bill of health before its schema exists")
ok("I1. the open-balance report asks whether the database can store a terms sale at all",
   "_terms_schema_ready(" in BAL)
ok("I2. ...and says so by NAME rather than answering an empty table",
   '"available": False' in BAL and "TERMS_MIGRATION" in BAL)
ok("I3. the W-9 report asks whether the certificate table exists",
   "_w9_docs_for(" in BAL or "_w9_docs_for(" in W9R)
ok("I4. ...and says so by name", '"available": False' in W9R and "W9_MIGRATION" in W9R)
ok("I5. the probe treats ANY failure as not-applied rather than as ready",
   "except Exception:" in ROUTER.split("def _w9_docs_for(", 1)[1].split("\ndef ", 1)[0])
ok("I6. the open-balance PAGE says the absence is not an empty list",
   "This is not an empty list" in PAGE)
ok("I7. the W-9 PAGE says the absence is not a clean bill of health",
   "not a clean bill of health" in W9PAGE)
ok("I8. the migration number rides the §19.36 boundary, not the sentence",
   "PlatformDetail" in PAGE and "PlatformDetail" in W9PAGE
   and not re.search(r"parts\.push\([^)]*1106", PAGE))


# ── §J every tenant ─────────────────────────────────────────────────────────────────────────────
print("\n§J  every tenant, and no tenant's words")
ok("J1. the module holds no org id and no tenant name",
   "org_id" not in CODE and "organization_id" not in CODE
   and not re.search(r"0{8}-0{4}", MOD))
ok("J2. ...and no carrier, tenant or POS vendor name",
   not re.search(r"(?i)(boost|total ?wireless|luxelink|cellfonz|vzone|b2bsoft|rq\b)", MOD))
ok("J3. the module reads no clock — the day is the caller's argument",
   "date.today" not in CODE and "now()" not in CODE)
ok("J4. ...and the same inputs give the same answer on any day",
   R.assess(sale(balance=5.0), [terms(due="2026-10-01")], as_of=dt.date(2026, 10, 2))["days_past_due"]
   == 1)
ok("J5. the ENDPOINT checks the asked-for store against the caller's span BEFORE using it",
   'raise HTTPException(403, "that store is outside your reporting span")' in BAL
   and BAL.index('_span_filter([{"store_code": store_code}]') < BAL.index('.table("sales")'))
ok("J6. ...resolving the span once, rather than twice with a chance to differ",
   BAL.count("_caller_store_keyset(") == 1)
ok("J7. every read in both reports is org-scoped",
   BAL.count('.eq("org_id", org_id)') >= 1 and W9R.count('.eq("org_id", org_id)') >= 1)
ok("J8. the W-9 report asks the ORG for its own settings rather than assuming house ones",
   "_w9_cfg(client, org_id)" in W9R)
ok("J9. NOTHING DECLARED IS SAID IN WORDS — an org with no wholesale customers is told so",
   "no wholesale customers on file" in W9PAGE)
ok("J10. ...and an org with no terms sale in range is told that too",
   "was taken on terms" in PAGE)
ok("J11. the wholesale word itself is the vocabulary home's, not this report's",
   "_w9.WHOLESALE" in W9R and '"Wholesale"' not in W9R)


# ── §K controls ─────────────────────────────────────────────────────────────────────────────────
print("\n§K  controls — each property really fails when it is removed")
ok("K1. control: the detectors in A8 really match a hand-rolled subtraction and a hand-rolled age",
   bool(_SUBTRACTS.search("const x = total - balance"))
   and bool(_AGES.search("const late = (now - due) / 86400000")))
ok("K2. control: the band detector in E3 really matches a respelled band",
   bool(re.search(r"\b(0-7|8-30|31-60|61-90|90\+)\b", 'B = ("31-60", 31, 60)')))
ok("K3. control: the code-only view really strips prose, so J1/J3/F5 are not vacuous",
   "THE THREE THINGS THAT MAKE THE ANSWER HONEST" in MOD
   and "THE THREE THINGS" not in CODE and "4,603" in MOD and "4,603" not in CODE)
ok("K4. control: the module really is importable with no database and no app config",
   "get_supabase" not in CODE and "fastapi" not in MOD)
ok("K5. control: the one home is a module, not a class on the router",
   not re.search(r"\bclass\b", CODE))
try:
    _funcs = {n.name for n in ast.walk(ast.parse(MOD)) if isinstance(n, ast.FunctionDef)}
except SyntaxError:                                             # pragma: no cover
    _funcs = set()
ok("K6. control: every function the endpoint and the tests call exists",
   {"assess", "summarise", "sentence", "due_date", "terms_days"} <= _funcs, sorted(_funcs))
ok("K7. control: the earliest due date wins, so one late leg is not hidden by a later one",
   R.due_date([terms(due="2026-12-01"), terms(due="2026-11-01")]).isoformat() == "2026-11-01")
ok("K8. control: a settling leg's due date is never read as a term",
   R.due_date([{**cash(), "due_date": "2026-11-01"}]) is None)
ok("K9. control: the rollup's past-due total really is only the overdue part",
   R.summarise([_future, _late])["past_due_total"] == 100.0
   and R.summarise([_future, _late])["open_total"] == 200.0)
# FOUND BY THE INTEGRATION TEST, 2026-10-31. `summarise` branched on a `receivable` flag, and the
# ENDPOINT's rows carry `state` and `outstanding` but no flag — so every header total read $0.00
# over rows that plainly showed a balance. The rollup now keys off `state` alone, which is also the
# one-answer-to-one-question form: a row cannot claim to be owed and be cleared.
ok("K10b. REGRESSION — the rollup reads the ENDPOINT's row shape, not a flag only `assess` sets",
   R.summarise([{"state": R.OPEN_PAST_DUE, "outstanding": 500.0, "band": "8-30",
                 "days_past_due": 16}])["open_total"] == 500.0)
ok("K10c. ...and cannot be told a cleared row is owed",
   R.summarise([{"state": R.CLEARED, "outstanding": 500.0, "receivable": True}])["open_total"]
   == 0.0)
# CODE is the tokenized view, so the key reads as `"receivable"` with spaces around it. The slice is
# the rollup alone — `assess` legitimately SETS the flag; only `summarise` must not read it.
_ROLLUP = CODE.split("def summarise", 1)[1].split("def sentence", 1)[0]
ok("K10d. ...because the rollup never reads a `receivable` key at all",
   not re.search(r'get\s*\(\s*"receivable"', _ROLLUP), _ROLLUP[:120])
ok("K10. control: the oldest age reported is the largest, not the last row seen",
   R.summarise([_late, R.assess(sale(balance=1.0), [terms(due="2026-09-01")],
                                as_of=TODAY)])["oldest_days_past_due"] == 60)


# ── §L registration ────────────────────────────────────────────────────────────────────────────
print("\n§L  registered where the house rules require")
ok("L1. the section exists in the index", "## §POS-19." in INDEX)
ok("L2. both routes are registered in the index", "/pos/open-balances" in INDEX
   and "/pos/w9-status" in INDEX)
ok("L3. this gate is named in the index", "harness_pos_receivable.py" in INDEX)
ok("L4. the one home is registered in the module graph",
   "app/modules/pos/receivable.py" in _read("backend/app/modules/core/module_graph.py"))
ok("L5. both reports are in the report directory, so a user can find them",
   "'/pos/open-balances'" in _read("frontend/src/lib/rbac.ts")
   and "'/pos/w9-status'" in _read("frontend/src/lib/rbac.ts"))
ok("L6. ...and in the drill-down ledger, with a written state",
   "/pos/open-balances\tflat" in _read("backend/harness_drilldown_pending.txt")
   and "/pos/w9-status\tflat" in _read("backend/harness_drilldown_pending.txt"))


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
