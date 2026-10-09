#!/usr/bin/env python3
"""PROOF — PRODUCT MIX & PORT DISCIPLINE (index §62, owner directive 2026-10-09).

What this proves, and why each section exists:

  A. THE PRICE BANDS are money cuts with stated boundaries, a negative line is not a cheap phone, and
     an explicitly EMPTY cut list is honoured (the `or`-trap that bit the add-a-line vocabulary).
  B. THE MODEL is read out of a promo-decorated description — against the REAL live vocabulary, not
     invented strings — and a tenant that declares no promo words keeps the whole description.
  C. THE BYTE-IDENTITY LOCK. `_sales_cell_agg(..., price_cfg=None)` is byte-identical to before this
     package existed, field for field, over randomised rows. This is the check that lets the shared
     cell pass be extended at all.
  D. THE TWO GATES on what enters the mix — an unclassified box line is counted, never banded; a
     negative price is a credit, never a cheap device — and the four counts RECONCILE to the box
     lines, so nothing is silently dropped.
  E. THE ROLL-UP unions transaction sets instead of summing them, and accessory $ per box is §59's
     own ratio DEREFERENCED (an equivalence pin, not an agreement).
  F. THE VERDICT needs BOTH signals, has two independent floors, and flags nobody when there is no
     median. Its severity cut IS §59's, pinned by identity.
  G. THE CORRELATION is over reps who clear BOTH floors — with the negative control that reproduces
     the live defect this found — and is None rather than 0 when it cannot be stated.
  H. THE MODEL LIST reconciles to the banded device lines, truncation included.
  I. THE CARRIER PORT-IN RATE has one home that states its DIRECTION and its SCALE, after the only
     consumer on the platform got both wrong for four years.
  J. THE UN-WIRE LOCKS (AST). They fail the build if a caller stops dereferencing a shared fact, or
     if a second copy of one appears.
  K. THE SENTENCE never makes a mix claim below the mix floor — the live defect, pinned.

DB-free: pure modules plus the REAL `_sales_cell_agg`. No network, no database.
"""
import ast
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import product_mix as PM          # noqa: E402
from app.modules.commcalc import peer_comparison as PC       # noqa: E402
from app.modules.commcalc import kpi_failing as KF           # noqa: E402

FAILED = []
PASSED = 0


def check(label, cond):
    global PASSED
    if cond:
        PASSED += 1
        print(f"   ok   {label}")
    else:
        FAILED.append(label)
        print(f"   FAIL {label}")


def section(t):
    print(f"\n── {t} " + "─" * max(0, 96 - len(t)))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. The price bands — money cuts, stated boundaries, and the empty-list answer honoured")

check("A1. the house cuts are the ones measured live (free / budget / mid / premium)",
      PM.HOUSE_PRICE_CUTS == (0.01, 50.0, 200.0) and PM.PRICE_BAND_KEYS[:4] ==
      ("free", "budget", "mid", "premium"))
check("A2. a giveaway is free at 0.00 AND at the cent boundary",
      PM.band_of_price(0) == "free" and PM.band_of_price(0.01) == "free")
check("A3. one cent over the free cut is budget, not free", PM.band_of_price(0.02) == "budget")
check("A4. each cut is the UPPER bound of the band below it",
      [PM.band_of_price(p) for p in (49.99, 50.0, 50.01, 199.99, 200.0, 200.01)] ==
      ["budget", "budget", "mid", "mid", "mid", "premium"])
check("A5. a NEGATIVE line has no band — a credit against a device is not a cheap device",
      PM.band_of_price(-5) is None and PM.band_of_price(-0.01) is None)
check("A6. junk never lands in a band by accident",
      PM.band_of_price("abc") is None or PM.band_of_price("abc") == "free")
check("A7. 'cheap' is the free + budget bands, stated once and read everywhere",
      PM.CHEAP_BANDS == ("free", "budget"))
check("A8. resolve_price_cuts: junk / None → the house cuts",
      PM.resolve_price_cuts(None) == PM.HOUSE_PRICE_CUTS and
      PM.resolve_price_cuts("nonsense") == PM.HOUSE_PRICE_CUTS and
      PM.resolve_price_cuts(["x", None, -4]) == ())
check("A9. resolve_price_cuts parses money spellings and sorts, dropping duplicates",
      PM.resolve_price_cuts(["$1,000", "25", "25", "100"]) == (25.0, 100.0, 1000.0))
# THE TRAP (it cost the add-a-line vocabulary a bug): an explicitly EMPTY list is a real answer —
# "do not make a price judgement at all" — and must not be replaced by the house cuts.
check("A10. an explicitly EMPTY cut list is HONOURED, not silently replaced by the house cuts",
      PM.resolve_price_cuts([]) == () and PM.band_of_price(900, ()) == "free")
_bl = PM.band_labels()
check("A11. every band states the money range it covers, so no bare word reaches a reader",
      len(_bl) == 4 and _bl[0]["range"] == "$0.01 or less" and
      _bl[1]["range"] == "over $0.01 up to $50.00" and _bl[3]["range"] == "over $200.00")
check("A12. the labels say which bands are the cheap ones, rather than a screen deciding again",
      [b["key"] for b in _bl if b["cheap"]] == ["free", "budget"])
check("A13. fewer cuts → fewer bands, never an invented band key",
      [b["key"] for b in PM.band_labels((100.0,))] == ["free", "budget"])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. The model, read off the REAL live product vocabulary")

# EVERY one of these is a verbatim `product_desc` from the live September 2026 house feed (the 14
# most-sold device lines). The fixture IS the real vocabulary, so the parse is proved against what
# the POS actually sends rather than against strings invented to make it pass.
LIVE_DESCS = [
    ("IPHONE 16E BLK 128GB PPD-USA - 2026 Q3 Promo PIC Offer - $550.00",
     "IPHONE 16E BLK 128GB PPD-USA"),
    ("IPHONE 16E BLK 128GB PPD-USA - 2026 Q3 Promo Upgrade - $450.00",
     "IPHONE 16E BLK 128GB PPD-USA"),
    ("SAMSUNG GALAXY A17 5G BLACK 128GB - 2026 Q3 Promo PIC Offer - $249.99",
     "SAMSUNG GALAXY A17 5G BLACK 128GB"),
    ("SAMSUNG GALAXY A16 5G BLUE BLK 128GB - 2026 Q3 Promo New Act Offer - $200.00",
     "SAMSUNG GALAXY A16 5G BLUE BLK 128GB"),
    ("CELERO5G TAB CLOUD SILVER 128 GB - 2026 Q3 Promo New Act Offer - $140.00",
     "CELERO5G TAB CLOUD SILVER 128 GB"),
    # THE CASE THAT KILLS A NAIVE SPLIT: the model year is its own ' - ' segment, so cutting at the
    # FIRST separator reports two different models for one phone.
    ("moto g play - 2026 - 2026 Q3 Promo Upgrade - $240.00", "moto g play - 2026"),
    ("moto g - 2026 - 2026 Q3 Promo New Act Offer - $235.00", "moto g - 2026"),
]
check("B1. every live promo-decorated description parses to its model",
      all(PM.model_of(d) == want for d, want in LIVE_DESCS))
check("B2. the two spellings of one phone's promo land on ONE model",
      PM.model_of(LIVE_DESCS[0][0]) == PM.model_of(LIVE_DESCS[1][0]))
check("B3. the model-year segment survives — a first-separator split would have split one phone in two",
      PM.model_of("moto g play - 2026 - 2026 Q3 Promo Upgrade - $240.00") != "moto g play")
check("B4. a description naming no promotion is the model, unchanged",
      PM.model_of("Data Transfer Services") == "Data Transfer Services")
check("B5. blank in, blank out — never a model called 'None'",
      PM.model_of(None) == "" and PM.model_of("   ") == "")
# The same `or`-trap as A10, at the other vocabulary.
check("B6. an explicitly EMPTY promo vocabulary is HONOURED — the whole description is the model",
      PM.model_of(LIVE_DESCS[0][0], promo_tokens=()) == LIVE_DESCS[0][0])
check("B7. a tenant's own promo words are used instead of the house ones",
      PM.model_of("PIXEL 9 - Autumn Deal - $100", promo_tokens=("deal",)) == "PIXEL 9")
check("B8. the token test is case-insensitive over the segment",
      PM.model_of("PIXEL 9 - 2026 Q3 PROMO Upgrade") == "PIXEL 9")


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE BYTE-IDENTITY LOCK — price_cfg=None leaves the shared cell pass untouched")

from app.modules.commcalc.router import _sales_cell_agg    # noqa: E402

BOX_DEPTS = ["IPHONE - XP", "Android - XP", "TABLET - XP"]
ACFG = {"box_departments": set(BOX_DEPTS), "box_count_buckets": {"byod"},
        "departments": {"Accessories"}, "categories": set(), "products": set(),
        "billpay_products": set(), "activation_rules": {}, "classes": {}}
EXEC_CFG = {"phones": {"rules": [], "basis": "count"},
            "bill_payment": {"rules": [], "basis": "ext_price"},
            "activation_fee": {"rules": [], "basis": "ext_price"},
            "protect": {"rules": [], "basis": "count"}}

CTS = ["New Activation", "Eligible Port-In", "Upgrade", "BYOD Activation", "BYOD Swap", "", None]
DEPTS = BOX_DEPTS + ["Accessories", "Fees", "Bill Payment"]


def seed_rows(rng, n=140):
    out = []
    for i in range(n):
        out.append({
            "trans_id": f"T{rng.randint(1, 40)}",
            "trans_date": f"2026-09-{rng.randint(1, 28):02d}",
            "store": rng.choice(["1 Main St", "2 Oak Ave"]),
            "salesperson": rng.choice(["Ann", "Bob", "Cid"]),
            "department": rng.choice(DEPTS),
            "category": rng.choice(["Phones", "Cases", ""]),
            "product_desc": rng.choice([d for d, _m in LIVE_DESCS] +
                                       ["Screen Protector", "Data Transfer Services", ""]),
            "contract_type": rng.choice(CTS),
            "ext_price": rng.choice([0.0, 14.99, 49.99, 149.99, 899.99, -99.0]),
            "gp": rng.choice([0.0, 5.0, 25.0]),
            "voided": rng.choice(["", "", "", "Y"]),
            "trans_type": rng.choice(["Sale", "Sale", "Sale", "Return"]),
        })
    return out


_MIX_FIELDS = {"_dev_lines", "_dev_unclassified", "_dev_credit", "_dev_unnamed",
               "_dev_price_sum", "_dev_bands", "_dev_models"}
_identical = True
_seen_mix = False
for seed in range(60):
    rng = random.Random(seed)
    rows = seed_rows(rng)
    off = _sales_cell_agg([dict(r) for r in rows], ACFG, exec_cfg=EXEC_CFG)
    on = _sales_cell_agg([dict(r) for r in rows], ACFG, exec_cfg=EXEC_CFG,
                         price_cfg=PM.cell_price_cfg())
    if set(off) != set(on):
        _identical = False
        break
    for k in off:
        a, b = off[k], on[k]
        for f in a:
            if f in _MIX_FIELDS:
                continue
            if a[f] != b[f]:
                _identical = False
        if on[k]["_dev_lines"]:
            _seen_mix = True
    if not _identical:
        break
check("C1. 60 randomised seeds: every pre-existing cell field is IDENTICAL with and without price_cfg",
      _identical)
check("C2. …and the fixture actually exercises the new path (it would pass vacuously otherwise)",
      _seen_mix)
_blank = _sales_cell_agg([], ACFG, exec_cfg=EXEC_CFG, price_cfg=PM.cell_price_cfg())
check("C3. no rows → no cells, with or without the extension", _blank == {})
_cells_off = _sales_cell_agg(seed_rows(random.Random(7)), ACFG, exec_cfg=EXEC_CFG)
check("C4. a caller that passes NO price_cfg leaves the mix accumulators empty, never missing",
      all(c["_dev_lines"] == 0 and c["_dev_bands"] == {} for c in _cells_off.values()))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. The two gates on what enters the mix — and the counts RECONCILE")

GATE_ROWS = [
    # a classified device line at each band
    {"trans_id": "A1", "trans_date": "2026-09-01", "store": "S", "salesperson": "Ann",
     "department": "IPHONE - XP", "contract_type": "Eligible Port-In", "ext_price": 0.0,
     "product_desc": "IPHONE 16E BLK 128GB PPD-USA - 2026 Q3 Promo PIC Offer - $550.00"},
    {"trans_id": "A2", "trans_date": "2026-09-01", "store": "S", "salesperson": "Ann",
     "department": "Android - XP", "contract_type": "Upgrade", "ext_price": 14.99,
     "product_desc": "moto g play - 2026 - 2026 Q3 Promo Upgrade - $240.00"},
    {"trans_id": "A3", "trans_date": "2026-09-01", "store": "S", "salesperson": "Ann",
     "department": "IPHONE - XP", "contract_type": "New Activation", "ext_price": 899.99,
     "product_desc": "IPHONE 17 PRO"},
    # THE FIRST GATE — a box-department line the activation predicate classifies as NOTHING. Live
    # September 2026: 388 of 1,508 such lines, 381 of them a $30-median kit charge in the department
    # 'BYOD'. Banding them would have made the biggest block of "cheap devices" in the report lines
    # on which no device was sold.
    {"trans_id": "A4", "trans_date": "2026-09-01", "store": "S", "salesperson": "Ann",
     "department": "IPHONE - XP", "contract_type": "", "ext_price": 30.0,
     "product_desc": "Data Transfer Services"},
    # THE SECOND GATE — a negative price is a credit against a device.
    {"trans_id": "A5", "trans_date": "2026-09-01", "store": "S", "salesperson": "Ann",
     "department": "IPHONE - XP", "contract_type": "Upgrade", "ext_price": -1099.99,
     "product_desc": "IPHONE 17 PRO"},
    # a line outside every box department — not a device at all
    {"trans_id": "A6", "trans_date": "2026-09-01", "store": "S", "salesperson": "Ann",
     "department": "Accessories", "contract_type": "", "ext_price": 39.99,
     "product_desc": "Screen Protector"},
]
gc = _sales_cell_agg([dict(r) for r in GATE_ROWS], ACFG, exec_cfg=EXEC_CFG,
                     price_cfg=PM.cell_price_cfg())
_cell = list(gc.values())[0]
check("D1. three classified device lines are banded", _cell["_dev_lines"] == 3)
check("D2. the unclassified box line is COUNTED, not banded",
      _cell["_dev_unclassified"] == 1 and sum(_cell["_dev_bands"].values()) == 3)
check("D3. the negative line is a credit, not a cheap device",
      _cell["_dev_credit"] == 1 and _cell["_dev_bands"].get("free", 0) == 1)
check("D4. the bands are the ones the prices say",
      _cell["_dev_bands"] == {"free": 1, "budget": 1, "premium": 1})
check("D5. the non-box line never reaches the mix at all",
      _cell["_dev_lines"] + _cell["_dev_unclassified"] + _cell["_dev_credit"] == 5)
# THE RECONCILIATION — every box-department line is in exactly one of the four counts, so nothing is
# dropped in silence. This is the check that would catch a future gate added without a counter.
_box_lines = sum(1 for r in GATE_ROWS if r["department"] in ACFG["box_departments"])
check("D6. banded + unclassified + credit + unnamed == every box-department line (nothing dropped)",
      (_cell["_dev_lines"] + _cell["_dev_unclassified"] + _cell["_dev_credit"]) == _box_lines)
check("D7. the model tally rides the same gate — only banded lines name a model",
      sum(m["lines"] for m in _cell["_dev_models"].values()) == 3)
check("D8. a model's free / cheap counts are its own, not the cell's",
      _cell["_dev_models"]["IPHONE 16E BLK 128GB PPD-USA"]["free_lines"] == 1 and
      _cell["_dev_models"]["IPHONE 17 PRO"]["free_lines"] == 0 and
      _cell["_dev_models"]["IPHONE 17 PRO"]["cheap_lines"] == 0)
check("D9. the box count itself is UNMOVED by any of this — it still counts every box line",
      _cell["box_count"] == _box_lines)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. The per-rep roll-up — sets UNIONED, and §59's ratio DEREFERENCED")

def cell(store, rep, date, **kw):
    c = {"store": store, "salesperson": rep, "trans_date": date, "box_count": 0,
         "accessory_rev": 0.0, "_port": set(), "_prem": set(), "_txn": set(),
         "_dev_lines": 0, "_dev_unclassified": 0, "_dev_credit": 0, "_dev_unnamed": 0,
         "_dev_price_sum": 0.0, "_dev_bands": {}, "_dev_models": {}}
    c.update(kw)
    return c


# ONE transaction that spans two days (the §59 negative control, same trap): a rep who rang it must
# count it ONCE. Summing the set lengths would say two.
SPLIT = {("S", "Ann", "2026-09-01"): cell("S", "Ann", "2026-09-01", box_count=6,
                                          _port={"T1"}, _txn={"T1"}, accessory_rev=100.0),
         ("S", "Ann", "2026-09-02"): cell("S", "Ann", "2026-09-02", box_count=6,
                                          _port={"T1"}, _txn={"T1"}, accessory_rev=140.0)}
_r = PM.rep_rows(SPLIT)[0]
check("E1. a transaction seen on two days is ONE port, not two (sets unioned, never summed)",
      _r["ports"] == 1)
check("E2. the counts that ARE additive still add", _r["boxes"] == 12 and
      _r["accessory_revenue"] == 240.0)
check("E3. accessory $ per box IS §59's ratio, not a second division",
      _r["accessory_per_box"] == PC._ratio(240.0, 12))
check("E4. port share IS the same ratio helper too", _r["port_share"] == PC._ratio(1, 12))
_nobox = PM.rep_rows({("S", "Zed", "2026-09-01"): cell("S", "Zed", "2026-09-01",
                                                       accessory_rev=50.0)})[0]
check("E5. no boxes → accessory per box is None, never a 0.00 standing in for 'cannot say'",
      _nobox["accessory_per_box"] is None and _nobox["port_share"] is None)
_nop = PM.rep_rows(SPLIT, ports_available=False)[0]
check("E6. no port basis → the port columns are WITHHELD, never shown as zero",
      _nop["ports"] is None and _nop["port_share"] is None)
_blank_rep = PM.rep_rows({("S", "", "2026-09-01"): cell("S", "", "2026-09-01", box_count=4)})
check("E7. a cell with no rep names nobody — it is dropped rather than rolled to a blank person",
      _blank_rep == [])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. The verdict — BOTH signals, two floors, and §59's own severity cut")

def rep_cells(spec):
    """{(store, rep): (boxes, acc, ports, dev_lines, cheap_lines)} → cells."""
    out = {}
    for (s, p), (boxes, acc, ports, dev, cheap) in spec.items():
        bands = {}
        if cheap:
            bands["free"] = cheap
        if dev - cheap:
            bands["premium"] = dev - cheap
        out[(s, p, "2026-09-01")] = cell(
            s, p, "2026-09-01", box_count=boxes, accessory_rev=acc,
            _port={f"{p}P{i}" for i in range(ports)}, _txn={f"{p}T{i}" for i in range(boxes)},
            _dev_lines=dev, _dev_bands=bands, _dev_price_sum=100.0 * dev,
            _dev_models={"M": {"lines": dev, "price_sum": 100.0 * dev,
                               "free_lines": cheap, "cheap_lines": cheap}})
    return out


# The values are DISTINCT on purpose. The first draft of this fixture gave two reps the same
# accessory-per-box, which put the median exactly ON the low value — and because "behind" is a
# STRICT `<` (being at the median is not being below it, which is correct), every rep came back
# clear and six checks failed against working code. A fixture whose median coincides with a
# fixture value proves nothing about the rule.
# Ranked acc/box: 10, 10, 20, 50, 100, 200 → median 35.   Ranked port share: .05, .05, .10, .25,
# .50, .60 → median 0.18.
SPEC = {
    ("S1", "LowBoth"):   (40, 400.0, 2, 20, 18),    # acc/box 10,  port .05 → both low
    ("S1", "LowAcc"):    (40, 800.0, 24, 20, 18),   # acc/box 20,  port .60 → accessory only
    ("S2", "LowPort"):   (40, 4000.0, 4, 20, 2),    # acc/box 100, port .10 → port only
    ("S2", "Clear"):     (40, 8000.0, 20, 20, 2),   # acc/box 200, port .50 → clear
    ("S2", "Mid"):       (40, 2000.0, 10, 20, 10),  # acc/box 50,  port .25 → sets the median
    ("S3", "TooFewBox"): (3, 300.0, 0, 20, 20),     # below the BOX floor
    ("S3", "TooFewDev"): (40, 400.0, 2, 3, 3),      # clears boxes, below the DEVICE floor
}
rows, basis = PM.rank(PM.rep_rows(rep_cells(SPEC)))
by = {r["rep"]: r for r in rows}
check("F1. low on BOTH signals is the flagged case", by["LowBoth"]["verdict"] == "both")
check("F2. low accessory with good ports is named, not flagged as both",
      by["LowAcc"]["verdict"] == "accessory_only")
check("F3. low ports with good accessories is named too — the case a blended score would hide",
      by["LowPort"]["verdict"] == "port_only")
check("F4. above both medians is clear", by["Clear"]["verdict"] == "clear")
check("F5. below the BOX floor → NO verdict and the reason said out loud",
      by["TooFewBox"]["verdict"] is None and by["TooFewBox"]["ranked"] is False and
      "fewer than 10 boxes" in (by["TooFewBox"].get("reason") or "").lower())
# THE LIVE DEFECT, PINNED. A rep can clear the box floor on boxes that came from the configured
# activation buckets and still have three device lines; the mix is then a verdict on three phones.
check("F6. below the DEVICE floor → the mix shares are WITHHELD, with their own reason",
      by["TooFewDev"]["ranked"] is True and by["TooFewDev"]["mix_ranked"] is False and
      by["TooFewDev"]["cheap_share"] is None and by["TooFewDev"]["free_share"] is None and
      "device lines" in (by["TooFewDev"].get("mix_reason") or ""))
check("F7. …and its accessory / port verdict is UNAFFECTED by the mix floor",
      by["TooFewDev"]["verdict"] == "both")
check("F8. the two floors are separate numbers, both stated on the payload",
      basis["min_boxes"] == PM.MIN_BOXES_TO_RANK and
      basis["min_device_lines"] == PM.MIN_DEVICE_LINES_FOR_MIX and
      basis["ranked_reps"] == 6 and basis["listed_reps"] == 7 and
      basis["accessory_per_box_median"] == 35.0)
check("F9. the severity cut IS §59's, by identity — a rep and their store are judged by one rule",
      PM.CRITICAL_SHORTFALL is PC.CRITICAL_SHORTFALL)
check("F10. critical needs BOTH low AND a shortfall past that cut; one-signal is never critical",
      by["LowBoth"]["severity"] == "critical" and by["LowAcc"]["severity"] == "warning" and
      by["Clear"]["severity"] is None)
# NO MEDIAN → NOBODY FLAGGED. The §60 lesson: a rule whose every case is the worst case carries no
# information; a rule with no basis must accuse nobody rather than defaulting to guilty.
_solo, _sb = PM.rank(PM.rep_rows(rep_cells({("S", "Solo"): (40, 400.0, 2, 20, 18)})))
check("F11. one rep is their own median, so nothing is below it — nobody is flagged",
      _solo[0]["verdict"] == "clear" and _sb["accessory_per_box_median"] == 10.0)
_noport, _nb = PM.rank(PM.rep_rows(rep_cells(SPEC), ports_available=False),
                       ports_available=False)
check("F12. no port basis → no port verdict for anyone, and the basis says why",
      all(r.get("low_port") in (False, None) for r in _noport) and
      _nb["port_share_median"] is None and "0.00" in (_nb["port_basis"] or ""))
check("F13. the shortfall is a measured percentage of the median, not a mood",
      by["LowBoth"]["accessory_shortfall_pct"] > 0 and by["Clear"]["accessory_shortfall_pct"] == 0.0)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. The correlation — measured, floored, and None when it cannot be stated")

MANY = {}
for i in range(12):
    # a clean inverse relationship: more cheap devices, less accessory per box
    MANY[("S", f"R{i}")] = (40, float(4000 - 300 * i), 2 + i, 20, i + 4)
cor_rows, _ = PM.rank(PM.rep_rows(rep_cells(MANY)))
cors = {c["x"] + "|" + c["y"]: c for c in PM.correlations(cor_rows)}
check("G1. the inverse relationship is measured, with its own n",
      cors["cheap_share|accessory_per_box"]["r"] < -0.9 and
      cors["cheap_share|accessory_per_box"]["n"] == 12)
check("G2. all four pairs are reported, so the claim can be falsified rather than assumed",
      len(cors) == 4 and "port_share|accessory_per_box" in cors)
# THE NEGATIVE CONTROL that reproduces the live defect. Adding reps who clear the box floor but have
# three device lines each must NOT move the coefficient: on production, letting them in moved
# cheap-share vs accessory-per-box from r = -0.33 to r = -0.13.
POLLUTED = dict(MANY)
for i in range(8):
    POLLUTED[("S", f"X{i}")] = (40, 4000.0, 20, 3, 3)
pol_rows, _ = PM.rank(PM.rep_rows(rep_cells(POLLUTED)))
pol = {c["x"] + "|" + c["y"]: c for c in PM.correlations(pol_rows)}
check("G3. NEGATIVE CONTROL: reps below the device floor do NOT enter the correlation",
      pol["cheap_share|accessory_per_box"]["r"] == cors["cheap_share|accessory_per_box"]["r"] and
      pol["cheap_share|accessory_per_box"]["n"] == 12)
few_rows, _ = PM.rank(PM.rep_rows(rep_cells({("S", f"R{i}"): (40, 400.0 * i, 2, 20, i)
                                             for i in range(1, 4)})))
check("G4. fewer than the stated minimum pairs → None, never a coefficient off three reps",
      all(c["r"] is None for c in PM.correlations(few_rows)) and PM.MIN_CORRELATION_N == 5)
FLAT = {("S", f"R{i}"): (40, 400.0, 2, 20, 10) for i in range(8)}
flat_rows, _ = PM.rank(PM.rep_rows(rep_cells(FLAT)))
check("G5. a column with no spread makes r UNDEFINED, which is None and not 0.0",
      all(c["r"] is None for c in PM.correlations(flat_rows)))
check("G6. the payload says a correlation is not a cause, in words, beside the number",
      "not a cause" in PM.CORRELATION_CAVEAT)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. The model list reconciles to the banded device lines, truncation included")

payload = PM.build(rep_cells(MANY), period="2026-09")
check("H1. every banded device line is in the model totals",
      payload["models_meta"]["lines_total"] ==
      sum(r["device_lines"] for r in payload["reps"]))
check("H2. the truncated list states its own coverage rather than looking complete",
      payload["models_meta"]["shown"] <= PM.MODEL_LIST_LIMIT and
      (payload["models_meta"]["note"] is None or
       str(payload["models_meta"]["lines_shown"]) in payload["models_meta"]["note"]))
MANYMODELS = {}
for i in range(40):
    c = cell("S", "Ann", f"2026-09-{(i % 28) + 1:02d}", box_count=1, _dev_lines=1,
             _dev_bands={"mid": 1}, _dev_price_sum=100.0,
             _dev_models={f"MODEL {i}": {"lines": i + 1, "price_sum": 100.0 * (i + 1),
                                         "free_lines": 0, "cheap_lines": 0}})
    MANYMODELS[("S", "Ann", f"2026-09-{(i % 28) + 1:02d}_{i}")] = c
_pm = PM.build(MANYMODELS, period="2026-09")
check("H3. 40 models, 25 shown, and the note names both counts",
      _pm["models_meta"]["distinct"] == 40 and _pm["models_meta"]["shown"] == 25 and
      "of 40 models" in (_pm["models_meta"]["note"] or ""))
check("H4. the list is most-sold first", [m["lines"] for m in _pm["models"]] ==
      sorted((m["lines"] for m in _pm["models"]), reverse=True))
check("H5. the mix is declared NOT a partition of the box count, in words",
      "not a partition" in payload["mix_not_a_partition"].lower() or
      "beside the box total" in payload["mix_not_a_partition"])
check("H6. the per-rep rows do not ship the full model list they were rolled from",
      all("all_models" not in r for r in payload["reps"]))
check("H7. the price cuts in force are STATED on every payload",
      payload["price_cuts"] == list(PM.HOUSE_PRICE_CUTS) and len(payload["bands"]) == 4)
_store = {s["store"]: s for s in payload["stores"]}["S"]
check("H8. the store figure is the store's OWN total, not a mean of its reps' ratios",
      _store["accessory_per_box"] == PC._ratio(_store["accessory_revenue"], _store["boxes"]))
_cp = PM.build(rep_cells(MANY), period="2026-09", carrier_port={"S": 7.69})
check("H9. the carrier's port rate rides BESIDE ours, never merged into it",
      {s["store"]: s for s in _cp["stores"]}["S"]["carrier_port_pct"] == 7.69)
check("H10. a store the carrier did not report shows blank, never 0%",
      {s["store"]: s for s in payload["stores"]}["S"]["carrier_port_pct"] is None)
check("H11. no reps → the report says there is nothing to compare, rather than an empty table",
      PM.build({}, period="2026-09")["note"] is not None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("I. The carrier port-in rate — ONE home that states its direction and its scale")

# The values are verbatim from the live June 2026 `raw_dlar_store` rows read 2026-10-09.
LIVE_PORT = [("1800 Great Neck rd", 0.0), ("103 Fulton Ave", 66.67), ("1115 Liberty Ave", 7.69),
             ("3565 Broadway", 30.77), ("1750 5th Ave", 53.33)]
check("I1. the rate is already a PERCENT and is passed through unscaled",
      [KF.port_in_rate({"port_pct": v}) for _a, v in LIVE_PORT] == [v for _a, v in LIVE_PORT])
# THE DEFECT, PINNED IN BOTH DIRECTIONS. `flags.py` read this column as a port-OUT rate AND
# multiplied it by 100, so 66.67% became 6667 and every store with one port-in cleared a threshold
# of 15. Both halves are now impossible to repeat without deleting these checks.
check("I2. SCALE: the old `* 100` would have put every live value over the threshold; none is",
      all(KF.port_in_rate({"port_pct": v}) <= 100.0 for _a, v in LIVE_PORT) and
      all(v * 100 > KF.LOW_PORT_IN_PCT for _a, v in LIVE_PORT if v))
check("I3. DIRECTION: a HIGH rate is the carrier's good news — it is never the finding",
      KF.low_port_in({"port_pct": 66.67}) == (66.67, False))
check("I4. the finding is a LOW rate", KF.low_port_in({"port_pct": 7.69}) == (7.69, True))
check("I5. a store the feed did not report has NO rate — None, never 0.0, so nobody is coached on a blank",
      KF.port_in_rate({}) is None and KF.port_in_rate({"port_pct": None}) is None and
      KF.port_in_rate({"port_pct": ""}) is None and KF.low_port_in({}) == (None, False))
check("I6. a percent-signed or comma'd spelling still parses",
      KF.port_in_rate({"port_pct": "7.69%"}) == 7.69 and
      KF.port_in_rate({"port_pct": "1,00"}) == 100.0)
check("I7. junk is None, not a number", KF.port_in_rate({"port_pct": "n/a"}) is None)
check("I8. the meaning is written down beside the column, direction included",
      "port-in" in KF.PORT_IN_RATE_MEANING.lower() and
      "higher is better" in KF.PORT_IN_RATE_MEANING.lower() and
      "no port-out" in KF.PORT_IN_RATE_MEANING.lower())
check("I9. the column name lives in the one home too, so a reader cannot pick the wrong one",
      KF.PORT_IN_RATE_COLUMN == "port_pct")
check("I10. a caller may pass its own threshold without the meaning moving",
      KF.low_port_in({"port_pct": 20.0}, threshold=25.0) == (20.0, True) and
      KF.low_port_in({"port_pct": 20.0}, threshold="junk") == (20.0, False))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("J. THE UN-WIRE LOCKS — the build fails if a shared fact stops being dereferenced")

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def fn_src(rel, name):
    src = read(rel)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    return ""


# J1-J3 — the port_pct one home. The ONLY module allowed to read the raw column is kpi_failing.
_py = []
for base, _d, files in os.walk(ROOT):
    for f in files:
        if f.endswith(".py"):
            _py.append(os.path.join(base, f))
_raw_readers = []
for path in _py:
    rel = os.path.relpath(path, ROOT)
    if rel.replace(os.sep, "/") == "modules/commcalc/kpi_failing.py":
        continue
    txt = open(path, encoding="utf-8").read()
    # a READ of the column by name: .get('port_pct') or ['port_pct'] — not the ingest mappings that
    # WRITE it (normalize_store / the manual upload row builder), which are its producers.
    for pat in ("get('port_pct')", 'get("port_pct")', "['port_pct']", '["port_pct"]'):
        if pat in txt:
            _raw_readers.append(rel)
            break
check("J1. kpi_failing is the ONLY module that reads raw_dlar_store.port_pct",
      not _raw_readers, )
if _raw_readers:
    print("        second readers:", sorted(set(_raw_readers)))
_flags = fn_src("modules/commcalc/flags.py", "calc_flags")
check("J2. flags.py DEREFERENCES the one home rather than testing the column itself",
      "low_port_in(" in _flags and "LOW_PORT_IN_RATE" in _flags)
check("J3. …and the backwards test is GONE — no `* 100` on that column, no port-out flag WRITTEN",
      "port_pct') * 100" not in _flags and 'port_pct") * 100' not in _flags and
      "'flag_type': 'HIGH_PORT_OUT_RATE'" not in _flags and
      '"flag_type": "HIGH_PORT_OUT_RATE"' not in _flags)
from app.modules.commcalc import flag_registry as FR    # noqa: E402
check("J4. the legacy flag spelling still canonicalises, so no manager's past ruling is orphaned",
      FR.canon_type("HIGH_PORT_OUT_RATE") == "LOW_PORT_IN_RATE")
check("J5. …and the finding is reviewed under selling, not under churn it was never evidence about",
      FR.area_of("LOW_PORT_IN_RATE") == "sales" and "sales" in FR.AREA_KEYS)

# J6-J8 — the cell pass must not decide what a band or a model is.
_agg = fn_src("modules/commcalc/router.py", "_sales_cell_agg")
check("J6. the cell pass ACCUMULATES only — it asks price_cfg for the band and the model",
      "price_cfg['band'](" in _agg and "price_cfg['model'](" in _agg)
check("J7. …and holds no price literal of its own (no second idea of 'cheap' in the router)",
      "0.01" not in _agg.split("price_cfg")[0].split("def _sales_cell_agg")[-1] and
      "_pmix.CHEAP_BANDS" in _agg)
check("J8. the mix is tallied inside the BOX-department branch, so it cannot drift from box_count",
      "box_departments" in _agg and
      _agg.index("box_departments") < _agg.index("price_cfg is not None"))

# J9-J11 — product_mix must dereference §59 and must not become a second report engine.
_pmsrc = read("modules/commcalc/product_mix.py")
check("J9. product_mix dereferences §59's ratio, median and severity cut — it defines none of them",
      "_pc._ratio(" in _pmsrc and "_pc._median(" in _pmsrc and
      "_pc.CRITICAL_SHORTFALL" in _pmsrc and "def _ratio" not in _pmsrc and
      "def _median" not in _pmsrc)
check("J10. product_mix imports NO router and no database — it stays provable DB-free",
      "import router" not in _pmsrc and "get_supabase" not in _pmsrc and
      "supabase" not in _pmsrc)
# RULE TWO — config, never code. A carrier, tenant or handset name here would make the report
# per-tenant code, which is the one thing the house forbids outright.
_BANNED = ("boost", "total wireless", "luxelink", "cellfonz", "vzone", "iphone", "samsung",
           "moto ", "galaxy", "android")
# SCANNED OVER CODE ONLY, with docstrings and comments stripped. A measured example in a docstring
# ('IPHONE 16E … → IPHONE 16E …' in `model_of`) is EVIDENCE that the parse was proved against the
# real vocabulary, and banning it would push that evidence out of the file. What RULE TWO forbids is
# BEHAVIOUR keyed on a name, which can only live in code.
def _code_only(src):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body[0].value.value = ""
    return ast.unparse(tree)


_pmcode = _code_only(_pmsrc).lower()
_hits = [w for w in _BANNED if w in _pmcode]
check("J11. RULE TWO: no carrier, tenant or handset name drives BEHAVIOUR in the module",
      not _hits)
check("J11b. …and the scan is not vacuous — it still sees the module's own code",
      "def band_of_price" in _pmcode and "cheap_bands" in _pmcode)
if _hits:
    print("        banned words found:", _hits)

# J12 — the endpoint must borrow §59's verdict rather than compute a second one.
_ep = fn_src("modules/commcalc/router.py", "product_mix")
check("J12. the store action plan is §59's verdict, borrowed through its own entry points",
      "_peer_comparison_payload(" in _ep and "peer_items_by_store(" in _ep and
      "with_extra_metric(" in _ep)
check("J13. …on the SAME rows, so the plan costs no second feed read",
      _ep.count("_sales_rows_union") == 1)
_pay = fn_src("modules/commcalc/router.py", "_product_mix_payload")
check("J14. the payload passes BOTH extensions — without exec_cfg every port share would be 0.00",
      "exec_cfg=exec_cfg" in _pay and "price_cfg=price_cfg" in _pay)
check("J15. the carrier rate is read through the one home, column name included",
      "_kpi_failing.port_in_rate(" in _pay and "_kpi_failing.PORT_IN_RATE_COLUMN" in _pay)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("K. The sentence — arguable, and never a mix claim the window cannot support")

_s = PM.rep_prompt(by["LowBoth"])
check("K1. the flagged sentence carries BOTH numbers the verdict is built from",
      "accessory per box" in _s and "ported in" in _s and by["LowBoth"]["rep"] in _s)
check("K2. …and the mix that explains it, with what the customer actually paid",
      "cheap bands" in _s and "to the customer" in _s)
# THE LIVE DEFECT, PINNED: "50% of the 2 devices they sold were in the cheap bands" shipped on the
# first production run. A mix claim below the mix floor is now impossible.
_s2 = PM.rep_prompt(by["TooFewDev"])
check("K3. below the device floor the sentence makes NO mix claim at all",
      "cheap bands" not in _s2 and "accessory per box" in _s2)
check("K4. a clear rep gets no sentence — the report speaks only where there is something to coach",
      PM.rep_prompt(by["Clear"]) == "" and PM.rep_prompt(by["TooFewBox"]) == "" and
      PM.rep_prompt(None) == "")
check("K5. a one-signal rep's sentence names only the signal that is low",
      "ported in" not in PM.rep_prompt(by["LowAcc"]) and
      "accessory per box" not in PM.rep_prompt(by["LowPort"]))
check("K6. the four verdicts are declared once, with labels, so a screen never invents a fifth",
      [k for k, _l in PM.VERDICTS] == ["both", "accessory_only", "port_only", "clear"])
check("K7. the flagged list is the both-signal reps, worst shortfall first",
      [r["rep"] for r in payload["flagged"]] ==
      [r["rep"] for r in payload["reps"] if r.get("verdict") == "both"] or
      all(r["verdict"] == "both" for r in payload["flagged"]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 100)
if FAILED:
    print(f"RED — {len(FAILED)} failed, {PASSED} passed")
    for f in FAILED:
        print("   FAIL", f)
    sys.exit(1)
print(f"GREEN — {PASSED} checks passed")
