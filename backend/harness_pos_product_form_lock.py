"""THE LOCK — a POS product has ONE form, and the register's "not found → create it" path writes
through it (owner 2026-10-16: "in the pos menu, let the user edit the product description fields and
add a product to the inventory if a new product is being entered manually, prompt the user to create
the new product if the product is not found"; CLAUDE.md 2026-09-02 duplicate check, 2026-09-20 "lock
it so it cannot un-wire"). stdlib only, DB-free.

    python3 harness_pos_product_form_lock.py        (from backend/)

  (a) ONE FORM COMPONENT — the catalog (pos/products/page.tsx) and the register (pos/sales/page.tsx)
      both import the default component from '../product-form' and render `<ProductFormFields`.
      Neither spells its own field list: no page under pos/ renders the product's own inputs
      (Short Name / Full Name / UPC / Manufacturer / Body Style) outside product-form.tsx.
  (b) ONE EMPTY FORM AND ONE PAYLOAD — the defaults (`EMPTY_PRODUCT_FORM`), the row→form mapper
      (`productFormFrom`), the POST/PATCH body (`productFormPayload`) and the save-enabled rule
      (`productFormReady`) are spelled only in product-form.tsx, and both pages dereference them.
      In particular the `body_style === 'Not Set' ? null` rule exists in exactly one place.
  (c) THE FIELD LIST AGREES WITH THE BACKEND — every key of ProductFormValue is in the router's
      PRODUCT_FIELDS whitelist, so no field the form offers is silently dropped on save.
  (d) THE NOT-FOUND PROMPT EXISTS — the register's picker offers to create what was searched for,
      and the quick-scan path that matches nothing lands in that same picker (one prompt, not two).
  (e) THE CREATE PATH PUTS STOCK ON HAND — it posts /pos/inventory/standard or
      /pos/inventory/serial, never an inventory table write of its own, and rings the product up.
  (f) ONE SUSPENDED-CART CLIENT — the register reaches the hold only through lib/pos-held-sale; it
      spells no /pos/held-sale URL itself, and it does not keep the cart in browser storage.
  (g) REGISTERED — index rows for the form, the register's create path and the suspended cart.
  Negative controls prove each rule goes red.
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_pass = _fail = 0

POS_FE = "frontend/src/app/(platform)/pos"
FORM = POS_FE + "/product-form.tsx"
CATALOG = POS_FE + "/products/page.tsx"
REGISTER = POS_FE + "/sales/page.tsx"
HELD_CLIENT = "frontend/src/lib/pos-held-sale.ts"
ROUTER = "backend/app/modules/pos/router.py"
INDEX = "docs/SYSTEM_DATA_FLOW_INDEX.md"

# The product's OWN inputs — their labels are what a second copy of the form would have to spell.
FORM_LABELS = ("Short Name *", "Full Name", "UPC / Barcode", "Manufacturer / Provider", "Body Style")
FORM_HELPERS = ("EMPTY_PRODUCT_FORM", "productFormFrom", "productFormPayload", "productFormReady")

# DECLARED, not counted. A size check ("at least 15 fields") is satisfied by a list that has lost a
# column and gained an unrelated one, so the set is written out by hand here: a field that leaves
# either side names itself in the diff this harness prints, and a field ADDED to the form without
# being added to the backend whitelist (where it would be silently dropped on save) goes red.
BACKEND_PRODUCT_FIELDS = {
    "upc", "short_name", "full_name", "department_id", "category_id", "system_category",
    "inventory_type", "manufacturer", "cost", "retail_price", "msrp", "is_taxable",
    "calculate_as_profit", "body_style", "is_active", "end_of_life",
}
# The form offers all of those except `msrp`, which no POS screen has ever exposed.
FORM_PRODUCT_FIELDS = BACKEND_PRODUCT_FIELDS - {"msrp"}


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name} {extra}")


def read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def fe_files():
    """Every .tsx/.ts under the POS frontend folder, plus the shared client helper."""
    out = {}
    base = os.path.join(ROOT, POS_FE)
    for dirpath, _dirs, files in os.walk(base):
        for f in files:
            if f.endswith((".tsx", ".ts")):
                full = os.path.join(dirpath, f)
                rel = POS_FE + full[len(base):].replace(os.sep, "/")
                out[rel] = read(rel)
    return out


# ── the rules, as functions, so a negative control can feed them a mutated source ─────────────────
def rule_page_uses_form(src):
    return ("from '../product-form'" in src) and ("<ProductFormFields" in src)


def rule_one_field_list(files):
    """Only product-form.tsx spells the product's own inputs."""
    return [rel for rel, src in files.items()
            if rel != FORM and sum(lbl in src for lbl in FORM_LABELS) >= 2]


# DECLARED EXCEPTION. pos/special-orders/manage/page.tsx also creates a catalogue row, but a
# NARROWER entity: a hidden-vendor special-order item (mig 864/865) written through
# POST /pos/special-orders/catalog, whose fields are 6 customer-facing ones plus the vendor linkage.
# It carries no department, category, inventory type, body style or cost, because a special-order
# item has none — folding it into the full product form would put five meaningless fields on an HQ
# screen. It is excused BY NAME here rather than silently: rule b6 pins that it stays narrow, so the
# day it grows the full field list this lock goes red and the two are made one.
SPECIAL_ORDER_FORM = POS_FE + "/special-orders/manage/page.tsx"
# The markers of the FULL product form's own default set — a page spelling two or more of these is
# carrying a second copy of the form, not a narrower entity of its own.
FULL_FORM_DEFAULTS = (r"inventory_type:\s*['\"]standard['\"]", r"calculate_as_profit:\s*true",
                      r"body_style:\s*['\"]Not Set['\"]", r"department_id:\s*['\"]['\"]")


def rule_one_payload(files):
    """The 'Not Set' → null rule, and the FULL default form object, exist only in product-form.tsx."""
    bad = []
    for rel, src in files.items():
        if rel == FORM:
            continue
        if re.search(r"body_style[^\n]*['\"]Not Set['\"]\s*\?", src):
            bad.append(rel + " (its own body_style payload rule)")
        if sum(bool(re.search(pat, src)) for pat in FULL_FORM_DEFAULTS) >= 2:
            bad.append(rel + " (its own copy of the full empty-form defaults)")
    return bad


def rule_special_order_stays_narrow(src):
    """The declared exception is still the narrow, different entity it was excused for being."""
    if "/api/v1/pos/special-orders/catalog" not in src:
        return False
    return not any(re.search(pat, src) for pat in FULL_FORM_DEFAULTS)


def rule_helpers_dereferenced(src):
    """A page must get the save BODY and the save-enabled RULE from the form, not spell either.
    Which other helpers a page needs differs — the catalog maps an existing row
    (`productFormFrom`), the register also reads a scanned entry (`productFormFromEntry`) — so
    those are checked where they belong (b5) rather than demanded of both."""
    return "productFormPayload(" in src and "productFormReady(" in src


def rule_held_one_client(files):
    """Only lib/pos-held-sale spells a /pos/held-sale URL, and the register keeps no cart in
    browser storage."""
    bad = []
    for rel, src in files.items():
        if "held-sale" in src and "from '@/lib/pos-held-sale'" not in src:
            bad.append(rel + " (its own held-sale URL)")
        if re.search(r"(localStorage|sessionStorage)[^\n]*cart", src, re.I):
            bad.append(rel + " (cart in browser storage)")
    return bad


print("\n(a)  one form component")
form = read(FORM)
catalog = read(CATALOG)
register = read(REGISTER)
files = fe_files()
check("a1 the catalog renders the shared form", rule_page_uses_form(catalog))
check("a2 the register renders the shared form", rule_page_uses_form(register))
check("a3 no OTHER POS page spells the product's own inputs",
      rule_one_field_list(files) == [], rule_one_field_list(files))
check("a4 the form itself is the one that spells them", all(lbl in form for lbl in FORM_LABELS))

print("\n(b)  one empty form, one payload, one save rule")
check("b1 the helpers are exported from the form", all(h in form for h in FORM_HELPERS))
check("b2 no page carries its own payload rule or defaults",
      rule_one_payload(files) == [], rule_one_payload(files))
check("b3 the catalog dereferences the payload and the save rule", rule_helpers_dereferenced(catalog))
check("b4 the register dereferences them too", rule_helpers_dereferenced(register))
check("b6 the DECLARED exception (the special-order admin item) is still the narrower entity it was "
      "excused for being — the day it grows the full field list, this goes red",
      rule_special_order_stays_narrow(read(SPECIAL_ORDER_FORM)))
check("b5 the form, not a page, decides what a scanned entry becomes",
      "productFormFromEntry" in form and "productFormFromEntry(" in register
      and not re.search(r"/\^\[0-9\]\{6,\}\$/", register))

print("\n(c)  the field list agrees with the backend whitelist")
router = read(ROUTER)
m = re.search(r"PRODUCT_FIELDS = \((.*?)\)", router, re.S)
backend_fields = set(re.findall(r'"([a-z_]+)"', m.group(1))) if m else set()
iface = re.search(r"export interface ProductFormValue \{(.*?)\n\}", form, re.S)
form_fields = set(re.findall(r"^\s*([a-z_]+):", iface.group(1), re.M)) if iface else set()
check("c1 the backend whitelist is EXACTLY the declared set — a column that leaves names itself here, "
      "rather than a size check that a shrinking list would still satisfy",
      backend_fields == BACKEND_PRODUCT_FIELDS,
      sorted(backend_fields ^ BACKEND_PRODUCT_FIELDS))
check("c2 the form's own field set is EXACTLY what it is declared to offer",
      form_fields == FORM_PRODUCT_FIELDS, sorted(form_fields ^ FORM_PRODUCT_FIELDS))
check("c3 every field the form offers is writable by the backend",
      form_fields and form_fields <= backend_fields, sorted(form_fields - backend_fields))

print("\n(d)  the not-found prompt")
check("d1 the picker offers to create what was searched for",
      "openProductCreate(" in register and "is not in the catalog" in register)
check("d2 it fires when the server search came back EMPTY",
      re.search(r"pickerResults\.length === 0[^\n]*\n(?:.*\n){0,24}?.*openProductCreate\(", register) is not None)
check("d3 a quick scan that matches nothing lands in that SAME picker (one prompt, not two)",
      re.search(r"function handleQuickScan[\s\S]{0,420}?setShowProductPicker\(true\)", register) is not None)
check("d4 the register does not roll its own confirm()/prompt() for this",
      not re.search(r"(?:window\.)?(?:confirm|prompt)\([^\n]*product", register, re.I))

print("\n(e)  the create path puts stock on hand, through the endpoints")
save = register.split("async function saveProductForm")[1].split("\n  function ")[0] if "async function saveProductForm" in register else ""
check("e1 the save path exists", bool(save))
check("e2 it creates the product through POST /pos/products", "'/api/v1/pos/products'" in save)
check("e3 a standard product's opening stock goes through POST /pos/inventory/standard",
      "/api/v1/pos/inventory/standard" in save)
check("e4 a serial product's first unit goes through POST /pos/inventory/serial (the IMEI guard)",
      "/api/v1/pos/inventory/serial" in save)
check("e5 it rings the new product up — which is why the cashier opened the form",
      "addToCart(created)" in save)
check("e6 a FAILED landing still reports, and never reads as no stock silently",
      "did not land" in save)
check("e7 the register writes no inventory table of its own",
      "inventory_standard" not in register and "inventory_serial" not in register)

print("\n(f)  one suspended-cart client")
held = read(HELD_CLIENT)
check("f1 the register reaches the hold only through lib/pos-held-sale",
      "from '@/lib/pos-held-sale'" in register)
check("f2 only that file spells a /pos/held-sale URL, and no POS page keeps the cart in browser storage",
      rule_held_one_client(files) == [], rule_held_one_client(files))
check("f3 the client owns the three calls and the debounce",
      all(k in held for k in ("fetchHeldSale", "suspendSale", "clearHeldSale", "AUTOSAVE_MS")))
check("f4 the autosave is GATED on the restore having run (or it overwrites the held cart with an empty one)",
      "heldRestored" in register and re.search(r"!activeStore \|\| !heldRestored", register) is not None)
check("f5 a checkout drops the hold", re.search(r"clearHeldSale\([\s\S]{0,80}?getRegisterNumber\(\)\)", register) is not None)
check("f6 the hold is keyed on the register, not just the store",
      register.count("getRegisterNumber()") >= 3)

print("\n(g)  registered")
idx = read(INDEX)
check("g1 the index names the shared form", "product-form.tsx" in idx)
check("g2 the index names the suspended cart", "pos-held-sale" in idx or "held_sale" in idx)
check("g3 the index names the create-on-not-found path", "/pos/inventory/standard" in idx)

print("\n(h)  negative controls")
check("neg-a1 a page that stops importing the form is RED",
      not rule_page_uses_form(register.replace("from '../product-form'", "from './my-form'")))
check("neg-a1b a page that stops rendering it is RED",
      not rule_page_uses_form(register.replace("<ProductFormFields", "<MyFields")))
check("neg-a3 a second copy of the field list anywhere under pos/ is RED",
      rule_one_field_list({**files, POS_FE + "/other/page.tsx":
                           "<label>Short Name *</label><label>Full Name</label>"}) != [])
check("neg-b2 a page spelling its own body_style payload rule is RED",
      rule_one_payload({**files, POS_FE + "/other/page.tsx":
                        "body_style: v.body_style === 'Not Set' ? null : v.body_style"}) != [])
check("neg-b2b a page spelling its own copy of the FULL empty-form defaults is RED",
      rule_one_payload({**files, POS_FE + "/other/page.tsx":
                        "inventory_type: 'standard', calculate_as_profit: true,"}) != [])
check("neg-b6 a special-order form that grew the full product field list is RED",
      not rule_special_order_stays_narrow(read(SPECIAL_ORDER_FORM)
                                          + "\nconst f = { inventory_type: 'standard' }\n"))
check("neg-c1 a backend whitelist that LOST a column is RED (a size check would not have caught it)",
      (backend_fields - {"msrp"}) != BACKEND_PRODUCT_FIELDS)
check("neg-c3 a form field the backend does not whitelist is RED",
      not ({"made_up_field"} | form_fields) <= backend_fields)
check("neg-e3 a create path that stopped landing stock is RED",
      "/api/v1/pos/inventory/standard" not in save.replace("/api/v1/pos/inventory/standard", "/nowhere"))
check("neg-f2 a page with its own held-sale URL is RED",
      rule_held_one_client({**files, POS_FE + "/other/page.tsx": "api('/api/v1/pos/held-sale')"}) != [])
check("neg-f2b a page keeping the cart in localStorage is RED",
      rule_held_one_client({**files, POS_FE + "/other/page.tsx":
                            "localStorage.setItem('cart', JSON.stringify(cart))"}) != [])
check("neg-f4 an ungated autosave is RED",
      re.search(r"!activeStore \|\| !heldRestored", register.replace("!activeStore || !heldRestored", "!activeStore")) is None)

print(f"\n{_pass} passed, {_fail} failed")
if _fail:
    sys.exit(1)
print("OK — one product form on both POS screens, one payload rule, the not-found prompt lands in one place, "
      "opening stock goes through the inventory endpoints, and one suspended-cart client.")
