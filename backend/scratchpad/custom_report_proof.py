"""Proof for agent/commission/custom-report (mig 211) — the config-driven universal Custom Report.

Two levels, no live DB:
  • PURE unit tests over commcalc.custom_report (registry merge / override / disable, RULE FIVE
    server-side filter BEFORE aggregation, group-by, totals, per-column permission gate, project,
    saved-definition validation, mig-210 dynamic categories).
  • INTEGRATION over the REAL router endpoints (monkeypatched sb() -> in-memory FakeClient that honors
    org_id + period filtering, and can mark a table "missing" to prove degradation).

Run:  cd backend && python3 scratchpad/custom_report_proof.py
"""
import sys, os, asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.modules.commcalc import router as R                     # noqa: E402
from app.modules.commcalc import custom_report as CR             # noqa: E402
import app.modules.storeops.router as SR                         # noqa: E402

# Deterministic: no span scope (the endpoint's own try/except would also degrade to this).
SR.scope_keyset = lambda *a, **k: None

HOUSE = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-0000000000ff"

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


# ── in-memory chainable Supabase stub (honors eq / in_ / neq + count + mutations) ──────────────────
class _Resp:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, client, table):
        self.c, self.table = client, table
        self.eqs, self.ins, self.neqs = [], [], []
        self.count_mode = False
        self.mode = "select"
        self.payload = None
        self.conflict = None

    def select(self, *a, **kw):
        if kw.get("count"):
            self.count_mode = True
        return self

    def eq(self, col, val):
        self.eqs.append((col, val)); return self

    def in_(self, col, vals):
        self.ins.append((col, list(vals))); return self

    def neq(self, col, val):
        self.neqs.append((col, val)); return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def range(self, *a, **k):
        return self

    def insert(self, row):
        self.mode, self.payload = "insert", row; return self

    def upsert(self, row, on_conflict=None):
        self.mode, self.payload, self.conflict = "upsert", row, on_conflict; return self

    def delete(self):
        self.mode = "delete"; return self

    def _match(self, r):
        for col, val in self.eqs:
            if str(r.get(col)) != str(val):
                return False
        for col, vals in self.ins:
            if r.get(col) not in vals:
                return False
        for col, val in self.neqs:
            if str(r.get(col) or "") == str(val):
                return False
        return True

    def execute(self):
        if self.table in self.c.missing:
            raise RuntimeError(f'relation "commcalc.{self.table}" does not exist')
        rows = self.c.tables.setdefault(self.table, [])
        if self.mode == "select":
            hit = [r for r in rows if self._match(r)]
            if self.count_mode:
                return _Resp(count=len(hit))
            return _Resp(data=[dict(r) for r in hit])
        if self.mode in ("insert", "upsert"):
            new = self.payload if isinstance(self.payload, list) else [self.payload]
            for nr in new:
                nr = dict(nr)
                if self.mode == "upsert" and self.conflict:
                    keys = [k.strip() for k in self.conflict.split(",")]
                    for i, ex in enumerate(rows):
                        if all(str(ex.get(k)) == str(nr.get(k)) for k in keys):
                            nr.setdefault("id", ex.get("id"))
                            rows[i] = nr
                            break
                    else:
                        nr.setdefault("id", f"id-{len(rows)+1}")
                        rows.append(nr)
                else:
                    nr.setdefault("id", f"id-{len(rows)+1}")
                    rows.append(nr)
            return _Resp(data=new)
        if self.mode == "delete":
            keep = [r for r in rows if not self._match(r)]
            self.c.tables[self.table] = keep
            return _Resp(data=[])
        return _Resp(data=[])


class FakeClient:
    def __init__(self, tables, missing=()):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.missing = set(missing)

    def schema(self, _s):
        return self

    def table(self, t):
        return _Query(self, t)


# ── seed data (period stored as 'March 2025'; a CLOSED month → raw_sales is the union primary) ──────
PERIOD = "2025-03"
def _sales(store, rep, tid, ct, cat, ext, gp, org=HOUSE, voided="", ttype="", dept="Phones", prod="X"):
    return {"org_id": org, "period": "March 2025", "trans_id": tid, "trans_date": "2025-03-05",
            "store": store, "salesperson": rep, "department": dept, "category": cat,
            "product_desc": prod, "contract_type": ct, "ext_price": ext, "gp": gp,
            "voided": voided, "trans_type": ttype}


BASE_TABLES = {
    "store_mapping": [
        {"org_id": HOUSE, "store_code": "S1", "store_address": "1 Main St", "market": "North"},
        {"org_id": HOUSE, "store_code": "S2", "store_address": "2 Oak Ave", "market": "South"},
    ],
    "raw_sales": [
        _sales("1 Main St", "ALICE", "t1", "Activation", "Phones", 100.0, 40.0),
        _sales("1 Main St", "ALICE", "t2", "BYOD Activation", "Phones", 50.0, 20.0),
        _sales("2 Oak Ave", "BOB", "t3", "Upgrade", "Phones", 80.0, 30.0),
        _sales("2 Oak Ave", "BOB", "t4", "Activation", "Accessories", 25.0, 10.0),
        _sales("1 Main St", "ALICE", "tv", "Activation", "Phones", 999.0, 999.0, voided="Yes"),   # excluded
        _sales("2 Oak Ave", "BOB", "tr", "Activation", "Phones", 999.0, 999.0, ttype="Return"),   # excluded
        _sales("9 Foreign Rd", "ZED", "tx", "Activation", "Phones", 500.0, 500.0, org=OTHER),      # other tenant
    ],
    "daily_sales_feed": [],
    "rep_commissions": [
        {"org_id": HOUSE, "period": "March 2025", "store": "1 Main St", "epay_salesperson": "ALICE",
         "storeops_name": "Alice A", "total_payout": 300.0, "subtotal": 300.0, "premium_acts": 2,
         "byod_acts": 1, "upgrade_acts": 0, "premium_comm": 100.0, "byod_comm": 25.0, "tier": 1.0},
        {"org_id": HOUSE, "period": "March 2025", "store": "2 Oak Ave", "epay_salesperson": "BOB",
         "storeops_name": "Bob B", "total_payout": 200.0, "subtotal": 200.0, "premium_acts": 1,
         "byod_acts": 0, "upgrade_acts": 1, "premium_comm": 50.0, "byod_comm": 0.0, "tier": 0.75},
        {"org_id": OTHER, "period": "March 2025", "store": "9 Foreign Rd", "epay_salesperson": "ZED",
         "total_payout": 9999.0},   # other tenant — must never appear
    ],
    "chargeback_items": [
        {"org_id": HOUSE, "period": "March 2025", "epay_salesperson": "ALICE", "store": "1 Main St",
         "source": "chargeback_review", "description": "clawback", "amount": 50.0, "deduct": True},
    ],
    "raw_ma_commission": [
        {"org_id": HOUSE, "period": "March 2025", "activation_type2": "BYOP", "imei": "111", "ban": "b1",
         "spiff_m1": 5.0, "spiff_m2": 5.0, "spiff_m3": 0, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0,
         "rebate": 10.0},
        {"org_id": HOUSE, "period": "March 2025", "activation_type2": "NEW", "imei": "222", "ban": "b2",
         "spiff_m1": 3.0, "spiff_m2": 0, "spiff_m3": 0, "spiff_m4": 0, "spiff_m5": 0, "spiff_m6": 0,
         "rebate": 4.0},
    ],
    # registry present (mig 211): house rows + an ORG override that DISABLES flags and RENAMES sales.
    "custom_report_dataset": [
        {"org_id": HOUSE, "dataset_key": "sales_line", "display_name": "Sales — line items",
         "enabled": True, "sort_order": 10},
        {"org_id": HOUSE, "dataset_key": "rep_commissions", "display_name": "Commissions — rep payout",
         "enabled": True, "sort_order": 20},
        {"org_id": HOUSE, "dataset_key": "ma_commission", "display_name": "MA — carrier commission",
         "enabled": True, "sort_order": 80},
        {"org_id": HOUSE, "dataset_key": "flags", "display_name": "Flags", "enabled": True, "sort_order": 70},
    ],
    "custom_report_def": [],
    "commission_org_config": [],   # empty -> residual_visibility 'all' -> carrier_residual granted by default
    "flags": [
        {"org_id": HOUSE, "period": "March 2025", "severity": "HIGH", "flag_type": "DUPLICATE_IMEI",
         "source": "sales", "store_address": "1 Main St", "epay_salesperson": "ALICE",
         "description": "dupe", "amount": 0},
    ],
    "raw_ma_daily_tx": [],
    "raw_dlar_store": [],
    "store_expenses": [],
}


def run(coro):
    # ASYNC-SWEEP 2026-08-04: commcalc's zero-`await` route handlers are now plain `def` (off the single
    # uvicorn event loop). Dual-shape: drive a coroutine, pass a plain result straight through.
    if asyncio.iscoroutine(coro):
        return asyncio.get_event_loop().run_until_complete(coro)
    return coro


def section(resp, key):
    return next((s for s in resp["sections"] if s["key"] == key), None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[A] PURE registry: code default -> HOUSE row -> org override (rename + disable)")
reg0 = CR.resolve_registry([])                       # mig 211 absent
check("A1 code-default registry non-empty", len(reg0) == len(CR.DATASETS))
check("A2 sorted by sort_order", [d["key"] for d in reg0][:2] == ["sales_line", "rep_commissions"])
cfg = [
    {"org_id": HOUSE, "dataset_key": "flags", "enabled": True, "sort_order": 70},
    {"org_id": OTHER, "dataset_key": "flags", "enabled": False},            # org disables flags
    {"org_id": OTHER, "dataset_key": "sales_line", "display_name": "My Sales", "sort_order": 5},
]
regO = CR.resolve_registry(cfg)
keys = [d["key"] for d in regO]
check("A3 org-disabled dataset dropped (flags gone)", "flags" not in keys)
check("A4 org override rename applied", CR.dataset_by_key and
      next(d for d in regO if d["key"] == "sales_line")["name"] == "My Sales")
check("A5 org override reorder applied (sales_line first at sort 5)", keys[0] == "sales_line")
check("A6 unknown config key ignored", all(k in {d["key"] for d in CR.DATASETS} for k in keys))

print("\n[B] PURE RULE FIVE filter BEFORE aggregation + group-by + totals")
sales_norm = [
    {"store": "1 Main St", "market": "North", "salesperson": "ALICE", "trans_date": "2025-03-05",
     "ext_price": 100.0, "gp": 40.0, "category": "Phones"},
    {"store": "1 Main St", "market": "North", "salesperson": "ALICE", "trans_date": "2025-03-06",
     "ext_price": 50.0, "gp": 20.0, "category": "Phones"},
    {"store": "2 Oak Ave", "market": "South", "salesperson": "BOB", "trans_date": "2025-03-05",
     "ext_price": 80.0, "gp": 30.0, "category": "Acc"},
]
fmap = CR.dataset_by_key("sales_line")["field_map"]
f_alice = CR.filter_rows(sales_norm, fmap, reps=["ALICE"])
check("B1 rep filter keeps only ALICE (2 rows)", len(f_alice) == 2 and all(r["salesperson"] == "ALICE" for r in f_alice))
f_south = CR.filter_rows(sales_norm, fmap, markets=["South"])
check("B2 market filter keeps only South (1 row)", len(f_south) == 1 and f_south[0]["store"] == "2 Oak Ave")
f_store = CR.filter_rows(sales_norm, fmap, stores=["1 Main St"])
check("B3 store filter keeps 2 rows", len(f_store) == 2)
f_day = CR.filter_rows(sales_norm, fmap, day_from="2025-03-06")
check("B4 day_from filter keeps 1 row", len(f_day) == 1 and f_day[0]["trans_date"] == "2025-03-06")
# group by store, on the ALICE+store filtered set -> aggregate ext/gp
cols = CR.dataset_by_key("sales_line")["columns"]
grp_field = CR.resolve_group_field(CR.dataset_by_key("sales_line"), "store")
grouped, gcols = CR.group_and_aggregate(sales_norm, cols, grp_field)
main = next(r for r in grouped if r["store"] == "1 Main St")
check("B5 group-by store sums ext_price (150) + count (2)", main["ext_price"] == 150.0 and main["_count"] == 2)
check("B6 group output has a Rows count column", any(c["field"] == "_count" for c in gcols))
totals = CR.compute_totals(sales_norm, cols)
check("B7 totals sum money (ext 230 / gp 90)", totals["ext_price"] == 230.0 and totals["gp"] == 90.0)
# pct column averages, never sums
kpi_cols = CR.dataset_by_key("kpi_metrics")["columns"]
kpi_rows = [{"atu": 50}, {"atu": 70}]
check("B8 pct column (atu) averages in totals", CR.compute_totals(kpi_rows, kpi_cols)["atu"] == 60.0)

print("\n[C] PURE per-column gate + project + mig-210 dynamic categories")
ds_ma = CR.dataset_by_key("ma_commission")
vis_on = CR.visible_columns(ds_ma, {"carrier_residual"})
vis_off = CR.visible_columns(ds_ma, set())
check("C1 grant present -> money columns visible", any(c["field"] == "spiff_m1" for c in vis_on))
check("C2 grant absent -> gated money columns dropped", not any(c.get("gate") for c in vis_off))
check("C3 grant absent -> non-money columns remain", any(c["field"] == "order_type" or c["field"] == "period" for c in vis_off))
proj = CR.project_rows([{"period": "March 2025", "spiff_m1": 5.0, "activation_type2": "BYOP"}], vis_off)
check("C4 projected row does NOT carry a gated field", "spiff_m1" not in proj[0])
# mig-210: dynamic categories (sales_category / kpi_category) light up only when a row carries them
ds_sales = CR.dataset_by_key("sales_line")
no_cat = CR.augment_columns(ds_sales, [{"store": "x"}])
check("C5 mig-210 absent -> no sales_category column", not any(c["field"] == "sales_category" for c in no_cat["columns"]))
with_cat = CR.augment_columns(ds_sales, [{"store": "x", "sales_category": "Activation payment"}])
check("C6 mig-210 present -> sales_category column appears (groupable)",
      any(c["field"] == "sales_category" and c["group"] for c in with_cat["columns"]))

print("\n[D] PURE saved-definition validation")
ok, res = CR.validate_definition({"name": "My Report", "config": {"datasets": ["sales_line", "bogus"]}},
                                 {"sales_line", "rep_commissions"})
check("D1 valid def accepted, bogus dataset dropped", ok and res["config"]["datasets"] == ["sales_line"])
bad, err = CR.validate_definition({"name": "", "config": {"datasets": ["sales_line"]}}, {"sales_line"})
check("D2 empty name rejected", not bad)
bad2, err2 = CR.validate_definition({"name": "x", "config": {"datasets": ["nope"]}}, {"sales_line"})
check("D3 no known dataset rejected", not bad2)
# select_columns: pick applies when it overlaps, keeps-all when disjoint (multi-dataset safety)
scols = CR.dataset_by_key("sales_line")["columns"]
check("D4 column pick narrows when overlapping", [c["field"] for c in CR.select_columns(scols, ["store", "gp"])] == ["store", "gp"])
check("D5 disjoint pick keeps all (no blanking a co-selected section)", CR.select_columns(scols, ["merchant_invoice"]) == scols)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n[E] INTEGRATION — GET /custom-report (org-scoped, RULE FIVE server-side, totals)")
R.sb = lambda: FakeClient(BASE_TABLES)
resp = run(R.custom_report_run(datasets="sales_line,rep_commissions,ma_commission", period=PERIOD,
                               org_id=HOUSE, authorization=""))
sl = section(resp, "sales_line")
check("E1 registry_source = config (mig 211 rows present)", resp["registry_source"] == "config")
check("E2 sales_line available", sl["available"])
check("E3 voided + Return excluded (4 line rows, not 6)", sl["row_count"] == 4)
check("E4 other-tenant sales EXCLUDED (org scope)", all("Foreign" not in str(r.get("store")) for r in sl["rows"]))
check("E5 sales totals: ext=255 (100+50+80+25), gp=100", sl["totals"]["ext_price"] == 255.0 and sl["totals"]["gp"] == 100.0)
check("E6 market attached from store_mapping", any(r.get("market") == "North" for r in sl["rows"]))
rc = section(resp, "rep_commissions")
check("E7 rep_commissions final_payout = total - chargeback (ALICE 300-50=250)",
      any(r["epay_salesperson"] == "ALICE" and r["final_payout"] == 250.0 for r in rc["rows"]))
check("E8 other-tenant rep (ZED) excluded", all(r["epay_salesperson"] != "ZED" for r in rc["rows"]))
opt = resp["filter_options"]
check("E9 pick-don't-type reps from REAL data (ALICE,BOB; no ZED)", set(opt["reps"]) >= {"ALICE", "BOB"} and "ZED" not in opt["reps"])
check("E10 markets options include store_mapping markets", {"North", "South"} <= set(opt["markets"]))

print("\n[F] INTEGRATION — RULE FIVE server-side filter narrows BEFORE aggregation")
resp2 = run(R.custom_report_run(datasets="sales_line", period=PERIOD, reps="ALICE",
                                org_id=HOUSE, authorization=""))
sl2 = section(resp2, "sales_line")
check("F1 rep=ALICE -> 2 rows, ext total 150", sl2["row_count"] == 2 and sl2["totals"]["ext_price"] == 150.0)
resp3 = run(R.custom_report_run(datasets="sales_line", period=PERIOD, markets="South",
                                org_id=HOUSE, authorization="", group_by="store"))
sl3 = section(resp3, "sales_line")
check("F2 market=South + group-by store -> one group (2 Oak Ave), ext 105",
      len(sl3["rows"]) == 1 and sl3["rows"][0]["store"] == "2 Oak Ave" and sl3["totals"]["ext_price"] == 105.0)
check("F3 grouped section reports grouped_by field", sl3["grouped_by"] == "store")
# group by a dim NOT in the column pick -> the group column is still present to label the rows
resp4 = run(R.custom_report_run(datasets="sales_line", period=PERIOD, group_by="market", columns="gp",
                                org_id=HOUSE, authorization=""))
sl4 = section(resp4, "sales_line")
check("F4 group column kept even when not column-selected", any(c["field"] == "market" for c in sl4["columns"]))
check("F5 grouped rows carry the group value", all("market" in r for r in sl4["rows"]))

print("\n[G] INTEGRATION — permission-gated MA money columns")
R._can_view_carrier_residual = lambda *a, **k: True
rg_on = section(run(R.custom_report_run(datasets="ma_commission", period=PERIOD, org_id=HOUSE, authorization="")), "ma_commission")
check("G1 grant ON -> spiff_m1 column present + summed",
      any(c["field"] == "spiff_m1" for c in rg_on["columns"]) and rg_on["totals"].get("spiff_m1") == 8.0)
R._can_view_carrier_residual = lambda *a, **k: False
resp_off = run(R.custom_report_run(datasets="ma_commission", period=PERIOD, org_id=HOUSE, authorization=""))
rg_off = section(resp_off, "ma_commission")
check("G2 grant OFF -> NO money column in metadata", not any(c.get("money") for c in rg_off["columns"]))
check("G3 grant OFF -> money field NOT in any row (no export leak)",
      all("spiff_m1" not in r and "rebate" not in r for r in rg_off["rows"]))
check("G4 grant OFF -> gated_columns_hidden lists the hidden money labels", "M1 $" in rg_off["gated_columns_hidden"])

print("\n[H] INTEGRATION — degradation: mig 211 absent + missing backing table never 500")
# reload the module to restore the real _can_view_carrier_residual (monkeypatched above)
import importlib  # noqa: E402
importlib.reload(R)
SR.scope_keyset = lambda *a, **k: None
R.sb = lambda: FakeClient(BASE_TABLES, missing=["custom_report_dataset", "custom_report_def"])
resp_nomig = run(R.custom_report_run(datasets="sales_line", period=PERIOD, org_id=HOUSE, authorization=""))
check("H1 mig 211 absent -> registry_source = code-default", resp_nomig["registry_source"] == "code-default")
check("H2 mig 211 absent -> page still renders sales_line", section(resp_nomig, "sales_line")["available"])
R.sb = lambda: FakeClient(BASE_TABLES, missing=["raw_ma_daily_tx"])
resp_unavail = run(R.custom_report_run(datasets="ma_daily_tx", period=PERIOD, org_id=HOUSE, authorization=""))
mdt = section(resp_unavail, "ma_daily_tx")
check("H3 missing backing table -> section unavailable (not 500)", mdt["available"] is False and "unavailable" in mdt["reason"])
# datasets endpoint degrades too
R.sb = lambda: FakeClient(BASE_TABLES, missing=["custom_report_dataset"])
dsresp = R.custom_report_datasets(authorization="", org_id=HOUSE)
check("H4 /datasets degrades to code-default registry", dsresp["registry_source"] == "code-default" and len(dsresp["datasets"]) == len(CR.DATASETS))

print("\n[I] INTEGRATION — saved-definition round-trip (org-scoped, org_id stamped)")
_shared = FakeClient(BASE_TABLES)   # ONE client so save/list/delete share state across endpoint calls
R.sb = lambda: _shared
save = R.custom_report_defs_save({"name": "Monthly Sales+Comm",
                                  "config": {"datasets": ["sales_line", "rep_commissions"],
                                             "group_by": "store"}}, org_id=HOUSE)
check("I1 save ok", save["ok"])
lst = R.custom_report_defs_list(org_id=HOUSE)["definitions"]
check("I2 saved def listed, org_id stamped HOUSE", len(lst) == 1 and lst[0]["org_id"] == HOUSE and lst[0]["name"] == "Monthly Sales+Comm")
check("I3 other tenant sees NONE of it (isolation)", len(R.custom_report_defs_list(org_id=OTHER)["definitions"]) == 0)
R.custom_report_defs_delete(lst[0]["id"], org_id=HOUSE)
check("I4 delete removes it", len(R.custom_report_defs_list(org_id=HOUSE)["definitions"]) == 0)

print(f"\n==== custom_report_proof: {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
