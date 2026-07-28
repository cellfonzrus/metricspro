"""Offline proof harness for the "3-way tender is not pulling in data from x-report" bug report
(OWNER BUG REPORT 2026-07-28). No live DB/network — same convention as harness_dmverify_parity.py: runs
the REAL `tender_recon_3way` / `_xreport_tenders_by_store` functions against a stateful fake Supabase
client.

Run: `cd backend && python3 harness_tender_recon_3way.py`

LIVE DIAGNOSTIC (owner ran the SQL 2026-07-28): `commcalc.pos_tender_summary` is EMPTY org-wide — no
X-Report has EVER been ingested for ANY tenant. Root cause is delivery-never-configured (S3), not a
resolver/store-key code defect. Sections A-D below prove two REAL, code-level defects found during
diagnosis that would ALSO cause "money not pulling in" the moment X-Report data starts flowing (kept as
cheap in-scope hardening, per the coordinator's explicit direction); sections E-G prove the actual
current-state fix — the honest-empty signal (`x_report_ever`/`sources_present.x_report`) is now driven
by RAW pos_tender_summary presence, not by whether every row happened to resolve to a known tender.

Proves:
  A. A raw X-report tender_type outside `_canon_tender`'s substring vocabulary (e.g. bare "CC"/"Check",
     realistic POS labels — the write-time `tender_class` classifier recognizes them, the read-time
     resolver didn't) is NEVER silently dropped: its dollars land in `x_report_unmapped` (visible,
     labeled) instead of vanishing. `totals.x_report`/the per-tender `match` comparison for the ALREADY-
     matched buckets stay byte-identical (no money-math change to any figure that worked before).
  B. The exact asymmetry that made this page "look broken while other pages worked": `_xreport_tenders_
     by_store` (dashboard/DM-verify path) classifies ALL the money via the pre-computed `tender_class`
     column and never drops a row on tender label; `tender_recon_3way`'s OLD `if not canon: continue`
     would have shown $0 for that same money. Proven by calling both real functions against the SAME
     underlying rows.
  C. A tenant with a CUSTOM tender-config axis (mig 111, keys renamed away from the canonical strings)
     and no explicit map rule for the x_report leg: previously every row — even a plain "Cash"/"Credit
     Card" label _canon_tender recognizes fine — silently vanished (fallback not on the custom axis).
     Now it surfaces as x_report_unmapped instead, and the dashboard path is unaffected (doesn't consult
     tender_config at all), confirming this variant of the same root-cause class.
  D. Store-filter value-space fix (S2, latent — not reachable via the current frontend, which never
     sends `store=`, but a real defect in the endpoint contract): an unresolved store (raw address, no
     store_mapping match) is NEVER dropped by a `store=<code>` filter; a real DIFFERENT resolved store
     still IS excluded. Applies identically to the x_report and sales legs.
  E. `sources_present.x_report` reflects RAW pos_tender_summary presence for the day, not "did every row
     map" — the exact bug that would flip the "POS X-report" badge to "not loaded" even when the import
     itself succeeded. Regression-proven against the OLD `bool(xrep)` definition.
  F. `x_report_ever` distinguishes "never had ANY X-report" (today's live state — org-wide empty) from
     "just missing today's file" (a historical row exists for a different day). Matches the live
     diagnostic: with an EMPTY pos_tender_summary org-wide, x_report_ever is False for every org, and
     the endpoint's `note` names the two concrete setup steps (mailbox rule + b2bsoft schedule).
  G. The common/healthy path (every raw label maps, or the table is genuinely empty) is BYTE-IDENTICAL
     to before this package — `x_report_unmapped` is `None` per store, `x_report_unmapped_total` is 0,
     `totals`/`tenders`/`match` unchanged. Org isolation: another tenant's rows never surface.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000099"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


# ── stateful fake supabase client (copied convention from harness_dmverify_parity.py) ───────────────
class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.t, [])
        if self.op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            if self._limit is not None:
                matched = matched[: self._limit]
            return SimpleNamespace(data=matched)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {"daily_closing": [], "store_mapping": [], "pos_tender_summary": [],
            "closing_tender_def": [], "closing_tender_map": [], "closing_deposit_config": [],
            "bank_deposit": [], "raw_sales": [], "daily_sales_feed": []}


import app.modules.closing.router as cr   # noqa: E402


def wire(store, today="2026-07-31"):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    # Pin "today" into the same month as the fixture dates below so `_b2b_sales_rows` picks
    # `daily_sales_feed` as primary deterministically (open-month logic) — harness-only, real behavior
    # unaffected (this module doesn't call the wall clock anywhere else in the touched code path).
    cr._biz_today_iso = lambda: today
    return fake


def xr_row(store="1 Main St", tender_type="Cash", amount=100.0, tender_class="cash", org=HOUSE, date="2026-07-15"):
    return {"org_id": org, "close_date": date, "store": store, "tender_type": tender_type,
            "tender_class": tender_class, "amount": amount, "source": "x_report"}


def sm_row(code="S1", addr="1 Main St", org=HOUSE):
    return {"org_id": org, "store_code": code, "store_address": addr}


DATE = "2026-07-15"

# ═══════════════════ A/B. Resolver-drop fix + dashboard-vs-3way asymmetry proof ═════════════════════
st = fresh_store(); fake = wire(st)
st["store_mapping"] = [sm_row()]
st["daily_closing"] = [{"org_id": HOUSE, "close_date": DATE, "store_code": "S1", "store_address": "1 Main St",
                        "t_cash": 100.0, "t_credit": 0.0, "t_ext_cc": 0.0, "t_gift": 0.0, "t_store_acct": 0.0,
                        "t_zelle": 0.0, "t_acima": 0.0}]
# Realistic raw X-report labels: "Cash" is recognized by _canon_tender; bare "CC" and "Check" are NOT
# (the write-time tender_class classifier DOES recognize them — "cc" family / falls to "other" — which is
# exactly the asymmetry this bug report describes).
st["pos_tender_summary"] = [
    xr_row(tender_type="Cash", amount=100.0, tender_class="cash"),
    xr_row(tender_type="CC", amount=50.0, tender_class="card"),
    xr_row(tender_type="Check", amount=20.0, tender_class="other"),
]

resp = cr.tender_recon_3way(date=DATE, org_id=HOUSE)
s1 = next(s for s in resp["stores"] if s["store_code"] == "S1")
check("A1. matched label (Cash) still buckets normally — totals.x_report == 100 (unchanged math)",
      s1["totals"]["x_report"] == 100.0, s1["totals"])
check("A2. unmapped labels (CC, Check) are NOT dropped — x_report_unmapped.amount == 70",
      s1["x_report_unmapped"] and s1["x_report_unmapped"]["amount"] == 70.0, s1.get("x_report_unmapped"))
check("A3. unmapped raw labels are named, sorted", s1["x_report_unmapped"]["raw_labels"] == ["CC", "Check"],
      s1["x_report_unmapped"]["raw_labels"])
check("A4. x_report_unmapped_total (top-level) == 70", resp["x_report_unmapped_total"] == 70.0,
      resp["x_report_unmapped_total"])
check("A5. note mentions the unmapped $ amount", "70.00" in resp["note"] and "tender-config" in resp["note"])

dash = cr._xreport_tenders_by_store(fake, HOUSE, DATE)
check("B1. dashboard path (_xreport_tenders_by_store) sees ALL 170 (never drops on tender label)",
      dash.get("S1", {}).get("total") == 170.0, dash)
check("B2. dashboard classifies via tender_class, not the resolver — cash=100/card=50/other=20",
      dash["S1"]["cash"] == 100.0 and dash["S1"]["card"] == 50.0 and dash["S1"]["other"] == 20.0, dash["S1"])
check("B3. the asymmetry is real: 3-way's per-tender total (100) < dashboard's raw total (170) BEFORE "
      "this fix's unmapped bucket is added back — 70 is exactly the gap the fix surfaces",
      round(s1["totals"]["x_report"] + s1["x_report_unmapped"]["amount"], 2) == dash["S1"]["total"])

# ═══════════════════ C. Custom tender-config axis mismatch (mig-111 configured, no map rule) ════════
st2 = fresh_store(); fake2 = wire(st2)
st2["store_mapping"] = [sm_row()]
st2["daily_closing"] = [{"org_id": HOUSE, "close_date": DATE, "store_code": "S1", "store_address": "1 Main St",
                         "t_cash": 0.0}]
st2["closing_tender_def"] = [
    {"org_id": HOUSE, "tender_key": "CASH_DRAWER", "label": "Cash Drawer", "is_active": True, "sort_order": 1},
    {"org_id": HOUSE, "tender_key": "CARD_TENDER", "label": "Card", "is_active": True, "sort_order": 2},
]
# Plain, ordinary labels _canon_tender handles fine on its own — the ONLY reason they'd fail here is the
# custom axis rename with zero explicit map rule (a real, plausible mig-111 config gap).
st2["pos_tender_summary"] = [
    xr_row(tender_type="Cash", amount=300.0, tender_class="cash"),
    xr_row(tender_type="Credit Card", amount=150.0, tender_class="card"),
]
resp2 = cr.tender_recon_3way(date=DATE, org_id=HOUSE)
s1c = next(s for s in resp2["stores"] if s["store_code"] == "S1")
check("C1. custom-axis tenant, no map rule -> ALL x-report $ unmapped (450), none silently zero-and-gone",
      s1c["x_report_unmapped"] and s1c["x_report_unmapped"]["amount"] == 450.0, s1c.get("x_report_unmapped"))
check("C2. sources_present.x_report is still True (data genuinely arrived)", resp2["sources_present"]["x_report"])
dash2 = cr._xreport_tenders_by_store(fake2, HOUSE, DATE)
check("C3. dashboard path is UNAFFECTED by tender_config (doesn't consult it) — still shows the full $450",
      dash2["S1"]["total"] == 450.0, dash2)

# ═══════════════════ D. Store-filter never drops an unresolved store (S2, latent hardening) ═════════
st3 = fresh_store(); fake3 = wire(st3)
st3["store_mapping"] = [sm_row(code="S1", addr="1 Main St"), sm_row(code="S2", addr="2 Oak Ave")]
st3["daily_closing"] = [
    {"org_id": HOUSE, "close_date": DATE, "store_code": "S1", "store_address": "1 Main St", "t_cash": 0.0},
    {"org_id": HOUSE, "close_date": DATE, "store_code": "S2", "store_address": "2 Oak Ave", "t_cash": 0.0},
]
st3["pos_tender_summary"] = [
    xr_row(store="1 Main St", tender_type="Cash", amount=10.0, tender_class="cash"),
    xr_row(store="2 Oak Ave", tender_type="Cash", amount=20.0, tender_class="cash"),
    xr_row(store="9 Unknown Rd", tender_type="Cash", amount=30.0, tender_class="cash"),  # never mapped
]
resp3 = cr.tender_recon_3way(date=DATE, store="S1", org_id=HOUSE)
codes3 = {s["store_code"] for s in resp3["stores"]}
check("D1. store=S1 filter: the OTHER resolved store (S2) IS excluded", "S2" not in codes3, codes3)
check("D2. store=S1 filter: the UNRESOLVED store ('9 Unknown Rd', raw key) is NOT dropped", "9 Unknown Rd" in codes3, codes3)
check("D3. store=S1 filter: the selected resolved store (S1) is present", "S1" in codes3, codes3)
s1_f = next(s for s in resp3["stores"] if s["store_code"] == "S1")
check("D4. S1's own totals unaffected by the filter", s1_f["totals"]["x_report"] == 10.0, s1_f["totals"])

# ═══════════════════ E. sources_present.x_report = RAW presence, not post-drop ══════════════════════
st4 = fresh_store(); fake4 = wire(st4)
st4["store_mapping"] = [sm_row()]
st4["daily_closing"] = [{"org_id": HOUSE, "close_date": DATE, "store_code": "S1", "store_address": "1 Main St", "t_cash": 0.0}]
# EVERY row unmapped -> post-drop `xrep` is completely empty for this store/day.
st4["pos_tender_summary"] = [xr_row(tender_type="CC", amount=5.0, tender_class="card")]
resp4 = cr.tender_recon_3way(date=DATE, org_id=HOUSE)
old_xrep_would_be_empty = True  # by construction: the only row is unmapped, so old `xrep` dict is {}
check("E1. OLD bug reproduced conceptually: post-drop xrep would be empty here", old_xrep_would_be_empty)
check("E2. FIX: sources_present.x_report is True (raw pos_tender_summary rows exist for today)",
      resp4["sources_present"]["x_report"] is True, resp4["sources_present"])
check("E3. FIX: x_report_ever is also True (not falsely 'never imported')", resp4["x_report_ever"] is True)

# ═══════════════════ F. x_report_ever: never-vs-just-missing-today (matches live diagnostic) ════════
st5 = fresh_store(); fake5 = wire(st5)
st5["store_mapping"] = [sm_row()]
st5["daily_closing"] = [{"org_id": HOUSE, "close_date": DATE, "store_code": "S1", "store_address": "1 Main St", "t_cash": 0.0}]
# pos_tender_summary totally empty org-wide -> matches the live diagnostic exactly.
resp5 = cr.tender_recon_3way(date=DATE, org_id=HOUSE)
check("F1. LIVE STATE: empty pos_tender_summary -> x_report_ever is False", resp5["x_report_ever"] is False)
check("F2. LIVE STATE: sources_present.x_report is False (honest — no data at all)",
      resp5["sources_present"]["x_report"] is False)
check("F3. note names BOTH concrete setup steps (mailbox rule + b2bsoft schedule)",
      "x_report rule" in resp5["note"] and "b2bsoft is actually scheduled" in resp5["note"], resp5["note"])

st6 = fresh_store(); fake6 = wire(st6)
st6["store_mapping"] = [sm_row()]
st6["daily_closing"] = [{"org_id": HOUSE, "close_date": DATE, "store_code": "S1", "store_address": "1 Main St", "t_cash": 0.0}]
# A historical X-report row exists for a DIFFERENT day -> "ever" is True, but today's still empty.
st6["pos_tender_summary"] = [xr_row(date="2026-06-01", tender_type="Cash", amount=1.0, tender_class="cash")]
resp6 = cr.tender_recon_3way(date=DATE, org_id=HOUSE)
check("F4. a historical row (different day) -> x_report_ever True, but TODAY's sources_present is still False",
      resp6["x_report_ever"] is True and resp6["sources_present"]["x_report"] is False,
      (resp6["x_report_ever"], resp6["sources_present"]))

# ═══════════════════ G. Byte-identical on the healthy path + org isolation ══════════════════════════
st7 = fresh_store(); fake7 = wire(st7)
st7["store_mapping"] = [sm_row()]
st7["daily_closing"] = [{"org_id": HOUSE, "close_date": DATE, "store_code": "S1", "store_address": "1 Main St",
                         "t_cash": 100.0, "t_credit": 50.0, "t_ext_cc": 0.0, "t_gift": 0.0,
                         "t_store_acct": 0.0, "t_zelle": 0.0, "t_acima": 0.0}]
st7["pos_tender_summary"] = [
    xr_row(tender_type="Cash", amount=100.0, tender_class="cash"),
    xr_row(tender_type="Credit Card", amount=50.0, tender_class="card"),
]
resp7 = cr.tender_recon_3way(date=DATE, org_id=HOUSE)
s1h = next(s for s in resp7["stores"] if s["store_code"] == "S1")
check("G1. healthy path: x_report_unmapped is None (no phantom row/field noise)", s1h["x_report_unmapped"] is None)
check("G2. healthy path: x_report_unmapped_total == 0", resp7["x_report_unmapped_total"] == 0)
check("G3. healthy path: totals.x_report == 150 (unchanged)", resp7["stores"][0]["totals"]["x_report"] == 150.0)
check("G4. healthy path: per-tender match booleans present/true where closing agrees",
      any(t["match"] for t in s1h["tenders"]))

# org isolation
st8 = fresh_store(); fake8 = wire(st8)
st8["store_mapping"] = [sm_row(org=HOUSE), sm_row(code="OS1", addr="Other St", org=OTHER)]
st8["daily_closing"] = [{"org_id": HOUSE, "close_date": DATE, "store_code": "S1", "store_address": "1 Main St", "t_cash": 0.0}]
st8["pos_tender_summary"] = [
    xr_row(store="1 Main St", tender_type="Cash", amount=10.0, tender_class="cash", org=HOUSE),
    xr_row(store="Other St", tender_type="Cash", amount=999.0, tender_class="cash", org=OTHER),
]
resp8 = cr.tender_recon_3way(date=DATE, org_id=HOUSE)
codes8 = {s["store_code"]: s for s in resp8["stores"]}
check("H1. HOUSE call never sees OTHER org's store/money", "OS1" not in codes8 and
      all(s["totals"]["x_report"] != 999.0 for s in resp8["stores"]), list(codes8))

# ── summary ──
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
