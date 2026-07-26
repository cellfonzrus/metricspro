"""Proof harness for agent/commission/plan-value-dropdowns — drives the REAL
app.modules.commcalc.plan_options over an in-memory FakeClient (no DB, no network).
Run:  cd backend && python3 scratchpad/plan_options_proof.py

What it proves
  A. VOCABULARY IS THE ENGINE'S — match fields / payout kinds come from commission_engine itself, and the
     three offered ops are exactly the ops _rule_matches implements (anything else behaves as 'equals').
  B. OPTIONS ARE THE TENANT'S OWN — distinct values + line counts per field, from that org's sales only.
  C. ORG ISOLATION (two-org differential) — org A never sees a value, a count, a period or a facet row
     belonging to org B, on the RPC path AND on the scan fallback.
  D. VOIDED + RETURN LINES ARE EXCLUDED, with the same tokens the pay path uses.
  E. RPC-vs-SCAN PARITY — with migration 240 absent the bounded Python scan produces the same options.
  F. SOURCE PRECEDENCE — raw_sales wins; the daily feed is used only when raw_sales has nothing, and then
     `sku` is honestly reported as unavailable (the feed has no sku column).
  G. ZERO-WIPE — a value stored on a plan/tier/trigger that no longer exists in the data is still offered
     (flagged stored_only), and a stored base_tier_metric is unioned into the metric list.
  H. CONTRACT-TYPE RESOLUTION — 'mapped' (mig 232) adds the resolved bucket values + the per-facet
     `ct_resolved` column; 'raw' (default) adds neither. Byte-identical for a default tenant.
  I. TRUNCATION IS HONEST — a capped facet list reports truncated + lines_covered/lines_total.
  J. MATCHER PARITY — facet_matches == commission_engine._rule_matches across a case grid; the grid is
     written to scratchpad/plan_match_cases.json for the browser-side mirror proof
     (frontend/scratchpad/prove_plan_match.mjs).
  K. CACHE — TTL-bounded, ALWAYS keyed by org (org A's payload can never be served to org B).
  L. DEGRADATION — no migration 240 and no sales at all still returns a usable payload (vocab + empty
     option lists + a note), never an exception.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import app.modules.commcalc.plan_options as PO
import app.modules.commcalc.commission_engine as CE
import app.modules.commcalc.accessory_catalog as AC

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


ORG_A = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"      # luxelink-shaped tenant
ORG_B = "00000000-0000-0000-0000-000000000001"      # house / Boost
VOID_TOKENS = ("true", "yes", "1", "voided", "void")


# ── in-memory fake supabase client, with the migration-240 RPCs implemented in Python ─────────────
class FakeResult:
    def __init__(self, data=None):
        self.data = data or []


class FakeQuery:
    def __init__(self, store, table, absent):
        self.store, self.t, self.absent = store, table, absent
        self.f, self.rng, self.cols = [], None, None

    def select(self, cols="*", **k):
        self.cols = cols
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v))
        return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v)))
        return self

    def limit(self, n):
        return self

    def order(self, *a, **k):
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def _m(self, r):
        for k, c, v in self.f:
            if k == "eq" and r.get(c) != v:
                return False
            if k == "in" and r.get(c) not in v:
                return False
        return True

    def execute(self):
        if self.t in self.absent:
            raise Exception(f'relation "commcalc.{self.t}" does not exist')
        rows = [r for r in self.store.get(self.t, []) if self._m(r)]
        # emulate PostgREST column projection: selecting a column a table doesn't have is an error
        if self.cols and self.cols != "*":
            want = [c.strip() for c in self.cols.split(",")]
            known = set()
            for r in self.store.get(self.t, []):
                known |= set(r.keys())
            missing = [c for c in want if known and c not in known]
            if missing:
                raise Exception(f'column {self.t}.{missing[0]} does not exist')
            rows = [{c: r.get(c) for c in want} for r in rows]
        if self.rng:
            a, b = self.rng
            rows = rows[a:b + 1]
        return FakeResult([dict(r) for r in rows])


def _live(rows):
    out = []
    for r in rows:
        if str(r.get("voided") or "").strip().lower() in VOID_TOKENS:
            continue
        if str(r.get("trans_type") or "").strip() == "Return":
            continue
        out.append(r)
    return out


class FakeSchema:
    def __init__(self, client, s):
        self.c, self.s = client, s

    def table(self, t):
        return FakeQuery(self.c.store, t, self.c.absent)

    def rpc(self, name, params):
        if name in self.c.absent_rpc:
            raise Exception(f'function commcalc.{name} does not exist')
        if name == "plan_match_facets":
            return _Rpc(self.c._facets(params))
        if name == "plan_match_facet_totals":
            return _Rpc(self.c._facet_totals(params))
        if name == "plan_sales_periods":
            return _Rpc(self.c._periods(params))
        raise Exception("no such rpc")


class _Rpc:
    def __init__(self, data):
        self._d = data

    def execute(self):
        return FakeResult(self._d)


class FakeClient:
    """The migration-240 SQL, re-implemented in Python so the endpoint's RPC path is exercised end to end."""

    def __init__(self, store, absent=None, absent_rpc=None):
        self.store = store
        self.absent = set(absent or [])
        self.absent_rpc = set(absent_rpc or [])

    def schema(self, s):
        return FakeSchema(self, s)

    def _src_rows(self, params):
        tbl = "daily_sales_feed" if params.get("p_source") == "feed" else "raw_sales"
        rows = [r for r in self.store.get(tbl, [])
                if r.get("org_id") == params["p_org"] and r.get("period") in params["p_periods"]]
        return tbl, _live(rows)

    def _facets(self, params):
        tbl, rows = self._src_rows(params)
        agg = {}
        for r in rows:
            key = tuple(str(r.get(c) or "").strip() if not (tbl == "daily_sales_feed" and c == "sku") else None
                        for c in PO.FACET_COLUMNS)
            agg[key] = agg.get(key, 0) + 1
        out = [dict(zip(PO.FACET_COLUMNS, k), lines=v) for k, v in agg.items()]
        out.sort(key=lambda x: (-x["lines"], str(x["department"]), str(x["category"])))
        lim = max(1, min(int(params.get("p_limit") or 4000), 20000))
        return out[:lim]

    def _facet_totals(self, params):
        tbl, rows = self._src_rows(params)
        combos = {tuple(str(r.get(c) or "").strip() for c in PO.FACET_COLUMNS) for r in rows}
        return [{"lines": len(rows), "combos": len(combos)}]

    def _periods(self, params):
        out = []
        for tbl, src in (("raw_sales", "raw_sales"), ("daily_sales_feed", "feed")):
            agg = {}
            for r in self.store.get(tbl, []):
                if r.get("org_id") != params["p_org"]:
                    continue
                p = str(r.get("period") or "").strip()
                if p:
                    agg[p] = agg.get(p, 0) + 1
            out += [{"period": p, "lines": n, "source": src} for p, n in agg.items()]
        return sorted(out, key=lambda x: -x["lines"])


def sale(org, period, **kw):
    row = {"org_id": org, "period": period, "department": "", "category": "", "contract_type": "",
           "tender_type": "", "trans_type": "Sale", "product_desc": "", "sku": "", "voided": "",
           "trans_id": "T1", "salesperson": "REP"}
    row.update(kw)
    return row


def feed_row(org, period, **kw):
    r = sale(org, period, **kw)
    r.pop("sku", None)          # daily_sales_feed has no sku column (mig 047)
    return r


# The window the module asks for is "the last 3 months + the previewed period"; the harness pins the
# period it uses so the fixtures are deterministic regardless of the day this runs.
PER = "June 2026"


def build_store():
    A = [
        # org A (luxelink-shaped): two Home-Internet products a human would pattern-match by hand
        *[sale(ORG_A, PER, department="Internet", category="Home Internet",
               product_desc="Home Internet Gateway", contract_type="New Activation") for _ in range(5)],
        *[sale(ORG_A, PER, department="Internet", category="Home Internet",
               product_desc="VHI Home Internet Router", contract_type="New Activation") for _ in range(3)],
        *[sale(ORG_A, PER, department="Accessories", category="Cases",
               product_desc="Otterbox Case", tender_type="Acima") for _ in range(7)],
        sale(ORG_A, PER, department="Accessories", category="Cases", product_desc="Otterbox Case",
             voided="TRUE"),                                  # voided -> excluded
        sale(ORG_A, PER, department="Accessories", category="Cases", product_desc="Otterbox Case",
             trans_type="Return"),                            # return  -> excluded
        sale(ORG_A, PER, department="Phones", category="Devices", product_desc="Moto G",
             contract_type="Upgrade", sku="MOTOG-64"),
    ]
    B = [sale(ORG_B, PER, department="BoostDept", category="BoostCat",
              product_desc="Boost Only Product", contract_type="BYOD") for _ in range(11)]
    return {
        "raw_sales": A + B,
        "daily_sales_feed": [],
        "commission_rule": [], "commission_plan": [], "plan_installment_schedule": [],
        "commission_org_config": [], "accessory_config": [],
    }


def opts(store, org, absent=None, absent_rpc=None, **kw):
    AC.invalidate()
    c = FakeClient(store, absent=absent, absent_rpc=absent_rpc)
    return c, PO.build(c, org, period=PER, **kw)


def vals(payload, field):
    return [v["value"] for v in payload["fields"][field]["values"]]


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("── A. the vocabulary IS the engine's ──")
v = PO.vocabulary()
check("match fields == commission_engine.MATCH_FIELDS",
      {f["value"] for f in v["match_fields"]} == set(CE.MATCH_FIELDS),
      f'{ {f["value"] for f in v["match_fields"]} ^ set(CE.MATCH_FIELDS) }')
check("payout kinds == commission_engine.PAYOUT_KINDS",
      {p["value"] for p in v["payout_kinds"]} == set(CE.PAYOUT_KINDS))
check("'any' is offered first", v["match_fields"][0]["value"] == "any")
# the ops the engine actually implements: contains / in / everything-else-is-equals
row = {"category": "Accessory"}
check("op 'equals' behaves as equals",
      CE._rule_matches(row, {"match_field": "category", "match_op": "equals", "match_value": "accessory"}))
check("op 'contains' is a substring match",
      CE._rule_matches(row, {"match_field": "category", "match_op": "contains", "match_value": "ccess"}))
check("op 'in' is a comma list",
      CE._rule_matches(row, {"match_field": "category", "match_op": "in", "match_value": "phones, accessory"}))
check("an UNKNOWN op falls through to equals (so 3 ops is the complete set)",
      CE._rule_matches(row, {"match_field": "category", "match_op": "regex", "match_value": "accessory"})
      and not CE._rule_matches(row, {"match_field": "category", "match_op": "regex", "match_value": "acce"}))
check("offered ops == {equals, contains, in}",
      {o["value"] for o in v["match_ops"]} == set(PO.MATCH_OPS) == {"equals", "contains", "in"})
check("tier bases are the engine's ('' legacy | lines | transactions)",
      {b["value"] for b in v["tier_bases"]} == {"", "lines", "transactions"}
      and CE._tier_basis({"tier_count_basis": "lines"}) == "lines"
      and CE._tier_basis({"tier_count_basis": "transactions"}) == "transactions"
      and CE._tier_basis({"tier_count_basis": ""}) == "rule_units")
check("activation_bucket vocabulary is the classifier's closed set",
      set(PO.SYNTHETIC_VALUES["activation_bucket"]) == {"premium", "upgrade", "byod"})

print("── B. options are this tenant's own observed values, with counts ──")
st = build_store()
cA, pA = opts(st, ORG_A)
check("source is the Postgres aggregate", pA["source"] == "rpc" and pA["source_table"] == "raw_sales")
check("category options are org A's", set(vals(pA, "category")) == {"Home Internet", "Cases", "Devices"},
      vals(pA, "category"))
cases = next(x for x in pA["fields"]["category"]["values"] if x["value"] == "Cases")
check("counts exclude the voided + Return line (7, not 9)", cases["lines"] == 7, cases)
check("product_desc is the only free-text field",
      pA["fields"]["product_desc"]["free_text"] is True
      and pA["fields"]["category"]["free_text"] is False)
check("synthetic fields are CLOSED (no free entry)",
      pA["fields"]["accessory"]["closed"] and pA["fields"]["activation_bucket"]["closed"])
check("'any' has no values", pA["fields"]["any"]["values"] == [])
check("periods list comes from the tenant's own sales", [p["value"] for p in pA["periods"]] == [PER])

print("── C. org isolation (two-org differential) ──")
_, pB = opts(st, ORG_B)
check("org B sees only its own category", vals(pB, "category") == ["BoostCat"], vals(pB, "category"))
check("org A never sees org B's product",
      "Boost Only Product" not in vals(pA, "product_desc"))
check("org B never sees org A's products",
      not set(vals(pB, "product_desc")) & set(vals(pA, "product_desc")))
check("facet rows are disjoint",
      not set(pA["facets"]["dict"]["product_desc"]) & set(pB["facets"]["dict"]["product_desc"]))
check("line totals are per-org (A=16 live, B=11)",
      pA["facets"]["lines_total"] == 16 and pB["facets"]["lines_total"] == 11,
      (pA["facets"]["lines_total"], pB["facets"]["lines_total"]))

print("── D. voided + Return exclusion uses the pay path's own tokens ──")
for tok in VOID_TOKENS:
    st2 = build_store()
    st2["raw_sales"].append(sale(ORG_A, PER, category="ShouldNotAppear", voided=tok.upper()))
    _, p2 = opts(st2, ORG_A)
    check(f"voided='{tok}' excluded", "ShouldNotAppear" not in vals(p2, "category"))
check("VOID_TOKENS mirrored from gp_report", set(VOID_TOKENS) == set(CE._VOID_TOKENS))

print("── E. RPC vs bounded-scan parity (migration 240 not applied) ──")
_, pScan = opts(build_store(), ORG_A, absent_rpc={"plan_match_facets", "plan_match_facet_totals",
                                                  "plan_sales_periods"})
check("scan path is flagged", pScan["source"] == "scan")
for f in ("category", "department", "product_desc", "contract_type", "sku", "tender_type", "trans_type"):
    check(f"scan == rpc for {f}",
          sorted((x["value"], x["lines"]) for x in pScan["fields"][f]["values"])
          == sorted((x["value"], x["lines"]) for x in pA["fields"][f]["values"]),
          f'{pScan["fields"][f]["values"]} vs {pA["fields"][f]["values"]}')
check("scan path still isolates orgs",
      "Boost Only Product" not in vals(pScan, "product_desc"))
check("no plan_sales_periods RPC -> the period picker degrades to free text", pScan["periods"] == [])

print("── F. source precedence: raw_sales wins, feed is the fallback ──")
stF = build_store()
stF["raw_sales"] = [r for r in stF["raw_sales"] if r["org_id"] != ORG_A]
stF["daily_sales_feed"] = [feed_row(ORG_A, PER, department="FeedDept", category="FeedCat",
                                    product_desc="Feed Product") for _ in range(4)]
_, pF = opts(stF, ORG_A)
check("feed used when raw_sales is empty for the window", pF["source_table"] == "feed")
check("feed values surface", vals(pF, "category") == ["FeedCat"])
check("sku is honestly reported as unavailable on the feed",
      pF["fields"]["sku"]["values"] == [] and "no SKU column" in (pF["fields"]["sku"]["note"] or ""))
check("raw_sales still wins when both exist",
      opts(build_store(), ORG_A)[1]["source_table"] == "raw_sales")

print("── G. zero-wipe: a stored value that is no longer in the data still shows ──")
stZ = build_store()
stZ["commission_rule"] = [{"org_id": ORG_A, "match_field": "category", "match_value": "Retired Category"},
                          {"org_id": ORG_B, "match_field": "category", "match_value": "House Only Value"}]
stZ["commission_plan"] = [{"org_id": ORG_A, "base_tier_metric": "gross_profit",
                           "tier_match_field": "department", "tier_match_value": "Legacy Dept"}]
stZ["plan_installment_schedule"] = [{"org_id": ORG_A, "trigger_match_field": "product_desc",
                                     "trigger_match_value": "Discontinued Router"}]
_, pZ = opts(stZ, ORG_A)
stored = [x for x in pZ["fields"]["category"]["values"] if x["value"] == "Retired Category"]
check("stored rule value is offered", len(stored) == 1 and stored[0].get("stored_only") is True)
check("stored value is flagged with 0 lines (not faked)", stored and stored[0]["lines"] == 0)
check("stored TIER matcher value is offered",
      "Legacy Dept" in vals(pZ, "department"))
check("stored INSTALLMENT trigger value is offered",
      "Discontinued Router" in vals(pZ, "product_desc"))
check("stored base_tier_metric is unioned into the metric list",
      "gross_profit" in pZ["vocab"]["tier_metrics"])
check("another org's stored value does NOT leak in",
      "House Only Value" not in vals(pZ, "category"))
check("a live value is not duplicated by the stored merge",
      [x["value"] for x in pZ["fields"]["category"]["values"]].count("Cases") == 1)

print("── H. contract-type resolution (mig 232) ──")
check("default tenant: 'raw', no bucket options, no ct_resolved column",
      pA["contract_type_resolution"] == "raw"
      and pA["facets"]["ct_resolved"] is None
      and not any(x.get("resolved_bucket") for x in pA["fields"]["contract_type"]["values"]))
stM = build_store()
stM["commission_org_config"] = [{"org_id": ORG_A, "plan_ct_resolution": "mapped"}]
stM["accessory_config"] = [{"org_id": ORG_A, "contract_type_map": {"new activation": "premium"},
                            "activation_rules": [{"bucket": "byod", "all_of": []}]}]
_, pM = opts(stM, ORG_A)
buckets = [x for x in pM["fields"]["contract_type"]["values"] if x.get("resolved_bucket")]
check("mapped tenant is offered the resolved buckets", {b["value"] for b in buckets} == {"premium", "upgrade", "byod"})
prem = next(b for b in buckets if b["value"] == "premium")
check("the bucket option carries the real line count (8 New Activation lines)", prem["lines"] == 8, prem)
check("facets carry a ct_resolved column only when mapped", pM["facets"]["ct_resolved"] is not None)
check("house/default tenant is unaffected", pB["facets"]["ct_resolved"] is None)

print("── I. truncation is honest ──")
stT = build_store()
stT["raw_sales"] += [sale(ORG_A, PER, category=f"Cat{i}", product_desc=f"P{i}") for i in range(40)]
_, pT = opts(stT, ORG_A, limit=5)
check("facet list is capped", len(pT["facets"]["rows"]) == 5)
check("truncated flag set", pT["facets"]["truncated"] is True)
check("coverage is reported honestly",
      pT["facets"]["lines_covered"] < pT["facets"]["lines_total"],
      (pT["facets"]["lines_covered"], pT["facets"]["lines_total"]))
check("a truncated field list allows free entry (can't claim to be complete)",
      pT["fields"]["category"]["truncated"] is True)

print("── J. matcher parity (python side) + case grid for the browser mirror ──")
grid_rows = [
    {"department": "Internet", "category": "Home Internet", "contract_type": "New Activation",
     "tender_type": "", "trans_type": "Sale", "product_desc": "Home Internet Gateway", "sku": ""},
    {"department": "Internet", "category": "Home Internet", "contract_type": "",
     "tender_type": "", "trans_type": "Sale", "product_desc": "VHI Home Internet Router", "sku": ""},
    {"department": "Accessories", "category": "Cases", "contract_type": "",
     "tender_type": "Acima", "trans_type": "Sale", "product_desc": "Otterbox Case", "sku": "OTB-1"},
    {"department": "", "category": "", "contract_type": "Upgrade",
     "tender_type": "", "trans_type": "Sale", "product_desc": "Moto G", "sku": "MOTOG-64"},
]
grid_rules = [
    {"match_field": "any", "match_op": "equals", "match_value": ""},
    {"match_field": "category", "match_op": "equals", "match_value": "Home Internet"},
    {"match_field": "category", "match_op": "equals", "match_value": "home internet"},   # case-insensitive
    {"match_field": "category", "match_op": "equals", "match_value": " Cases "},          # trimmed
    {"match_field": "product_desc", "match_op": "contains", "match_value": "home internet"},
    {"match_field": "product_desc", "match_op": "contains", "match_value": "vhi"},
    {"match_field": "product_desc", "match_op": "contains", "match_value": ""},           # empty -> no match
    {"match_field": "sku", "match_op": "in", "match_value": "OTB-1, MOTOG-64"},
    {"match_field": "sku", "match_op": "in", "match_value": ""},
    {"match_field": "contract_type", "match_op": "equals", "match_value": ""},            # blank matches blank
    {"match_field": "tender_type", "match_op": "equals", "match_value": "acima"},
    {"match_field": "trans_type", "match_op": "equals", "match_value": "Sale"},
    {"match_field": "department", "match_op": "regex", "match_value": "Internet"},        # unknown op = equals
    {"match_field": "accessory", "match_op": "equals", "match_value": "yes"},             # synthetic
]
cases, mismatches = [], 0
for ri, r in enumerate(grid_rows):
    for qi, q in enumerate(grid_rules):
        expect = PO.facet_matches(r, q)
        direct = CE._rule_matches(dict(r), q) if q["match_field"] in PO.FACET_COLUMNS or q["match_field"] == "any" else None
        if expect != direct:
            mismatches += 1
        cases.append({"row": r, "rule": q, "expect": expect})
check("facet_matches == commission_engine._rule_matches on every analysable case", mismatches == 0)
check("synthetic field is reported as NOT analysable (never a wrong warning)",
      PO.facet_matches(grid_rows[0], {"match_field": "accessory", "match_op": "equals", "match_value": "yes"}) is None)
# mapped-mode candidate: a contract_type rule ALSO matches the resolved bucket
check("mapped ct candidate mirrors the engine",
      PO.facet_matches(grid_rows[0], {"match_field": "contract_type", "match_op": "equals",
                                      "match_value": "premium"}, ct_resolved="premium") is True
      and PO.facet_matches(grid_rows[0], {"match_field": "contract_type", "match_op": "equals",
                                          "match_value": "premium"}) is False)
_grid_path = os.path.join(os.path.dirname(__file__), "plan_match_cases.json")
with open(_grid_path, "w") as fh:
    json.dump({"columns": list(PO.FACET_COLUMNS), "cases": cases}, fh, indent=1)
check("case grid written for the browser mirror proof", os.path.exists(_grid_path))

print("── K. cache is TTL-bounded and ALWAYS org-keyed ──")
AC.invalidate()
c1 = FakeClient(build_store())
p1 = PO.build(c1, ORG_A, period=PER)
p1b = PO.build(c1, ORG_A, period=PER)
check("second call for the same org+window is a cache hit", p1b is p1)
p2 = PO.build(c1, ORG_B, period=PER)
check("a different ORG never gets the cached payload", p2 is not p1 and vals(p2, "category") == ["BoostCat"])
p3 = PO.build(c1, ORG_A, period=PER, months=6)
check("a different WINDOW is a different key", p3 is not p1)
AC.invalidate(ORG_A)
p4 = PO.build(c1, ORG_A, period=PER)
check("invalidate drops the org's entry", p4 is not p1)
keys, _stats, _gen = AC.cache_snapshot()
check("every cache key carries an org", all(k[1] for k in keys))

print("── L. degradation ──")
stEmpty = {"raw_sales": [], "daily_sales_feed": []}
_, pE = opts(stEmpty, ORG_A, absent={"commission_rule", "commission_plan", "plan_installment_schedule",
                                     "commission_org_config", "accessory_config"},
             absent_rpc={"plan_match_facets", "plan_match_facet_totals", "plan_sales_periods"})
check("no data + no migration 240 + no config tables -> still a usable payload",
      pE["ready"] is True and pE["facets"]["rows"] == [] and pE["note"])
check("vocabulary is still complete in that state",
      {f["value"] for f in pE["vocab"]["match_fields"]} == set(CE.MATCH_FIELDS))
check("window covers the last 3 months + the previewed period",
      PER in pE["window"]["labels"] and len(pE["window"]["labels"]) >= 3)
check("every queried spelling passes through the engine's _pvariants",
      all(any(s in CE._pvariants(lab) for lab in pE["window"]["labels"])
          for s in pE["window"]["periods_queried"]))

print("── M. the HTTP endpoint itself (FastAPI, org_id as a QUERY PARAM per contract §2) ──")
import warnings
warnings.filterwarnings("ignore")
from fastapi import FastAPI                                    # noqa: E402
from fastapi.testclient import TestClient                      # noqa: E402
import app.modules.commcalc.router as R                        # noqa: E402

_http_store = build_store()
_http_client = FakeClient(_http_store)
R.sb = lambda: _http_client                                    # only this harness process
_app = FastAPI()
_app.include_router(R.router, prefix="/api/v1")
_tc = TestClient(_app)
AC.invalidate()
rA = _tc.get(f"/api/v1/commcalc/plan-field-options?months=3&period={PER}&org_id={ORG_A}")
rB = _tc.get(f"/api/v1/commcalc/plan-field-options?months=3&period={PER}&org_id={ORG_B}")
check("GET /commcalc/plan-field-options -> 200", rA.status_code == 200 and rB.status_code == 200,
      (rA.status_code, rB.status_code))
jA, jB = rA.json(), rB.json()
check("HTTP two-org differential: A's values never appear in B's payload",
      [x["value"] for x in jA["fields"]["category"]["values"]] != [x["value"] for x in jB["fields"]["category"]["values"]]
      and "Boost Only Product" not in [x["value"] for x in jA["fields"]["product_desc"]["values"]]
      and "Otterbox Case" not in [x["value"] for x in jB["fields"]["product_desc"]["values"]])
_spec = _tc.get("/openapi.json").json()["paths"]["/api/v1/commcalc/plan-field-options"]["get"]
check("org_id is a QUERY PARAM (contract §2), never a body/Form field",
      any(pm.get("name") == "org_id" and pm.get("in") == "query" for pm in _spec.get("parameters", []))
      and "requestBody" not in _spec,
      _spec.get("parameters"))
check("blank org_id is refused (require_org)",
      _tc.get("/api/v1/commcalc/plan-field-options?org_id=").status_code == 400)
def _keys(o, acc=None):
    acc = acc if acc is not None else set()
    if isinstance(o, dict):
        for k, vv in o.items():
            acc.add(k)
            _keys(vv, acc)
    elif isinstance(o, list):
        for vv in o:
            _keys(vv, acc)
    return acc


check("the payload carries NO rep / customer / money field (options only, never sale lines)",
      not (_keys(jA) & {"salesperson", "rep", "customer", "email", "mdn", "ext_price", "gp",
                        "total_payout", "trans_id", "amount", "pct"}),
      sorted(_keys(jA) & {"salesperson", "rep", "customer", "email", "mdn", "ext_price", "gp",
                          "total_payout", "trans_id", "amount", "pct"}))


class _Boom(FakeClient):
    def schema(self, s):
        raise Exception("database on fire")


R.sb = lambda: _Boom({})
AC.invalidate()
rD = _tc.get(f"/api/v1/commcalc/plan-field-options?org_id={ORG_A}")
jD = rD.json()
check("an unreadable database degrades to empty pickers + free text instead of a 500",
      rD.status_code == 200 and jD["degraded"] is True
      and all(not jD["fields"][f]["values"] for f in PO.FACET_COLUMNS)
      and "could not be read" in (jD["note"] or ""),
      (rD.status_code, jD.get("degraded"), jD.get("note")))
check("the vocabulary still comes through in that state (every dropdown keeps working)",
      {f["value"] for f in jD["vocab"]["match_fields"]} == set(CE.MATCH_FIELDS))

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
