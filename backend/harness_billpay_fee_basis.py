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
  F. migration 1045's text is tied to the code it configures;
  G. THE BUILD LOCK: no call site re-adds the legs for itself, and no second copy appears;
  H. every lock in F and G is ARMED -- each is shown to fail when the thing it guards is broken;
  J. THE FEE POLICY (owner ask 2026-10-03) -- whether a tenant charges a bill-payment fee is a
     DECLARED per-org fact with one home, "no fee line" is interpreted in exactly one place, a basis
     the policy says is understated is never compared against a rep and never netted from a drawer,
     an UNANSWERED policy changes nothing anywhere, and the settings screen can set it;
  K. every lock in J is ARMED too.

THE SECOND INVESTIGATION, 2026-10-03, which J exists because of. The fee correction above is right
for the house org and silent for the other live tenant, and the measurement says why: the house org
rings a fee leg on 736 of 772 September store-days ($14,184 -- removing it collapses agreement from
348 store-days to 20), and the other tenant rings one on 0 of 371, where removing the leg leaves the
classification IDENTICAL. So "no fee line" carried no information, and the system was reading it as
a shortfall: 226 of that tenant's 371 store-days came out under-declared. Its real cause is not a
fee at all -- 217 of its 373 September closings declare bill-pay cash of $0.00 against real POS
bill-pay activity -- and that cause is REPORTED, not absorbed. The class of defect is inferring a
fact from the absence of data; the fix is to ask the org.

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
print("\n== F. migration 1045 is tied to the code it configures ==")
_m = open("../database/migrations/1045_billpay_fee_product_desc.sql").read()
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

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== J. THE FEE POLICY -- a declared fact, one home, dereferenced ==")
from app.modules.closing import billpay_declaration_alerts as BDA   # noqa: E402

# ── J1-J6 the policy resolver: anything unrecognised is UNKNOWN, never a guess either way ─────────
check("J1 the three policy values exist and unknown is the HOUSE default",
      MR.FEE_POLICIES == ("yes", "no", "unknown") and MR.HOUSE_FEE_POLICY == MR.FEE_POLICY_UNKNOWN)
check("J2 an unset / blank / None policy resolves to unknown, not to either answer",
      all(MR.resolve_fee_policy(v) == MR.FEE_POLICY_UNKNOWN for v in (None, "", "   ", {}, 0, [])))
check("J3 a value is normalised by case and whitespace, so a hand-typed row still reads",
      MR.resolve_fee_policy("  YES ") == MR.FEE_POLICY_CHARGED
      and MR.resolve_fee_policy("No") == MR.FEE_POLICY_NOT_CHARGED)
check("J4 a junk or FUTURE value resolves to unknown rather than to a comparison",
      MR.resolve_fee_policy("maybe") == MR.FEE_POLICY_UNKNOWN
      and MR.resolve_fee_policy("true") == MR.FEE_POLICY_UNKNOWN)
check("J5 the policy is never a boolean -- True must not read as 'yes'",
      MR.resolve_fee_policy(True) == MR.FEE_POLICY_UNKNOWN
      and MR.resolve_fee_policy(False) == MR.FEE_POLICY_UNKNOWN)
check("J6 the resolver is PURE -- the same input twice gives the same answer and mutates nothing",
      MR.resolve_fee_policy("yes") == MR.resolve_fee_policy("yes") == "yes")

# ── J7-J16 the state machine: "no fee line" is THREE facts, and they stay apart ───────────────────
_ACTIVE = {"count": 15, "amount": 1410.81, "cash": 1410.81}
_ACTIVE_FEE = {"count": 15, "amount": 1410.81, "cash": 1406.81, "fee_cash": 4.0, "fee_lines": 2}
_IDLE = {"count": 0, "amount": 0.0, "cash": 0.0}
check("J7 no slot at all is no_feed -- absence of a feed is never a fee finding",
      MR.billpay_fee_state(None, "yes") == MR.FEE_STATE_NO_FEED
      and MR.billpay_fee_state(None, "no") == MR.FEE_STATE_NO_FEED)
check("J8 a fee leg present reads as charged, whatever the policy says it should be",
      MR.billpay_fee_state(_ACTIVE_FEE, "yes") == MR.FEE_STATE_CHARGED
      and MR.billpay_fee_state(_ACTIVE_FEE, "unknown") == MR.FEE_STATE_CHARGED)
check("J9 a fee leg present while the policy says NONE is the policy-contradiction state",
      MR.billpay_fee_state(_ACTIVE_FEE, "no") == MR.FEE_STATE_UNEXPECTED)
check("J10 policy 'no' + bill activity + no fee line is EXPECTED, which is the whole ask",
      MR.billpay_fee_state(_ACTIVE, "no") == MR.FEE_STATE_NOT_CHARGED)
check("J11 policy 'yes' + bill activity + no fee line is a FEED defect, not a store's problem",
      MR.billpay_fee_state(_ACTIVE, "yes") == MR.FEE_STATE_LINE_MISSING)
check("J12 an UNANSWERED policy + bill activity + no fee line asks the question, it does not answer it",
      MR.billpay_fee_state(_ACTIVE, None) == MR.FEE_STATE_POLICY_UNANSWERED
      and MR.billpay_fee_state(_ACTIVE, "unknown") == MR.FEE_STATE_POLICY_UNANSWERED)
check("J13 THE THREE FACTS ARE DISTINCT -- the same store-day reads differently under each policy, "
      "which is the defect this closes",
      len({MR.billpay_fee_state(_ACTIVE, p) for p in MR.FEE_POLICIES}) == 3)
check("J14 a store-day that rang NO bill payments is idle, never a missing-fee finding -- there was "
      "nothing to charge a fee on",
      MR.billpay_fee_state(_IDLE, "yes") == MR.FEE_STATE_IDLE
      and MR.billpay_fee_state(_IDLE, "unknown") == MR.FEE_STATE_IDLE)
check("J15 a bare figure (a processor total) carries no fee detail, so no fee claim is made of it",
      MR.billpay_fee_state(238.0, "yes") == MR.FEE_STATE_NOT_APPLICABLE)
check("J16 a fee counted only in LINES, or only on the card leg, still counts as charged -- the "
      "cash leg being zero is not the absence of a fee",
      MR.billpay_fee_state({"count": 3, "amount": 90.0, "fee": 4.0}, "yes") == MR.FEE_STATE_CHARGED
      and MR.billpay_fee_state({"count": 3, "amount": 90.0, "fee_lines": 1}, "yes")
      == MR.FEE_STATE_CHARGED)

# ── J17-J21 comparability: ONE home decides it, and the unsafe states are the two unsafe ones ─────
check("J17 every state the machine can return is declared in FEE_STATES",
      all(MR.billpay_fee_state(sl, p) in MR.FEE_STATES
          for sl in (None, 238.0, _IDLE, _ACTIVE, _ACTIVE_FEE, {})
          for p in (None, "yes", "no", "unknown", "junk")))
check("J18 exactly the two unsafe states are not comparable -- a feed defect and an open question",
      {st for st in MR.FEE_STATES if not MR.billpay_basis_comparable(st)}
      == {MR.FEE_STATE_LINE_MISSING, MR.FEE_STATE_POLICY_UNANSWERED, MR.FEE_STATE_NO_FEED})
check("J19 a CONTRADICTED policy is still comparable -- a fee that rang is real cash, so the "
      "arithmetic stands and it is the config that is reported",
      MR.billpay_basis_comparable(MR.FEE_STATE_UNEXPECTED))
check("J20 an unrecognised state is NOT comparable, so a future state fails safe",
      not MR.billpay_basis_comparable("something_new") and not MR.billpay_basis_comparable(None))
check("J21 comparability is asked of the home, never re-derived: the alert module names no state "
      "tuple of its own",
      "FEE_STATES_COMPARABLE" not in code_text("app/modules/closing/billpay_declaration_alerts.py"))

# ── J22-J29 the classifier: an unsafe basis is never a rep's error, and says WHICH problem it is ──
_T = 1.0
check("J22 pre-policy behaviour is byte-identical -- no fee_state means classify as before",
      BDA.classify(0.0, 1008.0, _T) == BDA.CLASS_UNDER
      and BDA.classify(71.0, 71.0, _T) == BDA.CLASS_AGREE
      and BDA.classify(692.0, 290.67, _T) == BDA.CLASS_OVER
      and BDA.classify(50.0, None, _T) == BDA.CLASS_NO_POS)
check("J23 policy 'no' + no fee line: the gap STANDS and the store is asked about it -- the measured "
      "tenant's 226 under-declared store-days do not vanish",
      BDA.classify(0.0, 1008.0, _T, fee_state=MR.FEE_STATE_NOT_CHARGED) == BDA.CLASS_UNDER)
check("J24 policy 'yes' + no fee line: the SAME figures are a feed defect, not an under-declaration",
      BDA.classify(0.0, 1008.0, _T, fee_state=MR.FEE_STATE_LINE_MISSING) == BDA.CLASS_FEE_MISSING)
check("J25 an UNANSWERED policy parks the same store-day as a question, distinct from the feed defect",
      BDA.classify(0.0, 1008.0, _T, fee_state=MR.FEE_STATE_POLICY_UNANSWERED)
      == BDA.CLASS_FEE_POLICY_UNSET
      and BDA.CLASS_FEE_POLICY_UNSET != BDA.CLASS_FEE_MISSING)
check("J26 THE WHOLE POINT: one store-day, one set of figures, three different findings by policy",
      len({BDA.classify(0.0, 1008.0, _T, fee_state=MR.billpay_fee_state(_ACTIVE, p))
           for p in MR.FEE_POLICIES}) == 3)
check("J27 no feed beats everything -- an absent basis is no_pos_figure whatever the fee state is",
      all(BDA.classify(0.0, None, _T, fee_state=st) == BDA.CLASS_NO_POS for st in MR.FEE_STATES))
check("J28 neither fee class is ALERTABLE, and both are refused -- an unsafe basis never reaches a "
      "manager's chase list",
      BDA.CLASS_FEE_MISSING not in BDA.ALERTABLE
      and BDA.CLASS_FEE_POLICY_UNSET not in BDA.ALERTABLE
      and BDA.CLASS_FEE_MISSING in BDA.REFUSED_AS_ALERT
      and BDA.CLASS_FEE_POLICY_UNSET in BDA.REFUSED_AS_ALERT)
check("J29 a contradicted policy is still compared, so real cash is never excused by a stale setting",
      BDA.classify(0.0, 1008.0, _T, fee_state=MR.FEE_STATE_UNEXPECTED) == BDA.CLASS_UNDER)

# ── J30-J37 the digest: a refusal is COUNTED and SAID, in one wording both renderings read ────────
_SD = [
    {"store_code": "A", "close_date": "2026-09-21", "declared": 0.0, "pos_basis": 1008.0,
     "fee_state": MR.FEE_STATE_NOT_CHARGED, "bill_txns": 16},
    {"store_code": "B", "close_date": "2026-09-21", "declared": 0.0, "pos_basis": 873.97,
     "fee_state": MR.FEE_STATE_LINE_MISSING, "bill_txns": 19},
    {"store_code": "C", "close_date": "2026-09-21", "declared": 0.0, "pos_basis": 500.0,
     "fee_state": MR.FEE_STATE_POLICY_UNANSWERED, "bill_txns": 9},
    {"store_code": "D", "close_date": "2026-09-21", "declared": 100.0, "pos_basis": 96.0,
     "fee_state": MR.FEE_STATE_UNEXPECTED, "bill_txns": 4},
    {"store_code": "E", "close_date": "2026-09-21", "declared": 10.0, "pos_basis": None,
     "fee_state": MR.FEE_STATE_NO_FEED},
]
_found = BDA.alert_items(_SD, tolerance=_T)
check("J30 only the two assessable store-days are alerted on, of five",
      [i["store_code"] for i in _found["items"]] == ["D", "A"], _found["items"])
check("J31 every class is counted, including the ones nobody is asked about",
      _found["counts"][BDA.CLASS_FEE_MISSING] == 1
      and _found["counts"][BDA.CLASS_FEE_POLICY_UNSET] == 1
      and _found["counts"][BDA.CLASS_NO_POS] == 1
      and _found["counts"][BDA.CLASS_UNDER] == 1
      and _found["counts"][BDA.CLASS_OVER] == 1, _found["counts"])
check("J32 the refused block names all three reasons separately, so each has its own remedy",
      set(_found["refused"]) == set(BDA.UNASSESSED)
      and all(_found["refused"][c] == 1 for c in BDA.UNASSESSED), _found["refused"])
check("J33 the policy contradiction is an ADVISORY, never a class -- it was compared normally",
      _found["advisories"][BDA.CLASS_FEE_UNEXPECTED_ADVISORY] == 1
      and BDA.CLASS_FEE_UNEXPECTED_ADVISORY not in BDA.GAP_CLASSES)
check("J34 nothing is lost: every store-day lands in exactly one class",
      sum(_found["counts"].values()) == len(_SD))
_dg = BDA.build_digest("Sam", _found["items"], counts=_found["counts"], label="2026-09-21",
                       advisories=_found["advisories"])
check("J35 both the feed defect and the open question are SAID in the digest, not merely counted",
      BDA.unassessed_sentence(BDA.CLASS_FEE_MISSING, 1) in _dg["html"]
      and BDA.unassessed_sentence(BDA.CLASS_FEE_POLICY_UNSET, 1) in _dg["text"])
check("J36 the two renderings carry the SAME sentences -- one wording, one home",
      all(BDA.unassessed_sentence(c, n) in _dg["html"]
          and BDA.unassessed_sentence(c, n) in _dg["text"]
          for c, n in BDA.unassessed_counts(_found["counts"])))
check("J37 the refusal wording names no tenant, carrier or product (RULE TWO in the copy too)",
      not any(w in " ".join(BDA.UNASSESSED_NOTES.values()).lower()
              for w in ("boost", "luxelink", "vidapay", "epay", "total wireless", "rtr")))

# ── J38-J42 the netting basis: an understated figure is never subtracted from a drawer ────────────
check("J38 the trusted basis equals the plain one whenever the policy does not forbid it",
      all(MR.pos_billpay_cash_trusted(_ACTIVE_FEE, p) == MR.pos_billpay_cash(_ACTIVE_FEE)
          for p in MR.FEE_POLICIES))
check("J39 policy 'yes' + no fee line: the basis is WITHHELD, so nothing is netted from the drawer",
      MR.pos_billpay_cash_trusted(_ACTIVE, "yes") is None
      and MR.pos_billpay_cash(_ACTIVE) == 1410.81)
check("J40 AN UNANSWERED POLICY CHANGES NOTHING -- a money-adjacent subtraction never moves on a guess",
      MR.pos_billpay_cash_trusted(_ACTIVE, None) == MR.pos_billpay_cash(_ACTIVE)
      and MR.pos_billpay_cash_trusted(_ACTIVE, "unknown") == MR.pos_billpay_cash(_ACTIVE))
check("J41 policy 'no' keeps netting on its own takings",
      MR.pos_billpay_cash_trusted(_ACTIVE, "no") == MR.pos_billpay_cash(_ACTIVE))
check("J42 only the positively-understated state withholds the basis",
      MR.FEE_STATES_BASIS_UNDERSTATED == (MR.FEE_STATE_LINE_MISSING,))

# ── J43-J52 THE BUILD LOCK: no caller spells a policy value or re-derives the interpretation ──────
_POLICY_LITERALS = ('"yes"', "'yes'", '"no"', "'no'", '"unknown"', "'unknown'")
# WHICH TEXT EACH FILE IS SCANNED FOR. The closing router is tens of thousands of lines and the
# words yes/no occur all over it in unrelated code, so its scan is anchored to the fee neighbourhood;
# the pure alert module is small enough to scan WHOLE. A None anchor means the whole file, and the
# fragment is asserted non-empty by K8 below -- an anchor that stops matching is how a lock starts
# passing because it looked at nothing, which is exactly what happened to this check in its first cut
# (the alert module spells "billpay_fee" nowhere, so its fragment was empty and the lock was vacuous).
_POLICY_CALLERS = {"app/modules/closing/router.py": "billpay_fee",
                   "app/modules/closing/billpay_declaration_alerts.py": None}


def _policy_fragment(path, anchor):
    body = code_text(path)
    if anchor is None:
        return body
    return "".join(body.split(anchor)[1:])[:4000] if anchor in body else ""


for _pth, _anchor in _POLICY_CALLERS.items():
    _frag = _policy_fragment(_pth, _anchor)
    check(f"J43 {_pth.split('/')[-1]} spells no fee-policy VALUE of its own",
          _frag and not any(lit in _frag for lit in _POLICY_LITERALS),
          [lit for lit in _POLICY_LITERALS if lit in _frag])
_home2 = code_text(_HOME)
check("J44 the home DOES spell them (so J43 restricts something real, and is not vacuous)",
      all(lit in _home2 for lit in ('"yes"', '"no"', '"unknown"')))
check("J45 'no fee line' is interpreted in exactly ONE function -- one def, one home",
      _home2.count("def billpay_fee_state") == 1
      and code_text("app/modules/closing/billpay_declaration_alerts.py").count(
          "def billpay_fee_state") == 0)
_cr2 = code_text("app/modules/closing/router.py")
check("J46 the sweep dereferences the state machine rather than reading the fee leg itself",
      "_mr.billpay_fee_state(slot, policy)" in _cr2)
check("J47 the netting basis dereferences the POLICY-GATED accessor, not the plain one",
      "pos_billpay_cash_trusted" in _cr2)
check("J48 the policy is read by the ONE reader, which lives beside the vocabulary reader",
      code_only("app/modules/commcalc/router.py").count("def _billpay_fee_policy") == 1
      and code_only("app/modules/commcalc/router.py").count("def _billpay_fee_tokens") == 1)
check("J49 the policy is read ONCE PER WINDOW, not once per store-day",
      _cr2.count("_billpay_fee_policy(client, org_id)") == 1)
check("J50 the classifier asks the home whether a state is comparable, by name",
      "billpay_basis_comparable" in code_text("app/modules/closing/billpay_declaration_alerts.py"))
check("J51 RULE TWO: no tenant, carrier or product name anywhere in the policy mechanism",
      not any(w in (_home2 + code_text("app/modules/closing/billpay_declaration_alerts.py")).lower()
              for w in ("boost", "luxelink", "vidapay", "t-cetra", "total wireless")))
# `code_text`, not `code_only`: the token-joined form inserts spaces around the dot and would never
# match a dotted name, which is the H6 mistake in a new costume.
_ccr = code_text("app/modules/commcalc/router.py")
check("J52 the API offers the vocabulary from the home, so the screen cannot spell its own",
      "_mr_cfg.FEE_POLICIES" in _ccr and "_mr_cfg.HOUSE_FEE_POLICY" in _ccr)
check("J52' the API also VALIDATES against the home, so a typo is rejected rather than stored as "
      "a silent 'unknown'",
      "not in _mr_cfg.FEE_POLICIES" in _ccr)

# -- J60-J62 the POLICY and the VOCABULARY are two facts, and neither grew a third reader ----------
# 19.42's own lock (harness_billpay_fee_one_home_lock.py, merged 2026-10-03) owns "WHICH product_desc
# is the fee". This one owns "IS there a fee". Complementary, not duplicate -- and these checks keep
# them from quietly becoming the same thing, or from each sprouting its own read of one column.
check("J60 the two facts live in DIFFERENT columns, so the two locks cannot drift into guarding the "
      "same thing",
      "billpay_fee_charged" in _ccr and "billpay_fee_product_desc" in _ccr)
check("J61 the settings screen reads the DECLARED vocabulary off the ONE whole-row config load, not "
      "a third round trip for the same cell (4b.1)",
      "billpay_fee_descs_raw" in _ccr
      # FIVE: derived once, named as its own key on the returned config (two on that line), then
      # read by the GET and by the PUT's untouched-field path. A SIXTH would be a new reader.
      and code_only("app/modules/commcalc/router.py").count("billpay_fee_descs_raw") == 5,
      code_only("app/modules/commcalc/router.py").count("billpay_fee_descs_raw"))
check("J62 the RAW cell and the RESOLVED vocabulary are both derived from that one read, so "
      "'inheriting the house wording' stays distinguishable from 'pinned to it'",
      "billpay_fee_descs_raw" in _ccr and "resolve_fee_descs" in _ccr)

# ── J53-J59 migration 1046 is tied to the code it configures ──────────────────────────────────────
_m46 = open("../database/migrations/1046_billpay_fee_charged.sql").read()
check("J53 it adds the column the reader reads, additively and idempotently",
      "billpay_fee_charged" in _m46 and "ADD COLUMN IF NOT EXISTS" in _m46)
check("J54 its default is the UNANSWERED value, so applying it judges nobody",
      "DEFAULT 'unknown'" in _m46)
check("J55 the database constrains the value to the SAME three the code knows",
      all(f"'{v}'" in _m46 for v in MR.FEE_POLICIES) and "CHECK (billpay_fee_charged IN" in _m46)
check("J56 it declares a REVERT, as every migration here must", "-- REVERT:" in _m46)
check("J57 it performs no backfill and moves no money",
      "INSERT INTO" not in _m46.upper()
      and "UPDATE " not in _m46.upper().replace("DO UPDATE", ""))
check("J58 it names the one home every caller must dereference",
      "billpay_fee_state" in _m46 and "billpay_basis_comparable" in _m46)
check("J59 it states the cause it does NOT claim to fix, rather than implying it does",
      "does not claim to fix" in _m46 and "does not hide" in _m46)

print("\n== K. the J locks are ARMED -- each fails when the thing it guards breaks ==")
check("K1 control: the policy-literal scan DOES match when a value is spelled",
      any(lit in 'if policy == "yes":' for lit in _POLICY_LITERALS))
check("K2 control: ... and does not match a correct dereference, so a good caller is never flagged",
      not any(lit in "st = _mr.billpay_fee_state(slot, policy)" for lit in _POLICY_LITERALS))
check("K3 control: the state machine really is load-bearing -- stubbing it to one answer would "
      "collapse J13's three findings into one",
      len({MR.FEE_STATE_NOT_CHARGED, MR.FEE_STATE_LINE_MISSING,
           MR.FEE_STATE_POLICY_UNANSWERED}) == 3)
check("K4 control: classify WITHOUT a fee_state cannot return either fee class, so the pre-policy "
      "path provably cannot be reached by the new code",
      {BDA.classify(d, b, _T) for d, b in ((0.0, 1008.0), (692.0, 290.67), (71.0, 71.0),
                                           (5.0, None))}
      .isdisjoint({BDA.CLASS_FEE_MISSING, BDA.CLASS_FEE_POLICY_UNSET}))
check("K5 control: the refusal registry is keyed by the classes it claims to cover -- a class added "
      "without a sentence would leave a silent refusal",
      set(BDA.UNASSESSED_NOTES) == set(BDA.UNASSESSED))
check("K6 control: an unassessed class with a ZERO count is not stated, so the footer never claims "
      "a refusal that did not happen",
      BDA.unassessed_counts({c: 0 for c in BDA.UNASSESSED}) == [])
check("K7 control: the singular/plural of a refusal sentence actually differs, so the count is "
      "really interpolated rather than the wording being fixed",
      BDA.unassessed_sentence(BDA.CLASS_FEE_MISSING, 1)
      != BDA.unassessed_sentence(BDA.CLASS_FEE_MISSING, 2))
check("K8 control: the fragment J43 scans is non-empty in BOTH files, so neither lock can pass "
      "because it looked at nothing",
      all(_policy_fragment(_p, _a) for _p, _a in _POLICY_CALLERS.items()),
      {_p: len(_policy_fragment(_p, _a)) for _p, _a in _POLICY_CALLERS.items()})
check("K9 control: the anchored fragment is genuinely NARROWER than the whole file, so the anchor "
      "is doing work rather than silently scanning everything",
      len(_policy_fragment("app/modules/closing/router.py", "billpay_fee"))
      < len(code_text("app/modules/closing/router.py")))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
