"""Harness — POS product filters (owner 2026-09-24: "create product filters on the pos page").

`GET /pos/products` gained optional filters — category_id, manufacturer, inventory_type, in_stock
(+ store_code) — beside the department_id / system_category / search it always had, all applied
through ONE helper (app/modules/pos/product_filters.py). The register's product picker and the catalog
page both call it. What is at risk, and therefore what is proved here, over the REAL handler:

  A. NO NEW PARAM = THE OLD ANSWER. A call carrying none of the new params builds the exact query the
     handler built before (same filter chain, same order, same cap) and returns the same rows. The
     pre-change handler is kept below VERBATIM as the reference.
  B. EACH FILTER NARROWS CORRECTLY — department, category, system category, manufacturer, inventory
     type — to exactly the rows an independent Python predicate picks.
  C. FILTERS COMBINE with each other and with the text search (AND, never OR).
  D. IN STOCK = a serial unit with status in_stock, or standard qty_on_hand > 0; at the given store;
     sold / zero-qty / other-org stock never counts; >150 on-hand ids are chunked and merged back
     newest-first under the same 500 cap.
  E. PAST THE 500-ROW CAP. A filter finds an OLD product that the unfiltered list (newest 500) drops —
     the reason the filters are server-side.
  F. ORG-SCOPED. Another org's identical products / stock / manufacturers never appear, and every
     query the handler issues carries org_id.
  G. The manufacturer option list: distinct, non-blank, A→Z ignoring case, active-only by default.
  H. A bad inventory_type is a 400, not a silent empty list.
  I. The special-order catalog search (the old second copy of the clause) answers exactly as before.

The fake client really filters (eq / gt / in / or-ilike), really orders, pages and caps, and records
every query's filter chain, so a dropped `.eq("org_id", …)` FAILS rather than passing on a stub that
ignored it. DB-free; no network.   Run:  cd backend && python3 harness_pos_product_filters.py
"""
import copy
import os
import sys
import types

sys.path.insert(0, os.path.dirname(__file__))
PASS, FAIL = [], []


def ok(c, w):
    (PASS if c else FAIL).append(w)
    print(("  PASS " if c else "  FAIL ") + w)


def raises(fn, needle=""):
    try:
        fn()
    except Exception as e:      # HTTPException included
        return needle.lower() in str(getattr(e, "detail", "") or e).lower()
    return False


# ── the fake PostgREST client ────────────────────────────────────────────────────────────────────
def _or_match(row, clause):
    """PostgREST `or=(a.ilike.%x%,b.ilike.%x%)` — any term matches."""
    for term in clause.split(","):
        col, op, val = term.split(".", 2)
        assert op == "ilike", op
        needle = val.strip("%").lower()
        if row.get(col) is not None and needle in str(row.get(col)).lower():
            return True
    return False


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.f = db, table, []
        self._order, self._limit, self._range = [], None, None

    def select(self, cols="*", **k): self.cols = cols; return self
    def eq(self, c, v): self.f.append(("eq", c, v)); return self
    def gt(self, c, v): self.f.append(("gt", c, v)); return self
    def in_(self, c, v): self.f.append(("in", c, list(v))); return self
    def or_(self, clause): self.f.append(("or", clause, None)); return self
    def order(self, c, desc=False): self._order.append((c, desc)); return self
    def limit(self, n): self._limit = n; return self
    def range(self, lo, hi): self._range = (lo, hi); return self

    def _match(self, r):
        for op, c, v in self.f:
            if op == "eq" and r.get(c) != v: return False
            if op == "gt" and not (r.get(c) is not None and r.get(c) > v): return False
            if op == "in" and r.get(c) not in v: return False
            if op == "or" and not _or_match(r, c): return False
        return True

    def execute(self):
        self.db.log.append((self.table, list(self.f), list(self._order), self._limit))
        out = [copy.deepcopy(r) for r in self.db.tables.get(self.table, []) if self._match(r)]
        for c, desc in reversed(self._order):      # stable multi-key sort, last key first
            out.sort(key=lambda r: (r.get(c) is None, r.get(c) if not isinstance(r.get(c), str)
                                    else r.get(c).lower()), reverse=desc)
        if self._range:
            out = out[self._range[0]:self._range[1] + 1]
        if self._limit is not None:
            out = out[:self._limit]
        return types.SimpleNamespace(data=out)


class _S:
    def __init__(self, db): self.db = db
    def table(self, n): return _Q(self.db, n)


class FakeClient:
    def __init__(self, tables):
        self.tables, self.log = tables, []

    def schema(self, n): return _S(self)
    def table(self, n): return _Q(self, n)


import app.modules.pos.router as R            # noqa: E402
import app.modules.pos.product_filters as PF  # noqa: E402

ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "11111111-2222-3333-4444-555555555555"


# ── the PRE-CHANGE handler, verbatim (the reference for §A) ──────────────────────────────────────
def before_list_products(search: str = "", department_id: str = "", system_category: str = "",
                         active_only: bool = True, org_id: str = R.ORG_ID):
    client = R.sb()
    q = client.schema("pos").table("products").select("*").eq("org_id", org_id)
    if active_only:
        q = q.eq("is_active", True)
    if department_id:
        q = q.eq("department_id", department_id)
    if system_category:
        q = q.eq("system_category", system_category)
    if search.strip():
        s = search.strip().replace("%", "").replace(",", " ")
        q = q.or_(f"short_name.ilike.%{s}%,full_name.ilike.%{s}%,upc.ilike.%{s}%")
    rows = q.order("product_code", desc=True).limit(500).execute().data or []
    cat = R.catalog(org_id)
    dname = {d["id"]: d["short_name"] for d in cat["departments"]}
    cname = {c["id"]: c["name"] for c in cat["categories"]}
    for r in rows:
        r["department_name"] = dname.get(r.get("department_id"))
        r["category_name"] = cname.get(r.get("category_id"))
    return {"products": rows}


def before_special_order_products(org_id, search="", active_only=True):
    q = (R.sb().schema("pos").table("products").select("*")
         .eq("org_id", org_id).eq("is_special_order", True))
    if active_only:
        q = q.eq("is_active", True)
    if (search or "").strip():
        s = search.strip().replace("%", "").replace(",", " ")
        q = q.or_(f"short_name.ilike.%{s}%,full_name.ilike.%{s}%,upc.ilike.%{s}%")
    return q.order("short_name").limit(500).execute().data or []


# ── fixture ──────────────────────────────────────────────────────────────────────────────────────
def build(n_filler=0, other_org=True):
    depts = [{"id": "D-PH", "org_id": ORG, "short_name": "Phones"},
             {"id": "D-AC", "org_id": ORG, "short_name": "Accessories"}]
    cats = [{"id": "C-SM", "org_id": ORG, "name": "Smartphones", "department_id": "D-PH"},
            {"id": "C-BA", "org_id": ORG, "name": "Basic", "department_id": "D-PH"},
            {"id": "C-CS", "org_id": ORG, "name": "Cases", "department_id": "D-AC"}]
    P = []

    def p(code, name, dept, cat, sysc, inv, mfr, active=True, upc=None, org=ORG, special=False):
        P.append({"id": f"P{code}" + ("" if org == ORG else "-x"), "org_id": org, "product_code": code,
                  "short_name": name, "full_name": name + " full", "upc": upc,
                  "department_id": dept, "category_id": cat, "system_category": sysc,
                  "inventory_type": inv, "manufacturer": mfr, "is_active": active,
                  "is_special_order": special})

    p(1, "Galaxy Old", "D-PH", "C-SM", "Cell Phone", "serial", "Maker A", upc="100001")
    p(2, "Flip Basic", "D-PH", "C-BA", "Cell Phone", "serial", "Maker B")
    p(3, "Clear Case", "D-AC", "C-CS", "Accessory", "standard", "Maker A", upc="300003")
    p(4, "Rugged Case", "D-AC", "C-CS", "Accessory", "standard", "maker c")
    p(5, "Phone X", "D-PH", "C-SM", "Cell Phone", "serial", "Maker B", upc="500005")
    p(6, "Retired Case", "D-AC", "C-CS", "Accessory", "standard", "Maker A", active=False)
    p(7, "Setup Service", None, None, "Service", "standard", None)
    p(8, "Blank Maker", "D-AC", None, "Regular", "standard", "   ")
    p(9, "Case Special", "D-AC", "C-CS", "Accessory", "standard", "Maker A", special=True)
    for i in range(n_filler):          # newest products: push the fixture past the 500 cap
        code = 1000 + i
        p(code, f"Filler {i}", "D-AC", "C-CS", "Regular", "standard", "Filler Co")
    if other_org:                      # the SAME attributes in another tenant
        for r in list(P[:9]):
            P.append(dict(r, id=r["id"] + "-x", org_id=OTHER))
        depts += [dict(d, org_id=OTHER) for d in depts]
    inv_serial = [
        {"id": "S1", "org_id": ORG, "product_id": "P1", "store_code": "ST1", "status": "in_stock"},
        {"id": "S2", "org_id": ORG, "product_id": "P2", "store_code": "ST1", "status": "sold"},
        {"id": "S3", "org_id": ORG, "product_id": "P5", "store_code": "ST2", "status": "in_stock"},
        {"id": "S4", "org_id": OTHER, "product_id": "P2", "store_code": "ST1", "status": "in_stock"},
    ]
    inv_std = [
        {"id": "T1", "org_id": ORG, "product_id": "P3", "store_code": "ST1", "qty_on_hand": 4},
        {"id": "T2", "org_id": ORG, "product_id": "P4", "store_code": "ST1", "qty_on_hand": 0},
        {"id": "T3", "org_id": ORG, "product_id": "P4", "store_code": "ST2", "qty_on_hand": -1},
        {"id": "T4", "org_id": OTHER, "product_id": "P4", "store_code": "ST1", "qty_on_hand": 9},
    ]
    return FakeClient({"products": P, "departments": depts, "categories": cats,
                       "inventory_serial": inv_serial, "inventory_standard": inv_std})


def use(fc):
    R.sb = lambda: fc
    return fc


def codes(resp):
    return [r["product_code"] for r in resp["products"]]


def call(**kw):
    kw.setdefault("org_id", ORG)
    return R.list_products(**{**dict(search="", department_id="", system_category="",
                                     active_only=True, category_id="", manufacturer="",
                                     inventory_type="", in_stock=False, store_code=""), **kw})


def expect(fc, pred, active_only=True):
    rows = [r for r in fc.tables["products"] if r["org_id"] == ORG and (r["is_active"] or not active_only)
            and pred(r)]
    return sorted([r["product_code"] for r in rows], reverse=True)


# ═════════════════════════════════════════════════════════════════════════════════════════════════
print("\n=== A · no new param = the old query and the old answer ===")
fc = use(build())
matrix = [dict(), dict(search="case"), dict(search="  Phone "), dict(search="100001"),
          dict(department_id="D-AC"), dict(system_category="Cell Phone"),
          dict(department_id="D-PH", system_category="Cell Phone", search="x"),
          dict(active_only=False), dict(active_only=False, search="case"),
          dict(search="50%,00"), dict(search="   ")]
for m in matrix:
    fc.log.clear()
    old = before_list_products(org_id=ORG, **m)
    old_log = list(fc.log)
    fc.log.clear()
    new = call(**m)
    new_log = list(fc.log)
    ok(old == new, f"A rows identical for {m or 'no params'} ({len(new['products'])} rows)")
    ok(old_log == new_log, f"A query chain identical for {m or 'no params'}")

print("\n=== B · each filter narrows to exactly the right rows ===")
fc = use(build())
cases = [
    ("department_id", "D-PH", lambda r: r["department_id"] == "D-PH"),
    ("department_id", "D-AC", lambda r: r["department_id"] == "D-AC"),
    ("category_id", "C-CS", lambda r: r["category_id"] == "C-CS"),
    ("category_id", "C-BA", lambda r: r["category_id"] == "C-BA"),
    ("system_category", "Accessory", lambda r: r["system_category"] == "Accessory"),
    ("system_category", "Service", lambda r: r["system_category"] == "Service"),
    ("manufacturer", "Maker A", lambda r: r["manufacturer"] == "Maker A"),
    ("manufacturer", "maker c", lambda r: r["manufacturer"] == "maker c"),
    ("inventory_type", "serial", lambda r: r["inventory_type"] == "serial"),
    ("inventory_type", "standard", lambda r: r["inventory_type"] == "standard"),
]
for k, v, pred in cases:
    got = codes(call(**{k: v}))
    want = expect(fc, pred)
    ok(got == want and len(got) > 0, f"B {k}={v!r} → {got}")
ok(codes(call(manufacturer="MAKER A")) == [], "B manufacturer is an EXACT match (free text as stored)")
ok(codes(call(category_id="C-NOPE")) == [], "B an unknown category narrows to nothing, never to 'any'")
all_active = expect(fc, lambda r: True)
ok(codes(call(department_id="", category_id="", manufacturer="  ", inventory_type="")) == all_active,
   "B blank values mean 'any' (a whitespace manufacturer adds no filter)")
ok(codes(call(manufacturer="Maker A", active_only=False)) == expect(fc, lambda r: r["manufacturer"] == "Maker A",
                                                                     active_only=False),
   "B active_only=false still honoured with a filter (the retired case appears)")
r0 = call(category_id="C-CS")["products"][0]
ok(r0.get("department_name") == "Accessories" and r0.get("category_name") == "Cases",
   "B filtered rows still carry the resolved department / category names")

print("\n=== C · filters combine with each other and with the search ===")
got = codes(call(department_id="D-AC", manufacturer="Maker A"))
ok(got == expect(fc, lambda r: r["department_id"] == "D-AC" and r["manufacturer"] == "Maker A"),
   f"C department AND manufacturer → {got}")
got = codes(call(search="case", manufacturer="Maker A"))
ok(got == [9, 3], f"C search 'case' AND manufacturer 'Maker A' → {got} (Rugged Case by 'maker c' excluded)")
got = codes(call(search="phone", inventory_type="serial", category_id="C-SM"))
ok(got == [5], f"C search + inventory type + category → {got}")
got = codes(call(search="300003", department_id="D-PH"))
ok(got == [], "C a UPC match in the WRONG department is excluded (AND, not OR)")
got = codes(call(search="case", department_id="D-AC", category_id="C-CS", system_category="Accessory",
                 manufacturer="Maker A", inventory_type="standard"))
ok(got == [9, 3], f"C all five filters + search → {got}")

print("\n=== D · in stock ===")
fc = use(build())
got = codes(call(in_stock=True))
ok(got == [5, 3, 1], f"D in stock anywhere → {got} (serial in_stock P1, P5; standard qty>0 P3)")
ok(2 not in got, "D a SOLD serial unit does not make its product in stock")
ok(4 not in got, "D qty 0 / negative is not on hand (and another org's qty 9 never counts)")
got = codes(call(in_stock=True, store_code="ST1"))
ok(got == [3, 1], f"D in stock at ST1 → {got}")
got = codes(call(in_stock=True, store_code="ST2"))
ok(got == [5], f"D in stock at ST2 → {got}")
got = codes(call(in_stock=True, inventory_type="serial", search="phone"))
ok(got == [5], f"D in stock + inventory type + search → {got}")
got = codes(call(in_stock=True, store_code="ST9"))
ok(got == [], "D a store with nothing on hand → no rows (never 'all products')")
ok(codes(call(store_code="ST1")) == expect(fc, lambda r: True),
   "D store_code alone (in_stock off) adds no filter")
stock_reads = [e for e in fc.log if e[0] in ("inventory_serial", "inventory_standard")]
ok(stock_reads and all(e[1][0] == ("eq", "org_id", ORG) for e in stock_reads),
   "D every stock read is org-scoped first")

# chunking: 400 on-hand products → 3 chunks of ≤150, merged newest-first under the 500 cap
fc = use(build(n_filler=700, other_org=False))
fc.tables["inventory_standard"] += [
    {"id": f"TF{i}", "org_id": ORG, "product_id": f"P{1000 + i}", "store_code": "ST1", "qty_on_hand": 1}
    for i in range(0, 700, 2)]         # every other filler: 350 products
fc.log.clear()
got = codes(call(in_stock=True, store_code="ST1"))
want = sorted([1000 + i for i in range(0, 700, 2)] + [3, 1], reverse=True)
ok(got == want, f"D 352 on-hand products across chunks come back complete and newest-first ({len(got)})")
chunks = [e for e in fc.log if e[0] == "products" and any(f[0] == "in" for f in e[1])]
ok(len(chunks) == 3 and all(len([f for f in e[1] if f[0] == "in"][0][2]) <= 150 for e in chunks),
   f"D ids go out in chunks of ≤150 ({len(chunks)} chunk queries)")
fc.tables["inventory_standard"] += [
    {"id": f"TG{i}", "org_id": ORG, "product_id": f"P{1000 + i}", "store_code": "ST1", "qty_on_hand": 1}
    for i in range(1, 700, 2)]
got = codes(call(in_stock=True, store_code="ST1"))
ok(len(got) == 500 and got == sorted(got, reverse=True) and got[0] == 1699,
   "D more than 500 on hand → capped at 500, newest first, like the unfiltered list")

print("\n=== E · past the 500-row cap ===")
fc = use(build(n_filler=700))
default = codes(call())
ok(len(default) == 500 and 1 not in default and 5 not in default,
   "E the unfiltered list is the newest 500 — the old fixture products are past the cap")
ok(codes(call(manufacturer="Maker B")) == [5, 2], "E manufacturer filter reaches them server-side")
ok(codes(call(category_id="C-SM")) == [5, 1], "E category filter reaches them server-side")
ok(codes(call(inventory_type="serial")) == [5, 2, 1], "E inventory-type filter reaches them server-side")
ok(codes(call(in_stock=True)) == [5, 3, 1], "E in-stock filter reaches them server-side")

print("\n=== F · org-scoped ===")
fc = use(build())
fc.log.clear()
for kw in (dict(), dict(manufacturer="Maker A"), dict(inventory_type="serial", search="phone"),
           dict(in_stock=True), dict(category_id="C-CS", department_id="D-AC")):
    rows = call(**kw)["products"]
    ok(rows and all(r["org_id"] == ORG for r in rows), f"F only this org's rows for {kw or 'no params'}")
prod_reads = [e for e in fc.log if e[0] == "products"]
ok(prod_reads and all(e[1][0] == ("eq", "org_id", ORG) for e in prod_reads),
   f"F every products query carries org_id as its FIRST filter ({len(prod_reads)} queries)")
other = R.list_products(search="", department_id="", system_category="", active_only=True,
                        category_id="", manufacturer="Maker A", inventory_type="", in_stock=True,
                        store_code="", org_id=OTHER)["products"]
ok([r["id"] for r in other] == [] , "F the other org sees only ITS stock (P2-x is not in its in-stock set)")
ok(all(r["org_id"] == OTHER for r in R.list_products(
    search="", department_id="", system_category="", active_only=True, category_id="", manufacturer="",
    inventory_type="", in_stock=False, store_code="", org_id=OTHER)["products"]),
   "F and the other org's unfiltered list is its own")
# negative control: an unscoped helper would leak — prove the fake would catch it
leak = FakeClient(fc.tables)
unscoped = leak.schema("pos").table("products").select("*").eq("manufacturer", "Maker A").execute().data
ok(any(r["org_id"] == OTHER for r in unscoped),
   "F negative control: the same filter WITHOUT org_id returns the other tenant's rows (the fake bites)")

print("\n=== G · the manufacturer option list ===")
fc = use(build())
m = R.list_product_manufacturers(org_id=ORG)["manufacturers"]
ok(m == ["Maker A", "Maker B", "maker c"], f"G distinct, non-blank, A→Z ignoring case → {m}")
fc.tables["products"].append({"id": "PZ", "org_id": ORG, "product_code": 99, "short_name": "Old",
                              "manufacturer": "Zeta Retired", "is_active": False})
ok("Zeta Retired" not in R.list_product_manufacturers(org_id=ORG)["manufacturers"],
   "G active products only by default")
ok("Zeta Retired" in R.list_product_manufacturers(active_only=False, org_id=ORG)["manufacturers"],
   "G active_only=false includes a retired product's maker")
fc.tables["products"].append({"id": "PQ", "org_id": OTHER, "product_code": 98, "short_name": "Theirs",
                              "manufacturer": "Other Tenant Maker", "is_active": True})
ok("Other Tenant Maker" not in R.list_product_manufacturers(org_id=ORG)["manufacturers"],
   "G another org's manufacturer never appears")
fc.log.clear()
R.list_product_manufacturers(org_id=ORG)
ok(all(e[1][0] == ("eq", "org_id", ORG) for e in fc.log), "G the manufacturer read is org-scoped")

print("\n=== H · a bad inventory type is refused ===")
ok(raises(lambda: call(inventory_type="bogus"), "inventory_type must be one of"),
   "H inventory_type='bogus' → 400 naming the allowed values")

print("\n=== I · the special-order search answers as before ===")
fc = use(build())
for s in ("", "case", "Case Special", "x%,y"):
    fc.log.clear()
    old = before_special_order_products(ORG, s)
    ol = list(fc.log)
    fc.log.clear()
    new = R._special_order_products(ORG, s)
    ok(old == new and ol == fc.log, f"I special-order catalog search {s!r} identical (rows + query)")

print("\n=== J · the paged reader is byte-identical without `where` ===")
import app.modules.pos.catalog_suggest as CS  # noqa: E402
fc = build()
fc.log.clear()
rows = CS._page(fc, "pos", "departments", "id,short_name", ORG)
ok(fc.log[0][1] == [("eq", "org_id", ORG)] and len(rows) == 2,
   "J _page without `where` issues exactly the old query")
ok(PF.search_clause("  ") is None and PF.search_clause("a%b,c") == "short_name.ilike.%ab c%,full_name.ilike.%ab c%,upc.ilike.%ab c%",
   "J search_clause: blank → none; % and , stripped exactly as before")

print(f"\n══ POS product filters: {len(PASS)} passed, {len(FAIL)} failed ══")
if FAIL:
    sys.exit(1)
print("OK — one filter helper; each filter narrows; they combine with search; no new param = the old "
      "answer; in stock at the store; past the 500 cap; org-scoped.")
