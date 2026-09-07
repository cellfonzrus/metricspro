"""Harness — confirming a PROPOSED classification must actually save it.

Owner report 2026-08-11: "MA Daily Tx — Product Name Classification: the proposed mapping when you hit
confirm does not save it and the subsidy is not going away."

Reproduced: `confirm` only ever UPDATED rows that already existed. The page lists every distinct
product_name in the feed and shows the built-in proposal beside it, but a proposal is not a row until
someone assigns it — so confirming matched nothing, reported the name in `not_found`, saved nothing,
and the name stayed on the list forever.

MEASURED on luxelink before the fix: 111 confirmed rows, while `Subsidy` (2,135 feed rows) and
`Trac Autopay Residual` (9,272) had NO row at all — both present in DEFAULT_PROPOSALS.
"""
import sys, os, types

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(c, w):
    (PASS if c else FAIL).append(w)
    print(("  PASS " if c else "  FAIL ") + w)


class _Q:
    def __init__(self, st, name, op, payload=None, on_conflict=None):
        self.st, self.n, self.op, self.p, self.f = st, name, op, payload, []

    def select(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def order(self, *a, **k): return self
    def range(self, *a, **k): return self

    def eq(self, c, v): self.f.append((c, v)); return self

    def execute(self):
        rows = self.st.setdefault(self.n, [])
        if self.op == "select":
            r = rows
            for c, v in self.f:
                r = [x for x in r if x.get(c) == v]
            return types.SimpleNamespace(data=[dict(x) for x in r])
        if self.op == "update":
            n = 0
            for x in rows:
                if all(x.get(c) == v for c, v in self.f):
                    x.update(self.p); n += 1
            self.st["_updates"] = self.st.get("_updates", 0) + n
            return types.SimpleNamespace(data=[])
        if self.op == "upsert":
            key = ("org_id", "source_report", "product_name")
            for x in rows:
                if all(x.get(k) == self.p.get(k) for k in key):
                    x.update(self.p)
                    return types.SimpleNamespace(data=[dict(x)])
            row = dict(self.p); row["id"] = f"id{len(rows) + 1}"
            rows.append(row)
            self.st["_inserts"] = self.st.get("_inserts", 0) + 1
            return types.SimpleNamespace(data=[dict(row)])
        raise AssertionError(self.op)


class _S:
    def __init__(self, st): self.st = st

    def table(self, n):
        st = self.st

        class T:
            def select(_s, *a, **k): return _Q(st, n, "select")
            def update(_s, p): return _Q(st, n, "update", p)
            def upsert(_s, p, on_conflict=None): return _Q(st, n, "upsert", p, on_conflict)
        return T()


class FakeClient:
    def __init__(self, st): self.st = st
    def schema(self, n): return _S(self.st)


import app.modules.commcalc.router as R  # noqa: E402
from app.modules.commcalc import ma_product_class as M  # noqa: E402

ORG = "854f6d7b"


def _body(model, d):
    """Build the request model FastAPI hands the handler, instead of a plain dict.

    This endpoint was migrated from `body: dict` to a declared pydantic model, so the handler reads
    `body.<field>`. Every probe below used to pass a dict and die with AttributeError BEFORE reaching
    the logic under test — the harness read as "failing" when it was not exercising the product at
    all. `model_validate` reproduces FastAPI's own call shape, including which fields count as
    explicitly set (`model_fields_set`), which several handlers branch on.
    """
    return model.model_validate(d)


def setup(existing):
    st = {"ma_product_class_map": [dict(r) for r in existing]}
    c = FakeClient(st)
    R.sb = lambda: c
    R.require_org = lambda *a, **k: None
    R._mpc_map_rows = lambda cl, o, sr: ([r for r in st["ma_product_class_map"]
                                          if r.get("org_id") == o and r.get("source_report") == sr], True)
    R._mpc_classes = lambda cl, o: ([], True)
    R._mpc_who = lambda a: "owner@test"
    M.assignable = lambda rows: ["subsidy", "residual", "commission", "spiff", "bill_payment", "device_sale"]
    return st


print("\n§1 · THE BUG: confirming a proposal with no saved row used to save NOTHING")
st = setup([])
res = R.confirm_ma_product_class(_body(R.ConfirmMaProductClassIn, {"product_names": ["Subsidy"]}), org_id=ORG)
ok(res["created_count"] == 1, f"Subsidy is CREATED on confirm (created_count={res['created_count']})")
ok(res["confirmed_count"] == 1, "and counted as confirmed")
row = st["ma_product_class_map"][0]
ok(row["product_class"] == "subsidy",
   f"class came from the built-in proposal, not invented (got {row['product_class']!r})")
ok(row["status"] == "confirmed" and row["confirmed_by"] == "owner@test",
   "saved as confirmed, with who and when stamped")
ok(row["org_id"] == ORG, "org_id stamped on the new row (RULE ONE)")
ok(not res["not_found"], "nothing reported missing")

print("\n§2 · THE UI'S OWN CLASS WINS (what the user saw is what gets saved)")
st = setup([])
R.confirm_ma_product_class(
    _body(R.ConfirmMaProductClassIn,
          {"items": [{"product_name": "Trac Autopay Residual", "product_class": "residual"}]}), org_id=ORG)
ok(st["ma_product_class_map"][0]["product_class"] == "residual",
   "the class shown on screen is the class stored")
ok(st["ma_product_class_map"][0]["product_name"] == "Trac Autopay Residual", "name stored trimmed/exact")

print("\n§3 · NEVER GUESSES — an unknown name with no class is REPORTED, not invented")
st = setup([])
res = R.confirm_ma_product_class(
    _body(R.ConfirmMaProductClassIn, {"product_names": ["Some Brand New Product 9000"]}), org_id=ORG)
ok(res["created_count"] == 0 and res["not_found"] == ["Some Brand New Product 9000"],
   "no proposal + no class ⇒ not_found, and nothing written")
ok(len(st["ma_product_class_map"]) == 0, "the table is untouched")

print("\n§4 · A RESERVED / UNKNOWN CLASS IS REFUSED")
st = setup([])
res = R.confirm_ma_product_class(
    _body(R.ConfirmMaProductClassIn, {"items": [{"product_name": "X", "product_class": "unmapped"}]}), org_id=ORG)
ok(res["created_count"] == 0 and "X" in res["not_found"], "'unmapped' can never be assigned")
res = R.confirm_ma_product_class(
    _body(R.ConfirmMaProductClassIn,
          {"items": [{"product_name": "Y", "product_class": "not_a_real_class"}]}), org_id=ORG)
ok(res["created_count"] == 0 and "Y" in res["not_found"], "an unknown class is refused, not stored")

print("\n§5 · EXISTING ROWS STILL JUST FLIP STATUS (no duplicate, no reclassification)")
st = setup([{"id": "id1", "org_id": ORG, "source_report": "ma_daily_tx", "product_name": "Subsidy",
             "product_class": "commission", "status": "proposed"}])
res = R.confirm_ma_product_class(_body(R.ConfirmMaProductClassIn, {"product_names": ["Subsidy"]}), org_id=ORG)
ok(len(st["ma_product_class_map"]) == 1, "still ONE row — confirm did not duplicate it")
ok(st["ma_product_class_map"][0]["status"] == "confirmed", "status flipped to confirmed")
ok(st["ma_product_class_map"][0]["product_class"] == "commission",
   "an EXISTING class is preserved — confirm never re-classifies (the docstring's promise)")
ok(res["created_count"] == 0, "nothing created for a name that already had a row")

print("\n§6 · IDEMPOTENT — confirming twice changes nothing")
before = len(st["ma_product_class_map"])
R.confirm_ma_product_class(_body(R.ConfirmMaProductClassIn, {"product_names": ["Subsidy"]}), org_id=ORG)
ok(len(st["ma_product_class_map"]) == before, "second confirm leaves the row count unchanged")

print("\n§7 · EMPTY REQUEST STILL 400s")
try:
    R.confirm_ma_product_class(_body(R.ConfirmMaProductClassIn, {}), org_id=ORG)
    ok(False, "an empty body should be rejected")
except Exception as e:
    ok("400" in str(e) or "required" in str(e).lower(), "empty body ⇒ 400, not a silent no-op")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  ✗ " + f)
sys.exit(1 if FAIL else 0)
