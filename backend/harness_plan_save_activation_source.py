"""HARNESS — save/GET round-trip of commission_plan.activation_source (mig 297, PR #97 UI backend).

Proves the small money-safe backend change made so the per-plan Activation source becomes UI-settable:
  A. save_commission_plan PERSISTS activation_source when the caller sends it and the column exists;
     list_commission_plans RETURNS it. ('activation_details' round-trips.)
  B. Omitting activation_source on an existing plan leaves the stored value UNTOUCHED (no silent wipe).
  C. A brand-new plan created without activation_source defaults to 'inherit' (the DB default).
  D. An unknown/garbage activation_source value collapses to 'inherit' (never persisted verbatim).
  E. PRE-297 database (no activation_source column): save still succeeds and writes NO activation_source
     key — byte-identical to before; the engine degrades every plan to 'inherit'.

Drives the REAL router.save_commission_plan / router.list_commission_plans over an in-memory fake
PostgREST client (no DB, no network). No payout math is exercised — this is a persistence round-trip only.

  python3 backend/harness_plan_save_activation_source.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import router as R  # noqa: E402

ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"

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


# ── minimal in-memory PostgREST fake ──────────────────────────────────────────────────────────
class _PgErr(Exception):
    pass


class _Q:
    def __init__(self, tbl, op, payload=None):
        self.tbl, self.op, self.payload = tbl, op, payload
        self._filters = []
        self._sel = "*"
        self._order = None
        self._limit = None
        self._on_conflict = None

    def select(self, cols="*", **k):
        self._sel = cols
        # PostgREST raises 42703 when a named column is absent — the probe relies on this.
        if cols and cols != "*":
            for c in str(cols).split(","):
                c = c.strip()
                if c and c not in self.tbl.cols:
                    raise _PgErr(f"column {self.tbl.name}.{c} does not exist (42703)")
        return self

    def eq(self, c, v):
        self._filters.append((c, v))
        return self

    def order(self, c, **k):
        self._order = c
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _match(self):
        rows = self.tbl.rows
        for c, v in self._filters:
            rows = [r for r in rows if str(r.get(c)) == str(v)]
        return rows

    class _Resp:
        def __init__(self, data):
            self.data = data

    def execute(self):
        if self.op == "select":
            rows = self._match()
            if self._order:
                rows = sorted(rows, key=lambda r: str(r.get(self._order)))
            if self._limit is not None:
                rows = rows[:self._limit]
            return _Q._Resp([dict(r) for r in rows])
        if self.op == "delete":
            doomed = self._match()
            for r in doomed:
                self.tbl.rows.remove(r)
            return _Q._Resp([dict(r) for r in doomed])
        if self.op == "update":
            hit = self._match()
            for r in hit:
                for k, v in self.payload.items():
                    if k not in self.tbl.cols:
                        raise _PgErr(f"column {self.tbl.name}.{k} does not exist (42703)")
                    r[k] = v
            return _Q._Resp([dict(r) for r in hit])
        if self.op in ("insert", "upsert"):
            batch = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for row in batch:
                for k in row:
                    if k not in self.tbl.cols:
                        raise _PgErr(f"column {self.tbl.name}.{k} does not exist (42703)")
                # upsert on_conflict=org_id,name → replace an existing row in place
                if self.op == "upsert" and self._on_conflict:
                    keys = self._on_conflict.split(",")
                    existing = [r for r in self.tbl.rows
                                if all(str(r.get(k)) == str(row.get(k)) for k in keys)]
                    if existing:
                        existing[0].update(row)
                        out.append(dict(existing[0]))
                        continue
                new = dict(row)
                new.setdefault("id", str(uuid.uuid4()))
                self.tbl.rows.append(new)
                out.append(dict(new))
            return _Q._Resp(out)
        raise AssertionError(self.op)


class _Table:
    def __init__(self, name, cols):
        self.name, self.cols, self.rows = name, set(cols), []

    def select(self, cols="*", **k):
        return _Q(self, "select").select(cols, **k)

    def insert(self, payload):
        return _Q(self, "insert", payload)

    def upsert(self, payload, on_conflict=None):
        q = _Q(self, "upsert", payload)
        q._on_conflict = on_conflict
        return q

    def update(self, payload):
        return _Q(self, "update", payload)

    def delete(self):
        return _Q(self, "delete")


class _Schema:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return self.db.tables[name]


class _Client:
    def __init__(self, with_activation_source=True):
        plan_cols = ["id", "org_id", "name", "carrier_id", "base_tier_metric", "is_active", "notes",
                     "tier_count_basis", "tier_match_field", "tier_match_op", "tier_match_value",
                     "tier_below_min_multiplier"]
        if with_activation_source:
            plan_cols.append("activation_source")
        self.tables = {
            "commission_plan": _Table("commission_plan", plan_cols),
            "commission_rule": _Table("commission_rule",
                                      ["id", "org_id", "plan_id", "label", "match_field", "match_op",
                                       "match_value", "qualifies", "payout_kind", "amount", "pct",
                                       "tiered", "sort", "unit_basis", "applies_scope_kind",
                                       "applies_scope_value"]),
            "commission_tier": _Table("commission_tier",
                                      ["id", "org_id", "plan_id", "metric", "min_count", "multiplier",
                                       "sort"]),
            "commission_plan_assignment": _Table("commission_plan_assignment",
                                                 ["id", "org_id", "plan_id", "scope", "scope_value",
                                                  "priority"]),
        }
        # DB default for the column (mig 297): NOT NULL DEFAULT 'inherit'.
        self._plan_default = {"activation_source": "inherit"} if with_activation_source else {}

    def schema(self, _s):
        return _Schema(self)


def _reset_probes():
    R._PLAN_ACTIVATION_SOURCE_COL_OK.clear()
    R._PLAN_TIER_COLS_OK.clear()
    R._RULE_GATE_COLS_OK.clear()
    R._RULE_SCOPE_COLS_OK.clear()


def _get_plan(client, plan_id):
    R.sb = lambda: client
    res = R.list_commission_plans(org_id=ORG)
    for p in res["plans"]:
        if p["id"] == plan_id:
            return p
    return None


def _seed_default(client, plan_id):
    """Emulate the DB DEFAULT: a row inserted without activation_source materializes 'inherit'."""
    for r in client.tables["commission_plan"].rows:
        if r["id"] == plan_id and "activation_source" in client.tables["commission_plan"].cols:
            r.setdefault("activation_source", "inherit")


# ─────────────────────────────────────────────────────────────────────────────────────────────
print("A/B/C/D — column PRESENT (post-297)")
client = _Client(with_activation_source=True)
R.sb = lambda: client
_reset_probes()

# A. create with activation_details → persisted + returned
out = R.save_commission_plan(R.SaveCommissionPlanIn(
    name="NY Plan", activation_source="activation_details"), org_id=ORG)
pid = out["id"]
p = _get_plan(client, pid)
check("A. save persists + GET returns activation_source='activation_details'",
      p and p.get("activation_source") == "activation_details", extra=str(p and p.get("activation_source")))

# B. re-save WITHOUT the field → stored value untouched
R.save_commission_plan(R.SaveCommissionPlanIn(id=pid, name="NY Plan"), org_id=ORG)
p = _get_plan(client, pid)
check("B. omitting field leaves stored 'activation_details' untouched (no wipe)",
      p and p.get("activation_source") == "activation_details", extra=str(p and p.get("activation_source")))

# C. new plan without the field → DB default 'inherit'
out2 = R.save_commission_plan(R.SaveCommissionPlanIn(name="Chicago Plan"), org_id=ORG)
_seed_default(client, out2["id"])
p2 = _get_plan(client, out2["id"])
check("C. new plan without field defaults to 'inherit'",
      p2 and p2.get("activation_source") == "inherit", extra=str(p2 and p2.get("activation_source")))

# D. garbage value collapses to 'inherit'
out3 = R.save_commission_plan(R.SaveCommissionPlanIn(
    name="Junk Plan", activation_source="banana"), org_id=ORG)
p3 = _get_plan(client, out3["id"])
check("D. unknown value collapses to 'inherit'",
      p3 and p3.get("activation_source") == "inherit", extra=str(p3 and p3.get("activation_source")))

# D2. valid raw_sales round-trips
out4 = R.save_commission_plan(R.SaveCommissionPlanIn(
    name="Pinned Plan", activation_source="raw_sales"), org_id=ORG)
p4 = _get_plan(client, out4["id"])
check("D2. 'raw_sales' round-trips",
      p4 and p4.get("activation_source") == "raw_sales", extra=str(p4 and p4.get("activation_source")))

print()
print("E — column ABSENT (pre-297): save succeeds, writes no activation_source key")
client0 = _Client(with_activation_source=False)
R.sb = lambda: client0
_reset_probes()
outE = R.save_commission_plan(R.SaveCommissionPlanIn(
    name="Legacy Plan", activation_source="activation_details"), org_id=ORG)
rowE = [r for r in client0.tables["commission_plan"].rows if r["id"] == outE["id"]][0]
check("E1. pre-297 save succeeds", outE.get("saved") is True)
check("E2. pre-297 row has NO activation_source key (not persisted verbatim)",
      "activation_source" not in rowE, extra=str(rowE))

print()
print(f"RESULT  {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
