"""Offline proof harness for retail-ops-26 (OWNER BUG REPORT 2026-08-03, PACKAGE C: "3 way recon for dm
shows all stores it should only show the stores selected and assigned to the dm"). The DM (span-restricted
manager) opened /closing/tender-recon-3way and saw every store in the company — that endpoint had ZERO
manager-span keyset enforcement at all (unlike closing_rollup, already fixed in retail-ops-24).

This harness proves the fix on the reported endpoint (/closing/tender-recon-3way, both single-day AND
date-range mode, plus its drill-down /closing/tender-drilldown) and the audit sweep of every other
closing-module report/read endpoint found to be missing the same keyset gate:
  /closing/submissions (Daily Closing dashboard's detail table — same "tiles != table" bug class as
    retail-ops-24, just a different pair of surfaces: /closing/rollup's tiles were already fixed while
    THIS table, sitting right below them on the same page, was not),
  /closing/epay-recon, /closing/accessory-recon, /closing/cash-position, /closing/pickups (explicitly
    named in the owner's audit list),
  /closing/duplicates, /closing/attempts (management review — already permission-gated to management,
    but an explicit per-role page grant can still admit a scoped, non-'all' role).

Same convention as harness_rollup_keyset_scope.py: runs the REAL router functions against a stateful fake
Supabase-chain client, monkeypatching `app.modules.storeops.router.scope_keyset` (every fix in this
package does a LOCAL `from app.modules.storeops.router import scope_keyset, in_keyset` re-executed on
every call, so patching the storeops module attribute is picked up live).

Run: `cd backend && python3 harness_closing_reports_span_scope.py`
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


# ── stateful fake supabase client (copied convention from harness_rollup_keyset_scope.py) ──────────
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
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r); r.setdefault("id", nid(self.t))
                rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {"daily_closing": [], "daily_closing_verification": [], "stores": [],
            "store_mapping": [], "pos_tender_summary": [], "raw_sales": [], "daily_sales_feed": [],
            "bank_deposit": [], "cash_pickup": [], "closing_attempt": [],
            "closing_tender_def": [], "closing_tender_map": [], "closing_deposit_config": []}


import app.modules.closing.router as cr             # noqa: E402
import app.modules.storeops.router as SO             # noqa: E402

AUTH_NONE = ""
AUTH_SCOPED = "Bearer dm-token"


def wire(store):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    SO.scope_keyset = lambda authorization="", org_id=HOUSE: None
    return fake


def scoped(codes):
    return lambda authorization="", org_id=HOUSE: (set(codes) if authorization == AUTH_SCOPED else None)


DATE = "2026-07-15"   # NOT "today" -> _b2b_sales_rows treats raw_sales as primary (simpler test data)


def sm_row(code, addr):
    return {"org_id": HOUSE, "store_code": code, "store_address": addr, "salesforce_id": f"sf-{code}"}


def dc_row(**kw):
    r = {"org_id": HOUSE, "close_date": DATE, "period": "2026-07",
         "store_code": "S1", "store_address": "1 Main St", "store_name": "1 Main St",
         "employee_name": "Jane Rep", "source": "manual",
         "t_cash": 100.0, "t_credit": 50.0, "t_ext_cc": 0.0, "t_gift": 0.0, "t_store_acct": 0.0,
         "t_zelle": 0.0, "t_acima": 0.0,
         "store_cash": 100.0, "store_cc": 50.0, "epay_cash": 0.0, "epay_cc": 0.0,
         "acc_sale": 25.0, "other_account": 0.0,
         "upgrade_count": 1, "new_line_count": 2, "postpaid_count": 0}
    r.update(kw)
    return r


def xrep_row(store_addr, amt, tender="Cash"):
    return {"org_id": HOUSE, "close_date": DATE, "store": store_addr, "tender_type": tender, "amount": amt}


def sales_row(store_addr, amt, tender="Cash"):
    return {"org_id": HOUSE, "trans_date": DATE, "period": "2026-07", "store": store_addr,
            "tender_type": tender, "ext_price": amt, "voided": "false", "trans_type": "Sale",
            "trans_id": nid("tx"), "salesperson": "Jane Rep", "product_desc": "Phone", "mdn": "5551234"}


def two_store_setup(st):
    st["store_mapping"] = [sm_row("S1", "1 Main St"), sm_row("S2", "2 Oak Ave")]
    st["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas",
                     "is_active": True},
                    {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Ohio",
                     "is_active": True}]
    st["daily_closing"] = [
        dc_row(id="s1a", store_code="S1", store_address="1 Main St", employee_name="Jane Rep",
               t_cash=100.0, t_credit=50.0),
        dc_row(id="s2a", store_code="S2", store_address="2 Oak Ave", employee_name="Mo Rep",
               t_cash=200.0, t_credit=75.0),
    ]
    st["pos_tender_summary"] = [xrep_row("1 Main St", 100.0), xrep_row("2 Oak Ave", 200.0)]
    st["raw_sales"] = [sales_row("1 Main St", 100.0), sales_row("2 Oak Ave", 200.0)]


# ══════════════════════ ① /closing/tender-recon-3way — THE REPORTED BUG ══════════════════════════
st = fresh_store(); wire(st); two_store_setup(st)

r_unscoped = cr.tender_recon_3way(date=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("1A. unscoped: both S1+S2 appear in the 3-way blocks",
      sorted(s["store_code"] for s in r_unscoped["stores"]) == ["S1", "S2"], str(r_unscoped["stores"]))

SO.scope_keyset = scoped({"S1", "1 MAIN ST"})
r_scoped = cr.tender_recon_3way(date=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("1B. THE BUG FIX: scoped DM (S1 only) sees ONLY S1's block, S2 gone entirely",
      [s["store_code"] for s in r_scoped["stores"]] == ["S1"], str(r_scoped["stores"]))
check("1C. scoped block's own per-tender totals are S1's real numbers (closing=150, x_report=100, sales=100)",
      r_scoped["stores"][0]["totals"]["closing"] == 150.0 and
      r_scoped["stores"][0]["totals"]["x_report"] == 100.0 and
      r_scoped["stores"][0]["totals"]["sales"] == 100.0,
      str(r_scoped["stores"][0]["totals"]))
check("1D. TOTALS == VISIBLE ROWS: a frontend summing `stores` gets S1-only money, not org-wide "
      "(150+... vs the org total that would include S2's 275)",
      sum(s["totals"]["closing"] for s in r_scoped["stores"]) == 150.0,
      str(sum(s["totals"]["closing"] for s in r_scoped["stores"])))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ── date_from/date_to RANGE mode gets the identical fix ──
SO.scope_keyset = scoped({"S1"})
r_range = cr.tender_recon_3way(date_from=DATE, date_to=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("1E. range mode: scoped DM sees only S1 in the day block",
      [s["store_code"] for s in r_range["days"][0]["stores"]] == ["S1"], str(r_range["days"]))
r_range_un = cr.tender_recon_3way(date_from=DATE, date_to=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("1F. range mode: unscoped caller still sees both stores (byte-identical to before the fix)",
      sorted(s["store_code"] for s in r_range_un["days"][0]["stores"]) == ["S1", "S2"])
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ── an explicit store= param for an OUT-OF-SPAN store never bypasses the keyset ──
SO.scope_keyset = scoped({"S1"})
r_bypass = cr.tender_recon_3way(date=DATE, store="S2", authorization=AUTH_SCOPED, org_id=HOUSE)
check("1G. explicit store=S2 (out-of-span) from a scoped DM returns NOTHING, not S2's data",
      r_bypass["stores"] == [], str(r_bypass["stores"]))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ── identity-less row (unresolved store string): excluded scoped, kept unscoped ──
st2 = fresh_store(); wire(st2); two_store_setup(st2)
st2["pos_tender_summary"].append(xrep_row("Some Unmapped Address", 999.0))
r_ghost_un = cr.tender_recon_3way(date=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("1H. unscoped: unresolved x-report row still counted (org total includes the ghost store)",
      any(s["store_code"] == "Some Unmapped Address" for s in r_ghost_un["stores"]))
SO.scope_keyset = scoped({"S1"})
r_ghost_sc = cr.tender_recon_3way(date=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("1I. scoped: unresolved row EXCLUDED (can't be proven inside the DM's span)",
      all(s["store_code"] != "Some Unmapped Address" for s in r_ghost_sc["stores"]) and
      [s["store_code"] for s in r_ghost_sc["stores"]] == ["S1"], str(r_ghost_sc["stores"]))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ── unmapped-tender totals also respect scope (x_report_unmapped_total) ──
st3 = fresh_store(); wire(st3); two_store_setup(st3)
st3["pos_tender_summary"].append(xrep_row("2 Oak Ave", 50.0, tender="SomeUnrecognizedLabel"))
SO.scope_keyset = scoped({"S1"})
r_unmapped = cr.tender_recon_3way(date=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("1J. an out-of-span store's unmapped-tender $ never inflates a scoped viewer's unmapped total",
      r_unmapped["x_report_unmapped_total"] == 0.0, str(r_unmapped["x_report_unmapped_total"]))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None
r_unmapped_un = cr.tender_recon_3way(date=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("1K. unscoped: the same unmapped $ IS counted (byte-identical to before)",
      r_unmapped_un["x_report_unmapped_total"] == 50.0, str(r_unmapped_un["x_report_unmapped_total"]))

# ══════════════════════ ② /closing/tender-drilldown — the per-store DRILL ═════════════════════════
st4 = fresh_store(); wire(st4); two_store_setup(st4)
d_un = cr.tender_drilldown(date=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("2A. unscoped: drilldown returns rows for BOTH stores", sorted({r["store_code"] for r in d_un["rows"]}) == ["S1", "S2"])

SO.scope_keyset = scoped({"S1"})
d_sc_own = cr.tender_drilldown(date=DATE, store="S1", authorization=AUTH_SCOPED, org_id=HOUSE)
check("2B. scoped viewer drilling their OWN store (S1) gets S1's transaction rows",
      len(d_sc_own["rows"]) == 1 and d_sc_own["rows"][0]["store_code"] == "S1", str(d_sc_own))

d_sc_bypass = cr.tender_drilldown(date=DATE, store="S2", authorization=AUTH_SCOPED, org_id=HOUSE)
check("2C. DIRECT-CALL BYPASS CLOSED: scoped viewer requesting store=S2 (out-of-span) gets ZERO rows, "
      "not S2's transaction detail", d_sc_bypass["rows"] == [], str(d_sc_bypass["rows"]))

d_sc_all = cr.tender_drilldown(date=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("2D. scoped viewer with no store= filter sees only in-span rows (S1), S2's never appear",
      [r["store_code"] for r in d_sc_all["rows"]] == ["S1"], str(d_sc_all["rows"]))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ══════════════════════ ③ /closing/submissions — dashboard detail table ═══════════════════════════
# (the same "tiles != table" class as retail-ops-24: /closing/rollup's tiles sit right above this table
#  on the SAME dashboard page and were already fixed — this table was not.)
st5 = fresh_store(); wire(st5); two_store_setup(st5)
sub_un = cr.closing_submissions(date_from=DATE, date_to=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("3A. unscoped: submissions detail table shows both stores",
      sorted(r["store_code"] for r in sub_un["rows"]) == ["S1", "S2"])
SO.scope_keyset = scoped({"S1"})
sub_sc = cr.closing_submissions(date_from=DATE, date_to=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("3B. scoped: submissions detail table shows ONLY S1 -- now consistent with the already-fixed "
      "rollup tiles above it on the dashboard", [r["store_code"] for r in sub_sc["rows"]] == ["S1"],
      str(sub_sc["rows"]))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ══════════════════════ ④ /closing/epay-recon ═══════════════════════════════════════════════════════
st6 = fresh_store(); wire(st6)
st6["daily_closing"] = [
    dc_row(id="e1", store_code="S1", store_address="1 Main St", epay_on_cash=40.0, epay_on_credit=0.0, epay_on_acima=0.0),
    dc_row(id="e2", store_code="S2", store_address="2 Oak Ave", epay_on_cash=90.0, epay_on_credit=0.0, epay_on_acima=0.0),
]
epay_un = cr.epay_recon(date=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("4A. unscoped: epay-recon shows both stores", sorted(r["store_code"] for r in epay_un["rows"]) == ["S1", "S2"])
SO.scope_keyset = scoped({"S1"})
epay_sc = cr.epay_recon(date=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("4B. scoped: epay-recon shows ONLY S1, totals recomputed over the visible set (declared_cash=40)",
      [r["store_code"] for r in epay_sc["rows"]] == ["S1"] and epay_sc["totals"]["declared_cash"] == 40.0,
      str((epay_sc["rows"], epay_sc["totals"])))
epay_bypass = cr.epay_recon(date=DATE, store="S2", authorization=AUTH_SCOPED, org_id=HOUSE)
check("4C. explicit store=S2 (out-of-span) from a scoped viewer returns nothing", epay_bypass["rows"] == [])
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ══════════════════════ ⑤ /closing/accessory-recon ══════════════════════════════════════════════════
st7 = fresh_store(); wire(st7)
st7["daily_closing"] = [
    dc_row(id="a1", store_code="S1", store_address="1 Main St", acc_sale=25.0),
    dc_row(id="a2", store_code="S2", store_address="2 Oak Ave", acc_sale=60.0),
]
acc_un = cr.accessory_recon(date=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("5A. unscoped: accessory-recon shows both stores", sorted(r["store_code"] for r in acc_un["rows"]) == ["S1", "S2"])
SO.scope_keyset = scoped({"S1"})
acc_sc = cr.accessory_recon(date=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("5B. scoped: accessory-recon shows ONLY S1, totals recomputed (declared=25)",
      [r["store_code"] for r in acc_sc["rows"]] == ["S1"] and acc_sc["totals"]["declared"] == 25.0,
      str((acc_sc["rows"], acc_sc["totals"])))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ══════════════════════ ⑥ /closing/cash-position ════════════════════════════════════════════════════
st8 = fresh_store(); wire(st8)
st8["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St"},
                 {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave"}]
st8["daily_closing"] = [
    dc_row(id="c1", store_code="S1", t_cash=100.0),
    dc_row(id="c2", store_code="S2", t_cash=250.0),
]
cp_un = cr.cash_position(date=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("6A. unscoped: cash-position lists both stores", sorted(r["store_code"] for r in cp_un["rows"]) == ["S1", "S2"])
SO.scope_keyset = scoped({"S1"})
cp_sc = cr.cash_position(date=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("6B. scoped: cash-position lists ONLY S1", [r["store_code"] for r in cp_sc["rows"]] == ["S1"], str(cp_sc["rows"]))
cp_bypass = cr.cash_position(date=DATE, stores="S2", authorization=AUTH_SCOPED, org_id=HOUSE)
check("6C. explicit stores=S2 (out-of-span) from a scoped viewer returns nothing", cp_bypass["rows"] == [])
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ══════════════════════ ⑦ /closing/pickups ══════════════════════════════════════════════════════════
st9 = fresh_store(); wire(st9)
st9["stores"] = [{"org_id": HOUSE, "store_code": "S1", "address": "1 Main St", "market": "Texas", "is_active": True},
                 {"org_id": HOUSE, "store_code": "S2", "address": "2 Oak Ave", "market": "Ohio", "is_active": True}]
st9["daily_closing"] = [
    dc_row(id="p1", store_code="S1", store_cash=50.0, epay_cash=0.0),
    dc_row(id="p2", store_code="S2", store_cash=80.0, epay_cash=0.0),
]
pk_un = cr.closing_pickups(date=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("7A. unscoped: pickups envelope list shows both stores",
      sorted(e["store_code"] for e in pk_un["envelopes"]) == ["S1", "S2"])
SO.scope_keyset = scoped({"S1"})
pk_sc = cr.closing_pickups(date=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("7B. scoped: pickups envelope list shows ONLY S1, ready_cash recomputed over the visible set (50)",
      [e["store_code"] for e in pk_sc["envelopes"]] == ["S1"] and pk_sc["ready_cash"] == 50.0,
      str((pk_sc["envelopes"], pk_sc["ready_cash"])))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ══════════════════════ ⑧ /closing/duplicates + /closing/attempts (management-gated) ═════════════════
st10 = fresh_store(); wire(st10)
st10["daily_closing"] = [
    dc_row(id="d1a", store_code="S1", store_address="1 Main St", employee_name="Jane Rep",
           source="manual", submitted_at="2026-07-15T09:00:00Z"),
    dc_row(id="d1b", store_code="S1", store_address="1 Main St", employee_name="Jane Rep",
           source="manual", submitted_at="2026-07-15T10:00:00Z"),
    dc_row(id="d2a", store_code="S2", store_address="2 Oak Ave", employee_name="Mo Rep",
           source="manual", submitted_at="2026-07-15T09:00:00Z"),
    dc_row(id="d2b", store_code="S2", store_address="2 Oak Ave", employee_name="Mo Rep",
           source="manual", submitted_at="2026-07-15T10:00:00Z"),
]
# management gate: super_admin caller (via _caller_perms -> requires _uid_from_token to resolve; simplest
# reliable path for an offline harness is to monkeypatch _can_mgmt_review directly, exercising ONLY the
# keyset gate this package added -- the permission gate itself is untouched/pre-existing behavior).
cr._can_mgmt_review = lambda perms: True
dup_un = cr.closing_duplicates(date=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("8A. unscoped: duplicates report finds both stores' dup groups",
      sorted(g["store_code"] for g in dup_un["groups"]) == ["S1", "S2"])
SO.scope_keyset = scoped({"S1"})
dup_sc = cr.closing_duplicates(date=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("8B. scoped: duplicates report finds ONLY S1's dup group (an explicit page-grant can admit a "
      "scoped, non-'all' role here -- see _can_mgmt_review)",
      [g["store_code"] for g in dup_sc["groups"]] == ["S1"], str(dup_sc["groups"]))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

st10b = fresh_store(); wire(st10b)
st10b["closing_attempt"] = [
    {"org_id": HOUSE, "close_date": DATE, "store_code": "S1", "store_address": "1 Main St",
     "employee_name": "Jane Rep", "attempt_no": 1, "accepted": True},
    {"org_id": HOUSE, "close_date": DATE, "store_code": "S2", "store_address": "2 Oak Ave",
     "employee_name": "Mo Rep", "attempt_no": 1, "accepted": True},
]
att_un = cr.closing_attempts(date=DATE, authorization=AUTH_NONE, org_id=HOUSE)
check("8C. unscoped: attempts (management review) shows both stores",
      sorted(g["store_code"] for g in att_un["groups"]) == ["S1", "S2"])
SO.scope_keyset = scoped({"S1"})
att_sc = cr.closing_attempts(date=DATE, authorization=AUTH_SCOPED, org_id=HOUSE)
check("8D. scoped: attempts (management review) shows ONLY S1",
      [g["store_code"] for g in att_sc["groups"]] == ["S1"], str(att_sc["groups"]))
SO.scope_keyset = lambda authorization="", org_id=HOUSE: None

# ══════════════════════ ⑨ Multi-tenant isolation unaffected (spot check on the reported endpoint) ═════
st11 = fresh_store(); wire(st11); two_store_setup(st11)
st11["daily_closing"].append(dc_row(id="intruder", org_id=OTHER, store_code="S1", store_address="1 Main St",
                                    employee_name="Intruder", t_cash=99999.0))
st11["store_mapping"].append(sm_row("S1", "1 Main St"))  # (harmless dup — org_id not on the fake filter key)
r_house = cr.tender_recon_3way(date=DATE, authorization=AUTH_NONE, org_id=HOUSE)
s1_block = next(s for s in r_house["stores"] if s["store_code"] == "S1")
check("9A. HOUSE call never sees OTHER org's row regardless of the keyset fix (closing total stays 150, not 99999+150)",
      s1_block["totals"]["closing"] == 150.0, str(s1_block["totals"]))

# ── Summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
