"""LOCK + PROOF — `POST /commcalc/recompute-rep` runs END-TO-END and writes exactly ONE rep's row, equal to what the
full Run Calculation writes for that rep (owner 2026-09-27, "Fix button, then save"; index §6k).

THE CLASS. #244 moved the identity map into `_resolve_plan_by_rep` and `recompute_rep` kept reading the old local
(`_id_map`), so the handler died with NameError on EVERY call — after it had already run both installment engines
with persist=True for the whole org. No harness ever called the handler, so nothing noticed for ten days.

DB-FREE: an in-memory client that serves the fixture tables and RECORDS every write. Two reps sell in one period
under one plan; the full path (`_run_calculation`, the Run Calculation background task) runs on one copy of the
store, the one-rep handler on another. Nothing is mocked that the code under test owns.

  A. the handler runs to the end (no exception) and answers ok;
  B. it writes EXACTLY one row — this rep's — and no other rep's row, no delete, no other table;
  C. the written row == the row the full Run Calculation writes for that rep (every column but id / timestamps);
  D. a stored row is updated IN PLACE (same id); an absent row is inserted; the other rep's stored row is untouched;
  E. no installment ledger is written: both engines are asked with persist=False (the full run asks True);
  F. NEGATIVE CONTROLS — each planted violation turns this proof RED:
       · the undefined name reintroduced (the #244 regression),
       · the handler writing a second rep's row.

    python3 backend/harness_recompute_rep_e2e.py
"""
import ast
import copy
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
for _k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY", "DATABASE_URL"):
    os.environ.pop(_k, None)

from app.modules.commcalc import router as R                          # noqa: E402
from app.modules.commcalc import installment_engine as IE              # noqa: E402
from app.modules.commcalc import sale_installment_engine as SIE        # noqa: E402

ORG = "00000000-0000-0000-0000-0000000e2e01"
PERIOD = "July 2026"
REP, OTHER = "Jona Sejat", "Other Rep"
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:300]))


# ── the in-memory store ──────────────────────────────────────────────────────────────────────────
class Store:
    def __init__(self, tables):
        self.t = copy.deepcopy(tables)
        self.writes = []            # (table, op, rows_or_payload, filters)
        self._id = 0

    def next_id(self):
        self._id += 1
        return "row-%d" % self._id


class Q:
    def __init__(self, store, name):
        self.s, self.name, self.f, self.op, self.payload = store, name, [], "select", None
        self._limit = None

    def _rows(self):
        return self.s.t.setdefault(self.name, [])

    def _match(self, r):
        for kind, col, val in self.f:
            v = r.get(col)
            if kind == "eq" and str(v) != str(val):
                return False
            if kind == "neq" and str(v) == str(val):
                return False
            if kind == "in" and str(v) not in {str(x) for x in val}:
                return False
        return True

    def select(self, *a, **k):
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v))
        return self

    def neq(self, c, v):
        self.f.append(("neq", c, v))
        return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v)))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _noop(self, *a, **k):
        return self
    is_ = gte = lte = gt = lt = ilike = like = order = or_ = not_ = contains = filter = maybe_single = single = _noop

    def range(self, a, b):
        self._range = (a, b)
        return self

    def insert(self, rows, **k):
        self.op, self.payload = "insert", rows
        return self

    def upsert(self, rows, **k):
        self.op, self.payload = "upsert", rows
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def execute(self):
        rows = self._rows()
        if self.op == "select":
            out = [copy.deepcopy(r) for r in rows if self._match(r)]
            rg = getattr(self, "_range", None)
            if rg:
                out = out[rg[0]: rg[1] + 1]
            return type("Res", (), {"data": out, "count": len(out)})()
        self.s.writes.append((self.name, self.op, copy.deepcopy(self.payload), list(self.f)))
        if self.op in ("insert", "upsert"):
            for r in (self.payload if isinstance(self.payload, list) else [self.payload]):
                r = dict(r)
                r.setdefault("id", self.s.next_id())
                rows.append(r)
            return type("Res", (), {"data": [], "count": 0})()
        if self.op == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(copy.deepcopy(self.payload))
            return type("Res", (), {"data": hit, "count": len(hit)})()
        if self.op == "delete":
            keep = [r for r in rows if not self._match(r)]
            self.s.t[self.name] = keep
            return type("Res", (), {"data": [], "count": 0})()


class Client:
    def __init__(self, store):
        self.s = store

    def schema(self, n):
        st = self.s

        class S:
            def table(self_, t):
                return Q(st, n + "." + t)

            def rpc(self_, *a, **k):
                return type("X", (), {"execute": lambda s=None: type("R", (), {"data": []})()})()
        return S()

    def table(self, t):
        return Q(self.s, "public." + t)

    def rpc(self, *a, **k):
        return type("X", (), {"execute": lambda s=None: type("R", (), {"data": []})()})()


# ── the fixture: a plan-mode org, two reps, one plan ($10 per activation event, $5 per upgrade event) ──
def L(rep, tid, product, category, serial="", ext=0.0, contract=""):
    return {"org_id": ORG, "period": PERIOD, "trans_id": tid, "trans_date": "2026-07-02", "store": "Store 1321",
            "salesperson": rep, "user_login": rep, "department": "Activations (Price Sheet)",
            "category": ">> Activations (Price Sheet) >> Carrier >> " + category, "product_desc": product,
            "serial_1": serial, "mdn": "", "ext_price": ext, "gp": ext, "voided": "No", "trans_type": None,
            "contract_type": contract, "quantity": 1.0, "customer": "A CUSTOMER"}


SALES = [
    L(REP, "T1", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "9297458084", 150.0),
    L(REP, "T1", "32066 My Biz Plan - New Act", "Additional Spiffs (Promotions)", "9297458084", 45.0),
    L(REP, "T2", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "3478963977", 150.0),
    L(REP, "T3", "DPA Upgrade iPhone", "Upgrades", "7185550101", 0.0, "Upgrade"),
    L(OTHER, "T9", "DPA New Act iPhone (Rate Plan Rebate)", "Rate Plan Rebates", "6465550199", 150.0),
]
TENANT_ADR = {"fields": ["category", "product_desc", "contract_type"],
              "tokens": {"byod": ["customer provided", "byod"], "port": ["port"], "upgrade": ["upgrade"],
                         "activation": ["new act", "add a line"], "hardware_only": ["hardware only"]}}
RULES = [{"id": "RACT", "label": "Activation", "match_field": "activation_bucket", "match_op": "in",
          "match_value": "premium,byod", "qualifies": True, "payout_kind": "flat_per_unit", "amount": 10.0,
          "pct": 0.0, "tiered": False, "unit_basis": None, "sort": 0},
         {"id": "RUPG", "label": "Upgrade", "match_field": "activation_bucket", "match_op": "in",
          "match_value": "upgrade", "qualifies": True, "payout_kind": "flat_per_unit", "amount": 5.0,
          "pct": 0.0, "tiered": False, "unit_basis": None, "sort": 1}]
BASE = {
    "commcalc.carrier": [{"id": "C1", "org_id": ORG, "name": "Carrier X", "code": "carrier_x", "is_default": True,
                          "engine_mode": "plan"}],
    "commcalc.commission_plan": [{"id": "FLAT", "org_id": ORG, "name": "Flat", "is_active": True, "carrier_id": "C1",
                                  "base_tier_metric": None, "commission_basis": "rules", "activation_source": "inherit"}],
    "commcalc.commission_rule": [dict(r, org_id=ORG, plan_id="FLAT") for r in RULES],
    "commcalc.commission_tier": [],
    "commcalc.commission_plan_assignment": [{"id": "A1", "org_id": ORG, "plan_id": "FLAT", "scope": "default",
                                             "scope_value": None, "priority": 0}],
    "commcalc.accessory_config": [{"org_id": ORG, "activation_details_rules": TENANT_ADR, "contract_type_map": {},
                                   "activation_rules": []}],
    "commcalc.raw_sales": SALES,
}
OTHER_STORED = {"id": "stored-other", "org_id": ORG, "period": PERIOD, "epay_salesperson": OTHER,
                "storeops_name": OTHER, "total_payout": 999.0, "plan_comm": 999.0, "subtotal": 999.0}
REP_STORED = {"id": "stored-rep", "org_id": ORG, "period": PERIOD, "epay_salesperson": REP, "storeops_name": "",
              "total_payout": 1.0, "plan_comm": 1.0, "subtotal": 1.0}

PERSIST = []
_orig_ie, _orig_sie = IE.compute_installments, SIE.compute_sale_installments


def _spy_ie(client, org_id, period, persist=False, **k):
    PERSIST.append(("installment_engine", persist))
    return _orig_ie(client, org_id, period, persist=persist, **k)


def _spy_sie(client, org_id, period, persist=False, **k):
    PERSIST.append(("sale_installment_engine", persist))
    return _orig_sie(client, org_id, period, persist=persist, **k)


IE.compute_installments = _spy_ie
SIE.compute_sale_installments = _spy_sie
R.installment_engine.compute_installments = _spy_ie
R.sale_installment_engine.compute_sale_installments = _spy_sie
R.require_org = lambda org_id: None

DROP = ("id", "created_at", "updated_at")


def rep_rows(rows, name):
    return [r for r in rows if name.upper() in {str(r.get("epay_salesperson") or "").upper(),
                                                 str(r.get("storeops_name") or "").upper()}]


def full_run_rows(extra=None):
    """What the FULL Run Calculation writes for the period (the background task, on its own store)."""
    st = Store(dict(BASE, **(extra or {})))
    R.sb = lambda: Client(st)
    PERSIST.clear()
    R._run_calculation(PERIOD, ORG, force=True, guard_token="harness")
    return st, [dict(p) for (t, op, pl, _f) in st.writes if t == "commcalc.rep_commissions" and op == "insert"
                for p in (pl if isinstance(pl, list) else [pl])], list(PERSIST)


def one_rep(fn, stored):
    """Run a recompute_rep-shaped handler on a store holding `stored` rep rows. Returns (store, result|exc)."""
    st = Store(dict(BASE, **{"commcalc.rep_commissions": copy.deepcopy(stored)}))
    R.sb = lambda: Client(st)
    PERSIST.clear()
    try:
        res = fn(R.RecomputeRepIn(period=PERIOD, rep=REP), org_id=ORG)
    except Exception as e:                        # noqa: BLE001 — the proof reports it
        res = e
    return st, res, list(PERSIST)


def e2e_failures(fn, full_rep_row):
    """Every way `fn` breaks the contract (empty = green)."""
    bad = []
    for label, stored in (("update", [OTHER_STORED, REP_STORED]), ("insert", [OTHER_STORED])):
        st, res, persist = one_rep(fn, stored)
        if isinstance(res, BaseException):
            bad.append(f"[{label}] raised {type(res).__name__}: {res}")
            continue
        if not (isinstance(res, dict) and res.get("ok")):
            bad.append(f"[{label}] not ok: {res}")
        w = st.writes
        if any(t != "commcalc.rep_commissions" for (t, *_x) in w):
            bad.append(f"[{label}] wrote another table: {sorted({t for (t, *_x) in w})}")
        rc = [x for x in w if x[0] == "commcalc.rep_commissions"]
        if any(op == "delete" for (_t, op, *_x) in rc):
            bad.append(f"[{label}] deleted rep_commissions rows")
        if len(rc) != 1:
            bad.append(f"[{label}] {len(rc)} rep_commissions writes (want exactly 1)")
        for (_t, op, pl, flt) in rc:
            rows = pl if isinstance(pl, list) else [pl]
            if op == "update":
                ids = [v for (k, c, v) in flt if c == "id"]
                if ids != [("stored-rep" if label == "update" else None)]:
                    bad.append(f"[{label}] update aimed at {ids}")
            for r in rows:
                names = {str(r.get("epay_salesperson") or "").upper(), str(r.get("storeops_name") or "").upper()} - {""}
                if names and REP.upper() not in names:
                    bad.append(f"[{label}] wrote ANOTHER rep's row: {names}")
        other = [r for r in st.t["commcalc.rep_commissions"] if r.get("id") == "stored-other"]
        if other != [OTHER_STORED]:
            bad.append(f"[{label}] the other rep's stored row changed: {other}")
        mine = rep_rows(st.t["commcalc.rep_commissions"], REP)
        if len(mine) != 1:
            bad.append(f"[{label}] {len(mine)} rows for the rep after the write (want 1)")
        elif {k: v for k, v in mine[0].items() if k not in DROP} != {k: v for k, v in full_rep_row.items() if k not in DROP}:
            diff = {k: (mine[0].get(k), full_rep_row.get(k)) for k in set(mine[0]) | set(full_rep_row)
                    if k not in DROP and mine[0].get(k) != full_rep_row.get(k)}
            bad.append(f"[{label}] row != the full run's row for the rep: {diff}")
        if any(p for (_e, p) in persist):
            bad.append(f"[{label}] an installment engine was asked to PERSIST (org-wide ledger write): {persist}")
    return bad


# ═════════════════════════════════════════════════════════════════════════════════════════════════
print("── the full Run Calculation (the reference) ──")
_st_full, full_rows, full_persist = full_run_rows()
full_rep = rep_rows(full_rows, REP)
check("R1 the full run writes one row per rep (2), Jona's paying $25.00 (2 activations x $10 + 1 upgrade x $5), "
      "with the standard calc's counts on it",
      len(full_rows) == 2 and len(full_rep) == 1 and full_rep[0].get("total_payout") == 25.0
      and (full_rep[0].get("premium_acts") or 0) + (full_rep[0].get("upgrade_acts") or 0) > 0,
      [(r.get("epay_salesperson"), r.get("total_payout")) for r in full_rows])
check("R2 the full run asks both installment engines to persist (the period's ledgers, every rep)",
      ("installment_engine", True) in full_persist and ("sale_installment_engine", True) in full_persist, full_persist)

print("── the one-rep handler, end to end ──")
bad = e2e_failures(R.recompute_rep, full_rep[0] if full_rep else {})
check("A-E recompute_rep: runs to the end, writes ONLY this rep's row (update in place / insert), equal to the "
      "full run's row, other rep untouched, no installment ledger write", not bad, bad)
st, res, persist = one_rep(R.recompute_rep, [OTHER_STORED, REP_STORED])
check("E2 both installment engines were asked, with persist=False",
      sorted(persist) == [("installment_engine", False), ("sale_installment_engine", False)], persist)
check("E3 the response says the ledgers were not written", "not written" in str((res or {}).get("installment_ledgers")))
check("D2 the stored row was updated IN PLACE (same id), total $1.00 → $25.00",
      [r.get("total_payout") for r in st.t["commcalc.rep_commissions"] if r.get("id") == "stored-rep"] == [25.0])

# ═════════════════════════════════════════════════════════════════════════════════════════════════
print("── negative controls ──")
_src = open(R.__file__).read()
_tree = ast.parse(_src)
_fn = next(n for n in _tree.body if isinstance(n, ast.FunctionDef) and n.name == "recompute_rep")
_lines = _src.splitlines(True)
_fsrc = "".join(_lines[_fn.lineno - 1:_fn.end_lineno])


def variant(old, new):
    assert _fsrc.count(old) == 1, old
    ns = dict(R.__dict__)
    exec(compile(textwrap.dedent(_fsrc.replace(old, new)), R.__file__, "exec"), ns)
    return ns["recompute_rep"]


v1 = variant("    id_map = _rep_canon_map(client, org_id)\n",
             "    id_map = _rep_canon_map(client, org_id)\n    _bridged = _id_map.get(rep.upper())\n")
check("F1 the undefined name reintroduced (the #244 regression) → RED", bool(e2e_failures(v1, full_rep[0])))
v2 = variant("    top = fresh[0]\n",
             "    client.schema('commcalc').table('rep_commissions').update({'total_payout': 0.0})"
             ".eq('org_id', org_id).eq('id', 'stored-other').execute()\n    top = fresh[0]\n")
check("F2 the handler writing a second rep's row → RED", bool(e2e_failures(v2, full_rep[0])))
v3 = variant('inp["carrier_mode"], persist_installments=False)', 'inp["carrier_mode"], persist_installments=True)')
check("F3 the handler persisting the org-wide installment ledgers → RED", bool(e2e_failures(v3, full_rep[0])))
check("F4 the unmodified handler is GREEN (the controls are not vacuous)", not e2e_failures(R.recompute_rep, full_rep[0]))

print("\n%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
