#!/usr/bin/env python3
"""PROOF: which store is this sale for, and what rate does the till charge (index §POS-14).

DB-free. Imports the REAL modules, reads the REAL router and the REAL pages, and RUNS the shared
facts against each other, so a lock here fails the build rather than describing an intention.

Owner 2026-10-30: *"since we have multiple stores under 1 tenant, the user if assigned to multiple
stores should confirm which store they are working in so the receipt and the sale are logged for
that store, the settings should give a list of stores to assign their respective sales tax on a
store level or a market level"*.

THE CLASS, not the instance. Both halves were the same defect wearing two hats — one question with
two answers, the shorter one winning where the money is:

  · "which store is this sale for" had NO home. Four screens spelled the same ladder, ending in
    `stores[0].store_code`, so a guess was indistinguishable from an answer; and
    `POST /pos/sales/checkout` never compared the store to the caller's span at all.
  · "what rate applies here" had a home on the server (store > market > org-wide) and a SECOND,
    two-rung copy in the register, which skipped the MARKET rung. A rate declared on a market read
    as `effective` in the Settings grid and was charged by nobody.

WHAT THIS HARNESS IS FOR. Each property below is the reason the fix is a design fix rather than a
patch, and each is pinned so a later change cannot quietly remove one:

  §A  the span is the caller's, and a closed or foreign store is never in it
  §B  the basis is honest — a guess is never an answer, and `stores[0]` is nobody's answer
  §C  the money path REFUSES, and refuses before the sale rather than after it
  §D  tax precedence is one rule with three rungs, and the market rung cannot be skipped
  §E  the keyset match agrees with `core.scope.in_keyset`, RUN over the same rows
  §F  one refusal is worded once — the TypeScript sentences ARE the Python ones
  §G  source locks: no second ladder, no second rate rule, no way back to the old home
  §H  negative controls — each lock really fails when its subject is broken
"""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.core import scope as CSCOPE                   # noqa: E402
from app.modules.pos import tax_precedence as TP       # noqa: E402
from app.modules.pos import working_store as WS        # noqa: E402

WSMOD = (ROOT / "app/modules/pos/working_store.py").read_text()
TPMOD = (ROOT / "app/modules/pos/tax_precedence.py").read_text()


def _code_only(src: str, comment: str = "#") -> str:
    """The source with its prose removed — comments, and (for Python) every docstring.

    A lock that asserts a token is ABSENT must read the code, not the explanation of it: these
    modules say in words that they hold no clock and no database, and a naive text search then
    fails on its own documentation. This harness caught exactly that on itself."""
    if comment == "#":
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    src = src.replace(doc, "")
    out = []
    for line in src.splitlines():
        stripped = line.split(comment, 1)[0] if comment == "#" else line
        if comment == "//":
            # Only a WHOLE-line // comment is dropped; a URL inside a string keeps its slashes.
            stripped = "" if line.strip().startswith(("//", "*", "/*")) else line
        out.append(stripped)
    return "\n".join(out)


WSCODE = _code_only(WSMOD)
LIBCODE = _code_only((ROOT.parent / "frontend/src/lib/pos-working-store.ts").read_text(), "//")
ROUTER = (ROOT / "app/modules/pos/router.py").read_text()
FE = ROOT.parent / "frontend/src"
LIB = (FE / "lib/pos-working-store.ts").read_text()
PAGES = {name: (FE / f"app/(platform)/pos/{name}/page.tsx").read_text()
         for name in ("sales", "activations", "inventory", "special-orders")}

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


ROSTER = [{"store_code": "B-1115", "address": "2723 Example Ave", "market": "Brooklyn"},
          {"store_code": "B-1598", "address": "88 Utica Ave", "market": "Brooklyn"},
          {"store_code": "B-2001", "address": "14 Main St", "market": "Long Island"}]
CODES = [{"id": "s", "rate": 8.875, "store_code": "B-1115", "market": None, "is_active": True},
         {"id": "m", "rate": 8.5, "store_code": None, "market": "Brooklyn", "is_active": True},
         {"id": "o", "rate": 4.0, "store_code": None, "market": None, "is_active": True}]

print("\n§A  the span is the caller's")
ok("A1. an unrestricted caller may ring at every store on the roster",
   WS.span(ROSTER, None)["stores"] == ["B-1115", "B-1598", "B-2001"])
ok("A2. a one-store grant is a span of one, and owes no confirmation",
   WS.span(ROSTER, {"B-1598"})["stores"] == ["B-1598"]
   and WS.span(ROSTER, {"B-1598"})["must_confirm"] is False)
ok("A3. the keyset matches a store by its ADDRESS too (scope_keyset widens it)",
   WS.span(ROSTER, {"2723 EXAMPLE AVE"})["stores"] == ["B-1115"])
ok("A4. a store nobody granted is not in the span",
   WS.span(ROSTER, {"B-1598"})["stores"] == ["B-1598"])
ok("A5. an EMPTY span still owes an answer rather than reading as 'go ahead'",
   WS.span(ROSTER, {"B-9999"})["must_confirm"] is True)
ok("A6. a login pin outside the span is not offered as the pin",
   WS.span(ROSTER, {"B-1598"}, login_store="B-2001")["login_store"] is None)
ok("A7. the roster read keeps ACTIVE stores only, in the router",
   'is_active") is not False' in ROUTER.split("def _working_span", 1)[1].split("def ", 1)[0],
   "a closed store is never where a sale happened")
ok("A8. the span is org-scoped at the read, so no tenant widens another",
   '.eq("org_id", org_id)' in ROUTER.split("def _working_span", 1)[1].split("return", 1)[0])
ok("A9. the span fails CLOSED — it goes through _caller_store_keyset, which 403s on error",
   "_caller_store_keyset(authorization, org_id)" in ROUTER.split("def _working_span", 1)[1].split("return", 1)[0])

print("\n§B  the basis is honest")
ok("B1. an explicit pick inside the span is `confirmed`",
   WS.resolve(WS.span(ROSTER, None), "B-1598")["basis"] == WS.BASIS_CONFIRMED)
ok("B2. a sole store is `sole_store`, not a guess and not a question",
   WS.resolve(WS.span(ROSTER, {"B-1598"}), None)["basis"] == WS.BASIS_SOLE)
ok("B3. unconfirmed NEVER carries a store code",
   WS.resolve(WS.span(ROSTER, None), None)["store_code"] is None)
ok("B4. the first store in the list is nobody's answer, in either order",
   all(WS.resolve(WS.span(r, None), None)["store_code"] is None
       for r in (ROSTER, list(reversed(ROSTER)))),
   "`stores[0]` was the defect")
ok("B5. a pick outside the span is refused, not quietly replaced",
   WS.resolve(WS.span(ROSTER, {"B-1598"}), "B-2001")["store_code"] is None)
ok("B6. a pick resolves to the ROSTER's spelling, not the typed one",
   WS.resolve(WS.span(ROSTER, None), "b-1115")["store_code"] == "B-1115",
   "'b-1115' once became a phantom store holding $289")
ok("B7. the module reads no clock, no request and no database",
   not any(t in WSCODE for t in ("get_supabase", "datetime", "Header", "HTTPException")),
   "read off the CODE, with docstrings stripped — the module explains this in words too")

print("\n§C  the money path refuses")
ok("C1. a granted store passes",
   WS.sale_refusal(WS.span(ROSTER, {"B-1598"}), "B-1598") is None)
ok("C2. a store outside the span is refused",
   WS.sale_refusal(WS.span(ROSTER, {"B-1598"}), "B-2001") is not None)
ok("C3. NO store is refused even when the span holds exactly one",
   WS.sale_refusal(WS.span(ROSTER, {"B-1598"}), None) is not None,
   "pos.checkout writes NULL, and _span_filter then shows that sale to every manager")
_CO = ROUTER.split('@router.post("/sales/checkout")', 1)[1].split("@router.", 1)[0]
ok("C4. checkout dereferences the one home for its refusal",
   "_wstore.sale_refusal(" in _CO)
ok("C5. checkout refuses BEFORE it calls pos.checkout",
   _CO.index("_wstore.sale_refusal") < _CO.index('rpc("checkout"'))
ok("C6. checkout stamps the roster's spelling onto the sale",
   "_wstore.resolve(" in _CO and 'sale["store_code"]' in _CO)
_AC = ROUTER.split('@router.post("/activations")', 1)[1].split("@router.", 1)[0]
ok("C7. THE SIBLING: the activation write is gated by the same one home",
   "_wstore.sale_refusal(" in _AC,
   "an activation carries the same store_code into the commission feed")
ok("C8. the activation refusal happens before the insert",
   _AC.index("_wstore.sale_refusal") < _AC.index(".insert("))
ok("C9. neither gate words its own refusal",
   "Confirm which store" not in _CO and "Confirm which store" not in _AC)

print("\n§D  tax precedence is ONE rule with THREE rungs")
ok("D1. a store rate wins", TP.resolve(CODES, "B-1115", "Brooklyn")["scope"] == "store")
ok("D2. the MARKET rung applies where a store has no rate of its own",
   TP.resolve(CODES, "B-1598", "Brooklyn")["rate"] == 8.5)
ok("D3. the org-wide rate is the last rung",
   TP.resolve(CODES, "B-2001", "Long Island")["scope"] == "org")
ok("D4. the market rung cannot be skipped — the register's copy charged 4.0 here",
   TP.resolve(CODES, "B-1598", "Brooklyn")["rate"] != 4.0)
ok("D5. nothing declared is explicit, never a silent zero",
   TP.resolve([], "B-1598", "Brooklyn")["scope"] == "none"
   and TP.resolve([], "B-1598", "Brooklyn")["warning"])
ok("D6. a retired rate never wins",
   TP.resolve([dict(CODES[0], is_active=False), CODES[1]], "B-1115", "Brooklyn")["scope"] == "market")
ok("D7. is_active is tested IS NOT FALSE, so a NULL flag is not dropped",
   TP.resolve([dict(CODES[0], is_active=None)], "B-1115", "")["scope"] == "store")
ok("D8. the percent-to-fraction conversion lives here, once",
   TP.resolve(CODES, "B-1115", "")["fraction"] == 0.08875 and TPMOD.count("/ 100") == 1)
ok("D9. the router DEREFERENCES the rule rather than holding a copy",
   "_taxrule.pick(" in ROUTER and 'return hit, "store"' not in ROUTER)
ok("D10. the grid and the till reach the same rule — one `_resolve_tax` body",
   ROUTER.count("def _resolve_tax") == 1)

print("\n§E  the keyset match agrees with core.scope, RUN over the same rows")
_cases = [(None, "B-1115"), ({"B-1115"}, "B-1115"), ({"B-1115"}, "B-1598"),
          ({"2723 EXAMPLE AVE"}, "2723 Example Ave"), ({"B-1115"}, "b-1115"),
          ({"B-1115"}, ""), (set(), "B-1115")]
_disagree = [(k, v) for k, v in _cases
             if WS._in_keyset(k, v) != CSCOPE.in_keyset(k, v)]
ok("E1. the two implementations agree on every case, not by assertion but by running both",
   not _disagree, _disagree)
ok("E2. the pure module does not import the one that needs a client",
   "core.scope" not in WSCODE and "core import scope" not in WSCODE)

print("\n§F  one refusal is worded once")


def _ts_const(name):
    """The string literal a TS `export const NAME = '…'` holds, across one or two lines."""
    body = LIB.split(f"export const {name} =", 1)[1]
    body = body.split("export const", 1)[0]
    parts = []
    for raw in body.splitlines():
        raw = raw.strip()
        if raw.startswith("'") or raw.startswith('"'):
            parts.append(raw.strip("'\"").rstrip("'\""))
        elif parts:
            break
    return " ".join(p for p in parts if p).strip()


ok("F1. the ASK sentence is byte-identical on both sides",
   _ts_const("ASK_SENTENCE") == WS.ASK_SENTENCE,
   f"ts={_ts_const('ASK_SENTENCE')!r} py={WS.ASK_SENTENCE!r}")
ok("F2. the NO-STORE sentence is byte-identical on both sides",
   _ts_const("NO_STORE_SENTENCE") == WS.NO_STORE_SENTENCE,
   f"ts={_ts_const('NO_STORE_SENTENCE')!r} py={WS.NO_STORE_SENTENCE!r}")
ok("F3. the three bases are spelled the same on both sides",
   all(f"'{v}'" in LIB for v in (WS.BASIS_CONFIRMED, WS.BASIS_SOLE, WS.BASIS_UNCONFIRMED)))
ok("F4. the till does not word the refusal itself — it shows the one home's",
   "working.refusal" in PAGES["sales"])

print("\n§G  source locks")
ok("G1. the old home is GONE, so no caller can drift back to it",
   not (FE / "lib/pos-store.ts").exists())
for name, page in PAGES.items():
    ok(f"G2.{name}. no positional store fallback survives on this page",
       "stores[0]?.store_code" not in page and "stores[0].store_code" not in page,
       "the `|| stores[0]` ladder is the defect")
    ok(f"G3.{name}. the page reaches the store through the one home",
       "pos-working-store" in page)
ok("G4. no page derives a tax rate of its own any more",
   not any("t.store_code === activeStore" in p for p in PAGES.values())
   and "/ 100 // rate is a PERCENT" not in PAGES["sales"])
ok("G5. the register ASKS the server for the rate",
   "/pos/tax-codes/resolve" in PAGES["sales"])
ok("G6. the register asks the server for the span",
   "/pos/working-store" in PAGES["sales"])
ok("G7. the saved pick is keyed by the LOGIN, not by the terminal alone",
   "STORE_KEY_PREFIX = 'pos_active_store:'" in LIB
   and "STORE_KEY_PREFIX + userId" in LIB)
ok("G8. the legacy shared key is read but never written back",
   "localStorage.removeItem(LEGACY_STORE_KEY)" in LIB
   and "setItem(LEGACY_STORE_KEY" not in LIB)
ok("G9. the client holds no rate resolver of its own",
   not any(tok in LIBCODE for tok in ("tax", "rate", "Tax", "Rate")),
   "read off the CODE — the module's own comments explain the rule it does NOT hold")
ok("G10. the one home exposes every function its callers use",
   {"span", "resolve", "sale_refusal"}
   <= {n.name for n in ast.walk(ast.parse(WSMOD)) if isinstance(n, ast.FunctionDef)})
ok("G11. the working-store endpoint exists where the pages ask for it",
   '@router.get("/working-store")' in ROUTER)
ok("G12. the till BLOCKS on an unconfirmed store rather than warning about it",
   "needsConfirmation" in PAGES["sales"] and "getCheckoutBlockers" in PAGES["sales"])

print("\n§H  negative controls — each lock really fires")
ok("H1. control: a positional fallback would be caught by G2",
   "stores[0]?.store_code" not in PAGES["sales"]
   and "stores[0]?.store_code" in "const x = stores[0]?.store_code")
ok("H2. control: the span really does exclude a store, so A2/A4 are not vacuous",
   len(WS.span(ROSTER, {"B-1598"})["stores"]) < len(ROSTER))
ok("H3. control: §E would notice a disagreement",
   WS._in_keyset({"B-1115"}, "B-1598") is False and CSCOPE.in_keyset(None, "anything") is True)
ok("H4. control: §F compares a real sentence, not an empty string",
   len(_ts_const("ASK_SENTENCE")) > 40)
ok("H4b. control: the prose stripper really strips, and really keeps the code",
   "get_supabase" not in WSCODE and "def sale_refusal" in WSCODE
   and "localStorage" in LIBCODE and "the rate the till charges" not in LIBCODE)
ok("H5. control: the two-rung copy really would have charged the wrong rate",
   TP.resolve([CODES[0], CODES[2]], "B-1598", "Brooklyn")["rate"] == 4.0,
   "with the market row removed, 4.0 is what the register used to charge")
ok("H6. control: sale_refusal really can pass, so C2/C3 are not vacuous",
   WS.sale_refusal(WS.span(ROSTER, None), "B-1115") is None)
ok("H7. control: the gate text really is in the checkout body that was read",
   len(_CO) > 500 and "p_items" in _CO)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
