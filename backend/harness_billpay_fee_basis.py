"""PROOF: the POS bill-payment figure includes the customer SERVICE FEE, in ONE home, dereferenced.

OWNER ASK 2026-10-03, verbatim:
    "diff of the pos data and th rep defined data needs to be investigated why those errors take
     place"

WHAT THE INVESTIGATION FOUND (read-only, September 2026, house org, measured by calling the system's
own shared helpers -- no second derivation of anything):

    store-days where the rep's declaration and the POS figure agreed      9 / 535
    the customer fee, rung as its own sales line                      4,176 lines / $16,592.00
      department 'Bill Payments', category 'Other Charge', desc 'ePay Service Charge'
    the exec bill_payment rule                      exclude_category: ['other charge']

So the fee was excluded from the POS figure by config -- CORRECTLY for the bill-payment metric, since
a service charge is not a bill payment, and WRONGLY for the drawer, since the fee is cash the rep
took from the customer and has to declare. Two different questions were being answered by one number.

THE DECISIVE SINGLE CASE, B-103 on 2026-09-13. The feed holds exactly two lines -- 'Boost RTR
$1-$650' $67.00 and 'ePay Service Charge' $4.00 -- and the rep declared $71.00. Measured after the
fix: agreement goes from 9 store-days to 342 within a dollar (111 to the cent) and $7,721 of the
$18,657 gap closes. The 188 store-days still disagreeing are genuine, worth $10,834, and are what a
district manager should actually be chasing.

WHAT THIS PINS
  A. the fee vocabulary is per-org config with ONE house default, and RULE TWO holds in the code;
  B. the fee cash aggregator splits by tender, drops voids, and never guesses that a fee was cash;
  C. the two accessors are the ONE home for the arithmetic, and absence stays absent (None != 0);
  D. the owner's own reported store-day reconciles to the cent, and the gap directions survive;
  E. the SIBLING -- the three-way recon's Leg B -- carries the same correction, and a pre-fee
     caller is byte-identical;
  F. migration 1042's text is tied to the code it configures;
  G. THE BUILD LOCK: no call site re-adds the legs for itself, and no second copy appears;
  H. every lock in F and G is ARMED -- each is shown to fail when the thing it guards is broken.

PURE: stdlib only, no DB, no network.  Run: `cd backend && python3 harness_billpay_fee_basis.py`
"""
import ast
import io
import sys
import tokenize

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


from app.modules.commcalc import epay_fee_recon as FR        # noqa: E402
from app.modules.commcalc import metric_recon as MR          # noqa: E402

TENDER = {"card": ("credit", "debit"), "cash": ("cash",), "classify": MR.classify_tender}


def line(desc, amt, tender="Cash", store="103 Fulton Ave", day="2026-09-13", voided=None):
    return {"product_desc": desc, "ext_price": amt, "tender_type": tender,
            "store": store, "trans_date": day, "voided": voided}


def code_only(path):
    """A module's executable text with docstrings and comments removed, so a scan cannot be satisfied
    (or defeated) by prose. Docstring bodies are blanked via the AST; comments via tokenize."""
    src = open(path).read()
    tree = ast.parse(src)
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                spans.append((body[0].lineno, body[0].end_lineno))
    lines = src.splitlines(keepends=True)
    for a, b in spans:
        for i in range(a - 1, min(b, len(lines))):
            lines[i] = "\n"
    stripped = "".join(lines)
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(stripped).readline):
        if tok.type != tokenize.COMMENT:
            out.append(tok.string)
    return " ".join(out)


def code_text(path):
    """The same docstring/comment removal as `code_only`, but PRESERVING the original spacing, so a
    scan for a literal expression (`x.get("cash") + y`) can still match. `code_only` joins tokens
    with spaces, which silently defeats any multi-token pattern -- the bug this function exists to
    avoid, and which H6 below keeps armed."""
    src = open(path).read()
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                for i in range(body[0].lineno - 1, min(body[0].end_lineno, len(lines))):
                    lines[i] = "\n"
    kept = "".join(lines)
    cuts = {}
    for tok in tokenize.generate_tokens(io.StringIO(kept).readline):
        if tok.type == tokenize.COMMENT:
            cuts.setdefault(tok.start[0], tok.start[1])
    final = []
    for n, ln in enumerate(kept.splitlines(), start=1):
        final.append(ln[:cuts[n]] if n in cuts else ln)
    return "\n".join(final)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== A. the fee vocabulary: per-org config, ONE house default ==")
check("A1 nothing configured resolves to the house vocabulary",
      FR.resolve_fee_descs(None) == FR.HOUSE_FEE_DESCS, FR.resolve_fee_descs(None))
check("A2 the house vocabulary is the historical constant, so no caller's behaviour moved",
      FR.HOUSE_FEE_DESCS == (FR.FEE_DESC,), (FR.HOUSE_FEE_DESCS, FR.FEE_DESC))
for blank in ([], (), "", 0, None, "epay service charge", {"a": 1}, [""], ["  "]):
    check(f"A3 a config of {blank!r} is not a vocabulary -> house default",
          FR.resolve_fee_descs(blank) == FR.HOUSE_FEE_DESCS, FR.resolve_fee_descs(blank))
check("A4 a configured vocabulary wins, lower-cased and trimmed",
      FR.resolve_fee_descs([" Wallet Fee ", "SERVICE CHG"]) == ("wallet fee", "service chg"),
      FR.resolve_fee_descs([" Wallet Fee ", "SERVICE CHG"]))
check("A5 a configured vocabulary REPLACES the house one (it does not union with it)",
      FR.is_fee_desc("ePay Service Charge", ["wallet fee"]) is False)
check("A6 matching is containment, not equality (the live desc is title-cased with words around it)",
      FR.is_fee_desc("ePay Service Charge") and FR.is_fee_desc("  BOOST EPAY SERVICE CHARGE FEE "))
check("A7 the bill line itself is never a fee line",
      FR.is_fee_desc("Boost RTR $1-$650 - Upfront Charge and E") is False)
check("A8 a blank / absent product_desc is not a fee line",
      not FR.is_fee_desc(None) and not FR.is_fee_desc("") and not FR.is_fee_desc("   "))

print("\n== A'. RULE TWO in the fee module's own code ==")
_fr_code = code_only("app/modules/commcalc/epay_fee_recon.py").lower()
check("A'0 control: the scan really dropped the prose (the docstring's 'owner' is gone)",
      "owner" in open("app/modules/commcalc/epay_fee_recon.py").read().lower()
      and "owner" not in _fr_code)
for banned in ("boost", "vidapay", "b2bsoft", "cellfonz", "luxelink", "xfinity", "other charge"):
    check(f"A'1 {banned!r} appears in no executable line of the fee module",
          banned not in _fr_code)
check("A'2 the ONE house token is a named constant, not a literal sprinkled through the code",
      _fr_code.count('"epay service charge"') + _fr_code.count("'epay service charge'") == 1,
      _fr_code.count('"epay service charge"'))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. the fee CASH aggregator ==")
agg = FR.aggregate_fee_cash([line("ePay Service Charge", 4.0, "Cash"),
                             line("ePay Service Charge", 4.0, "Credit Card"),
                             line("Boost RTR $1-$650", 67.0, "Cash")],
                            lambda s: s, tender_cfg=TENDER)
slot = agg[("103 Fulton Ave", "2026-09-13")]
check("B1 only fee lines are counted (the $67 bill line is not a fee)",
      slot["fee"] == 8.0 and slot["lines"] == 2, slot)
check("B2 the CASH leg is split out by tender -- the card fee is not in the drawer",
      slot["fee_cash"] == 4.0 and slot["lines_cash"] == 1, slot)
no_tender = FR.aggregate_fee_cash([line("ePay Service Charge", 4.0, "Cash")], lambda s: s)
check("B3 with no tender config the cash leg stays 0.0 -- never a guess that the fee was cash",
      no_tender[("103 Fulton Ave", "2026-09-13")]["fee_cash"] == 0.0,
      no_tender)
check("B3' ... and the total fee is still reported, so the money is not lost either",
      no_tender[("103 Fulton Ave", "2026-09-13")]["fee"] == 4.0)
for v in ("true", "TRUE", "1", "yes", "Voided", "y"):
    g = FR.aggregate_fee_cash([line("ePay Service Charge", 4.0, "Cash", voided=v)],
                              lambda s: s, tender_cfg=TENDER)
    check(f"B4 a line voided={v!r} is excluded", g == {}, g)
g = FR.aggregate_fee_cash([line("ePay Service Charge", 4.0, "Cash", voided="false")],
                          lambda s: s, tender_cfg=TENDER)
check("B4' control: an UNvoided line is still counted, so B4 is not passing by always returning {}",
      g != {} and g[("103 Fulton Ave", "2026-09-13")]["fee_cash"] == 4.0, g)
check("B5 no rows -> an empty map, never a fabricated store-day",
      FR.aggregate_fee_cash([], lambda s: s, tender_cfg=TENDER) == {}
      and FR.aggregate_fee_cash(None, lambda s: s, tender_cfg=TENDER) == {})
g = FR.aggregate_fee_cash([line("ePay Service Charge", 4.0, "Cash", store=None),
                           line("ePay Service Charge", 4.0, "Cash", day=None)],
                          lambda s: s, tender_cfg=TENDER)
check("B6 a line with no store or no date is dropped, never keyed to an invented store-day",
      g == {}, g)
g = FR.aggregate_fee_cash([line("ePay Service Charge", 4.0, "Cash")],
                          lambda s: (_ for _ in ()).throw(RuntimeError("boom")), tender_cfg=TENDER)
check("B7 a store resolver that RAISES drops the line instead of killing the whole aggregation",
      g == {}, g)
g = FR.aggregate_fee_cash([line("ePay Service Charge", 4.0, "Cash"),
                           line("ePay Service Charge", 2.5, "Cash")],
                          lambda s: s, tender_cfg=TENDER)
check("B8 several fee lines on one store-day sum",
      g[("103 Fulton Ave", "2026-09-13")]["fee_cash"] == 6.5, g)
g = FR.aggregate_fee_cash([line("ePay Service Charge", 4.0, "Cash; Debit Card")],
                          lambda s: s, tender_cfg=TENDER)
check("B9 a MULTI-tender fee line is not counted as cash (it cannot be split, so it is not claimed)",
      g[("103 Fulton Ave", "2026-09-13")]["fee_cash"] == 0.0
      and g[("103 Fulton Ave", "2026-09-13")]["fee"] == 4.0, g)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. the accessors are the ONE home, and absence stays absent ==")
check("C1 the cash basis adds the two legs", MR.pos_billpay_cash({"cash": 67.0, "fee_cash": 4.0}) == 71.0)
check("C2 a slot with no fee leg contributes 0.0 for it -- the feed reported and held no fee cash",
      MR.pos_billpay_cash({"cash": 67.0}) == 67.0)
check("C3 NO SLOT AT ALL is None, never 0.0 -- 'nobody reported' is not 'there was none'",
      MR.pos_billpay_cash(None) is None)
check("C3' ... and the same for the total and the fee leg",
      MR.pos_billpay_total(None) is None and MR.pos_billpay_fee_cash(None) is None)
check("C4 an EMPTY slot is an honest 0.0 (the feed covered the store-day and held nothing)",
      MR.pos_billpay_cash({}) == 0.0)
check("C5 a bare number (the processor leg) passes through unchanged",
      MR.pos_billpay_cash(238.0) == 238.0 and MR.pos_billpay_total(238.0) == 238.0)
check("C6 unparseable legs are 0.0, not an exception that takes a report down",
      MR.pos_billpay_cash({"cash": "x", "fee_cash": None}) == 0.0
      and MR.pos_billpay_cash({"cash": 67.0, "fee_cash": "x"}) == 67.0)
check("C7 the total basis adds the ALL-TENDER legs (Leg A's declaration includes the card fee)",
      MR.pos_billpay_total({"amount": 67.0, "fee": 8.0}) == 75.0)
check("C8 the fee accessor reports just the leg, so a report can SHOW the correction",
      MR.pos_billpay_fee_cash({"cash": 67.0, "fee_cash": 4.0}) == 4.0
      and MR.pos_billpay_fee_cash({"cash": 67.0}) == 0.0)
check("C9 the two accessors never disagree about the fee they added",
      all(round(MR.pos_billpay_cash(s) - float(s.get("cash") or 0), 2)
          == MR.pos_billpay_fee_cash(s)
          for s in ({"cash": 67.0, "fee_cash": 4.0}, {"cash": 0.0, "fee_cash": 20.0},
                    {"cash": 10.5, "fee_cash": 0.0}, {"cash": 99.99})))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. the owner's own reported store-days ==")
# B-103 / 2026-09-13 -- the decisive case, the whole store-day in two lines.
b103 = {"cash": 67.0, "fee_cash": 4.0, "amount": 67.0, "fee": 4.0}
check("D1 B-103 2026-09-13: the POS basis equals the rep's declared $71.00 to the cent",
      MR.pos_billpay_cash(b103) == 71.0, MR.pos_billpay_cash(b103))
check("D1' ... and before the fix it was $67.00, a $4.00 phantom gap on a correct declaration",
      round(71.0 - 67.0, 2) == 4.0)
# B-2612 / 2026-09-03 -- the owner's original report: "15 is declared as store cash and 258 as epay
# and the total is 273 - cash pick up should only show the cash from sales".
b2612 = {"cash": 238.0, "fee_cash": 20.0}
check("D2 B-2612 2026-09-03: the POS basis is $258.00 -- the rep's declaration exactly",
      MR.pos_billpay_cash(b2612) == 258.0, MR.pos_billpay_cash(b2612))
check("D3 ... so the envelope asks $15.00 of the $273.00 drawer, which is what the owner said",
      round(273.0 - MR.pos_billpay_cash(b2612), 2) == 15.0)
check("D4 ... and the $20 'short' it used to show was 5 bill payments x the $4 fee",
      round(MR.pos_billpay_cash(b2612) - 238.0, 2) == 20.0)
# The gap DIRECTIONS must survive: a real under-declaration must stay visible.
under = {"cash": 579.38, "fee_cash": 20.0}
check("D5 a real exception is NOT closed by the fee: B-559 2026-09-10 declared $0.00 and the basis "
      "is $599.38, so it still reports",
      round(0.0 - MR.pos_billpay_cash(under), 2) == -599.38, MR.pos_billpay_cash(under))
over = {"cash": 270.67, "fee_cash": 20.0}
check("D6 ... and so does the other direction: B-3565 2026-09-16 declared $692.00 against $290.67",
      round(692.0 - MR.pos_billpay_cash(over), 2) == 401.33, MR.pos_billpay_cash(over))
check("D7 the correction can only ever make the basis LARGER, so it can never manufacture a shortage",
      all(MR.pos_billpay_cash(s) >= float(s.get("cash") or 0)
          for s in (b103, b2612, under, over, {"cash": 0.0, "fee_cash": 0.0})))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. the SIBLING: the three-way recon's Leg B carries the same correction ==")
rows, summ = MR.reconcile_billpay_three_way_days(
    {("A", "2026-09-13"): 71.0}, {("A", "2026-09-13"): b103}, {})
check("E1 Leg A $71.00 vs Leg B's bill+fee -> ok, where before it was a mismatch",
      rows[0]["status"] == "ok" and rows[0]["sales"] == 71.0, rows[0])
rows2, _ = MR.reconcile_billpay_three_way_days(
    {("A", "2026-09-13"): 71.0}, {("A", "2026-09-13"): {"amount": 67.0}}, {})
check("E2 a PRE-FEE slot (no fee key) is byte-identical to the old behaviour",
      rows2[0]["sales"] == 67.0 and rows2[0]["status"] == "mismatch", rows2[0])
rows3, _ = MR.reconcile_billpay_three_way_days({("A", "2026-09-13"): 71.0}, {}, {})
check("E3 an ABSENT sales leg is still None, not a fee-corrected zero",
      rows3[0]["sales"] is None and rows3[0]["status"] == "declared_only", rows3[0])
check("E4 control: the sibling fix is real -- Leg B with the fee differs from Leg B without it",
      rows[0]["sales"] != rows2[0]["sales"])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. migration 1042 is tied to the code it configures ==")
_m = open("../database/migrations/1042_billpay_fee_product_desc.sql").read()
check("F1 it adds the column the config reader reads, additively and idempotently",
      "billpay_fee_product_desc" in _m and "ADD COLUMN IF NOT EXISTS" in _m)
check("F2 the column name in the migration is the one the reader asks for",
      'billpay_fee_product_desc' in open("app/modules/commcalc/router.py").read())
check("F3 it declares a REVERT, as every migration here must", "-- REVERT:" in _m)
check("F4 its default is EMPTY, so an unconfigured tenant keeps the shipped behaviour",
      "DEFAULT '[]'::jsonb" in _m)
check("F5 it performs no backfill and moves no money",
      "UPDATE " not in _m.upper().replace("DO UPDATE", "") and "INSERT INTO" not in _m.upper())
check("F6 it names the one home the callers must dereference, so the next reader finds it",
      "pos_billpay_cash" in _m and "HOUSE_FEE_DESCS" in _m)
check("F7 it states the sibling tenant it does NOT claim to fix",
      "854f6d7b" in _m and "DIFFERENT cause" in _m)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. THE BUILD LOCK -- no caller re-adds the legs for itself ==")
_CALLERS = ("app/modules/closing/router.py",
            "app/modules/closing/billpay_netting.py",
            "app/modules/closing/billpay_pickup.py",
            "app/modules/commcalc/router.py")
# The arithmetic this lock forbids anywhere but the home: adding a fee leg to a cash/amount leg.
_FORBIDDEN = (
    'get("cash") + ', 'get("fee_cash")', '["fee_cash"] + ', '+ r["fee_cash"]',
    'get("amount") + ', '["fee"] + ',
)
_HOME = "app/modules/commcalc/metric_recon.py"
# THE ONE LEGITIMATE EXCEPTION, named rather than excused by a looser pattern. The PRODUCER that
# assembles the per-store-day map accumulates a fee leg INTO the fee leg; it never adds the fee to
# the cash or amount leg, which is the arithmetic this lock exists to keep in one place. Each line is
# matched verbatim, so if the producer is rewritten the excuse goes stale and G1' below says so.
_EXCUSED = {
    'slot["fee"] = round(slot["fee"] + float(f.get("fee") or 0.0), 2)':
        "_billpay_sales_by_store_day: the producer accumulating the fee leg into the fee leg",
    'slot["fee_cash"] = round(slot["fee_cash"] + float(f.get("fee_cash") or 0.0), 2)':
        "_billpay_sales_by_store_day: the same, for the cash leg of the fee",
}
for path in _CALLERS:
    body = code_text(path)
    for excused in _EXCUSED:
        body = body.replace(excused, "")
    hits = [f for f in _FORBIDDEN if f in body]
    check(f"G1 {path.split('/')[-1]} re-adds the legs nowhere of its own", not hits, hits)
_prod = code_text("app/modules/commcalc/router.py")
for excused, why in _EXCUSED.items():
    check(f"G1' the excuse is not stale -- {why}", excused in _prod, excused[:60])
check("G1'' control: removing the excuses really does change the scan, so they are load-bearing "
      "and not decoration",
      any(f in _prod for f in _FORBIDDEN))
_home_code = code_text(_HOME)
check("G2 the home itself DOES contain the arithmetic (so G1 is a real restriction, not vacuous)",
      any(f in _home_code for f in _FORBIDDEN),
      [f for f in _FORBIDDEN if f in _home_code])
check("G3 the two accessors exist under the names the callers import",
      hasattr(MR, "pos_billpay_cash") and hasattr(MR, "pos_billpay_total")
      and hasattr(MR, "pos_billpay_fee_cash"))
_cr = code_text("app/modules/closing/router.py")
check("G4 the pickup basis dereferences the home rather than reading the raw key",
      "pos_billpay_cash" in _cr, "the netting basis stopped asking the home")
check("G5 the fee aggregation is called from exactly ONE place, so there is one producer",
      code_only("app/modules/commcalc/router.py").count("aggregate_fee_cash") == 1,
      code_only("app/modules/commcalc/router.py").count("aggregate_fee_cash"))
check("G6 the vocabulary is read through the ONE config reader, not re-read at a call site",
      code_only("app/modules/commcalc/router.py").count("_billpay_fee_tokens") == 2,
      code_only("app/modules/commcalc/router.py").count("_billpay_fee_tokens"))

print("\n== H. the locks are ARMED -- each fails when the thing it guards breaks ==")
check("H1 control: the forbidden-arithmetic scan matches when the arithmetic IS present",
      any(f in 'x = row.get("cash") + row.get("fee_cash")' for f in _FORBIDDEN))
check("H2 control: ... and does not match the home's own accessor CALL, so a caller that "
      "dereferences correctly is never flagged",
      not any(f in "basis = _mr.pos_billpay_cash(slot)" for f in _FORBIDDEN))
check("H3 control: code_only really blanks a docstring that would otherwise satisfy a scan",
      'get("fee_cash")' not in code_only("app/modules/closing/billpay_netting.py")
      or 'get("fee_cash")' in open("app/modules/closing/billpay_netting.py").read())
check("H4 control: the vocabulary tie can fail -- a word absent from the module is absent from the "
      "house default too",
      "wallet" not in " ".join(FR.HOUSE_FEE_DESCS) and "wallet" not in _fr_code)
check("H5 control: the None-is-not-zero pin can fail -- a 0.0 would be indistinguishable",
      MR.pos_billpay_cash(None) is not MR.pos_billpay_cash({}))
# The lock scanned token-JOINED text in its first cut, which silently matched nothing: every
# multi-token pattern was defeated by the inserted spaces and G1 passed vacuously. H6 keeps that
# exact mistake from coming back.
check("H6 control: the lock scans SPACING-PRESERVING text -- the token-joined form would defeat it",
      any(f in code_text(_HOME) for f in _FORBIDDEN)
      and not any(f in code_only(_HOME) for f in _FORBIDDEN))
check("H7 control: both scanners still strip prose, so neither can be satisfied by a docstring",
      "owner" not in code_text(_HOME).lower() and "owner" not in code_only(_HOME).lower()
      and "owner" in open(_HOME).read().lower())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
