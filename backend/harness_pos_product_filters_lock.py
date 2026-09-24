"""THE LOCK — POS PRODUCT FILTERS have ONE backend filter helper and ONE frontend filter component (owner
2026-09-24: "create product filters on the pos page"; CLAUDE.md 2026-09-02 duplicate check, 2026-09-20 "lock it
so it cannot un-wire"). stdlib only, DB-free — runs in CI beside the other POS locks (carrier-vocab-guard.yml).
    python3 harness_pos_product_filters_lock.py        (from backend/)

  (a) ONE BACKEND HELPER — `GET /pos/products` (router.list_products) filters ONLY through
      pos/product_filters.py: it calls `_pf.clean(` and `_pf.list_rows(` and carries no `.eq(` / `.or_(` /
      `.table("products")` chain of its own. `apply` walks `EQ_FILTERS`, which names all five filters.
  (b) ONE SEARCH CLAUSE — the product search or-string (`short_name.ilike … full_name.ilike … upc.ilike`) is spelled
      only in product_filters.py; the special-order catalog calls `_pf.search_clause(`.
  (c) ORG-SCOPED — every `.table(...)` read in product_filters.py is org-scoped (`.eq("org_id"` or the org-scoped
      paged reader `_page(client, …, org_id`).
  (d) ONE FRONTEND COMPONENT — pos/sales/page.tsx and pos/products/page.tsx both import the default component AND
      `productFilterParams` from '../product-filters', render `<ProductFilters`, and build the filtered query with
      `productFilterParams(`.
  (e) ONE SPELLING OF THE PARAMS — under app/(platform)/pos/ only product-filters.tsx sets a filter param
      (department_id / category_id / system_category / manufacturer / inventory_type / in_stock) on a query string or
      writes one into a /pos/products URL.
  (f) STALE RESPONSES — both pages guard the filtered load with a monotonic ticket (index §23k).
  (g) REGISTERED — index rows for the endpoint, the params, the helper and the component; CI runs this lock and the
      proof (harness_pos_product_filters.py) and watches the files.
  Negative controls prove each rule goes red.
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_pass = _fail = 0

ROUTER = "backend/app/modules/pos/router.py"
HELPER = "backend/app/modules/pos/product_filters.py"
POS_FE = "frontend/src/app/(platform)/pos"
COMPONENT = POS_FE + "/product-filters.tsx"
PAGES = (POS_FE + "/sales/page.tsx", POS_FE + "/products/page.tsx")
FILTER_PARAMS = ("department_id", "category_id", "system_category", "manufacturer", "inventory_type", "in_stock")
INDEX = "docs/SYSTEM_DATA_FLOW_INDEX.md"
WORKFLOW = ".github/workflows/carrier-vocab-guard.yml"


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}" + (f"  — {str(extra)[:400]}" if extra != "" else ""))


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def func_body(src, name):
    """The source of top-level `def name(` up to the next top-level statement."""
    m = re.search(rf"^def {re.escape(name)}\(.*?(?=^\S)", src + "\nEOF", re.S | re.M)
    return m.group(0) if m else ""


# ── the rules, as functions over TEXT so the negative controls can feed them mutated text ──────────
def rule_handler(router_src):
    body = func_body(router_src, "list_products")
    return (bool(body) and "_pf.clean(" in body and "_pf.list_rows(" in body
            and ".eq(" not in body and ".or_(" not in body and 'table("products")' not in body)


def rule_helper(helper_src):
    m = re.search(r"EQ_FILTERS\s*=\s*\(([^)]*)\)", helper_src)
    names = re.findall(r'"(\w+)"', m.group(1)) if m else []
    apply_body = func_body(helper_src, "apply")
    return (set(names) == {"department_id", "category_id", "system_category", "manufacturer", "inventory_type"}
            and "for k in EQ_FILTERS" in apply_body and ".eq(k, filters[k])" in apply_body)


SEARCH_RX = re.compile(r"short_name\.ilike\.%\{\w+\}%,full_name\.ilike")


def rule_one_search_clause(files):
    """files: {rel: text} over backend/app — the clause only in the helper."""
    return sorted(rel for rel, t in files.items() if SEARCH_RX.search(t) and not rel.endswith("pos/product_filters.py"))


def rule_special_orders(router_src):
    return "_pf.search_clause(" in func_body(router_src, "_special_order_products")


def rule_org_scoped(helper_src):
    bad = []
    for m in re.finditer(r"\.table\(\"(\w+)\"\)", helper_src):
        chain = helper_src[m.start():m.start() + 160]
        if '.eq("org_id", org_id)' not in chain:
            bad.append(m.group(1))
    for m in re.finditer(r"_page\(([^)]*)", helper_src):
        if "org_id" not in m.group(1):
            bad.append("_page:" + m.group(1)[:40])
    return bad


def rule_page(page_src):
    imp = re.search(r"import\s+ProductFilters\s*,\s*\{([^}]*)\}\s*from\s*'\.\./product-filters'", page_src)
    return bool(imp and "productFilterParams" in imp.group(1)
                and "<ProductFilters" in page_src and "productFilterParams(" in page_src)


PARAM_SET_RX = re.compile(r"\.set\(\s*['\"](" + "|".join(FILTER_PARAMS) + r")['\"]")
PARAM_URL_RX = re.compile(r"/pos/products\?[^`'\"]*\b(" + "|".join(FILTER_PARAMS) + r")=")


def rule_one_spelling(files):
    """files: {rel: text} under the pos frontend folder — only the component spells a filter param."""
    return sorted(rel for rel, t in files.items()
                  if rel != COMPONENT and (PARAM_SET_RX.search(t) or PARAM_URL_RX.search(t)))


def rule_ticket(page_src):
    return bool(re.search(r"(\w+Ticket)\s*=\s*useRef\(0\)", page_src)
                and re.search(r"ticket\s*(===|!==)\s*\w+Ticket\.current", page_src))


def scan(root_rel, exts):
    out = {}
    base = os.path.join(ROOT, root_rel)
    for dp, _dn, fns in os.walk(base):
        for fn in fns:
            if fn.endswith(exts):
                p = os.path.join(dp, fn)
                out[os.path.relpath(p, ROOT).replace(os.sep, "/")] = io.open(p, encoding="utf-8").read()
    return out


# ═════════════════════════════════════════════════════════════════════════════════════════════════
router_src, helper_src = read(ROUTER), read(HELPER)
print("\n(a) one backend filter helper")
check("a1 list_products filters only through _pf.clean / _pf.list_rows — no filter chain of its own",
      rule_handler(router_src), func_body(router_src, "list_products")[:300])
check("a2 product_filters.apply walks EQ_FILTERS, which names all five filters", rule_helper(helper_src))

print("\n(b) one product search clause")
be_files = scan("backend/app", (".py",))
stray = rule_one_search_clause(be_files)
check("b1 the product search or-string is spelled only in pos/product_filters.py", not stray, stray)
check("b2 the special-order catalog search calls _pf.search_clause", rule_special_orders(router_src))

print("\n(c) org-scoped")
bad = rule_org_scoped(helper_src)
check("c1 every read in product_filters.py is org-scoped", not bad, bad)

print("\n(d) one frontend component, both pages")
check("d0 the shared component exists and exports ProductFilters + productFilterParams",
      os.path.exists(os.path.join(ROOT, COMPONENT))
      and "export default function ProductFilters" in read(COMPONENT)
      and "export function productFilterParams" in read(COMPONENT))
for pg in PAGES:
    check(f"d1 {pg.split('/pos/')[1]} imports and renders the ONE filter component, queries via productFilterParams",
          rule_page(read(pg)))

print("\n(e) one spelling of the filter params")
fe_files = scan(POS_FE, (".tsx", ".ts"))
stray = rule_one_spelling(fe_files)
check("e1 under pos/ only product-filters.tsx sets a product filter param", not stray, stray)

print("\n(f) stale responses")
for pg in PAGES:
    check(f"f1 {pg.split('/pos/')[1]} guards the filtered load with a monotonic ticket", rule_ticket(read(pg)))

print("\n(g) registered")
idx = read(INDEX)
check("g1 the index registers GET /pos/products/manufacturers and the new /pos/products params",
      "GET /pos/products/manufacturers" in idx and "inventory_type" in idx and "in_stock" in idx
      and "GET /pos/products" in idx)
check("g2 the index names the helper, the component and both harnesses",
      "product_filters.py" in idx and "product-filters.tsx" in idx
      and "harness_pos_product_filters.py" in idx and "harness_pos_product_filters_lock.py" in idx)
wf = read(WORKFLOW)
check("g3 CI runs the lock and the proof and watches the helper",
      "harness_pos_product_filters_lock.py" in wf and "harness_pos_product_filters.py" in wf
      and "backend/app/modules/pos/product_filters.py" in wf)

print("\n(neg) negative controls — each rule must go RED on a broken copy")
bad_handler = router_src.replace("rows = _pf.list_rows(", 'rows = client.schema("pos").table("products").select("*").eq("org_id", org_id).eq("department_id", department_id)\n    rows = _pf.list_rows(', 1)
check("neg-a1 a handler that grows its own .eq chain is RED", not rule_handler(bad_handler))
check("neg-a1b a handler that stops calling _pf.list_rows is RED",
      not rule_handler(router_src.replace("_pf.list_rows(", "_other_list(", 1)))
check("neg-a2 an EQ_FILTERS missing manufacturer is RED",
      not rule_helper(helper_src.replace('"manufacturer", ', "", 1)))
check("neg-b1 a second copy of the search clause elsewhere is RED",
      rule_one_search_clause({**be_files, "backend/app/modules/pos/other.py":
                              'q.or_(f"short_name.ilike.%{s}%,full_name.ilike.%{s}%")'}) != [])
check("neg-b2 a special-order search with its own clause is RED",
      not rule_special_orders(router_src.replace("_pf.search_clause(search)", "None", 1)))
check("neg-c1 an unscoped products read is RED",
      rule_org_scoped(helper_src.replace('.select("*").eq("org_id", org_id)', '.select("*")', 1)) != [])
sales = read(PAGES[0])
check("neg-d1 a page that stops importing the component is RED",
      not rule_page(sales.replace("from '../product-filters'", "from './my-filters'")))
check("neg-d1b a page that stops rendering it is RED", not rule_page(sales.replace("<ProductFilters", "<MyFilters")))
check("neg-e1 a page that spells params.set('manufacturer', …) itself is RED",
      rule_one_spelling({**fe_files, PAGES[1]: read(PAGES[1]) + "\nparams.set('manufacturer', m)\n"}) != [])
check("neg-e1b a page that writes ?category_id= into a /pos/products URL is RED",
      rule_one_spelling({**fe_files, PAGES[0]: sales + "\napi(`/api/v1/pos/products?category_id=${c}`)\n"}) != [])
check("neg-f1 a page without the ticket guard is RED",
      not rule_ticket(re.sub(r"\w+Ticket", "x", sales)))

print(f"\n{_pass} passed, {_fail} failed")
if _fail:
    sys.exit(1)
print("OK — one filter helper behind GET /pos/products, one filter component on both POS pages, one spelling of "
      "the params; org-scoped; registered.")
