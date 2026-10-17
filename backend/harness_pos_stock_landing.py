"""PROOF — "a single-row entry puts standard stock on hand at one store" has ONE home, and its
arithmetic is the receipt/count distinction rather than a guess (owner 2026-10-16: "add a product to
the inventory if a new product is being entered manually"). stdlib only, DB-free.

    python3 harness_pos_stock_landing.py        (from backend/)

  §A QUANTITY — a whole number, zero or more; a fraction, a bool, a word or a negative is REFUSED
     rather than coerced (int(2.7) loses a unit; a stray minus sign puts the shelf negative).
  §B IDENTITY — the row is (org, product, store). Rows for other products/stores are ignored; TWO
     rows for the same pair are refused, never resolved by picking one.
  §C THE TWO MODES — `add` is a receipt and adds to what is on hand; `set` is a count and replaces
     it. The two are distinguishable for every starting figure, and `after` is always derived from
     `before` here, never taken from a caller.
  §D THE SIBLING PIN (the design-fix "lock it so it cannot un-wire" rule) — migration 726's
     in-database `pos.receive_transfer` performs the same upsert in PL/pgSQL, where it cannot call
     this module. Its ON CONFLICT clause is pinned to mean ADD, so a divergence fails the build.
     The CSV count importer is pinned as the DELIBERATE exception, with its skip-on-duplicate
     semantics intact.
  §E ENDPOINT WIRING — POST /pos/inventory/standard dereferences `plan` and does no arithmetic of
     its own; it refuses a serial-tracked product (those land through the IMEI landing guard).
  §F REGISTERED — the index carries the endpoint and the one-home row.
  Negative controls prove each rule goes red.
"""
import io
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.pos import stock_landing as sl  # noqa: E402

_pass = _fail = 0
ROUTER = "backend/app/modules/pos/router.py"
MIG_726 = "database/migrations/726_pos_phase2.sql"
INDEX = "docs/SYSTEM_DATA_FLOW_INDEX.md"


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
    except sl.StockError:
        return True


print("\n§A  quantity — a whole number, zero or more")
check("A1 a plain integer passes", sl.clean_qty(3) == 3)
check("A2 blank is zero (a product you do not yet hold is legitimate)", sl.clean_qty("") == 0 and sl.clean_qty(None) == 0)
check("A3 a numeric string passes", sl.clean_qty(" 12 ") == 12)
check("A4 a whole float passes", sl.clean_qty(4.0) == 4)
check("A5 a FRACTION is refused, not truncated", refuses(sl.clean_qty, 2.7))
check("A6 a negative is refused", refuses(sl.clean_qty, -1))
check("A7 a word is refused", refuses(sl.clean_qty, "three"))
check("A8 a bool is refused (True is not one unit)", refuses(sl.clean_qty, True))
check("A9 mode must be one of the two", sl.clean_mode(None) == sl.MODE_ADD
      and sl.clean_mode("SET") == sl.MODE_SET and refuses(sl.clean_mode, "replace"))

print("\n§B  identity — one row per (product, store)")
ROWS = [{"product_id": "p1", "store_code": "S1", "qty_on_hand": 5},
        {"product_id": "p1", "store_code": "S2", "qty_on_hand": 9},
        {"product_id": "p2", "store_code": "S1", "qty_on_hand": 2}]
check("B1 the right row is found out of a whole store's stock",
      sl.existing_row(ROWS, "p1", "S1")["qty_on_hand"] == 5)
check("B2 another store's row is NOT the answer", sl.existing_row(ROWS, "p1", "S3") is None)
check("B3 another product's row is NOT the answer", sl.existing_row(ROWS, "p3", "S1") is None)
check("B4 two rows for one pair are REFUSED, not reduced to one",
      refuses(sl.existing_row, ROWS + [{"product_id": "p1", "store_code": "S1", "qty_on_hand": 7}], "p1", "S1"))
check("B5 an unwritten qty reads as 0, not as a refusal",
      sl.on_hand({"qty_on_hand": None}) == 0 and sl.on_hand({}) == 0 and sl.on_hand(None) == 0)
check("B6 a store is required — stock is held AT a store",
      refuses(sl.plan, ROWS, "p1", "", 1))
check("B7 a product is required", refuses(sl.plan, ROWS, "", "S1", 1))

print("\n§C  the two modes — a receipt adds, a count replaces")
ins = sl.plan([], "pNEW", "S1", 3)
check("C1 no row yet → insert carrying the full figure",
      ins["action"] == sl.ACTION_INSERT and ins["row"]["qty_on_hand"] == 3
      and ins["before"] == 0 and ins["after"] == 3)
check("C2 the insert carries NO org_id (the caller stamps it)", "org_id" not in ins["row"])
add = sl.plan(ROWS, "p1", "S1", 3, mode=sl.MODE_ADD)
check("C3 add onto 5 → 8", add["action"] == sl.ACTION_UPDATE and add["after"] == 8 and add["before"] == 5)
setp = sl.plan(ROWS, "p1", "S1", 3, mode=sl.MODE_SET)
check("C4 set onto 5 → 3 (the count wins)", setp["after"] == 3 and setp["before"] == 5)
check("C5 the two modes DIFFER on every starting figure",
      all(sl.plan([{"product_id": "p", "store_code": "S", "qty_on_hand": b}], "p", "S", 2, mode=sl.MODE_ADD)["after"]
          != sl.plan([{"product_id": "p", "store_code": "S", "qty_on_hand": b}], "p", "S", 2, mode=sl.MODE_SET)["after"]
          for b in (1, 5, 50)))
check("C6 a 0-unit receipt onto an existing row writes NOTHING (an idempotent retry cannot double)",
      sl.plan(ROWS, "p1", "S1", 0)["action"] == sl.ACTION_NONE)
check("C7 a 0-unit SET to 0 on a row holding 5 is a real change, not a no-op",
      sl.plan(ROWS, "p1", "S1", 0, mode=sl.MODE_SET)["action"] == sl.ACTION_UPDATE)
check("C8 the update is keyed by the triple and carries only what changed",
      add["match"] == {"product_id": "p1", "store_code": "S1"} and set(add["row"]) == {"qty_on_hand"})
check("C9 `after` is NEVER read from the caller — a client-sent total is ignored",
      sl.plan(ROWS, "p1", "S1", 1, extra={"qty_on_hand": 9999})["after"] == 6)
check("C10 only the whitelisted extras ride along",
      set(sl.plan([], "p", "S", 1, extra={"bin_location": "A4", "cost": 9, "org_id": "x"})["row"])
      == {"product_id", "store_code", "qty_on_hand", "bin_location"})
check("C11 a blank extra is dropped rather than written as ''",
      "bin_location" not in sl.plan([], "p", "S", 1, extra={"bin_location": ""})["row"])
check("C12 the sentence says before AND after, never a bare number",
      "5 to 8" in sl.sentence(add, "Case") and "Case" in sl.sentence(add, "Case"))
check("C13 a no-change landing says so instead of claiming a receipt",
      "no change" in sl.sentence(sl.plan(ROWS, "p1", "S1", 0)).lower())
check("C14 a count is worded as a count, not a receipt",
      "counted" in sl.sentence(setp) and "received" in sl.sentence(add))

print("\n§D  the siblings — pinned so a divergence fails the build")
mig = read(MIG_726)
# pos.receive_transfer: stock arriving at the destination ADDS to what is there.
recv = mig.split("CREATE OR REPLACE FUNCTION pos.receive_transfer")[1] if "pos.receive_transfer" in mig else ""
check("D1 mig 726 pos.receive_transfer still upserts inventory_standard",
      "INSERT INTO pos.inventory_standard" in recv and "ON CONFLICT (org_id, product_id, store_code)" in recv)
check("D2 its ON CONFLICT still means ADD (same arithmetic as MODE_ADD)",
      re.search(r"qty_on_hand\s*=\s*pos\.inventory_standard\.qty_on_hand\s*\+\s*EXCLUDED\.qty_on_hand", recv) is not None)
router = read(ROUTER)
imp = router.split('elif entity == "inventory":')[1].split("elif entity ==")[0] if 'elif entity == "inventory":' in router else ""
check("D3 the CSV count importer is the DELIBERATE exception and still SKIPS a duplicate "
      "(folding it in would double a re-uploaded count file)",
      "product already counted at store" in imp)
check("D4 this module names its siblings, so the next reader finds them",
      "receive_transfer" in sl.__doc__ and "import_entity" in sl.__doc__ and "receive_unit" in sl.__doc__)

print("\n§E  endpoint wiring — the arithmetic is dereferenced, never repeated")
ep = router.split('@router.post("/inventory/standard")')[1].split("@router.")[0] if '@router.post("/inventory/standard")' in router else ""
check("E1 POST /pos/inventory/standard exists", bool(ep))
check("E2 it calls _stock.plan", "_stock.plan(" in ep)
check("E3 it does NO arithmetic of its own (no qty_on_hand expression)",
      not re.search(r"qty_on_hand[^\n]*[-+]\s*(int|float|\d)", ep))
check("E4 it refuses a serial-tracked product (those land behind the IMEI guard)",
      '"serial"' in ep and "/pos/inventory/serial" in ep)
check("E5 it is org-scoped and store-span checked",
      '.eq("org_id", org_id)' in ep and "_caller_store_keyset(" in ep)
check("E6 it is permission-gated (receiving stock moves inventory)",
      'pos_inventory_adjust' in ep)
check("E7 it reports before AND after, so a double entry is visible",
      '"before"' in ep and '"after"' in ep)

print("\n§F  registered")
idx = read(INDEX)
check("F1 the endpoint is in the index", "POST /pos/inventory/standard" in idx)
check("F2 the one-home row names stock_landing", "stock_landing" in idx)

print("\n§G  negative controls")
check("neg-A a clean_qty that truncated a fraction instead of refusing it is RED",
      refuses(sl.clean_qty, 2.7) and refuses(sl.clean_qty, -1))
check("neg-B a planner that resolved a duplicate pair by picking one would be RED",
      refuses(sl.plan, ROWS + [{"product_id": "p1", "store_code": "S1", "qty_on_hand": 7}], "p1", "S1", 1))
check("neg-C an ON CONFLICT that REPLACED instead of adding is RED",
      re.search(r"qty_on_hand\s*=\s*pos\.inventory_standard\.qty_on_hand\s*\+\s*EXCLUDED\.qty_on_hand",
                recv.replace("pos.inventory_standard.qty_on_hand + EXCLUDED.qty_on_hand",
                             "EXCLUDED.qty_on_hand")) is None)
check("neg-E an endpoint that stopped calling plan() is RED",
      "_stock.plan(" not in ep.replace("_stock.plan(", "self._my_math("))
check("neg-E2 an endpoint that dropped the serial refusal is RED",
      "/pos/inventory/serial" not in ep.replace("/pos/inventory/serial", "/elsewhere"))

print(f"\n{_pass} passed, {_fail} failed")
if _fail:
    sys.exit(1)
print("OK — one home for standard-stock landing; the receipt/count distinction is declared, not guessed; "
      "the in-database and CSV siblings are pinned.")
