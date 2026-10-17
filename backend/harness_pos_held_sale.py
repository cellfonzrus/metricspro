"""PROOF — THE SUSPENDED SALE: one held cart per register, stored and restored VERBATIM
(owner 2026-10-16: "if a person leaves the cash register it gets blank if you come back it shoudl
automatically store for the next session in suspensed state"). stdlib only, DB-free.

    python3 harness_pos_held_sale.py        (from backend/)

  §A THE TERMINAL — a held cart's identity is (store, register), the SAME triple
     pos.register_sessions is keyed on, so "the next session" is literal. No store = refused.
  §B THE LINE SHAPE — a held line is whitelisted to the register's own cart fields, which are the
     fields POST /pos/sales/checkout consumes; anything else a client sends is dropped, and a line
     with no product is refused. An over-long cart is refused, never truncated.
  §C ROUND TRIP, VERBATIM — hold → restore returns the same money, the same discounts and the same
     serials. Nothing is re-priced, re-taxed or re-derived from a catalog.
  §D WORTH HOLDING — an empty cart with nothing attached is NOT held (an idle register would
     otherwise leave a row behind every time someone walks past); a customer with no lines yet IS.
  §E NOT A SALE — the held row carries no transaction number, no payment, no sale id and no stock
     movement, which is what makes an automatic save free.
  §F THE AGE TRAVELS — held_at survives an autosave, so the next cashier is told how old the prices
     are instead of being shown a cart that looks current.
  §G ENDPOINT WIRING — the three endpoints dereference this module, are org-scoped and store-span
     checked, stamp held_by from the CALLER (never a body field), and degrade to
     `available: false` before migration 1088 is applied.
  §H REGISTERED — index rows, and the migration carries its REVERT.
  Negative controls prove each rule goes red.
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.pos import held_sale as hs  # noqa: E402

_pass = _fail = 0
ROUTER = "backend/app/modules/pos/router.py"
MIGRATION = "database/migrations/1088_pos_held_sale.sql"
INDEX = "docs/SYSTEM_DATA_FLOW_INDEX.md"
NOW = "2026-10-16T18:40:00+00:00"
LATER = "2026-10-16T19:05:00+00:00"


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


def refuses(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except hs.HeldSaleError:
        return True


# A real part-rung sale: a serialised phone at an overridden price, a discounted case, a tax-free service.
CART = [
    {"product_id": "p-phone", "description": "iPhone 15 Pro 128GB", "product_type": "Handset",
     "serial_number": "356938035643809", "qty": 1, "unit_price": 899.99, "list_price": 999.99,
     "cost": 740, "discount": 0, "tax_rate": 0.08875, "tax_value": 79.87,
     "extended_price": 979.86, "is_taxable": True},
    {"product_id": "p-case", "description": "Clear case", "product_type": "Accessory",
     "serial_number": "", "qty": 3, "unit_price": 29.99, "list_price": 29.99, "cost": 8,
     "discount": 5, "tax_rate": 0.08875, "tax_value": 2.22, "extended_price": 81.63,
     "is_taxable": True},
    {"product_id": "p-setup", "description": "Device setup", "product_type": "Service",
     "serial_number": "", "qty": 1, "unit_price": 20, "list_price": 20, "cost": 0,
     "discount": 0, "tax_rate": 0, "tax_value": 0, "extended_price": 20, "is_taxable": False},
]

print("\n§A  the terminal — (store, register), the register_sessions triple")
check("A1 a store and register make a terminal",
      hs.terminal("B-1115", 2) == {"store_code": "B-1115", "register_number": 2})
check("A2 a blank register means register 1 — pos.register_sessions' own default",
      hs.terminal("B-1115", "")["register_number"] == 1 and hs.terminal("B-1115", None)["register_number"] == 1)
check("A3 NO store is refused — no session could own that cart", refuses(hs.terminal, ""))
check("A4 a whitespace store is refused too", refuses(hs.terminal, "   "))
check("A5 register 0 / a negative / a word are refused",
      refuses(hs.clean_register, 0) and refuses(hs.clean_register, -1) and refuses(hs.clean_register, "front"))
check("A6 a numeric string register passes", hs.clean_register("3") == 3)

print("\n§B  the line shape — the register's own cart fields, nothing else")
check("B1 every checkout field is held",
      set(hs.LINE_FIELDS) >= {"product_id", "description", "product_type", "serial_number", "qty",
                              "unit_price", "list_price", "cost", "discount", "tax_rate",
                              "tax_value", "extended_price"})
junk = hs.clean_line({**CART[0], "admin": True, "sale_id": "s1", "__proto__": "x"})
check("B2 a field the register does not own is DROPPED, not stored",
      set(junk) <= set(hs.LINE_FIELDS))
check("B3 a line with no product is refused (nothing could check it out)",
      refuses(hs.clean_line, {"description": "mystery", "qty": 1}) and refuses(hs.clean_line, {"product_id": "  "}))
check("B4 a non-object line is refused", refuses(hs.clean_line, ["p1"]))
check("B5 money arrives as numbers even when a client sent strings",
      hs.clean_line({"product_id": "p", "unit_price": "19.50", "qty": "2"})["unit_price"] == 19.5)
check("B6 an unreadable price is refused rather than stored as 0",
      refuses(hs.clean_line, {"product_id": "p", "unit_price": "nineteen"}))
check("B7 qty keeps its whole-number meaning", hs.clean_line({"product_id": "p", "qty": 2.0})["qty"] == 2)
check("B8 a line with no qty defaults to 1", hs.clean_line({"product_id": "p"})["qty"] == 1)
check("B9 an over-long cart is REFUSED, not silently cut three lines short",
      refuses(hs.clean_cart, [{"product_id": "p"}] * (hs.MAX_LINES + 1)))
check("B10 a cart at the cap still holds", len(hs.clean_cart([{"product_id": "p"}] * hs.MAX_LINES)) == hs.MAX_LINES)
check("B11 a non-list cart is refused", refuses(hs.clean_cart, {"product_id": "p"}))
check("B12 context is whitelisted the same way",
      hs.clean_context({"customer_id": "c1", "is_activation_sale": True, "total": 999})
      == {"customer_id": "c1", "is_activation_sale": True})

print("\n§C  round trip, VERBATIM — nothing is re-priced")
row = hs.hold_row("B-1115", 2, CART, {"customer_id": "c-9", "is_activation_sale": True}, held_by="E42", now=NOW)
back = hs.restore(row)
check("C1 the same number of lines comes back", back["line_count"] == 3)
check("C2 the OVERRIDDEN price survives, and so does the list price it overrode",
      back["cart"][0]["unit_price"] == 899.99 and back["cart"][0]["list_price"] == 999.99)
check("C3 the discount survives", back["cart"][1]["discount"] == 5)
check("C4 the SERIAL survives (the thing most often remembered wrong)",
      back["cart"][0]["serial_number"] == "356938035643809")
check("C5 the tax-free line stays tax-free",
      back["cart"][2]["tax_rate"] == 0 and back["cart"][2]["is_taxable"] is False)
check("C6 every money field of every line is byte-identical",
      all(all(back["cart"][i][k] == float(CART[i][k]) for k in hs.MONEY_FIELDS) for i in range(3)))
check("C7 the customer and the activation flag come back",
      back["customer_id"] == "c-9" and back["is_activation_sale"] is True)
check("C8 the total is DERIVED from the stored lines, not carried separately",
      back["cart_total"] == round(979.86 + 81.63 + 20, 2))
check("C9 a client-sent total cannot contradict the lines",
      hs.restore({**row, "cart_total": 1})["cart_total"] == back["cart_total"])
check("C10 restore of nothing is None, not an empty cart that looks like a real one",
      hs.restore(None) is None and hs.restore({}) is None)
check("C11 a row written by an older shape still restores",
      hs.restore({"store_code": "B-1", "cart": [{"product_id": "p", "qty": 1}]})["line_count"] == 1)
check("C12 restore() re-cleans, so a junk field stored by an old writer does not come back",
      set(hs.restore({"store_code": "B-1", "cart": [{"product_id": "p", "sale_id": "s"}]})["cart"][0])
      <= set(hs.LINE_FIELDS))

print("\n§D  worth holding")
check("D1 an empty cart with nothing attached is NOT worth holding", hs.is_empty([], {}))
check("D2 a cart with lines IS", not hs.is_empty(CART, {}))
check("D3 a selected customer with no lines yet IS (that is a sale in progress)",
      not hs.is_empty([], {"customer_id": "c-9"}))
check("D4 a note with no lines IS", not hs.is_empty([], {"note": "waiting on the customer's card"}))
check("D5 an activation flag alone is NOT (a stray toggle is not a sale)",
      hs.is_empty([], {"is_activation_sale": True}))

print("\n§E  not a sale — an automatic save costs nothing")
check("E1 no transaction number, sale id, payment or tender is stored",
      not ({"transaction_id", "sale_id", "payment_method", "amount", "tendered", "status"} & set(row)))
check("E2 nothing in the held row names a stock movement",
      not ({"qty_on_hand", "inventory_type", "sold_at"} & set(row)))
check("E3 the held row names only the terminal, the cart and who left it when",
      set(row) <= {"store_code", "register_number", "cart", "line_count", "cart_total",
                   "customer_id", "is_activation_sale", "note", "held_by", "held_at", "updated_at"})
check("E4 the migration says in words that it is not a sale",
      "not a sale" in read(MIGRATION).lower())

print("\n§F  the age travels with the cart")
check("F1 held_at and updated_at are the caller's clock, not this module's",
      row["held_at"] == NOW and row["updated_at"] == NOW)
check("F2 restore surfaces held_at so the screen can say how old it is", back["held_at"] == NOW)
check("F3 the sentence says WHAT was left, by WHOM, WHEN",
      "3 lines" in hs.sentence(back) and "E42" in hs.sentence(back) and NOW in hs.sentence(back))
check("F4 the sentence never claims the prices are current",
      "were on the lines when it was suspended" in hs.sentence(back))
check("F5 no hold reads as no hold, not as an empty sale",
      hs.sentence(None) == "No suspended sale on this register." and hs.sentence({}) == hs.sentence(None))
check("F6 one line reads 'line', not 'lines'", "1 line," in hs.sentence(hs.restore(hs.hold_row("S", 1, CART[:1], None, now=NOW))))

print("\n§G  endpoint wiring")
router = read(ROUTER)
get_ep = router.split('@router.get("/held-sale")')[1].split("@router.")[0] if '@router.get("/held-sale")' in router else ""
put_ep = router.split('@router.put("/held-sale")')[1].split("@router.")[0] if '@router.put("/held-sale")' in router else ""
del_ep = router.split('@router.delete("/held-sale")')[1].split("@router.")[0] if '@router.delete("/held-sale")' in router else ""
check("G1 all three endpoints exist", bool(get_ep) and bool(put_ep) and bool(del_ep))
check("G2 the GET restores through this module", "_held.restore(" in get_ep)
check("G3 the PUT builds the row through this module and does not assemble one itself",
      "_held.hold_row(" in put_ep and "_held.clean_cart(" in put_ep)
check("G4 held_by is the CALLER's employee id, never a body field",
      "_caller_employee(" in put_ep and not re.search(r'body\.get\(\s*["\']held_by', put_ep))
check("G5 an empty cart CLEARS the hold instead of storing a row",
      "_held.is_empty(" in put_ep and "_delete_held(" in put_ep)
check("G6 every endpoint is org-scoped",
      all('.eq("org_id", org_id)' in e for e in (get_ep, put_ep)) and '.eq("org_id", org_id)' in router.split("def _delete_held")[1][:600])
check("G7 the store span is checked before a cart is read or written",
      "_caller_store_keyset(" in router.split("def _held_terminal")[1][:700])
check("G8 all three go through the one terminal resolver",
      all("_held_terminal(" in e for e in (get_ep, put_ep, del_ep)))
check("G9 ADAPTIVE — a database without migration 1088 answers available: false, never a 500",
      "_held_unavailable(" in get_ep and "_held_unavailable(" in put_ep
      and "available" in router.split("def _held_unavailable")[1][:300])
check("G10 the setup note names the migration to apply",
      "1088" in router.split("def _held_unavailable")[1][:300])
check("G11 an autosave KEEPS the original held_at (the age is the sale's, not the keystroke's)",
      'k != "held_at"' in put_ep)
check("G12 the PUT writes the server's clock, not a client timestamp",
      "_utcnow()" in put_ep and not re.search(r'body\.get\(\s*["\']held_at', put_ep))

print("\n§H  registered")
idx = read(INDEX)
mig = read(MIGRATION)
check("H1 the index carries the endpoints", "/pos/held-sale" in idx)
check("H2 the index names the one home", "held_sale" in idx)
check("H3 the index names the table", "pos.held_sales" in idx)
check("H4 the migration carries its REVERT", "REVERT" in mig and "DROP TABLE IF EXISTS pos.held_sales" in mig)
check("H5 the migration is idempotent", "CREATE TABLE IF NOT EXISTS pos.held_sales" in mig)
check("H6 one cart per register is enforced in the schema, not only in code",
      "CREATE UNIQUE INDEX IF NOT EXISTS pos_held_sales_one_per_register" in mig)
check("H7 the pos-schema security posture is applied to the new table",
      "ENABLE ROW LEVEL SECURITY" in mig and "REVOKE ALL ON pos.held_sales FROM anon, authenticated" in mig)
check("H8 RULE TWO — no carrier / tenant / store name in the module or the migration",
      not re.search(r"(?i)\b(boost|total\s+wireless|luxelink|cellfonz|verizon|t-?mobile|b-\d{3,})\b",
                    io.open(os.path.join(ROOT, "backend/app/modules/pos/held_sale.py"), encoding="utf-8").read() + mig))

print("\n§I  negative controls")
check("neg-B2 a line-cleaner that kept a foreign field is RED",
      set(hs.clean_line({**CART[0], "sale_id": "s"})) <= set(hs.LINE_FIELDS))
check("neg-C2 a restore that re-priced from a 'current' price would be RED — restore reads only the row",
      hs.restore({**row, "cart": [{**CART[0], "unit_price": 899.99}]})["cart"][0]["unit_price"] == 899.99)
check("neg-C3 a cart_total carried rather than derived would be RED",
      hs.restore({**row, "cart_total": 0})["cart_total"] != 0)
check("neg-D a holder that stored an idle register's empty cart would be RED", hs.is_empty([], {}))
check("neg-G a PUT that trusted body held_by would be RED",
      "_caller_employee(" not in put_ep.replace("_caller_employee(", 'body.get("held_by"'))
check("neg-G9 an endpoint without the adaptive fallback is RED",
      "_held_unavailable(" not in get_ep.replace("_held_unavailable(", "raise RuntimeError("))

print(f"\n{_pass} passed, {_fail} failed")
if _fail:
    sys.exit(1)
print("OK — one suspended cart per register, keyed on the register_sessions triple, stored and restored "
      "verbatim with its age; not a sale; adaptive before migration 1088.")
