"""HARNESS — Residual per Subscriber: carrier source resolution + store-name attribution.

Owner report 2026-09-04: "residual per subscriber is not giving any information on the luxelink
side, it is also not showing the store name just the store codes, need to get accurate reporting and
use the index to update store names."

Proves, with NO DB and stdlib only (a tiny in-memory fake stands in for the PostgREST client):

  A. COVERAGE COUNTER CANNOT KILL THE FIGURES (the LuxeLink root cause). `_aggregate_ma`'s
     per-period coverage counter used to be seeded with a FIXED key set and then incremented with
     `c[key] += 1`, so the first `residual_rows` tick raised KeyError inside the residual sweep's
     blanket `except Exception: pass` — the whole Total-side residual aggregation aborted after ONE
     row and the report showed airtime margin alone as "residual". Measured live on luxelink
     2026-09-04: 18,070 residual rows / $73,846.71 reported as $0 (Aug 2026 read $19,488.16 =
     $19,481.36 merchant discount + the single $6.80 row that got in before the raise).
     Here: every residual row is counted, and a NEW coverage key can be added without raising.
  B. THE RESIDUAL FILTER IS THE BOOKING PREDICATE, NOT A SIBLING. The rows the report sums are
     exactly the rows `ma_residual_row_matcher` (mig 309/314, per-org
     `pl_ma_residual_order_types`) books to the P&L's `mi_income`: the UNION of the product_name
     family and the configured order-type family. The old server-side `.ilike('%residual%')` was
     half that union and dropped order-type-only rows (5 live luxelink rows the books DO book).
     Pinned as an EQUALITY against `ma_tx_pnl_bookings`, so report and P&L cannot drift again.
  C. CONFIG, NEVER CODE. Widening `residual_order_types` widens the report with no code change; an
     explicit empty list narrows it back to the label family. No carrier/tenant branch anywhere.
  D. WINDOW COMES FROM THE TENANT'S OWN RESIDUAL FEED. `_latest_ma_period` takes the LATER of
     raw_ma_daily_tx / raw_ma_commission — a tenant whose residual feed runs ahead of (or exists
     without) the commission sheet no longer has its months filtered away.
  E. STORE-NAME TRUTH TABLE. `resolve_ma_account_store` renders the mig-314 canonical store NAME
     plus the org's own store_code/market — never the processor account id, never the master-agent
     entity name. Truth table incl. the UNASSIGNED case: an account the index cannot place renders
     "(Unassigned)" honestly (never dropped, never guessed onto a plausible store), and a store the
     index places but the store vocabulary doesn't know keeps its money visible under a blank code.
  F. ONE STORE, ONE ROW. Residual (keyed by raw_ma_daily_tx.account_id) and subscribers (keyed by
     raw_ma_commission.merchant_account_id) resolve through the SAME index, so a store's dollars
     and its subscriber count land on ONE row — the split that showed stores with money and no
     subscribers next to stores with subscribers and no money is gone.
  G. NO VENDOR NAME IN COPY (RULE TWO). The provenance line is built from the org's mig-953
     `report_term` vocabulary (`report_labels.term_from_payload`); a carrier with no preset reads
     the NEUTRAL noun, never another carrier's vendor word.

Run:  cd backend && python3 harness_residual_per_sub.py
"""
import sys

sys.path.insert(0, ".")

from app.modules.account import residual_subs as rs                       # noqa: E402
from app.modules.commcalc import report_labels as rl                      # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


# ── a stdlib fake of the PostgREST client surface residual_subs uses ─────────────────────────────
class _Q:
    def __init__(self, rows):
        self._rows, self._lo, self._hi = list(rows), 0, None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._rows = [r for r in self._rows if str(r.get(col, "")) == str(val)]
        return self

    def in_(self, col, vals):
        vs = {str(v) for v in vals}
        self._rows = [r for r in self._rows if str(r.get(col, "")) in vs]
        return self

    def ilike(self, col, pat):                       # the OLD half-union filter; kept so the
        needle = pat.strip("%").lower()              # harness can reproduce the pre-fix behaviour
        self._rows = [r for r in self._rows if needle in str(r.get(col) or "").lower()]
        return self

    def order(self, col, desc=False):
        self._rows = sorted(self._rows, key=lambda r: str(r.get(col) or ""), reverse=desc)
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def range(self, lo, hi):
        self._lo, self._hi = lo, hi
        return self

    def execute(self):
        rows = self._rows if self._hi is None else self._rows[self._lo:self._hi + 1]
        return type("R", (), {"data": rows})()


class FakeClient:
    def __init__(self, tables):
        self._t = tables

    def schema(self, _s):
        return self

    def table(self, name):
        return _Q(self._t.get(name, []))

    def rpc(self, *a, **k):                          # no Boost RPC in these fixtures
        return type("R", (), {"execute": lambda self=None: type("R", (), {"data": []})()})()


def _tx(i, period, acct, entity, product, order_type, retail, disc):
    return {"id": f"{i:06d}", "period": period, "account_id": acct, "account_name": entity,
            "product_name": product, "order_type": order_type,
            "retail_cost": retail, "merchant_discount": disc}


# LuxeLink-shaped fixture: both residual spellings, an order-type-only residual row, a plain
# airtime row, two processor accounts, two master-agent entities.
TX = [
    _tx(1, "August 2026", "170084", "Luxelink Wireless LLC", "Residual", "Postpaid Residual Order", -10.00, 1.00),
    _tx(2, "August 2026", "170084", "Luxelink Wireless LLC", "Trac Autopay Residual", "Postpaid Residual Order", -5.00, 0.50),
    # residual-ness carried by ORDER TYPE alone — the row the old '%residual%' filter dropped
    _tx(3, "August 2026", "170086", "Luxelink Wireless LLC", "Autopay Credit", "Postpaid Residual Order", -3.00, 0.25),
    _tx(4, "August 2026", "168876", "Novawave Communications INC", "Airtime Top-Up", "Refill Order", 0.0, 2.00),
    _tx(5, "July 2026", "170084", "Luxelink Wireless LLC", "Residual", "Postpaid Residual Order", -4.00, 0.10),
]
COMM = [{"id": "c1", "period": "August 2026", "merchant_account_id": "170084", "period_year": 2026, "period_month": 8},
        {"id": "c2", "period": "August 2026", "merchant_account_id": "170084", "period_year": 2026, "period_month": 8},
        {"id": "c3", "period": "August 2026", "merchant_account_id": "170086", "period_year": 2026, "period_month": 8}]
for r in TX:
    mo = {"July 2026": 7, "August 2026": 8}[r["period"]]
    r["period_year"], r["period_month"] = 2026, mo

CFG_ROW = [{"org_id": "LUX", "pl_merchant_discount_own_line": True,
            "pl_ma_residual_order_types": ["Postpaid Residual Order"]}]
for t in TX + COMM:
    t["org_id"] = "LUX"


def _client(tx=None, comm=None, cfg=None):
    return FakeClient({"raw_ma_daily_tx": TX if tx is None else tx,
                       "raw_ma_commission": COMM if comm is None else comm,
                       "commission_org_config": CFG_ROW if cfg is None else cfg,
                       "raw_mi": []})


# ── A. the coverage counter never kills the figures ──────────────────────────────────────────────
print("A. coverage counter cannot abort the residual sweep")
meta = {}
agg = rs._aggregate_ma(_client(), "LUX", 2, meta=meta)
cov = meta["ma_coverage"]
check("every residual row is counted (3 in Aug, 1 in Jul)",
      cov["August 2026"]["residual_rows"] == 3 and cov["July 2026"]["residual_rows"] == 1, cov)
check("airtime-only rows counted as daily_tx, not residual",
      cov["August 2026"]["daily_tx_rows"] == 4, cov)
check("entities named per period (the coverage note's input) — RESIDUAL coverage only, so an "
      "entity that only filed airtime rows is not claimed as residual coverage",
      cov["August 2026"]["entities"] == ["Luxelink Wireless LLC"], cov)
by = {(a["period"], a["store_label"]): a for a in agg}
check("residual $ booked, sign-flipped (Aug 170084 = 10.00 + 5.00)",
      round(by[("August 2026", "170084")]["sum_mi"], 2) == 15.00, agg)
check("order-type-only residual row booked (Aug 170086 = 3.00)",
      round(by[("August 2026", "170086")]["sum_mi"], 2) == 3.00, agg)
check("airtime margin still summed on every row (Aug 168876 = 2.00)",
      round(by[("August 2026", "168876")]["sum_atu"], 2) == 2.00
      and round(by[("August 2026", "168876")]["sum_mi"], 2) == 0.0, agg)

# a NEW coverage key must not raise — the exact shape of the original defect
_probe = {}
_agg2 = rs._aggregate_ma(_client(), "LUX", 2, meta=_probe)
check("coverage dict tolerates keys it was not seeded with",
      all("residual_rows" in c for c in _probe["ma_coverage"].values()))

# ── B. the report's residual basis IS the P&L's mig-309 booking ──────────────────────────────────
print("B. report residual == P&L mi_income basis (mig 309/314), row for row")
cfg = rs.load_ma_pnl_config(_client(), "LUX")
pnl = {}
for line, amt in rs.ma_tx_pnl_bookings([r for r in TX if r["period"] == "August 2026"], cfg):
    pnl[line] = round(pnl.get(line, 0.0) + amt, 2)
report_res = round(sum(a["sum_mi"] for a in agg if a["period"] == "August 2026"), 2)
report_atu = round(sum(a["sum_atu"] for a in agg if a["period"] == "August 2026"), 2)
check("Σ report residual == Σ P&L mi_income", report_res == pnl["mi_income"] == 18.00,
      (report_res, pnl))
check("Σ report airtime == Σ P&L merchant discount",
      report_atu == pnl["ma_merchant_discount"] == 3.75, (report_atu, pnl))

print("B2. the OLD '%residual%' filter is strictly narrower (the dropped rows)")
old_rows = [r for r in TX if "residual" in str(r["product_name"]).lower()]
new_rows = [r for r in TX if rs.ma_residual_row_matcher(cfg)(r["product_name"], r["order_type"])]
check("union picks up the order-type-only row the ilike missed",
      len(new_rows) - len(old_rows) == 1 and TX[2] in new_rows and TX[2] not in old_rows)

# ── C. config, never code ────────────────────────────────────────────────────────────────────────
print("C. config, never code")
wide = rs.ma_residual_row_matcher({"residual_order_types": ["Postpaid Residual Order", "Refill Order"]})
check("widening the configured order types widens the report, no code change",
      wide("Airtime Top-Up", "Refill Order") is True)
narrow = rs.ma_residual_row_matcher({"residual_order_types": []})
check("explicit [] narrows back to the label family only",
      narrow("Trac Autopay Residual", "Postpaid Residual Order") is True
      and narrow("Autopay Credit", "Postpaid Residual Order") is False)
check("unresolved config falls back to the mig-309 default order type",
      rs.ma_residual_row_matcher(None)("Autopay Credit", "Postpaid Residual Order") is True)
check("no tenant/carrier literal decides residual-ness",
      rs.ma_residual_row_matcher(cfg)("Luxelink Wireless LLC", "Refill Order") is False)

# ── D. the window comes from the tenant's own residual feed ──────────────────────────────────────
print("D. period window = the LATER of the two MA feeds")
tx_ahead = [dict(r, period="September 2026", period_year=2026, period_month=9) for r in TX[:1]] + TX
check("daily-tx month ahead of the commission sheet wins",
      rs._latest_ma_period(_client(tx=tx_ahead), "LUX") == (2026, 9))
check("commission sheet wins when IT is ahead",
      rs._latest_ma_period(_client(comm=[dict(COMM[0], period_month=12)]), "LUX") == (2026, 12))
check("residual feed alone (no commission sheet at all) still sets the window",
      rs._latest_ma_period(_client(comm=[]), "LUX") == (2026, 8))

# ── E. store-name truth table ────────────────────────────────────────────────────────────────────
print("E. store-name truth table (mig-314 index + the org's own store vocabulary)")
IDX = {"170084": "4640-A W Diversey Ave", "170086": "5601 W Belmont Ave",
       "168876": "218-80 Hempstead Avenue", "170999": "77 Nowhere Rd"}
VOCAB = {"4640-a w diversey ave": {"store_code": "Diversey", "market": "Chicago"},
         "5601 w belmont ave": {"store_code": "Belmont", "market": "Chicago"},
         "218-80 hempstead avenue": {"store_code": "QV", "market": "NY"}}
TRUTH = [
    # (account,   store,                     code,        market,         resolved)
    ("170084", "4640-A W Diversey Ave", "Diversey", "Chicago", True),
    ("168876", "218-80 Hempstead Avenue", "QV", "NY", True),
    # placed by the index, unknown to the store vocabulary → money stays visible, code blank
    ("170999", "77 Nowhere Rd", "", "(Unassigned)", True),
    # not in the index at all → HONEST "(Unassigned)", never the account id, never a guess
    ("170405", "(Unassigned)", "", "(Unassigned)", False),
    ("", "(Unassigned)", "", "(Unassigned)", False),
    (None, "(Unassigned)", "", "(Unassigned)", False),
]
for acct, store, code, market, resolved in TRUTH:
    got = rs.resolve_ma_account_store(acct, IDX, VOCAB)
    check(f"account {acct!r} -> {store!r}",
          got["store"] == store and got["store_code"] == code
          and got["market"] == market and got["resolved"] is resolved, got)
check("an unplaced account is NEVER rendered as its own id",
      rs.resolve_ma_account_store("170405", IDX, VOCAB)["store"] != "170405")
check("the master-agent ENTITY name is never a store label",
      all(r["store"] != "Luxelink Wireless LLC"
          for r in (rs.resolve_ma_account_store(a, IDX, VOCAB) for a, *_ in TRUTH)))
check("no store row is dropped — every account still produces a row",
      all(rs.resolve_ma_account_store(a, IDX, VOCAB)["store"] for a, *_ in TRUTH))

# ── F. one store, one row (dollars and subscribers reunited) ─────────────────────────────────────
print("F. residual (daily-tx account) and subscribers (commission account) land on ONE store")
rows = {}
for a in agg:
    r = rs.resolve_ma_account_store(a["store_label"], IDX, VOCAB)
    d = rows.setdefault(r["store"], {"mi": 0.0, "subs": 0})
    d["mi"] += a["sum_mi"]
    d["subs"] += a["subs"]
check("170084's $15.00 (Aug) and its 2 subscribers share the Diversey row",
      round(rows["4640-A W Diversey Ave"]["mi"], 2) == 19.00
      and rows["4640-A W Diversey Ave"]["subs"] == 2, rows)
check("170086's order-type residual and its 1 subscriber share the Belmont row",
      round(rows["5601 W Belmont Ave"]["mi"], 2) == 3.00
      and rows["5601 W Belmont Ave"]["subs"] == 1, rows)
check("no row is keyed by a bare processor account id",
      not any(k.isdigit() for k in rows))

# ── G. no vendor name in copy ────────────────────────────────────────────────────────────────────
print("G. provenance copy comes from the mig-953 report_term vocabulary")
total_payload = rl.build_payload(
    rl.parse_label_rows(
        [{"org_id": rl.HOUSE_ORG, "scope": "report_term:total", "key": "processor", "label": "VidaPay"},
         {"org_id": rl.HOUSE_ORG, "scope": "report_term:total", "key": "distributor", "label": "VidaPay / T-CETRA"}],
        "LUX", ["total"]), ["total"], "total")
boost_payload = rl.build_payload(
    rl.parse_label_rows(
        [{"org_id": rl.HOUSE_ORG, "scope": "report_term:boost", "key": "processor", "label": "ePay"}],
        "BST", ["boost"]), ["boost"], "boost")
t_terms = {k: rl.term_from_payload(total_payload, k)[0] for k in ("processor", "distributor")}
b_terms = {k: rl.term_from_payload(boost_payload, k)[0] for k in ("processor", "distributor")}
tl = rs._source_label("vidapay_ma", t_terms)
bl = rs._source_label("boost_mi_atu", b_terms)
check("Total-side label names the tenant's own distributor", "VidaPay / T-CETRA" in tl, tl)
check("Total-side label carries NO Boost vocabulary",
      "boost" not in tl.lower() and "epay" not in tl.lower(), tl)
check("Boost-side label names the tenant's own processor", bl.startswith("ePay"), bl)
check("Boost-side label carries NO Total vocabulary",
      "vidapay" not in bl.lower() and "t-cetra" not in bl.lower(), bl)
none_payload = rl.build_payload(rl.parse_label_rows([], "NEW", []), [], "")
n_terms = {k: rl.term_from_payload(none_payload, k)[0] for k in ("processor", "distributor")}
nl = rs._source_label("vidapay_ma", n_terms)
check("a preset-less carrier reads the NEUTRAL noun, not another carrier's word",
      nl.startswith("distributor") and "vidapay" not in nl.lower() and "epay" not in nl.lower(), nl)
check("tenant override beats the house carrier preset",
      rl.term_from_payload(
          rl.build_payload(
              rl.parse_label_rows(
                  [{"org_id": rl.HOUSE_ORG, "scope": "report_term:total", "key": "processor", "label": "VidaPay"},
                   {"org_id": "LUX", "scope": "report_term", "key": "processor", "label": "Our Processor"}],
                  "LUX", ["total"]), ["total"], "total"), "processor")[0] == "Our Processor")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
