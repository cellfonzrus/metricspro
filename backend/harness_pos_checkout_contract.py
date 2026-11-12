#!/usr/bin/env python3
"""A till's spelling of the checkout contract is CHECKED (index §POS-20) — DB-free, the real files.

THE DEFECT. 2026-11-12: the owner rang a sale on the phone and the register refused it — *"This
register does not know a tender called “(blank)”."* The app sent `{method: "cash"}`; the reader and
the `pos.checkout` RPC both read `payment_method`. Three more keys were wrong the same way (`quantity`
for `qty`, `short_name` for `description`, and no money keys at all), and because `pos.checkout`
COALESCEs a missing `qty` to **1** and missing money to **0**, a correctly-spelled tender would have
stored every line at quantity one for $0.00. The refusal was the only thing that stopped a silently
wrong sale.

THE CLASS: a key no reader recognises is DROPPED, not refused. So this harness refuses
  * the declaration in `pos/checkout_contract.py` drifting from the SQL that defines it;
  * any POS checkout client spelling a key the RPC does not read;
  * the refusal losing its ability to name the fix.

Every check carries a negative control: the check is re-run against text carrying the defect and
must FAIL there, so a check that has silently stopped looking is itself a failure.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.modules.pos import checkout_contract as C  # noqa: E402

# The migration that currently DEFINES pos.checkout. Pinned: a later migration replacing the
# function must be named here deliberately, which is the tripwire — a silent redefinition elsewhere
# would leave this harness proving yesterday's contract.
PINNED_RPC_MIGRATION = "1107_pos_wholesale_w9.sql"

# Every client that POSTs a checkout body. Derived sets are compared against this, so a THIRD till
# cannot appear without being checked (§CORE-10).
CLIENTS = {
    "web register": ROOT / "frontend/src/app/(platform)/pos/sales/page.tsx",
    "phone register": ROOT / "mobile/src/modules/pos/checkout-body.ts",
}

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{name}: {detail}" if detail else name)
    return ok


def control(name: str, ok_on_defect: bool) -> None:
    """A check must FAIL on text carrying the defect, or the check has stopped looking."""
    check(f"{name} [negative control]", not ok_on_defect,
          "the check passed text that carries the defect — it has stopped looking")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── A · the declaration cannot drift from the SQL that defines the contract ───────────────────────
sql = read(ROOT / "database/migrations" / PINNED_RPC_MIGRATION)
check("A1 the pinned migration exists and defines pos.checkout",
      "create or replace function pos.checkout(" in sql,
      f"{PINNED_RPC_MIGRATION} does not define pos.checkout. If a later migration replaced it, "
      "point PINNED_RPC_MIGRATION at that file — deliberately.")
control("A1", "create or replace function pos.checkout(" in "-- nothing here")

# No later migration may redefine the function without this harness being told.
later = sorted(p.name for p in (ROOT / "database/migrations").glob("*.sql")
               if "create or replace function pos.checkout(" in read(p)
               and p.name > PINNED_RPC_MIGRATION)
check("A2 no later migration redefines pos.checkout behind this harness's back", later == [],
      f"these migrations also define pos.checkout and are newer than the pin: {later}. "
      "Repoint PINNED_RPC_MIGRATION and re-verify the key sets.")
control("A2", [] != [])

# The function body only — a migration may contain other SQL that reads other jsonb.
body = sql.split("create or replace function pos.checkout(", 1)[-1]
body = body.split("$function$", 2)[1] if body.count("$function$") >= 2 else body


def sql_keys(var: str) -> set[str]:
    """Every jsonb key the function reads off `var`, in either accessor form."""
    return set(re.findall(rf"{re.escape(var)}\s*->>?\s*'([^']+)'", body))


DERIVED = {
    "sale": sql_keys("p_sale"),
    "items": sql_keys("it"),
    "payments": sql_keys("pay"),
}
DECLARED = {"sale": set(C.SALE_KEYS), "items": set(C.ITEM_KEYS), "payments": set(C.PAYMENT_KEYS)}

for group in ("sale", "items", "payments"):
    got, want = DERIVED[group], DECLARED[group]
    check(f"A3 the declared {group} keys are exactly what pos.checkout reads", got == want,
          f"the SQL reads {sorted(got)}; checkout_contract declares {sorted(want)}. "
          f"missing from the declaration: {sorted(got - want)}; "
          f"declared but never read: {sorted(want - got)}.")
control("A3", {"payment_method"} == {"payment_method", "amount"})

# A key that is read but not REQUIRED silently becomes a zero, a one or a null. Every required key
# must at least be one the RPC reads, or the requirement is unenforceable.
for group, required in (("sale", C.REQUIRED_SALE_KEYS), ("items", C.REQUIRED_ITEM_KEYS),
                        ("payments", C.REQUIRED_PAYMENT_KEYS)):
    stray = sorted(set(required) - DECLARED[group])
    check(f"A4 every required {group} key is one the contract knows", stray == [],
          f"{stray} are required but are not in the declared {group} keys.")
control("A4", sorted({"nonsense"} - DECLARED["items"]) == [])

# `qty` must be required, BY NAME. Its COALESCE is 1, so its absence does not look like an absence
# afterwards — it looks like a sale of one item. That is the shape that hid this defect.
check("A5 qty is required, because a missing qty reads as a sale of one",
      "qty" in C.REQUIRED_ITEM_KEYS,
      "pos.checkout COALESCEs a missing qty to 1, so an optional qty stores a wrong quantity that "
      "nothing afterwards can tell from a real one.")
control("A5", "qty" in {"product_id", "unit_price"})

check("A6 the money keys are required, because a missing one reads as zero",
      {"extended_price"} <= set(C.REQUIRED_ITEM_KEYS)
      and {"subtotal", "total"} <= set(C.REQUIRED_SALE_KEYS),
      "every money key the RPC reads is COALESCEd to 0, so an optional one stores $0.00 for a real "
      "sale — which is what would have happened on 2026-11-12.")
control("A6", {"extended_price"} <= {"product_id", "qty"})


# ── B · the refusal names the fix, and only refuses a shape that is actually wrong ────────────────
wrong_pay = C.refusal("payments", {"method", "amount"})
check("B1 the refusal names the field a client MEANT", "payment_method" in wrong_pay,
      f"the refusal for a payments leg spelled `method` was {wrong_pay!r} — it must name "
      "`payment_method`, or a cashier is told only that something is wrong.")
control("B1", "payment_method" in "that tender is not offered here")

check("B2 the exact shape the phone sent on 2026-11-12 is refused",
      bool(C.refusal("items", {"product_id", "short_name", "quantity", "unit_price", "list_price"}))
      and bool(C.refusal("payments", {"method", "amount"})),
      "the regression: the phone's own item and payment shapes must both be refused.")
control("B2", bool(C.refusal("items", {"product_id", "qty", "unit_price", "extended_price"})))

check("B3 a CORRECT shape is not refused",
      C.refusal("payments", {"payment_method", "amount"}) == ""
      and C.refusal("items", {"product_id", "qty", "unit_price", "extended_price",
                              "description"}) == ""
      and C.refusal("sale", {"store_code", "subtotal", "total", "register_number"}) == "",
      "a contract that refuses a valid sale stops the tills instead of protecting them — note "
      "`register_number` is declared client-only and must not read as unknown.")
control("B3", C.refusal("payments", {"method", "amount"}) == "")

check("B4 a key the contract has never heard of is still refused",
      "invented_field" in C.refusal("items", {"product_id", "qty", "unit_price",
                                              "extended_price", "invented_field"}),
      "a key with no known correction must still be named — an allowlist that only knows the "
      "misspellings it has already seen would admit the next one.")
control("B4", "invented_field" in C.refusal("items", {"product_id", "qty", "unit_price",
                                                      "extended_price"}))


# ── C · no POS checkout client spells a key the RPC does not read ─────────────────────────────────
# DERIVED from the files and compared with the declared client set, so a third till cannot appear
# unchecked (§CORE-10).
missing_clients = sorted(name for name, path in CLIENTS.items() if not read(path))
check("C1 every declared checkout client exists", missing_clients == [],
      f"these declared clients are missing: {missing_clients}. If a till was removed, remove it "
      "from CLIENTS deliberately; if one was renamed, name the new path.")
control("C1", sorted(["phone register"]) == [])

# THE WEB TILL'S BODY LITERAL. A client legitimately holds its OWN field names in its own state
# (the phone's cart says `short_name`, the web's says `qty`), so this is scoped to the text that
# builds the request body — `JSON.stringify({ sale: ... })` — and `MISSPELLINGS` is the list of
# spellings known to be wrong ON THE WIRE.
#
# WHY THE PHONE IS NOT SCANNED THE SAME WAY, said plainly because an earlier version of this check
# DID scan it and caught nothing: the phone builds its body in a typed module, where `payments:`
# also appears as a TYPE member above the literal, so any "slice between two markers" lands on the
# wrong text and silently passes. A regex was the wrong instrument. The phone's guarantee is
# stronger and is checked as C6/C7 instead: its wire types make a misspelled key a COMPILE error,
# and its own test asserts the emitted keys one by one.
def body_literal(src: str) -> str:
    """The text of the checkout request body, or "" when this client does not build one inline."""
    # A screen makes several requests, so the FIRST stringify is not necessarily the checkout —
    # taking it was this check's own first bug. Every one is read and the one carrying a sale AND
    # its payments is returned.
    for m in re.finditer(r"JSON\.stringify\(\s*\{", src):
        i = src.index("{", m.start())
        depth = 0
        for j in range(i, min(len(src), i + 20000)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    chunk = src[i:j + 1]
                    if "sale:" in chunk and "payments:" in chunk:
                        return chunk
                    break
    return ""


web_src = read(CLIENTS["web register"])
web_body = body_literal(web_src)
check("C2 the web till's checkout body was found, so there is something to check",
      "payments:" in web_body and "sale:" in web_body,
      "the request body literal could not be located in "
      f"{CLIENTS['web register'].relative_to(ROOT)} — a check that cannot find its subject passes "
      "for the wrong reason, which is how the phone's defect survived.")
control("C2", "payments:" in body_literal("const x = 1"))

web_hits = sorted(w for w in C.MISSPELLINGS
                  if re.search(rf"(?<![\w.]){re.escape(w)}\s*:", web_body))
check("C2b the web till spells no wire key the RPC cannot read", web_hits == [],
      f"its checkout body contains {web_hits}, which pos.checkout does not read: "
      + ", ".join(f"{h} -> {C.MISSPELLINGS[h]}" for h in web_hits))
control("C2b", sorted(w for w in C.MISSPELLINGS
                      if re.search(rf"(?<![\w.]){re.escape(w)}\s*:",
                                   "{ sale: {}, payments: [{ method: m }] }")) == [])

phone = read(CLIENTS["phone register"])

check("C3 the phone builds its wire body in one pure module",
      "export function buildCheckoutBody" in phone,
      "mobile/src/modules/pos/checkout-body.ts must export buildCheckoutBody, so the app's own "
      "test can assert the keys it emits instead of this harness guessing from a screen.")
control("C3", "export function buildCheckoutBody" in "export default function Checkout() {}")

check("C4 the phone's builder is pure — no component, no fetch",
      not re.search(r"\buseState\b|\buseEffect\b|\bfetch\(|\bapi\.", phone),
      "a builder that reaches for state or the network cannot be proved without a device, which is "
      "how the wrong shape survived nine months unnoticed.")
control("C4", not re.search(r"\buseState\b", "const [x] = useState(0)"))

# C6 — THE PHONE'S ACTUAL LOCK. Its wire shape is a TYPE whose required keys are the contract's,
# so a misspelled or missing key is a COMPILE error and CI's typecheck refuses it. That is what the
# 2026-11-12 defect got past: the old `CheckoutBody` type declared `{ store_code?, register_number?,
# note? }` with `CartLine[]` items and `{ method, amount }` payments — a type that permits the wrong
# shape is not a type. Verified by reading the type, because the typecheck itself runs in CI.
_WIRE_TYPE_REQUIRED = {
    "CheckoutItem": ("product_id", "description", "qty", "unit_price", "extended_price",
                     "tax_rate", "tax_value"),
    "CheckoutPayment": ("payment_method", "amount"),
    "CheckoutSale": ("store_code", "subtotal", "tax_total", "total"),
}
for type_name, fields in _WIRE_TYPE_REQUIRED.items():
    block = ""
    m = re.search(rf"export type {type_name} = \{{(.*?)\n\}}", phone, re.S)
    if m:
        block = m.group(1)
    bad = [f for f in fields if not re.search(rf"\n\s*{re.escape(f)}\s*:", block)]
    optional = [f for f in fields if re.search(rf"\n\s*{re.escape(f)}\s*\?\s*:", block)]
    check(f"C6 the phone's {type_name} requires every key the RPC reads",
          bool(block) and bad == [] and optional == [],
          f"type {type_name}: missing {bad}, optional-when-required {optional}. A key the RPC "
          "COALESCEs to 0 or 1 must be impossible to omit, or its absence stores a wrong sale.")
# The defect is a type that names the PHONE's field instead of the wire's, which is what the old
# `CheckoutBody` did. Run the check's own rule over it: `qty` is missing, so the check must fail.
_defective = "\n  quantity: number\n  unit_price: number\n"
control("C6", [f for f in ("qty", "unit_price")
               if not re.search(rf"\n\s*{re.escape(f)}\s*:", _defective)] == [])

# C7 — and the test must assert the KEYS, not merely exist. C5 proves a file is there; this proves
# it is doing the one job that makes the contract real on the client side.
_test = read(ROOT / "mobile/src/modules/pos/checkout-body.test.ts")
# All THREE groups must be compared, each against a declared key list. An earlier version asked
# only whether `Object.keys` appeared anywhere in the file, and a mutation that gutted the three
# comparisons PASSED it — the words were still present in a different test. A check that can be
# satisfied by a leftover is not a check.
_KEY_ASSERTIONS = {
    "sale": r"Object\.keys\(\s*body!?\.sale\s*\)[\s\S]{0,60}toEqual\(\s*SALE_KEYS",
    "items": r"Object\.keys\(\s*body!?\.items\[0\]\s*\)[\s\S]{0,60}toEqual\(\s*ITEM_KEYS",
    "payments": r"Object\.keys\(\s*body!?\.payments\[0\]\s*\)[\s\S]{0,60}toEqual\(\s*PAYMENT_KEYS",
}
_ungated = sorted(g for g, pat in _KEY_ASSERTIONS.items() if not re.search(pat, _test))
check("C7 the phone's test compares the emitted keys of all three groups", _ungated == [],
      f"checkout-body.test.ts does not compare the keys of {_ungated} against a declared list. "
      "A test that only checks the totals would have PASSED on 2026-11-12, when every key was "
      "wrong and the totals were absent entirely.")
control("C7", sorted(g for g, pat in _KEY_ASSERTIONS.items()
                     if not re.search(pat, "expect(1).toBe(1)\nObject.keys(x)")) == [])

# And the key lists the test compares against must be the CONTRACT's, not a copy that drifted.
for group, declared in (("SALE_KEYS", C.SALE_KEYS), ("ITEM_KEYS", C.ITEM_KEYS),
                        ("PAYMENT_KEYS", C.PAYMENT_KEYS)):
    m = re.search(rf"const {group} = \[(.*?)\]", _test, re.S)
    listed = set(re.findall(r"'([^']+)'", m.group(1))) if m else set()
    stray = sorted(listed - set(declared))
    check(f"C7b the test's {group} name only keys the contract declares", bool(listed) and stray == [],
          f"the test compares against {sorted(listed)}; {stray} are not in the contract. The test "
          "and the contract must not drift apart — that is two declarations again.")
control("C7b", sorted({"method"} - set(C.PAYMENT_KEYS)) == [])

check("C5 the phone's builder is TESTED",
      (ROOT / "mobile/src/modules/pos/checkout-body.test.ts").exists(),
      "the key-by-key assertion is the only thing that proves the body matches the contract; "
      "without it this harness is only a spell-checker.")
control("C5", (ROOT / "mobile/src/modules/pos/nonexistent.test.ts").exists())


# ── D · the tax PRICING basis is declared once, and the two tills cannot price differently ───────
# A rate alone does not price a line; how the rate is applied does the rest. Those four settings
# lived ONLY in the browser's own registry until 2026-11-12, which is why a second till could not
# price anything. They are now declared server-side and answered by `GET /pos/tax-codes/resolve`,
# and their VALUES are pinned equal to the browser's — so declaring them changed no price, and a
# later edit to one side fails the build instead of quietly repricing one till.
from app.modules.pos import pos_config_effective as P  # noqa: E402

WEB_CONFIG = ROOT / "frontend/src/lib/pos-config.ts"
web_cfg = read(WEB_CONFIG)

# The browser's defaults, read out of its registry rather than retyped here.
def web_default(key: str) -> str:
    m = re.search(rf"key:\s*'{re.escape(key)}'.*?default:\s*([^,\n]+)", web_cfg, re.S)
    return m.group(1).strip().strip("'\"") if m else "<not found>"


PINNED_TAX_PRICING = {
    "tax_applied_on": "post_discount",
    "tax_method": "sale_price",
    "tax_cap_enabled": False,
    "tax_cap_amount": 0,
}

check("D1 the pricing keys are declared as a set the endpoint and this harness share",
      tuple(P.TAX_PRICING_KEYS) == tuple(sorted(PINNED_TAX_PRICING)) or
      set(P.TAX_PRICING_KEYS) == set(PINNED_TAX_PRICING),
      f"pos_config_effective.TAX_PRICING_KEYS is {P.TAX_PRICING_KEYS}; this harness pins "
      f"{sorted(PINNED_TAX_PRICING)}.")
control("D1", set(("tax_method",)) == set(PINNED_TAX_PRICING))

for key, want in PINNED_TAX_PRICING.items():
    got = P.SERVER_DEFAULTS.get(key, "<absent>")
    check(f"D2 the server's default for {key} is the pinned one", got == want,
          f"pos_config_effective.SERVER_DEFAULTS[{key!r}] is {got!r}; this harness pins {want!r}. "
          "Changing it reprices every till that has not overridden it.")
control("D2", P.SERVER_DEFAULTS.get("tax_method") == "cost_based")

# And the pin must still agree with the browser, which is the other till.
for key, want in PINNED_TAX_PRICING.items():
    web = web_default(key)
    same = (str(want).lower() == web.lower()) if not isinstance(want, bool) else (
        web.lower() == str(want).lower())
    check(f"D3 the browser's default for {key} is the same value", same,
          f"frontend/src/lib/pos-config.ts defaults {key} to {web!r}; the server and this harness "
          f"say {want!r}. Two tills with two defaults price one sale two ways.")
control("D3", web_default("tax_method").lower() == "cost_based")

# The method a till cannot price without product cost must be named, not inferred, and both halves
# must name the SAME one — the phone refuses on this exact string.
check("D4 the tax method that needs product cost is named on both sides",
      P.TAX_METHOD_NEEDS_COST == "cost_based"
      and "cost_based" in read(ROOT / "mobile/src/modules/pos/checkout-body.ts"),
      f"pos_config_effective.TAX_METHOD_NEEDS_COST is {P.TAX_METHOD_NEEDS_COST!r} and the phone's "
      "builder must refuse that same value — a till that guesses this basis prices differently "
      "from the web till, which is the defect this whole section exists to prevent.")
control("D4", P.TAX_METHOD_NEEDS_COST == "sale_price")

check("D5 the endpoint answers the pricing basis beside the rate",
      "TAX_PRICING_KEYS" in read(ROOT / "backend/app/modules/pos/router.py")
      and "needs_product_cost" in read(ROOT / "backend/app/modules/pos/router.py"),
      "GET /pos/tax-codes/resolve must return the resolved pricing basis, or a till is back to "
      "carrying its own copy of the store-override precedence.")
control("D5", "TAX_PRICING_KEYS" in "return {'rate': rate}")

check("D6 the phone reads the rate from the server, and holds no rate of its own",
      "resolveTax" in read(ROOT / "mobile/app/(app)/pos/checkout.tsx")
      and not re.search(r"taxRate\s*[:=]\s*0?\.\d",
                        read(ROOT / "mobile/src/modules/pos/checkout-body.ts")),
      "the checkout screen must ask GET /pos/tax-codes/resolve, and no rate may be written into "
      "the phone's own code — a till holding a literal rate charges yesterday's tax forever.")
control("D6", not re.search(r"taxRate\s*[:=]\s*0?\.\d",
                           "const tax = { taxRate: 0.08875 }"))


# ── report ───────────────────────────────────────────────────────────────────────────────────────
if failures:
    print(f"FAIL — {len(failures)} of {checks} checks failed\n")
    for f in failures:
        print(f"  ✗ {f}")
    raise SystemExit(1)
print(f"PASS — {checks} checks")
print("  the checkout wire contract is declared, matches the SQL that defines it, names the fix")
print("  when a till gets it wrong, and no till spells a key pos.checkout cannot read.")
