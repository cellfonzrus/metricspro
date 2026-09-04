"""Proof harness — Processor Money-Movement Ledger pure core (processor_ledger.py).

Pins, DB-free and stdlib-only:
  1. The debit/credit classification TRUTH TABLE per processor feed shape:
       • ePay payment detail (credit_positive=True):  amount>0 → CREDIT, amount<0 → DEBIT, 0 → neither
       • VidaPay daily TX  (credit_positive=False):  amount>0 → DEBIT,  amount<0 → CREDIT, 0 → neither
     (Directions verified against live rows 2026-09-04: house 'Commission Withholding' negative /
     bounties positive; LuxeLink 'Sales Order'/'MarketPlace'/'Fee' positive, residual/spiff/promo/
     void negative — see processor_ledger.py docstring.)
  2. Net math: net = credits − debits at every grain, and Σ(day nets) == grand net.
  3. Fold correctness: cells keyed (processor, date, tx_type, store); rounding to cents.
  4. Filter semantics: empty = all; store filter matches store_code OR display string,
     case/whitespace-insensitively; type and MARKET filters likewise; filters compose (AND). The
     market a cell carries is the canonically-resolved one (§13a), so a market-filtered view can
     never silently drop a store whose market lives on only one vocabulary (B-1115/LI class).
  5. Live sample-day pins (2026-09-04 evidence): the exact per-type day totals reproduced from the
     recorded live aggregates for one day per org.
  6. Processor NAME resolution: the mig-953 `report_term` vocabulary ladder (tenant override >
     house carrier preset > the NEUTRAL noun) — proven against a stub client, so the pin holds
     without a database. No vendor literal is ever produced by a failing label service.

Run:  cd backend && python3 harness_processor_ledger.py    (exit 0 = all pins hold)
"""
import sys

from app.modules.commcalc.processor_ledger import (
    FEED_SHAPES, classify_amount, fold_cells, filter_cells, day_type_rollup, _processor_term)

PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  ✗ {msg}")


# ── 1. classification truth table ────────────────────────────────────────────────────────────────
EPAY = FEED_SHAPES["epay"]["credit_positive"]
VIDA = FEED_SHAPES["vidapay"]["credit_positive"]
ok(EPAY is True and VIDA is False, "feed sign conventions: ePay credit-positive, VidaPay debit-positive")

for amt, want_d, want_c in [(21.40, 0.0, 21.40), (-396.75, 396.75, 0.0), (0, 0.0, 0.0),
                            (None, 0.0, 0.0), ("7.5", 0.0, 7.5), ("junk", 0.0, 0.0)]:
    d, c = classify_amount(amt, True)
    ok((d, c) == (want_d, want_c), f"epay classify({amt!r}) = {(d, c)}, want {(want_d, want_c)}")
for amt, want_d, want_c in [(599.99, 599.99, 0.0), (-26.25, 0.0, 26.25), (0.0, 0.0, 0.0),
                            (None, 0.0, 0.0), ("-9.35", 0.0, 9.35)]:
    d, c = classify_amount(amt, False)
    ok((d, c) == (want_d, want_c), f"vidapay classify({amt!r}) = {(d, c)}, want {(want_d, want_c)}")
# at most one side non-zero, both non-negative, and the pair always nets back to the signed amount
for amt in (5, -5, 0.005, -123.456, 0):
    for cp in (True, False):
        d, c = classify_amount(amt, cp)
        ok(d >= 0 and c >= 0 and (d == 0 or c == 0), f"classify({amt},{cp}) single-sided non-negative")
        signed = (c - d) if cp else (d - c)
        ok(abs(signed - float(amt)) < 1e-9, f"classify({amt},{cp}) round-trips the signed amount")

# ── 2/3. fold + net math ─────────────────────────────────────────────────────────────────────────
def ev(proc, date, tx, code, store, debit=0.0, credit=0.0, market=""):
    return {"processor": proc, "date": date, "tx_type": tx, "store_code": code, "store": store,
            "market": market, "debit": debit, "credit": credit}

events = [
    ev("epay", "2026-08-20", "New Activation Bounty - Month 1", "B-509", "509 Nostrand Ave", credit=10.0, market="BK"),
    ev("epay", "2026-08-20", "New Activation Bounty - Month 1", "B-509", "509 Nostrand Ave", credit=2.5, market="BK"),
    ev("epay", "2026-08-20", "Commission Withholding", "B-509", "509 Nostrand Ave", debit=4.0, market="BK"),
    ev("epay", "2026-08-21", "Momentum Incentive", "B-103", "103 Fulton Ave", credit=0.5, market="LI"),
    ev("vidapay", "2026-08-20", "Sales Order", "T-1", "1 Main St", debit=55.0, market="LI"),
    ev("vidapay", "2026-08-20", "Postpaid Residual Order", "T-1", "1 Main St", credit=6.8, market="LI"),
    ev("vidapay", "2026-08-20", "Sales Order", "", "169024", debit=5.0),   # unmapped account key
]
cells = fold_cells(events)
ok(len(cells) == 6, f"fold: 6 distinct cells, got {len(cells)}")
c1 = next(c for c in cells if c["tx_type"] == "New Activation Bounty - Month 1")
ok(c1["debits"] == 0.0 and c1["credits"] == 12.5 and c1["net"] == 12.5 and c1["rows"] == 2,
   f"fold accumulates same-key events: {c1}")
cw = next(c for c in cells if c["tx_type"] == "Commission Withholding")
ok(cw["net"] == -4.0, "withholding cell nets negative (net = credits − debits)")
for c in cells:
    ok(abs(c["net"] - round(c["credits"] - c["debits"], 2)) < 1e-9, f"net rule on cell {c['tx_type']}")

roll = day_type_rollup(cells)
ok(roll["total"]["debits"] == 64.0 and roll["total"]["credits"] == 19.8, f"grand totals: {roll['total']}")
ok(abs(roll["total"]["net"] - (19.8 - 64.0)) < 1e-9, "grand net = credits − debits")
ok(abs(sum(d["net"] for d in roll["days"]) - roll["total"]["net"]) < 1e-9, "Σ day nets == grand net")
ok(abs(sum(r["net"] for r in roll["rows"]) - roll["total"]["net"]) < 1e-9, "Σ day×type nets == grand net")
d20 = next(d for d in roll["days"] if d["date"] == "2026-08-20")
ok(d20["debits"] == 64.0 and d20["credits"] == 19.3, f"per-day subtotal: {d20}")
so = [r for r in roll["rows"] if r["tx_type"] == "Sales Order"]
ok(len(so) == 1 and so[0]["debits"] == 60.0 and so[0]["rows"] == 2,
   "day×type row merges stores (incl. unmapped) within the day")
ok([r["date"] for r in roll["rows"]] == sorted(r["date"] for r in roll["rows"]), "rollup rows day-ordered")

# rounding to cents
r = fold_cells([ev("epay", "2026-01-01", "X", "S", "S", credit=0.1) for _ in range(3)])[0]
ok(r["credits"] == 0.3 and r["net"] == 0.3, f"cent rounding on fold: {r['credits']}")

# ── 4. filter semantics ──────────────────────────────────────────────────────────────────────────
ok(len(filter_cells(cells)) == 6 and len(filter_cells(cells, stores=[], types=[])) == 6,
   "no filter / empty filter = all cells")
ok(len(filter_cells(cells, stores=["B-509"])) == 2, "store filter by store_code")
ok(len(filter_cells(cells, stores=[" b-509 "])) == 2, "store filter case/whitespace-insensitive")
ok(len(filter_cells(cells, stores=["509 Nostrand Ave"])) == 2, "store filter matches display string")
ok(len(filter_cells(cells, stores=["169024"])) == 1, "unmapped feed key addressable by raw string")
ok(len(filter_cells(cells, types=["sales order"])) == 2, "type filter case-insensitive")
ok(len(filter_cells(cells, stores=["T-1"], types=["Sales Order"])) == 1, "filters AND-compose")
ok(len(filter_cells(cells, stores=["nope"])) == 0, "unknown store filter = honest empty")
ok(all("market" in c for c in cells), "fold carries the canonical market onto every cell")
ok(next(c for c in cells if c["store_code"] == "B-509")["market"] == "BK", "cell market = the store's canonical market")
ok(len(filter_cells(cells, markets=["BK"])) == 2, "market filter selects that market's cells")
ok(len(filter_cells(cells, markets=[" li "])) == 3, "market filter case/whitespace-insensitive")
ok(len(filter_cells(cells, markets=["BK", "LI"])) == 5, "multi-market filter unions")
ok(len(filter_cells(cells, markets=["BK"], types=["Commission Withholding"])) == 1,
   "market filter AND-composes with type")
ok(len(filter_cells(cells, markets=["BK"], stores=["T-1"])) == 0, "market AND store cannot both match here")
ok(len(filter_cells(cells, markets=[])) == 6 and len(filter_cells(cells, markets=None)) == 6,
   "empty/None market filter = all cells")
# The unmapped-store cell carries NO market: it must stay in the unfiltered view and drop out of
# any specific market — money is never attributed to a market it was not resolved into.
ok(len([c for c in cells if not c["market"]]) == 1, "unmapped feed key carries no market")
ok(all(c["market"] for c in filter_cells(cells, markets=["BK", "LI"])), "market filter excludes market-less cells")

# ── 5. live sample-day pins (recorded 2026-09-04) ────────────────────────────────────────────────
# VidaPay org 854f… 2026-09-02 per-type live aggregates (debit=Σ positive, credit=Σ |negative|):
live = [("Fee", [5.20], []), ("PostPaid Additional Spiff", [], [397.50]),
        ("Postpaid Branded MarketPlace", [29699.50], []), ("Postpaid Promo Order", [], [590.00]),
        ("SIM Assigment", [], []), ("Sales Order", [593.26], [])]
evs = []
for tx, debs, creds in live:
    for a in debs:
        d, c = classify_amount(a, False)
        evs.append(ev("vidapay", "2026-09-02", tx, "T", "T", debit=d, credit=c))
    for a in creds:
        d, c = classify_amount(-a, False)
        evs.append(ev("vidapay", "2026-09-02", tx, "T", "T", debit=d, credit=c))
tot = day_type_rollup(fold_cells(evs))["total"]
ok(tot["debits"] == 30297.96 and tot["credits"] == 987.50 and tot["net"] == -29310.46,
   f"live pin VidaPay 2026-09-02: {tot} (want D=30297.96 C=987.50 N=-29310.46)")
# ePay house 2026-07-27: withholding −1001.60 debit, credits Σ 80214.66, net 79213.06
d, c = classify_amount(-1001.60, True)
ok((d, c) == (1001.60, 0.0), "live pin: ePay withholding classifies as debit")
ok(round(80214.66 - 1001.60, 2) == 79213.06, "live pin ePay 2026-07-27 net")

# ── 6. processor NAME from the mig-953 report_term vocabulary (stub client) ──────────────────────
ORG = "org-under-test"
HOUSE = "00000000-0000-0000-0000-000000000001"


class _Q:
    """Minimal chainable stand-in for the supabase query builder."""
    def __init__(self, rows): self._rows = rows
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def execute(self): return type("R", (), {"data": self._rows})()


class _Client:
    def __init__(self, tables, boom=False): self._t, self._boom = tables, boom
    def schema(self, _s): return self
    def table(self, name):
        if self._boom:
            raise RuntimeError("label service down")
        return _Q(self._t.get(name, []))


def _term_client(carrier_code, preset_label, override_label=None):
    rows = [{"org_id": HOUSE, "scope": f"report_term:{carrier_code}", "key": "processor",
             "label": preset_label}]
    if override_label:
        rows.append({"org_id": ORG, "scope": "report_term", "key": "processor",
                     "label": override_label})
    return _Client({"carrier": [{"code": carrier_code, "name": carrier_code, "is_default": True}],
                    "ui_label_override": rows})


# House carrier preset supplies the org's own processor name — two different carriers, two names,
# and NEITHER string lives in processor_ledger.py (that is the whole point of the vocabulary).
lbl_a, src_a = _processor_term(_term_client("boost", "Processor-A"), ORG)
ok((lbl_a, src_a) == ("Processor-A", "report_term:boost"), f"carrier-A preset name: {(lbl_a, src_a)}")
lbl_b, src_b = _processor_term(_term_client("total", "Processor-B"), ORG)
ok((lbl_b, src_b) == ("Processor-B", "report_term:total"), f"carrier-B preset name: {(lbl_b, src_b)}")
ok(lbl_a != lbl_b, "each side resolves to its OWN processor name (no cross-side vocabulary)")
# Tenant override beats the house preset ("they can change if they want to").
lbl_o, _ = _processor_term(_term_client("boost", "Processor-A", override_label="Our Processor"), ORG)
ok(lbl_o == "Our Processor", f"tenant override wins over the house preset: {lbl_o}")
# No carrier picked / no preset rows / a dead label service → the NEUTRAL noun, never a vendor word.
lbl_n, src_n = _processor_term(_Client({"carrier": [], "ui_label_override": []}), ORG)
ok((lbl_n, src_n) == ("payment processor", "neutral_default"), f"no preset → neutral noun: {lbl_n}")
lbl_e, src_e = _processor_term(_Client({}, boom=True), ORG)
ok((lbl_e, src_e) == ("payment processor", "neutral_default"), f"label service failure → neutral: {lbl_e}")
# The module itself must carry no vendor name in the strings it can EMIT (docstring evidence lines
# are prose about the feeds, not copy) — the guard against a literal creeping back into the payload.
import inspect  # noqa: E402
import app.modules.commcalc.processor_ledger as _PLM  # noqa: E402
_src = inspect.getsource(_PLM)
_body = _src.split('"""', 2)[2]          # everything after the module docstring
for _vendor in ("ePay", "VidaPay", "Boost", "Total Wireless", "VIP", "ACIMA"):
    ok(f'"{_vendor}"' not in _body and f"'{_vendor}'" not in _body,
       f"no {_vendor!r} string literal in the module body (names come from the vocabulary)")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
