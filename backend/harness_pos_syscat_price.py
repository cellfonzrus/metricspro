"""Harness — tenant-editable POS system categories + the register price override (migration 745).

Owner directive 2026-08-11: "in the POS the unit price should be editable and the system category
should be addressed or editable." Owner then chose price-editable-by-anyone (with the original
recorded) and a tenant-editable category list.

What is actually at risk, and therefore what is tested here:

  1. DROPPING THE CHECK CONSTRAINT LEAVES THE COLUMN UNGUARDED. Migration 745 removed
     products_system_category_check, so if `_valid_system_category` does not hold the line the next
     import can invent a fifth spelling of "Accessory" that no dropdown ever offers.
  2. RENAMING ORPHANS PRODUCTS. pos.products stores the category NAME, not a foreign key. Renaming
     the config row alone would leave every product pointing at a value that no longer exists — the
     product silently reads as uncategorised. The rename must carry its products.
  3. THE PRICE OVERRIDE MUST LEAVE A TRACE. A line that records only what was CHARGED makes a $199
     case sold for $99 indistinguishable from a $99 product. list_price must survive checkout, and
     a client that sends none must be recorded as "no override" rather than 0.
  4. TENANT ISOLATION. Every read and write must carry org_id.

The fake client APPLIES .eq() to writes as well as reads ([[fake-client-eq-noop-trap]]): a query
that forgets .eq('org_id', ...) touches the other tenant's rows and FAILS the test, rather than
passing because the stub ignored the filter.
"""
import sys, os, types, copy

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(c, w):
    (PASS if c else FAIL).append(w)
    print(("  PASS " if c else "  FAIL ") + w)


def raises(fn, needle=""):
    try:
        fn()
    except Exception as e:      # HTTPException included
        return needle.lower() in (getattr(e, "detail", "") or str(e)).lower()
    return False


class _Q:
    """A query that really filters — on SELECT, UPDATE and DELETE alike."""

    def __init__(self, store, name, calls):
        self.store, self.name, self.calls, self.f = store, name, calls, []

    def select(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def order(self, *a, **k): return self

    def eq(self, c, v):
        self.f.append((c, v)); return self

    def _match(self, row):
        return all(row.get(c) == v for c, v in self.f)

    def insert(self, rows):
        # supabase-py stages the write and runs it on .execute(); returning the rows here instead
        # would let a missing .execute() pass unnoticed.
        self._ins = rows if isinstance(rows, list) else [rows]
        return self

    def update(self, patch):
        self._patch = patch; return self

    def delete(self):
        self._del = True; return self

    def execute(self):
        rows = self.store[self.name]
        if getattr(self, "_ins", None) is not None:
            out = []
            for r in self._ins:
                r = dict(r)
                r.setdefault("id", f"{self.name}-{len(rows) + len(out) + 1}")
                r.setdefault("is_active", True)
                r.setdefault("is_builtin", False)
                # stand in for the UNIQUE (org_id, name) constraint
                for e in rows + out:
                    if e.get("org_id") == r.get("org_id") and e.get("name") == r.get("name"):
                        raise RuntimeError("duplicate key value violates unique constraint")
                out.append(r)
            rows.extend(out)
            self.calls.append(("insert", self.name, list(self.f)))
            return self._done(copy.deepcopy(out))
        if getattr(self, "_patch", None) is not None:
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self._patch)
            self.calls.append(("update", self.name, list(self.f)))
            return self._done(copy.deepcopy(hit))
        if getattr(self, "_del", False):
            hit = [r for r in rows if self._match(r)]
            self.store[self.name] = [r for r in rows if not self._match(r)]
            self.calls.append(("delete", self.name, list(self.f)))
            return self._done(copy.deepcopy(hit))
        self.calls.append(("select", self.name, list(self.f)))
        return self._done(copy.deepcopy([r for r in rows if self._match(r)]))

    def _done(self, data):
        return types.SimpleNamespace(data=data)


class _S:
    def __init__(self, store, calls): self.store, self.calls = store, calls

    def table(self, n):
        self.store.setdefault(n, [])
        return _Q(self.store, n, self.calls)

    def rpc(self, name, params):
        self.calls.append(("rpc", name, params))
        return types.SimpleNamespace(execute=lambda: types.SimpleNamespace(
            data={"id": "sale-1", "org_id": params.get("p_org")}))


class FakeClient:
    def __init__(self, store): self.store, self.calls = store, []

    def schema(self, n): return _S(self.store, self.calls)

    # storeops-style unqualified access, in case anything calls .table() directly
    def table(self, n): return _S(self.store, self.calls).table(n)


import app.modules.pos.router as R  # noqa: E402

ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "00000000-0000-0000-0000-000000000001"

print("\n=== A · lazy seeding of the four builtins ===")

store = {"system_categories": [], "products": []}
fc = FakeClient(store)
R.sb = lambda: fc

rows = R._system_categories(ORG)
ok(len(rows) == 4, f"A1 first read seeds exactly 4 builtins (got {len(rows)})")
ok(sorted(r["name"] for r in rows) == ["Accessory", "Cell Phone", "Regular", "Service"],
   "A2 the seeded names are the original four")
ok(all(r["is_builtin"] for r in rows), "A3 seeded rows are marked builtin")
ok(all(r["org_id"] == ORG for r in store["system_categories"]),
   "A4 seeded rows carry the caller's org_id")

before = len(store["system_categories"])
R._system_categories(ORG)
ok(len(store["system_categories"]) == before, "A5 a second read does NOT re-seed")

# a different tenant seeds its own set and sees only its own
R._system_categories(OTHER)
ok(len([r for r in store["system_categories"] if r["org_id"] == OTHER]) == 4,
   "A6 a second tenant gets its own four")
ok(all(r["org_id"] == ORG for r in R._system_categories(ORG)),
   "A7 tenant isolation — org A's read returns none of org B's rows")

print("\n=== B · the column is still guarded now the CHECK is gone ===")

ok(R._valid_system_category(ORG, "Accessory") is None, "B1 a known active name is accepted")
ok(R._valid_system_category(ORG, None) is None, "B2 None is accepted (optional column)")
ok(R._valid_system_category(ORG, "") is None, "B3 empty is accepted")
ok(raises(lambda: R._valid_system_category(ORG, "Tablet"), "not one of"),
   "B4 an unconfigured name is REJECTED (the CHECK's job, now in code)")
ok(raises(lambda: R._valid_system_category(ORG, "accessory"), "not one of"),
   "B5 rejection is case-sensitive — 'accessory' is a different value to the DB")

# add Tablet, and it becomes valid
R.create_system_category({"name": "Tablet"}, org_id=ORG)
ok(R._valid_system_category(ORG, "Tablet") is None, "B6 once added, the new name validates")
ok(raises(lambda: R._valid_system_category(OTHER, "Tablet"), "not one of"),
   "B7 org A's new category is NOT valid for org B")

print("\n=== C · add / rename / switch off / delete ===")

ok(raises(lambda: R.create_system_category({"name": "tablet"}, org_id=ORG), "already exists"),
   "C1 duplicate add is refused case-insensitively")
ok(raises(lambda: R.create_system_category({"name": "   "}, org_id=ORG), "name required"),
   "C2 a blank name is refused")

tablet = [r for r in store["system_categories"] if r["org_id"] == ORG and r["name"] == "Tablet"][0]
acc = [r for r in store["system_categories"] if r["org_id"] == ORG and r["name"] == "Accessory"][0]

# products in BOTH tenants share the category name — only org A's may move.
store["products"] = [
    {"id": "p1", "org_id": ORG, "system_category": "Accessory"},
    {"id": "p2", "org_id": ORG, "system_category": "Accessory"},
    {"id": "p3", "org_id": ORG, "system_category": "Regular"},
    {"id": "p9", "org_id": OTHER, "system_category": "Accessory"},
]
res = R.update_system_category(acc["id"], {"name": "Add-on"}, org_id=ORG)
ok(res.get("products_moved") == 2, f"C3 rename carries its products (moved {res.get('products_moved')})")
ok([p["system_category"] for p in store["products"] if p["org_id"] == ORG] == ["Add-on", "Add-on", "Regular"],
   "C4 only the matching category moved, the others are untouched")
ok([p for p in store["products"] if p["org_id"] == OTHER][0]["system_category"] == "Accessory",
   "C5 THE OTHER TENANT'S products did not move")
ok(not [r for r in store["system_categories"] if r["org_id"] == ORG and r["name"] == "Accessory"],
   "C6 no product is left pointing at the old name")

ok(raises(lambda: R.update_system_category(acc["id"], {"name": "Regular"}, org_id=ORG), "already exists"),
   "C7 renaming onto an existing name is refused")
ok(raises(lambda: R.update_system_category(acc["id"], {"name": " "}, org_id=ORG), "cannot be blank"),
   "C8 renaming to blank is refused")
ok(raises(lambda: R.update_system_category("no-such-id", {"name": "X"}, org_id=ORG), "not found"),
   "C9 an unknown id is 404, not a silent no-op")

R.update_system_category(tablet["id"], {"is_active": False}, org_id=ORG)
ok(tablet["is_active"] is False, "C10 switch-off flips is_active")
ok(len(R._system_categories(ORG, active_only=True)) == len(R._system_categories(ORG)) - 1,
   "C11 active_only drops the switched-off one")
ok(raises(lambda: R._valid_system_category(ORG, "Tablet"), "not one of"),
   "C12 a switched-off category can no longer be assigned")

ok(raises(lambda: R.delete_system_category(acc["id"], org_id=ORG), "not deleted"),
   "C13 a BUILT-IN cannot be deleted (rename/switch off instead)")
store["products"].append({"id": "p4", "org_id": ORG, "system_category": "Tablet"})
ok(raises(lambda: R.delete_system_category(tablet["id"], org_id=ORG), "still in use"),
   "C14 a category still on a product cannot be deleted")
store["products"] = [p for p in store["products"] if p["id"] != "p4"]
ok(R.delete_system_category(tablet["id"], org_id=ORG).get("ok") is True,
   "C15 an unused custom category deletes")

print("\n=== D · product writes are validated ===")

ok(raises(lambda: R.create_product({"short_name": "X", "system_category": "Nope"}, org_id=ORG),
          "not one of"), "D1 create_product rejects an unknown system_category")
ok(raises(lambda: R.update_product("p1", {"system_category": "Nope"}, org_id=ORG),
          "not one of"), "D2 update_product rejects an unknown system_category")
r = R.update_product("p1", {"system_category": "Regular"}, org_id=ORG)
ok(r["product"]["system_category"] == "Regular", "D3 a valid update still goes through")
r = R.update_product("p1", {"cost": 5}, org_id=ORG)
ok(r["product"]["cost"] == 5, "D4 an update that omits system_category is not blocked by it")

print("\n=== E · the price override leaves a trace ===")

store2 = {"system_categories": [], "products": [], "sales": []}
fc2 = FakeClient(store2)
R.sb = lambda: fc2
R._caller_employee = lambda auth, org: "E100"

# a normal line: the client sends both, and the override is visible
R.checkout({"sale": {}, "items": [
    {"product_id": "x", "unit_price": 99.0, "list_price": 199.0, "qty": 1},
]}, authorization="Bearer t", org_id=ORG)
sent = [c for c in fc2.calls if c[0] == "rpc"][-1][2]["p_items"]
ok(sent[0]["list_price"] == 199.0 and sent[0]["unit_price"] == 99.0,
   "E1 an overridden line keeps BOTH prices — the $99 sale of a $199 item stays visible")

# a legacy client that sends no list_price must read as "no override", not as 0
R.checkout({"sale": {}, "items": [{"product_id": "y", "unit_price": 25.0, "qty": 1}]},
           authorization="Bearer t", org_id=ORG)
sent = [c for c in fc2.calls if c[0] == "rpc"][-1][2]["p_items"]
ok(sent[0]["list_price"] == 25.0,
   "E2 no list_price sent ⇒ recorded as the charged price (no override), NOT 0")

R.checkout({"sale": {}, "items": [{"product_id": "z", "unit_price": 10.0, "list_price": "", "qty": 1}]},
           authorization="Bearer t", org_id=ORG)
sent = [c for c in fc2.calls if c[0] == "rpc"][-1][2]["p_items"]
ok(sent[0]["list_price"] == 10.0, "E3 an empty-string list_price is treated the same way")

ok(raises(lambda: R.checkout({"sale": {}, "items": []}, authorization="Bearer t", org_id=ORG),
          "at least one item"), "E4 an empty cart is still refused")

print(f"\n{'=' * 70}\nRESULT: {len(PASS)}/{len(PASS) + len(FAIL)} passed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
