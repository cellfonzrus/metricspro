"""Proof for agent/commission/productivity-module — pure compute + org-scoped integration + the INERT
commission tie-in byte-identity. No live DB.

Run:  cd backend && python3 scratchpad/productivity_proof.py

Parts:
  A  registry round-trip: defaults, edit override, add-from-catalog custom, disable, delete-a-default
     (hidden), delete custom (absence), deterministic order.
  B  attainment + weighted_score: absolute vs relative mode, missing value → None (n/a, excluded, never
     a divide-by-zero / a 0 that tanks a score), weight-normalization, met flag.
  C  compute_rankings: only count_in_stack_ranker items, RELATIVE field-max, deterministic competition
     ranking + name tiebreak, disabled excluded, per-metric explainable breakdown.
  D  compute_review: only count_in_review items, missing-source item shows n/a and is EXCLUDED from the
     total (never zeroes the review), weighted total.
  E  compute_productivity: boxes/hr, acc$/hr, store baseline, index vs baseline, ZERO-HOURS guard (sales
     but no punches → None, not a crash), hours-only rep at a selling store → 0 output, store totals.
  F  perf_kpi_value: performance_score, perf:<item_key>, unknown → None.
  G  integration through the REAL router (_prod_gather / endpoints) over an org-aware FakeClient with a
     TWO-TENANT fixture: hours×sales join, store baseline, zero-hours, store-upkeep derivation, targets
     attainment (store→rep), KPI attainment, and ORG ISOLATION (A never leaks into B and vice-versa);
     plus config round-trip + isolation through the PUT/DELETE/reset endpoints.
  H  COMMISSION BYTE-IDENTITY (tie-in inertness): commission_engine.preview is byte-identical with the
     productivity tables present+populated vs absent; + structural inertness (no calc engine imports it).
"""
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.modules.commcalc import productivity as P          # noqa: E402
from app.modules.commcalc import router                      # noqa: E402
from app.modules.commcalc import commission_engine           # noqa: E402

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def approx(a, b, eps=0.05):
    return a is not None and b is not None and abs(a - b) <= eps


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("A — registry round-trip")
defaults = P.resolve_registry([])
dkeys = {d["item_key"] for d in defaults}
check("A1 defaults expose 9 seeded placeholders", len(defaults) == 9)
check("A2 ranking + review seeds present",
      {"acc_sales", "activations", "upgrades", "swaps", "boxes"} <= dkeys and
      {"targets_achieved", "kpi_achieved", "accessory_sales", "store_upkeep"} <= dkeys)
check("A3 default ranking items count_in_stack_ranker, review items count_in_review",
      next(d for d in defaults if d["item_key"] == "boxes")["count_in_stack_ranker"] is True and
      next(d for d in defaults if d["item_key"] == "store_upkeep")["count_in_review"] is True)
# edit override — change boxes weight + enable in review
edited = P.resolve_registry([{"item_key": "boxes", "weight": 3, "count_in_review": True}])
b = next(d for d in edited if d["item_key"] == "boxes")
check("A4 edit override applies (weight 3, review on)", b["weight"] == 3.0 and b["count_in_review"] is True)
check("A5 edit override keeps other default fields (source_key)", b["source_key"] == "boxes")
# add-from-catalog custom item
added = P.resolve_registry([{"item_key": "my_hours", "label": "My Hours", "source_key": "hours_worked",
                             "standard": 160, "count_in_review": True, "is_seed_default": False}])
check("A6 custom item added (10 items)", len(added) == 10 and any(d["item_key"] == "my_hours" for d in added))
check("A7 custom item is_seed_default False",
      next(d for d in added if d["item_key"] == "my_hours").get("is_seed_default") is False)
# disable a default (still listed, enabled False)
dis = P.resolve_registry([{"item_key": "swaps", "enabled": False}])
check("A8 disabled default still listed but enabled False",
      next(d for d in dis if d["item_key"] == "swaps")["enabled"] is False)
# delete a default → hidden → dropped
hid = P.resolve_registry([{"item_key": "swaps", "hidden": True}])
check("A9 hidden (deleted) default dropped from registry", not any(d["item_key"] == "swaps" for d in hid))
check("A10 deterministic order by sort then key",
      [d["item_key"] for d in defaults] == sorted(dkeys, key=lambda k: (
          next(x["sort"] for x in defaults if x["item_key"] == k), k)))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("B — attainment + weighted_score")
check("B1 absolute attainment value/standard", approx(P.attainment(80, 100), 0.8))
check("B2 relative attainment value/field_max", approx(P.attainment(50, None, 200), 0.25))
check("B3 standard preferred over field_max", approx(P.attainment(50, 100, 200), 0.5))
check("B4 missing value → None", P.attainment(None, 100, 200) is None)
check("B5 zero standard falls back to field_max", approx(P.attainment(50, 0, 200), 0.25))
check("B6 no standard and no field_max → None (no divide-by-zero)", P.attainment(50, None, 0) is None)
items = [{"item_key": "a", "source_key": "sa", "standard": 100, "standard_type": "number", "weight": 1,
          "enabled": True},
         {"item_key": "b", "source_key": "sb", "standard": 50, "standard_type": "number", "weight": 3,
          "enabled": True}]
score, bd = P.weighted_score(items, {"sa": 50, "sb": 50}, {})
# a: 0.5*1, b: 1.0*3 → (0.5+3)/(1+3)=0.875 → 87.5
check("B7 weight-normalized score", approx(score, 87.5))
check("B8 met flag correct", bd[0]["met"] is False and bd[1]["met"] is True)
# missing source value → that item n/a, EXCLUDED (does not zero the score)
score2, bd2 = P.weighted_score(items, {"sa": None, "sb": 50}, {})
check("B9 n/a item excluded from score (not a 0 that tanks it)", approx(score2, 100.0))
check("B10 n/a item flagged na", bd2[0]["na"] is True and bd2[0]["attainment"] is None)
check("B11 all-missing → score None (not 0)", P.weighted_score(items, {}, {})[0] is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("C — compute_rankings")
reg = P.resolve_registry([])   # ranking items: acc_sales, activations, upgrades, swaps, boxes (relative)
prv = {
    "JANE": {"acc_sales": 100, "activations": 10, "upgrades": 4, "swaps": 2, "boxes": 20, "_label": "Jane"},
    "JOHN": {"acc_sales": 50, "activations": 5, "upgrades": 2, "swaps": 1, "boxes": 10, "_label": "John"},
    "MARY": {"acc_sales": 100, "activations": 10, "upgrades": 4, "swaps": 2, "boxes": 20, "_label": "Mary"},
}
rk = P.compute_rankings(reg, prv)
srcs = {b["source_key"] for r in rk["rows"] for b in r["breakdown"]}
check("C1 ranker uses ONLY count_in_stack_ranker sources",
      srcs == {"acc_sales", "activations", "upgrades", "swaps", "boxes"})
check("C2 no review-only source (store_upkeep) in ranker", "store_upkeep" not in srcs)
jane = next(r for r in rk["rows"] if r["rep_key"] == "JANE")
john = next(r for r in rk["rows"] if r["rep_key"] == "JOHN")
check("C3 relative attainment: field leader scores 100", approx(jane["score"], 100.0))
check("C4 half-of-leader scores 50", approx(john["score"], 50.0))
check("C5 Jane & Mary tie on score", approx(jane["score"], next(r for r in rk["rows"] if r["rep_key"] == "MARY")["score"]))
check("C6 competition ranking — tie shares rank 1, next is rank 3",
      [r["rank"] for r in rk["rows"]] == [1, 1, 3])
check("C7 deterministic tiebreak by name (Jane before Mary)",
      [r["rep_key"] for r in rk["rows"][:2]] == ["JANE", "MARY"])
# disabled ranking item excluded
reg_dis = P.resolve_registry([{"item_key": "boxes", "enabled": False}])
rk2 = P.compute_rankings(reg_dis, prv)
check("C8 disabled item excluded from ranker",
      "boxes" not in {b["source_key"] for r in rk2["rows"] for b in r["breakdown"]})
check("C9 per-metric breakdown present (explainability)", len(jane["breakdown"]) == 5)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("D — compute_review")
rv = P.compute_review(reg, {
    "JANE": {"acc_sales": 1200, "targets_attainment": 90, "kpi_attainment": 100, "store_upkeep": 45, "_label": "Jane"},
    "JOHN": {"acc_sales": 800, "targets_attainment": 50, "kpi_attainment": None, "store_upkeep": None, "_label": "John"},
})
rsrcs = {b["source_key"] for r in rv["rows"] for b in r["items"]}
check("D1 review uses ONLY count_in_review sources",
      rsrcs == {"acc_sales", "targets_attainment", "kpi_attainment", "store_upkeep"})
john = next(r for r in rv["rows"] if r["rep_key"] == "JOHN")
na = [b for b in john["items"] if b["na"]]
check("D2 John's missing kpi + upkeep marked n/a", len(na) == 2)
# John: accessory_sales 800/1000=0.8, targets 50/100=0.5 → (0.8+0.5)/2 = 0.65 → 65.0 (n/a excluded)
check("D3 n/a items EXCLUDED from review total (not a 0 that tanks it)", approx(john["review_score"], 65.0))
jane = next(r for r in rv["rows"] if r["rep_key"] == "JANE")
# Jane: acc 1200/1000=1.2, targets .9, kpi 1.0, upkeep 45/90=0.5 → mean = (1.2+.9+1.0+.5)/4 = 0.9 → 90.0
check("D4 all-present review total weighted correctly", approx(jane["review_score"], 90.0))
check("D5 review rows sorted score desc", rv["rows"][0]["rep_key"] == "JANE")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("E — compute_productivity (Feature 1 + zero-hours guard)")
store_reps = {
    ("S1", "JANE"): {"store_label": "1 Main St", "market": "North", "rep_label": "Jane",
                     "boxes": 2, "acc_sales": 50, "activations": 1, "upgrades": 1, "swaps": 0, "txns": 3},
    ("S1", "MARY"): {"store_label": "1 Main St", "market": "North", "rep_label": "Mary",
                     "boxes": 1, "acc_sales": 0, "activations": 1, "upgrades": 0, "swaps": 0, "txns": 1},
    ("S1", "KEN"):  {"store_label": "1 Main St", "market": "North", "rep_label": "Ken",
                     "boxes": 0, "acc_sales": 0, "activations": 0, "upgrades": 0, "swaps": 0, "txns": 0},
    ("S2", "JOHN"): {"store_label": "2 Oak Ave", "market": "South", "rep_label": "John",
                     "boxes": 1, "acc_sales": 30, "activations": 0, "upgrades": 0, "swaps": 0, "txns": 2},
}
hours = {("S1", "JANE"): 15.0, ("S1", "KEN"): 4.0, ("S2", "JOHN"): 8.0}   # MARY has NO punch
pr = P.compute_productivity(store_reps, hours)
s1 = next(s for s in pr["stores"] if s["store_code"] == "S1")
check("E1 store S1 hours = 15+4+0 = 19", approx(s1["store_hours"], 19.0))
check("E2 store S1 boxes = 3", approx(s1["store_boxes"], 3.0))
check("E3 store baseline boxes/hr = 3/19", approx(s1["store_boxes_per_hr"], 3 / 19))
janer = next(r for r in s1["reps"] if r["rep_key"] == "JANE")
check("E4 Jane boxes/hr = 2/15", approx(janer["boxes_per_hr"], 2 / 15))
check("E5 Jane index vs store baseline", approx(janer["boxes_index"], (2 / 15) / (3 / 19)))
maryr = next(r for r in s1["reps"] if r["rep_key"] == "MARY")
check("E6 ZERO-HOURS guard: Mary boxes/hr None (no divide-by-zero)", maryr["boxes_per_hr"] is None)
check("E7 Mary flagged no_hours + index None", maryr["no_hours"] is True and maryr["boxes_index"] is None)
kenr = next(r for r in s1["reps"] if r["rep_key"] == "KEN")
check("E8 hours-only rep Ken present with 0 output, rate 0.0", kenr["boxes"] == 0 and kenr["boxes_per_hr"] == 0.0)
check("E9 totals boxes/acc/hours", approx(pr["totals"]["boxes"], 4) and approx(pr["totals"]["hours"], 27))
# a store with sales but zero hours → baseline None, not a crash
pr0 = P.compute_productivity({("SX", "AL"): {"store_label": "X", "rep_label": "Al", "boxes": 5, "acc_sales": 9}},
                             {})
check("E10 store with zero hours → baseline None (no crash)",
      pr0["stores"][0]["store_boxes_per_hr"] is None and pr0["stores"][0]["reps"][0]["no_hours"] is True)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("F — perf_kpi_value (tie-in resolver)")
rvrow = P.compute_review(reg, {"JANE": {"acc_sales": 1200, "targets_attainment": 90, "kpi_attainment": 100,
                                        "store_upkeep": 90, "_label": "Jane"}})["rows"][0]
check("F1 performance_score resolves to review score",
      approx(P.perf_kpi_value("performance_score", rvrow["review_score"], rvrow["items"]), rvrow["review_score"]))
check("F2 perf:<item> resolves to that item's attainment %",
      P.perf_kpi_value("perf:targets_achieved", rvrow["review_score"], rvrow["items"]) is not None)
check("F3 unknown key → None", P.perf_kpi_value("perf:nope", rvrow["review_score"], rvrow["items"]) is None)
kk = P.perf_kpi_keys(reg)
check("F4 kpi_keys expose performance_score + one perf:<item> per review item",
      any(k["kpi_key"] == "performance_score" for k in kk) and
      any(k["kpi_key"].startswith("perf:") for k in kk))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# Org-aware in-memory Supabase double (enforces eq/in_/gte/lt so isolation is REAL) + writes.
class _Resp:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class _Q:
    def __init__(self, client, table):
        self.c = client
        self.table = table
        self.f = []
        self.count_exact = False
        self.mode = "select"
        self.payload = None
        self.conflict = None
        self.rng = None

    def select(self, *a, **k):
        if k.get("count"):
            self.count_exact = True
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v)); return self

    def neq(self, c, v):
        self.f.append(("neq", c, v)); return self

    def in_(self, c, v):
        self.f.append(("in", c, set(v))); return self

    def gte(self, c, v):
        self.f.append(("gte", c, v)); return self

    def gt(self, c, v):
        self.f.append(("gt", c, v)); return self

    def lt(self, c, v):
        self.f.append(("lt", c, v)); return self

    def lte(self, c, v):
        self.f.append(("lte", c, v)); return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def range(self, s, e):
        self.rng = (s, e); return self

    def insert(self, payload):
        self.mode = "insert"; self.payload = payload; return self

    def upsert(self, payload, on_conflict=None):
        self.mode = "upsert"; self.payload = payload; self.conflict = on_conflict; return self

    def delete(self):
        self.mode = "delete"; return self

    def _key(self):
        return (self.c.schema_name, self.table)

    def _match(self, r):
        for op, c, v in self.f:
            rv = r.get(c)
            if op == "eq" and rv != v:
                return False
            if op == "neq" and rv == v:
                return False
            if op == "in" and rv not in v:
                return False
            if op == "gte" and not (rv is not None and str(rv) >= str(v)):
                return False
            if op == "gt" and not (rv is not None and str(rv) > str(v)):
                return False
            if op == "lt" and not (rv is not None and str(rv) < str(v)):
                return False
            if op == "lte" and not (rv is not None and str(rv) <= str(v)):
                return False
        return True

    def execute(self):
        store = self.c.tables.setdefault(self._key(), [])
        if self.mode == "select":
            rows = [r for r in store if self._match(r)]
            if self.rng:
                rows = rows[self.rng[0]:self.rng[1] + 1]
            if self.count_exact:
                return _Resp(data=rows[:1], count=len(rows))
            return _Resp(data=[dict(r) for r in rows])
        if self.mode in ("insert", "upsert"):
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            keys = [k.strip() for k in (self.conflict or "").split(",") if k.strip()]
            for row in payload:
                if self.mode == "upsert" and keys:
                    match = next((r for r in store if all(r.get(k) == row.get(k) for k in keys)), None)
                    if match:
                        match.update(row)
                        continue
                store.append(dict(row))
            return _Resp(data=[dict(r) for r in payload])
        if self.mode == "delete":
            keep = [r for r in store if not self._match(r)]
            self.c.tables[self._key()] = keep
            return _Resp(data=[])
        return _Resp(data=[])


class FakeClient:
    def __init__(self, tables):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.schema_name = "public"

    def schema(self, s):
        self.schema_name = s
        return self

    def table(self, t):
        return _Q(self, t)


def _sales(org, rep, store, tid, dept, ct, ext, pd="", cat=""):
    return {"org_id": org, "period": "May 2026", "trans_date": "2026-05-10", "trans_id": tid,
            "store": store, "salesperson": rep, "department": dept, "category": cat, "product_desc": pd,
            "contract_type": ct, "ext_price": ext, "gp": ext, "voided": "", "trans_type": ""}


def _tl(org, name, code, hours, work_date="2026-05-10", closed=True):
    return {"org_id": org, "employee_name": name, "store_code": code, "hours": hours,
            "clock_out": ("2026-05-10T18:00:00" if closed else None), "work_date": work_date}


PERIOD = "May 2026"   # closed month → raw_sales leads (no feed needed)

TABLES = {
    ("commcalc", "store_mapping"): [
        {"org_id": "A", "store_code": "S1", "store_address": "1 Main St", "market": "North"},
        {"org_id": "A", "store_code": "S2", "store_address": "2 Oak Ave", "market": "South"},
        {"org_id": "B", "store_code": "BZ", "store_address": "9 Beta Rd", "market": "West"},
    ],
    ("commcalc", "raw_sales"): [
        _sales("A", "Jane", "1 Main St", "T1", "IPHONE - XP", "Activation", 100, "iphone"),
        _sales("A", "Jane", "1 Main St", "T2", "Ondigo", "", 50, "case", "Accessory"),
        _sales("A", "Jane", "1 Main St", "T3", "IPHONE - XP", "Upgrade", 80, "iphone"),
        _sales("A", "John", "2 Oak Ave", "T4", "Android - XP", "BYOD", 0, "byod phone"),
        _sales("A", "John", "2 Oak Ave", "T5", "Ondigo", "", 30, "screen"),
        _sales("A", "Mary", "1 Main St", "T6", "IPHONE - XP", "Activation", 60, "iphone"),
        _sales("B", "Zack", "9 Beta Rd", "TB1", "IPHONE - XP", "Activation", 200, "iphone"),
        _sales("B", "Zack", "9 Beta Rd", "TB2", "Ondigo", "", 25, "case", "Accessory"),
    ],
    ("commcalc", "daily_sales_feed"): [],
    ("storeops", "timelog"): [
        _tl("A", "Jane", "S1", 10.0), _tl("A", "Jane", "S1", 5.0),
        _tl("A", "Jane", "S1", 3.0, closed=False),          # OPEN punch → excluded
        _tl("A", "Ken", "S1", 4.0),                          # hours only, no sales
        _tl("A", "John", "S2", 8.0),
        _tl("B", "Zack", "BZ", 20.0),
    ],
    ("storeops", "stores"): [
        {"org_id": "A", "address": "1 Main St"}, {"org_id": "A", "address": "2 Oak Ave"},
        {"org_id": "B", "address": "9 Beta Rd"},
    ],
    ("commcalc", "targets"): [
        {"org_id": "A", "period": "May 2026", "store_code": "S1", "activations_monthly": 4},
    ],
    ("storeops", "store_visits"): [
        {"org_id": "A", "id": "v1", "store_code": "S1", "status": "submitted", "check_in_at": "2026-05-05T10:00:00"},
        {"org_id": "A", "id": "v2", "store_code": "S1", "status": "submitted", "check_in_at": "2026-05-20T10:00:00"},
        {"org_id": "B", "id": "vb", "store_code": "BZ", "status": "submitted", "check_in_at": "2026-05-06T10:00:00"},
    ],
    ("storeops", "store_visit_responses"): [
        {"org_id": "A", "visit_id": "v1", "checked": True}, {"org_id": "A", "visit_id": "v1", "checked": False},
        {"org_id": "A", "visit_id": "v1", "checked": False}, {"org_id": "A", "visit_id": "v1", "checked": False},  # v1: 1/4 = 25? -> set to 2/4
        {"org_id": "A", "visit_id": "v2", "checked": True}, {"org_id": "A", "visit_id": "v2", "checked": True},
        {"org_id": "A", "visit_id": "v2", "checked": True}, {"org_id": "A", "visit_id": "v2", "checked": True},   # v2: 4/4 = 100
        {"org_id": "B", "visit_id": "vb", "checked": True},
    ],
    ("commcalc", "rep_commissions"): [
        {"org_id": "A", "period": "May 2026", "salesperson": "Jane", "kpi_values": {"atu": 60, "protect": 90,
         "byod": 40, "familyplan": 50, "tmr3": 80, "aal": 6}},
        {"org_id": "A", "period": "May 2026", "salesperson": "John", "kpi_values": {"atu": 50, "protect": 70, "byod": 30}},
    ],
    ("commcalc", "payout_config"): [],
    ("commcalc", "productivity_item"): [],
    # accessory-config sources — empty → resolver falls back to the ['Ondigo'] department default
    ("commcalc", "accessory_config"): [], ("commcalc", "flag_rules"): [], ("commcalc", "gp_category_map"): [],
    ("commcalc", "name_map"): [], ("commcalc", "rep_aliases"): [],
}
# fix v1 to 2/4 checked (adjust one response above): make second response checked True → 2 checked / 4
for r in TABLES[("storeops", "store_visit_responses")]:
    pass
# rewrite v1 responses cleanly: 2 checked, 2 unchecked
TABLES[("storeops", "store_visit_responses")] = [
    {"org_id": "A", "visit_id": "v1", "checked": True}, {"org_id": "A", "visit_id": "v1", "checked": True},
    {"org_id": "A", "visit_id": "v1", "checked": False}, {"org_id": "A", "visit_id": "v1", "checked": False},  # 2/4 = 50
    {"org_id": "A", "visit_id": "v2", "checked": True}, {"org_id": "A", "visit_id": "v2", "checked": True},
    {"org_id": "A", "visit_id": "v2", "checked": True}, {"org_id": "A", "visit_id": "v2", "checked": True},    # 4/4 = 100
    {"org_id": "B", "visit_id": "vb", "checked": True},
]

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("G — integration through the REAL router over a two-tenant FakeClient")
fake = FakeClient(TABLES)
router.sb = lambda: fake   # config endpoints call sb()

gA = router._prod_gather(fake, "A", PERIOD)
prvA = gA["per_rep_values"]
check("G1 tenant A reps = {JANE, JOHN, MARY, KEN}", set(prvA.keys()) == {"JANE", "JOHN", "MARY", "KEN"})
check("G2 ORG ISOLATION — tenant B's rep ZACK absent from A", "ZACK" not in prvA)
check("G3 hours×sales join: Jane hours = 10+5 (open punch excluded)", approx(prvA["JANE"]["hours_worked"], 15.0))
check("G4 Jane sales aggregated: boxes 2, acc 50, activations 1, upgrades 1",
      prvA["JANE"]["boxes"] == 2 and approx(prvA["JANE"]["acc_sales"], 50) and
      prvA["JANE"]["activations"] == 1 and prvA["JANE"]["upgrades"] == 1)
check("G5 Jane boxes/hr = 2/15", approx(prvA["JANE"]["boxes_per_hour"], 2 / 15))
check("G6 ZERO-HOURS: Mary has sales, no punch → boxes_per_hour None",
      prvA["MARY"]["hours_worked"] == 0 and prvA["MARY"]["boxes_per_hour"] is None)
check("G7 hours-only rep Ken present (0 output, 4 hrs)",
      approx(prvA["KEN"]["hours_worked"], 4.0) and prvA["KEN"]["boxes"] == 0)
check("G8 store-upkeep derivation: S1 avg pass-rate (50 + 100)/2 = 75 applied to S1 reps",
      approx(prvA["JANE"]["store_upkeep"], 75.0) and approx(prvA["MARY"]["store_upkeep"], 75.0))
check("G9 upkeep n/a for S2 rep (no visit)", prvA["JOHN"]["store_upkeep"] is None)
check("G10 targets attainment (store→rep) shared by S1 reps, n/a for S2",
      prvA["JANE"]["targets_attainment"] is not None and
      prvA["JANE"]["targets_attainment"] == prvA["MARY"]["targets_attainment"] and
      prvA["JOHN"]["targets_attainment"] is None)
check("G11 KPI attainment per rep: Jane 100%, John 0%",
      approx(prvA["JANE"]["kpi_attainment"], 100.0) and approx(prvA["JOHN"]["kpi_attainment"], 0.0))
check("G12 per-rep kpi_atu surfaced from rep_commissions", approx(prvA["JANE"]["kpi_atu"], 60.0))

# Feature-1 endpoint over the fixture
prodA = router.get_productivity(PERIOD, org_id="A", stores=None, markets=None, reps=None)
s1 = next(s for s in prodA["stores"] if s["store_code"] == "S1")
check("G13 Feature-1 store S1 baseline boxes/hr = 3/19", approx(s1["store_boxes_per_hr"], 3 / 19))
check("G14 Feature-1 Jane index vs S1 baseline",
      approx(next(r for r in s1["reps"] if r["rep_key"] == "JANE")["boxes_index"], (2 / 15) / (3 / 19)))
check("G15 Feature-1 filters options pick-don't-type from real data",
      "1 Main St" in prodA["filters"]["stores"] and "North" in prodA["filters"]["markets"])

# store filter (RULE FIVE, server-side)
prodF = router.get_productivity(PERIOD, org_id="A", stores=["2 Oak Ave"], markets=None, reps=None)
check("G16 store filter restricts to S2 only",
      [s["store_code"] for s in prodF["stores"]] == ["S2"])

# rankings + review endpoints
rkA = router.get_productivity_rankings(PERIOD, org_id="A", stores=None, markets=None, reps=None)
check("G17 rankings ranked, Jane present, deterministic ranks",
      rkA["rows"][0]["rank"] == 1 and any(r["rep_key"] == "JANE" for r in rkA["rows"]))
rvA = router.get_productivity_review(PERIOD, org_id="A", stores=None, markets=None, reps=None)
check("G18 review scorecard per rep with per-item attainment",
      all("items" in r for r in rvA["rows"]) and any(r["rep_key"] == "JANE" for r in rvA["rows"]))

# tenant B isolation on the endpoints
gB = router._prod_gather(fake, "B", PERIOD)
check("G19 tenant B reps = {ZACK} only (A absent)", set(gB["per_rep_values"].keys()) == {"ZACK"})
check("G20 tenant B never sees A's stores", all("Main" not in s and "Oak" not in s
      for r in gB["per_rep_values"].values() for s in r["_stores"]))

# config round-trip + isolation via the endpoints
router.put_productivity_config({"item_key": "my_hours", "label": "My Hours", "source_key": "hours_worked",
                                "standard": 160, "count_in_review": True}, org_id="A")
router.put_productivity_config({"item_key": "boxes", "weight": 3}, org_id="A")
router.delete_productivity_config("swaps", org_id="A")
cfgA = router.get_productivity_config(org_id="A")
akeys = {i["item_key"] for i in cfgA["items"]}
check("G21 config: custom item persisted", "my_hours" in akeys)
check("G22 config: default edit persisted (boxes weight 3)",
      next(i for i in cfgA["items"] if i["item_key"] == "boxes")["weight"] == 3.0)
check("G23 config: deleted default hidden", "swaps" not in akeys)
cfgB = router.get_productivity_config(org_id="B")
check("G24 config ISOLATION: A's custom item absent for B",
      "my_hours" not in {i["item_key"] for i in cfgB["items"]} and
      next(i for i in cfgB["items"] if i["item_key"] == "boxes")["weight"] == 1.0)
router.reset_productivity_config(org_id="A")
check("G25 reset restores code defaults", {i["item_key"] for i in router.get_productivity_config(org_id="A")["items"]}
      == {d["item_key"] for d in P.DEFAULT_ITEMS})
# kpi-values endpoint (inert tie-in surface)
kv = router.get_productivity_kpi_values(PERIOD, org_id="A", stores=None, markets=None, reps=None)
check("G26 kpi-values endpoint returns inert flag + performance_score per rep",
      kv["inert"] is True and all("performance_score" in r["values"] for r in kv["rows"]))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("H — commission BYTE-IDENTITY (tie-in is INERT)")
COMMON = {
    ("commcalc", "commission_plan"): [
        {"org_id": "A", "id": "p1", "name": "Base", "is_active": True, "base_tier_metric": "none"}],
    ("commcalc", "commission_rule"): [
        {"org_id": "A", "id": "r1", "plan_id": "p1", "label": "Flat/line", "payout_kind": "flat_per_unit",
         "amount": 5, "match_field": "any", "qualifies": True, "tiered": False, "sort": 0}],
    ("commcalc", "commission_tier"): [],
    ("commcalc", "commission_plan_assignment"): [
        {"org_id": "A", "id": "a1", "plan_id": "p1", "scope": "default", "scope_value": "", "priority": 0}],
    ("commcalc", "raw_sales"): [
        _sales("A", "Jane", "1 Main St", "T1", "IPHONE - XP", "Activation", 100, "iphone"),
        _sales("A", "Jane", "1 Main St", "T3", "IPHONE - XP", "Upgrade", 80, "iphone"),
        _sales("A", "John", "2 Oak Ave", "T4", "Android - XP", "BYOD", 0, "byod phone")],
    ("commcalc", "raw_mi"): [], ("commcalc", "raw_catalog"): [],
    ("commcalc", "store_mapping"): [
        {"org_id": "A", "store_code": "S1", "store_address": "1 Main St", "market": "North"}],
    ("commcalc", "daily_sales_feed"): [],
}
base = commission_engine.preview(FakeClient(COMMON), "A", PERIOD)
# now the SAME tenant WITH the productivity module present + populated (registry + a computed score world)
withprod = dict(COMMON)
withprod[("commcalc", "productivity_item")] = [
    {"org_id": "A", "item_key": "boxes", "weight": 9, "count_in_stack_ranker": True},
    {"org_id": "A", "item_key": "perf_custom", "source_key": "acc_sales", "count_in_review": True, "standard": 1}]
after = commission_engine.preview(FakeClient(withprod), "A", PERIOD)
check("H1 commission preview pays (fixture is non-trivial)", base["totals"]["payout"] > 0)
check("H2 BYTE-IDENTICAL commission output with productivity tables present+populated",
      json.dumps(base, sort_keys=True) == json.dumps(after, sort_keys=True))
eng_src = open(os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc",
                            "commission_engine.py")).read()
calc_src = open(os.path.join(os.path.dirname(__file__), "..", "app", "modules", "commcalc",
                             "calculator.py")).read()
check("H3 structural inertness: commission_engine.py never imports/reads productivity",
      "productivity" not in eng_src)
check("H4 structural inertness: calculator.py never imports/reads productivity",
      "productivity" not in calc_src)

print(f"\n{'='*70}\n  {PASS} passed, {FAIL} failed  ({PASS + FAIL} checks)\n{'='*70}")
sys.exit(1 if FAIL else 0)
