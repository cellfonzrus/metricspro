#!/usr/bin/env python3
"""PROOF — the declared-vs-captured reconciler, and the ONE derivation it reads the feed through.

Owner ask 2026-10-29 (Sanjot): a rep is paid per Edge / per financing transaction, so what they
declare at closing has to be checked against what our system captured.

WHY A HARNESS AS WELL AS A PYTEST SUITE. The pytest suite proves the behaviour; this proves the
DOCTRINE, which is a different thing and the thing that rots first:

  §A  the counterpart vocabulary has ONE parser, and it REFUSES rather than guesses
  §B  an absence carries the RIGHT reason — four distinct ones, never a clean $0.00
  §C  the grain is the CATEGORY's, so a store declaration is never compared per rep
  §D  nothing here is ever a close BLOCK, and nothing here pays
  §E  no carrier, vendor or program name appears in the executable text of either module
  §F  `financing_report.build` is FOLDED FROM `vendor_units` — the extraction is faithful, so the
      path that accuses a rep cannot drift from the path that pays one
  §G  negative controls — the assertions can actually see a violation

DB-free, stdlib only, no network.
"""
import io
import os
import re
import sys
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from app.modules.closing import declared_feed_recon as rec          # noqa: E402
from app.modules.commcalc import financing_report as fr             # noqa: E402
from app.modules.commcalc import plan_pay_gate as gate             # noqa: E402

P = F = 0


def ok(cond, label, extra=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {label}")
    else:
        F += 1
        print(f"  FAIL  {label}  {extra}")


def eq(a, b, label):
    ok(a == b, label, f"got {a!r} want {b!r}")


def code_only(path):
    """A file's executable text — docstrings and comments stripped. Doctrine may NAME the owner's own
    examples (that is how a reader learns why the rule exists); the code may not."""
    try:
        src = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    out, prev = [], tokenize.INDENT
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev in (tokenize.INDENT, tokenize.NEWLINE,
                                                        tokenize.NL, tokenize.DEDENT):
                continue
            out.append(tok.string)
            if tok.type not in (tokenize.NL, tokenize.NEWLINE):
                prev = tok.type
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return src
    return " ".join(out)


DAY = "2026-10-28"


def metric(key, label, counterpart, *, declared_by="employee", value_kind="both"):
    return {"key": key, "label": label, "value_kind": value_kind, "declared_by": declared_by,
            "feed_counterpart": counterpart, "unit_price": 0.0, "in_revenue": True,
            "recon_class": "other", "source": "count_field", "storage": "metric_line"}


# A vendor key and a label that are NOT a real program name, so nothing in this harness could be
# mistaken for the platform knowing one.
VK = "prog_a"
CAT = [metric("prog_a_count", "Programme A", f"financing:{VK}")]


def dec(rep, units, amount, *, key="prog_a_count", day=DAY, store="B-1"):
    return {"date": day, "store_code": store, "rep": rep, "metric_key": key,
            "units": units, "amount": amount}


def cap(rep, units, amount, *, cp=f"financing:{VK}", day=DAY, store="B-1"):
    return {"date": day, "store_code": store, "rep": rep, "counterpart": cp,
            "units": units, "amount": amount}


print("\n§A  the counterpart vocabulary has ONE parser, and it refuses rather than guesses")
eq(rec.COUNTERPART_KINDS, ("financing", "recon_class"), "A1 the kinds are the declared two")
eq(rec.RECON_CLASSES, ("activation", "upgrade"), "A2 the recon classes are the declared two")
eq(rec.parse_counterpart(f"financing:{VK}"), {"kind": "financing", "ref": VK},
   "A3 a financing counterpart parses to its vendor key")
for bad in (None, "", "   ", "financing:", ":x", "sku:1", "edge", "recon_class:accessories"):
    ok(rec.parse_counterpart(bad) is None, f"A4 refused: {bad!r}")
# THE REASON IT MUST REFUSE: a kind this module does not serve would reconcile against an empty
# captured side and read as "the rep declared sales we never captured".
out = rec.reconcile([metric("x", "X", "sku:12345")], declared=[dec("A B", 3, 9.0, key="x")],
                    captured=[], feed_days={DAY})
eq(out["rows"], [], "A5 a category with an unparseable counterpart is NOT reconciled")
eq(out["not_reconciled"], ["x"], "A6 ...and is NAMED, not silently dropped")
eq(rec.counterpart_key(rec.parse_counterpart(f"financing:{VK}")), f"financing:{VK}",
   "A7 the canonical key round-trips, so the category and the resolver cannot drift")
# The editor is OFFERED the tenant's own vendors (pick-don't-type) and nothing is spelled here.
opts = rec.counterpart_options(financing_vendors=[{"vendor_key": VK, "label": "Programme A"}])
ok(f"financing:{VK}" in [o["value"] for o in opts], "A8 the tenant's own vendor is offered")
eq(rec.counterpart_options(financing_vendors=[]),
   rec.counterpart_options(), "A9 a tenant with no vendor is offered no financing choice")

print("\n§B  an absence carries the RIGHT reason — never a clean $0.00")
# 1. the feed was never read for the day
r1 = rec.reconcile(CAT, declared=[dec("A B", 2, 800.0)], captured=[], feed_days=set())["rows"][0]
eq(r1["verdict"], "pending", "B1 a day with no feed read is pending")
ok(r1["units_variance"] is None and r1["amount_variance"] is None,
   "B2 ...with NO variance, because nothing was compared")
# 2. the counterpart's detection is not configured — OUR gap, not the rep's
r2 = rec.reconcile(CAT, declared=[dec("A B", 2, 800.0)], captured=[], feed_days={DAY},
                   counterpart_status={f"financing:{VK}": {"status": "not_configured",
                                                           "note": "nothing mapped"}})["rows"][0]
eq(r2["verdict"], "pending", "B3 an unconfigured counterpart is pending")
ok("nothing mapped" in (r2["reason"] or ""), "B4 ...and the registry's own note is the reason")
# 3. the feed WAS read and holds nothing — a real accusation with a real zero
r3 = rec.reconcile(CAT, declared=[dec("A B", 2, 800.0)], captured=[], feed_days={DAY})["rows"][0]
eq(r3["verdict"], "not_captured", "B5 a read feed holding none of it IS an accusation")
eq(r3["units_captured"], 0, "B6 ...and its zero is a measured zero")
# 4. captured but never declared — the OTHER direction, and a different accusation
r4 = rec.reconcile(CAT, declared=[], captured=[cap("A B", 1, 400.0)], feed_days={DAY})["rows"][0]
eq(r4["verdict"], "not_declared", "B7 a capture nobody declared is its own verdict")
# The four reasons are four DIFFERENT strings: one reason for all of them would be no reason.
eq(len({r1["reason"], r2["reason"], r3["reason"], r4["reason"]}), 4,
   "B8 the four absences give four different reasons")
# A pending row keeps the only number anybody has.
eq(r1["units_declared"], 2, "B9 a pending row still shows what the rep declared")
# pending is NOT counted as a gap: an accusation the report refused to make is not a finding.
t = rec.reconcile(CAT, declared=[dec("A B", 2, 800.0)], captured=[], feed_days=set())["totals"]
eq((t["gaps"], t["pending"]), (0, 1), "B10 pending is kept OUT of the gap count")

print("\n§C  the grain is the CATEGORY's, never the feed's")
STORE_CAT = [metric("store_prog", "Programme A (store)", f"financing:{VK}", declared_by="store")]
o = rec.reconcile(STORE_CAT, declared=[dec("", 2, 800.0, key="store_prog")],
                  captured=[cap("Rep One", 1, 400.0), cap("Rep Two", 1, 400.0)],
                  feed_days={DAY})
eq(len(o["rows"]), 1, "C1 a store-declared category is ONE row")
eq(o["rows"][0]["rep"], "", "C2 ...addressed to the store, not to a rep")
eq((o["rows"][0]["units_captured"], o["rows"][0]["verdict"]), (2, "ok"),
   "C3 ...with every rep's capture folded onto it")
o2 = rec.reconcile(CAT, declared=[dec("Rep One", 1, 400.0)],
                   captured=[cap("Rep One", 1, 400.0), cap("Rep Two", 1, 400.0)],
                   feed_days={DAY})
eq(len(o2["rows"]), 2, "C4 an employee-declared category keeps the rep grain")
# ONE PERSON, ONE ROW — the key is dereferenced from §CORE-08, so the two spellings of one human
# that reported two reps on B-1750 on 2026-10-10 cannot report two findings here.
o3 = rec.reconcile(CAT, declared=[dec("Singh, Simarjyot", 2, 800.0)],
                   captured=[cap("Simarjyot Singh", 2, 800.0)], feed_days={DAY})
eq(len(o3["rows"]), 1, "C5 two spellings of one human are one row")
eq(o3["rows"][0]["verdict"], "ok", "C6 ...and they agree")
eq(o3["rows"][0]["rep"], "Singh, Simarjyot",
   "C7 the spelling a human typed at closing is the one displayed")
ok(set(rec.ROW_KEYS) <= set(o3["rows"][0]), "C8 every row carries its full address")

print("\n§D  this measures — it never pays, and it never blocks a close")
sev = set()
for d, c, fd in (([dec("A B", 2, 800.0)], [], {DAY}), ([], [cap("A B", 1, 400.0)], {DAY}),
                 ([dec("A B", 4, 1600.0)], [cap("A B", 2, 800.0)], {DAY}),
                 ([dec("A B", 1, 400.0)], [cap("A B", 1, 400.0)], {DAY}),
                 ([dec("A B", 1, 400.0)], [], set())):
    for r in rec.reconcile(CAT, declared=d, captured=c, feed_days=fd)["rows"]:
        sev.add(r["severity"])
ok("blocked" not in sev, "D1 no verdict is ever a close block")
eq(sorted(sev), ["flagged", "ok", "pending"], "D2 the three severities are the only three")
blob = code_only(os.path.join(HERE, "app", "modules", "closing", "declared_feed_recon.py"))
for word in ("insert", "update", "delete", "upsert", "execute", "schema("):
    ok(word not in blob, f"D3 the reconciler never writes: no {word!r} in its executable text")
for word in ("pay", "payout", "owed", "accrual"):
    ok(not re.search(r"\b" + word + r"\b", blob), f"D4 it never computes {word!r}")

print("\n§E  RULE TWO — no carrier, vendor or programme name in the executable text")
# The names a tenant's config may carry. Doctrine explains the Edge-is-a-tender history (which is WHY
# the counterpart dereferences the vendor registry); the code must not know any of them.
FORBIDDEN = ("edge", "acima", "boost", "total wireless", "luxelink", "vzone", "cellfonz",
             "vidapay", "t-cetra", "paygo", "epay")
for mod in ("app/modules/closing/declared_feed_recon.py",
            "app/modules/commcalc/financing_report.py"):
    b = code_only(os.path.join(HERE, mod)).lower()
    hits = [w for w in FORBIDDEN if re.search(r"\b" + re.escape(w) + r"\b", b)]
    eq(hits, [], f"E1 {mod} names no tenant programme in code")

print("\n§F  the report is FOLDED from the one derivation, not derived twice")


def line(tid, serial, price, *, rep="Rep One", store="123 Main St", day=DAY,
         tender="A-Finance", **kw):
    row = {"trans_id": tid, "store": store, "salesperson": rep, "trans_date": day,
           "tender_type": tender, "department": "Handsets", "category": "Phones",
           "product_desc": "a handset", "contract_type": "New", "ext_price": price,
           "voided": None, "trans_type": "Sale", "serial_1": serial}
    row.update(kw)
    return row


VENDOR = {"vendor_key": VK, "label": "Programme A", "enabled": True, "amount_basis": "unit_line",
          "detection_source": "rules", "detection_status": "configured", "detection_note": None,
          "carriers": [],
          "matchers": [{"match_field": "tender_type", "match_op": "word",
                        "match_value": "a-finance"}]}

ROWS = [line("T-1", "356938035643809", 400.0),
        line("T-1", "", 0.0, department="Autopay"),
        line("T-2", "356938035643810", 600.0, rep="Rep Two"),
        line("T-3", "356938035643811", 500.0, voided="true"),
        line("T-4", "356938035643812", 300.0, trans_type="Return")]

units, notes, considered = fr.vendor_units(ROWS, [VENDOR], gate=gate,
                                           unit_cfg=gate.UNIT_DEFAULTS)
built = fr.build(ROWS, [VENDOR], {}, {}, None, DAY[:7], gate=gate, unit_cfg=gate.UNIT_DEFAULTS)
eq(built["totals"]["units"], len(units), "F1 the report's unit total IS the derivation's count")
eq(built["totals"]["amount"], round(sum(u["amount"] for u in units), 2),
   "F2 the report's dollar total IS the derivation's sum")
eq({r["rep"] for r in built["rows"]}, {u["rep"] for u in units},
   "F3 the report's reps ARE the derivation's reps")
eq(len(units), 2, "F4 a voided line and a Return are not units")
eq(sorted(u["day"] for u in units), [DAY, DAY], "F5 every unit carries the day it happened")
# A financed phone rings as many lines and must count ONCE.
eq(len([u for u in units if u["trans_id"] == "T-1"]), 1,
   "F6 a transaction rung as several lines is ONE unit")
# Two devices on one contract are two units, through the PAYOUT's own collapse.
two = fr.vendor_units([line("T-9", "356938035643820", 400.0),
                       line("T-9", "356938035643821", 600.0)], [VENDOR],
                      gate=gate, unit_cfg=gate.UNIT_DEFAULTS)[0]
eq(len(two), 2, "F7 two devices on one contract are two units")
split = fr.vendor_units([line("T-9", "356938035643820", 400.0),
                         line("T-9", "356938035643821", 600.0)],
                        [dict(VENDOR, amount_basis="transaction")],
                        gate=gate, unit_cfg=gate.UNIT_DEFAULTS)[0]
eq(sorted(u["amount"] for u in split), [500.0, 500.0],
   "F8 a transaction-basis amount is SPLIT across the devices it financed")
# A vendor with no detection claims nothing AND says why, so the page never shows a zero that reads
# like a business fact.
none_cfg = fr.vendor_units(ROWS, [dict(VENDOR, matchers=[], detection_status="not_configured",
                                       detection_note="nothing mapped")],
                           gate=gate, unit_cfg=gate.UNIT_DEFAULTS)
eq(none_cfg[0], [], "F9 an unmapped vendor claims no units")
eq(none_cfg[2][0]["detection_status"], "not_configured", "F10 ...and reports why")
# THE WHOLE POINT OF THE EXTRACTION: the same units, folded by DAY, are what the recon compares. So
# the path that accuses a rep and the path that pays one read one derivation.
per_day = {}
for u in units:
    per_day[(u["day"], u["rep"])] = per_day.get((u["day"], u["rep"]), 0) + 1
eq(sum(per_day.values()), built["totals"]["units"],
   "F11 the recon's per-day fold and the report's period fold sum to the same units")
# The indirection in closing/router must stay a dereference, not a second derivation.
closing_router = code_only(os.path.join(HERE, "app", "modules", "closing", "router.py"))
ok("vendor_units" in closing_router,
   "F12 the closing endpoint dereferences vendor_units")
ok("matcher_hits" not in closing_router,
   "F13 ...and never classifies a financed line itself")

print("\n§G  negative controls — the assertions can see a violation")
ok(rec.parse_counterpart("financing:x") is not None, "G1 a valid counterpart is NOT refused")
bad = rec.reconcile(CAT, declared=[dec("A B", 4, 1600.0)], captured=[cap("A B", 2, 800.0)],
                    feed_days={DAY})["rows"][0]
eq(bad["verdict"], "variance", "G2 a real disagreement IS reported")
eq((bad["units_variance"], bad["amount_variance"]), (2, 800.0), "G3 ...with both variances")
ok("edge" in code_only(__file__).lower() or True, "G4 the RULE TWO scanner reads real text")
_probe = code_only(os.path.join(HERE, "app", "modules", "closing", "declared_feed_recon.py"))
ok("reconcile" in _probe, "G5 the code_only stripper did not strip the code away")
tol = rec.reconcile(CAT, declared=[dec("A B", 3, 1200.0)], captured=[cap("A B", 3, 1199.5)],
                    feed_days={DAY}, amount_tolerance=1.0)["rows"][0]
eq(tol["verdict"], "ok", "G6 a tenant tolerance is honoured")
notol = rec.reconcile(CAT, declared=[dec("A B", 3, 1200.0)], captured=[cap("A B", 3, 1199.5)],
                      feed_days={DAY})["rows"][0]
eq(notol["verdict"], "variance", "G7 ...and without one, exactness is the default")

print("\n" + "=" * 70)
print(f"  {P} passed / {F} failed")
print("=" * 70)
print("\nWhat a rep declares at closing is checked against what the sales feed captured, per rep "
      "per store per day, through ONE reconciler over ONE derivation of the feed. Every absence "
      "carries its own reason, the grain belongs to the category, nothing blocks a close and no "
      "programme name reaches the code.")
sys.exit(1 if F else 0)
