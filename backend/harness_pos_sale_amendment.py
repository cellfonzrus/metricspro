#!/usr/bin/env python3
"""PROOF: editing a sale that is already rung, keeping its number (index §POS-12).

DB-free. Imports the REAL module, the real router and reads the real migration and the real page, so
a lock here fails the build rather than describing an intention.

Owner 2026-10-29: *"im not able to edit the receipt ot add more tiems to teh same rec3ipt"*. Offered
re-ringing under a new number, a second sale, or editing in place, he chose **edit in place, same
number** after being told plainly that it rewrites a tendered record.

WHAT THIS HARNESS IS FOR. Editing a tendered sale is the riskiest thing in the POS, and three of its
properties are the whole reason it is acceptable. Each is pinned here so a later change cannot quietly
remove one:

  §A  the rules are one home, and the router holds no second opinion
  §B  the line arithmetic is the server's, and the body's figures never survive
  §C  the superseded version is always kept — the edit cannot proceed without it
  §D  stock is reversed and re-applied by the SAME paths that have always moved it
  §E  the cart vocabulary is §POS-01's, not a second one
  §F  each line keeps the tax rate its sale was struck at
  §G  the window is declared, not hardcoded; the grant is its own
  §H  the till says what is happening, and says the printed receipt is now stale
  §I  negative controls — each lock really fails when its subject is broken
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.modules.pos import held_sale as HS           # noqa: E402
from app.modules.pos import sale_amendment as SA      # noqa: E402

MOD = (ROOT / "app/modules/pos/sale_amendment.py").read_text()
ROUTER = (ROOT / "app/modules/pos/router.py").read_text()
MIG = (ROOT.parent / "database/migrations/1103_pos_sale_amendment.sql").read_text()
PAGE = (ROOT.parent / "frontend/src/app/(platform)/pos/sales/page.tsx").read_text()
LIB = (ROOT.parent / "frontend/src/lib/pos-sale-amend.ts").read_text()

_passed = 0
_failed = 0


def ok(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def _try(fn, arg):
    """True when `fn(arg)` refuses with the module's own error — used by the locks that assert a
    refusal, and itself controlled by §I10 so a helper that never fires cannot pass them all."""
    try:
        fn(arg)
        return False
    except SA.AmendmentError:
        return True
    except Exception:
        return False


SALE = {"id": "s-1", "transaction_id": 4605, "status": "completed", "store_code": "B-1115",
        "customer_id": "c-1", "is_activation_sale": False, "revision": 1,
        "subtotal": 193.00, "discount_total": 2.00, "tax_total": 17.13, "total": 210.13,
        "created_at": "2026-10-29T14:00:00Z"}
PHONE = {"product_id": "p-1", "description": "Samsung A9 +", "product_type": "handset",
         "serial_number": "355123456789012", "qty": 1, "unit_price": 175.00, "list_price": 199.00,
         "cost": 120.00, "discount": 0, "tax_rate": 0.08875, "tax_value": 15.53,
         "extended_price": 190.53}
CASE = {"product_id": "p-2", "description": "Case", "product_type": "accessory",
        "serial_number": "", "qty": 2, "unit_price": 10.00, "list_price": 10.00, "cost": 3.00,
        "discount": 1.00, "tax_rate": 0.08875, "tax_value": 1.60, "extended_price": 19.60}
ITEMS = [PHONE, CASE]
NOW = "2026-10-29T19:30:00Z"


# ── §A the rules are one home ────────────────────────────────────────────────────────────────────
print("\n§A  one home for whether a sale may be edited")

ok("A1. the module answers it at all", SA.refusal(SALE, now=NOW, window_days=1) is None)
ok("A2. a voided sale is refused", bool(SA.refusal(dict(SALE, status="voided"), now=NOW,
                                                   window_days=1)))
ok("A3. an unreadable date is refused, never read as recent",
   bool(SA.refusal(dict(SALE, created_at="nonsense"), now=NOW, window_days=3650)))

# The router must DEREFERENCE the rules, not restate them. A second judgement there is how the Sale
# Log's button and the save come to disagree about the same sale.
_amend_region = ROUTER.split("# ── Editing a rung sale in place", 1)
ok("A4. the router has the amend region", len(_amend_region) == 2)
# BOUNDED at the next section, or the locks below would be counting the whole rest of the router
# and a count of "exactly three gated routes" would be meaningless.
REGION = (_amend_region[1].split("# ── Register (cash drawer) sessions", 1)[0]
          if len(_amend_region) == 2 else "")
ok("A4b. the region is bounded, not the rest of the file",
   0 < len(REGION) < 12000 and "/register/open" not in REGION, len(REGION))
ok("A5. the router calls the one home for the refusal", "_amend.refusal(" in REGION)
ok("A6. the router calls the one home for the plan", "_amend.plan(" in REGION)
# No second opinion: the router must not re-derive status, age or the window default itself.
ok("A7. the router does not re-judge 'voided' for itself",
   "voided" not in REGION.replace("sale_amendment", ""),
   "the router names a sale status; the refusal sentence belongs to the one home")
ok("A8. the router holds no window default of its own",
   not re.search(r"window_days\s*=\s*\d", REGION))
ok("A9. the refusal SENTENCE is written once — the client composes none",
   "reason ||" in LIB or "plan.reason" in PAGE,
   "the page shows the server's sentence verbatim")
ok("A10. every refusal is a sentence, not a code",
   all(isinstance(SA.refusal(s, now=NOW, window_days=1), (str, type(None)))
       and (SA.refusal(s, now=NOW, window_days=1) is None
            or len(SA.refusal(s, now=NOW, window_days=1).split()) > 3)
       for s in (SALE, dict(SALE, status="voided"), {}, None)))


# ── §B the figures are the server's ──────────────────────────────────────────────────────────────
print("\n§B  the line arithmetic is the server's, and the body's figures never survive")

ok("B1. the handset line reproduces what mig 725 stored (15.53 / 190.53)",
   SA.line_money(PHONE)["tax_value"] == 15.53
   and SA.line_money(PHONE)["extended_price"] == 190.53)
ok("B2. the discounted two-unit line reproduces it too (1.60 / 19.60)",
   SA.line_money(CASE)["tax_value"] == 1.60
   and SA.line_money(CASE)["extended_price"] == 19.60)
_lied = dict(CASE, extended_price=0.01, tax_value=0.00)
ok("B3. a body-supplied extended_price is DISCARDED",
   SA.line_money(_lied)["extended_price"] == 19.60)
ok("B4. a body-supplied tax_value is DISCARDED", SA.line_money(_lied)["tax_value"] == 1.60)
_t = SA.totals(ITEMS)
ok("B5. subtotal is tax-exclusive and net of discounts", _t["subtotal"] == 193.00)
ok("B6. subtotal + tax == total, by construction",
   round(_t["subtotal"] + _t["tax_total"], 2) == _t["total"])
ok("B7. tax rounds per line then sums (15.53 + 3.99 = 19.52 at qty 5)",
   SA.totals([PHONE, dict(CASE, qty=5)])["tax_total"] == 19.52)
# The ROUTER must send its own figures, never the body's.
ok("B8. the router builds the patch from the plan's totals",
   '**plan["totals"]' in REGION)
ok("B9. the router sends the plan's priced items, not the body's",
   'plan["items"]' in REGION and '"p_items": body' not in REGION)
ok("B10. the router never forwards a body total",
   not re.search(r'"(total|subtotal|tax_total|discount_total)":\s*body', REGION))
ok("B11. an empty set is refused rather than priced as zero", _try(SA.priced, []))


# ── §C the superseded version is always kept ─────────────────────────────────────────────────────
print("\n§C  the version the printed receipt was struck from is always kept")

_snap = SA.snapshot(SALE, ITEMS)
ok("C1. the snapshot keeps the sale's OWN stored total, not a re-derived one",
   _snap["total"] == 210.13,
   "re-deriving would record what the receipt should have said, not what it did")
ok("C2. the snapshot keeps every line", len(_snap["items"]) == 2)
ok("C3. a sale rung before the column existed is revision 1, not 0",
   SA.snapshot(dict(SALE, revision=None), ITEMS)["revision"] == 1
   and SA.next_revision(dict(SALE, revision=None)) == 2)
ok("C4. the migration creates the history table", "create table if not exists pos.sale_amendments" in MIG)
ok("C5. one record per superseded version — no double filing",
   "unique (sale_id, revision)" in MIG)
ok("C6. the function files the snapshot BEFORE it touches anything",
   MIG.index("insert into pos.sale_amendments") < MIG.index("delete from pos.sale_items"),
   "a failure after the snapshot still leaves the superseded version on record")
ok("C7. the snapshot is the row as it stands, figures included", "to_jsonb(s)" in MIG)
ok("C8. the endpoint REFUSES when the history table is absent",
   "_amend_available" in REGION and "503" in REGION,
   "a database that cannot keep the superseded version must not overwrite one")
ok("C9. the table is granted by the migration that creates it (§CORE-13)",
   "grant select, insert, update, delete on pos.sale_amendments to service_role;" in MIG)
ok("C10. the history is never widened to anon", "to anon" not in MIG)
ok("C11. STEPS states the snapshot comes first", SA.STEPS[0] == "file_snapshot")


# ── §D stock moves by the paths that have always moved it ────────────────────────────────────────
print("\n§D  stock is reversed and re-applied by the existing paths, not a hand-computed delta")

ok("D1. the function reverses serials to in_stock exactly as a void does",
   "set status = 'in_stock', sold_at = null, sold_in_sale_id = null" in MIG)
ok("D2. it puts non-serialised quantity back exactly as a void does",
   "qty_on_hand = st.qty_on_hand + coalesce(it.qty, 0)" in MIG)
ok("D3. it deletes the old lines and re-inserts, so the mig-725 trigger takes stock again",
   "delete from pos.sale_items where sale_id = p_sale" in MIG
   and "insert into pos.sale_items" in MIG)
ok("D4. the reverse precedes the re-apply",
   MIG.index("qty_on_hand = st.qty_on_hand + coalesce") < MIG.index("insert into pos.sale_items"))
ok("D5. STEPS states that order", SA.STEPS[1:] == ("reverse_old_lines", "apply_new_lines",
                                                   "restate_totals"))
ok("D6. the sale row is locked, so two cashiers cannot both file one revision",
   "for update" in MIG)
ok("D7. emptying a sale is refused in the FUNCTION too, not only in Python",
   "a sale needs at least one item" in MIG and "void it instead" in MIG)
ok("D8. the identity of the sale is left alone — no transaction_id in the update",
   not re.search(r"update pos\.sales[\s\S]*?set[\s\S]*?transaction_id\s*=", MIG),
   "the owner asked for the same number; nothing here may change it")
ok("D9. the revision is bumped by the function, never by the caller",
   "revision       = v_revision + 1" in MIG and "p_sale_patch->>'revision'" not in MIG)
ok("D10. the migration carries a REVERT note", "-- REVERT:" in MIG)


# ── §E one cart vocabulary ───────────────────────────────────────────────────────────────────────
print("\n§E  the cart is §POS-01's vocabulary, not a second one")

_cart = SA.cart_from(ITEMS)
ok("E1. every loaded line is within held_sale.LINE_FIELDS",
   all(set(l) <= set(HS.LINE_FIELDS) for l in _cart))
ok("E2. held_sale.clean_cart accepts it unchanged", HS.clean_cart(_cart) == _cart)
ok("E3. the module spells no line-field list of its own",
   "LINE_FIELDS = (" not in MOD and "_hs.LINE_FIELDS" in MOD)
ok("E4. the context is within held_sale.CONTEXT_FIELDS",
   "_hs.CONTEXT_FIELDS" in MOD or "CONTEXT_FIELDS = (" not in MOD)
ok("E5. the client reuses the cart-line type rather than declaring a second",
   "export type AmendLine = HeldLine" in LIB)
ok("E6. the page maps a stored line to a cart line in ONE place",
   PAGE.count("function cartItemFromLine") == 1
   and PAGE.count("product_id: l.product_id") == 1,
   "the restore effect and the edit path must share it")
ok("E7. both loaders use that one mapping",
   PAGE.count("cartItemFromLine(l)") >= 2)


# ── §F each line keeps the rate its sale was struck at ───────────────────────────────────────────
print("\n§F  a rung sale's tax rate is not restated")

ok("F1. an existing line keeps its stored rate", SA.cart_from(ITEMS)[0]["tax_rate"] == 0.08875)
ok("F2. the edit path does NOT re-price through recalcItem",
   "setCart(plan.cart.map(l => cartItemFromLine(l)))" in PAGE,
   "recalcItem would restate tax already struck, and for an older sale already remitted")
ok("F3. the suspended-cart path still DOES re-price (the sibling is unchanged)",
   "recalcItem(cartItemFromLine(l))" in PAGE)
ok("F4. the difference is stated where it would otherwise look like an oversight",
   "§POS-01.4" in PAGE and "in force" in PAGE.replace("\n", " "))
ok("F5. the migration does not recompute any rate", "tax_rate" in MIG and "* tax_rate" not in MIG)


# ── §G the window is declared; the grant is its own ──────────────────────────────────────────────
print("\n§G  declared config, and a right of its own")

ok("G1. the house default is one day", SA.HOUSE_WINDOW_DAYS == 1)
ok("G2. an unreadable declaration falls back to the house default, never to 'forever'",
   SA.declared_window("nonsense") == SA.HOUSE_WINDOW_DAYS
   and SA.declared_window(None) == SA.HOUSE_WINDOW_DAYS)
ok("G3. a declared window is honoured",
   SA.refusal(dict(SALE, created_at="2026-10-20T14:00:00Z"), now=NOW, window_days=30) is None)
ok("G4. the setting key is named in the one home and read from it",
   SA.WINDOW_SETTING == "sale_reopen_window_days" and "_amend.WINDOW_SETTING" in REGION)
ok("G5. the migration seeds no per-tenant row (RULE TWO)",
   "insert into pos.pos_settings" not in MIG.lower())
ok("G6. no carrier, tenant or product is named in the module",
   not re.search(r"(?i)\b(boost|total wireless|luxelink|cellfonz|verizon|vzone|samsung)\b", MOD))
ok("G7. editing has its own grant, not the void one",
   '"pos_amend"' in REGION and REGION.count('"pos_void"') == 0)
ok("G8. all three routes are gated", REGION.count("_require_pos_perm(") == 3)
ok("G9. every route is store-span scoped", REGION.count("_span_filter(") == 3)
ok("G10. every route is org-scoped", REGION.count('.eq("org_id", org_id)') >= 2)


# ── §H the till says what is happening ───────────────────────────────────────────────────────────
print("\n§H  the cashier is told what this is, and what it costs")

_plan = SA.plan(SALE, ITEMS, [PHONE], now=NOW, window_days=1)
ok("H1. the sentence names the transaction", "4605" in _plan["sentence"])
ok("H2. it names the revision being created", "revision 2" in _plan["sentence"])
ok("H3. it says the number does NOT change", "does not change" in _plan["sentence"])
ok("H4. it says the customer's printed receipt is now stale",
   "print them a new one" in _plan["sentence"])
ok("H5. a refused plan's sentence IS the reason — no second wording",
   SA.plan(dict(SALE, status="voided"), ITEMS, [PHONE], now=NOW,
           window_days=1)["sentence"] ==
   SA.plan(dict(SALE, status="voided"), ITEMS, [PHONE], now=NOW, window_days=1)["reason"])
ok("H6. a refused plan carries no items and no totals",
   SA.plan(dict(SALE, status="voided"), ITEMS, [PHONE], now=NOW,
           window_days=1)["items"] == []
   and SA.plan(dict(SALE, status="voided"), ITEMS, [PHONE], now=NOW,
               window_days=1)["totals"] == {})
ok("H7. an edit that changes nothing is refused",
   SA.plan(SALE, ITEMS, ITEMS, now=NOW, window_days=1)["allowed"] is False)
ok("H8. the register banner says it is not a new sale",
   "this is not a new sale" in PAGE)
ok("H9. the banner says the printed receipt is out of date",
   "out of" in PAGE and "date, so" in PAGE.replace("\n", " ").replace("  ", " "))
ok("H10. the green button SAVES while editing instead of tendering",
   "Save changes to #" in PAGE and "saveSaleEdit" in PAGE)
ok("H11. the suspended-cart autosave is OFF while editing",
   "!heldRestored || amending" in PAGE,
   "holding a rung sale's lines would offer the next session a sale already paid for")
ok("H12. a resumed hold cannot land on top of a sale being edited",
   "&& !amending" in PAGE)
ok("H13. a serialised unit is keyed by serial, not product",
   SA.line_key(PHONE)[0] == "serial" and SA.line_key(CASE)[0] == "product")
_d = SA.diff(ITEMS, [PHONE, dict(CASE, qty=5)])
ok("H14. a quantity change reads as a change, not an add plus a remove",
   not _d["added"] and not _d["removed"] and len(_d["changed"]) == 1)


# ── §I negative controls ─────────────────────────────────────────────────────────────────────────
print("\n§I  negative controls — each lock really fails when its subject is broken")

ok("I1. control: trusting the body's extended_price would be caught",
   dict(CASE, extended_price=0.01)["extended_price"] != SA.line_money(
       dict(CASE, extended_price=0.01))["extended_price"])
# C1 says the snapshot keeps the STORED total. On this fixture the stored and derived figures
# agree, so that lock alone could pass either way — this control gives the sale a stored total that
# its own lines do not add up to (a real case: a sale rung before a tax-rate correction) and proves
# the snapshot reports the stored one.
_stale = dict(SALE, total=999.99)
ok("I2. control: the snapshot reports the STORED total even when the lines disagree",
   SA.snapshot(_stale, ITEMS)["total"] == 999.99
   and SA.totals(ITEMS)["total"] != 999.99)
ok("I3. control: keying a serialised line on its product would hide a swapped IMEI",
   len(SA.diff(ITEMS, [dict(PHONE, serial_number="999"), CASE])["added"]) == 1)
ok("I4. control: an unreadable window really would widen it if it fell back to 0",
   SA.declared_window("nonsense") != 0)
ok("I5. control: the module is importable with no database and no app config",
   "get_supabase" not in MOD and "import os" not in MOD)
ok("I6. control: the module takes no clock of its own",
   "datetime.now" in MOD and MOD.count("datetime.now") == 1,
   "exactly one, in the `now`-defaulting helper; a second would be a hidden clock")
ok("I7. control: the one home is a module, not a method on the router",
   "class " not in MOD.split("class AmendmentError", 1)[1])
try:
    _parsed = ast.parse(MOD)
    _funcs = {n.name for n in ast.walk(_parsed) if isinstance(n, ast.FunctionDef)}
except SyntaxError:
    _funcs = set()
ok("I8. control: every function the router and tests call exists",
   {"refusal", "plan", "cart_from", "line_money", "totals", "priced", "diff", "snapshot",
    "current_revision", "next_revision", "declared_window", "line_key"} <= _funcs,
   sorted(_funcs))
ok("I9. control: the migration number is not already claimed",
   len(list((ROOT.parent / "database/migrations").glob("1103_*.sql"))) == 1)
ok("I10. control: B11's helper really detects a refusal",
   _try(SA.priced, []) is True and _try(SA.priced, [PHONE]) is False)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
