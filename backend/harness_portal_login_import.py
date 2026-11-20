#!/usr/bin/env python3
"""PASTE-ONCE LOGIN IMPORT — the lock (index §COMMCALC-83).

DB-free. It fails the build if the mechanism that makes this safe comes un-wired:

  §A  three states, not two — a line is ready / skipped / unresolved, and an unplaced store is
      never filed under a plausible code;
  §B  a credential is NEVER in an answer — not in a verdict, not in a reason, not in a row's echo;
  §C  one writer — the import stores rows through the SAME `_save_source_row` the settings form
      uses, so a password cannot be sealed on one door and not the other;
  §D  one store resolver — this module owns no second opinion about which store a word means;
  §E  nothing is printed or logged here;
  §F  negative controls: each check is shown to FAIL on the shape it forbids.

Run: python3 backend/harness_portal_login_import.py
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from app.modules.commcalc import portal_login_import as pli   # noqa: E402

PASS, FAIL = [], []


def ck(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (("   " + detail) if (detail and not ok) else ""))


ROSTER = {"b-1115": "B-1115", "b-1598": "B-1598"}


def resolve(raw):
    k = str(raw or "").strip().lower()
    if k in ROSTER:
        return ROSTER[k], "code"
    if k == "jamaica ave":
        return "B-1598", "address"
    return None, None


SECRETS = ("hunter2xx", "s3cret-pw", "pw-three", "pw-four")
BLOCK = "\n".join([
    "Store\tUsername\tPassword",
    "B-1115\tuser1\t" + SECRETS[0],
    "jamaica ave\tuser2\t" + SECRETS[1],
    "Nowhere Mall\tuser3\t" + SECRETS[2],
    "b-1598\tuser4\t",
    "",
    "B-1115\tdupe\t" + SECRETS[3],
])

print("§A  three states, not two")
out = pli.parse(BLOCK, processor="epay", portal_url="https://example.test/", resolve=resolve)
ck("(a) the placed lines are READY — by code and by declared address",
   out["ready"] == 2 and [r["store_code"] for r in out["rows"]] == ["B-1115", "B-1598"])
ck("(a) a store this org has not declared is UNRESOLVED, not stored",
   out["unresolved"] == 1 and all(r["store_code"] != "Nowhere Mall" for r in out["rows"]))
ck("(a) an unplaced spelling never becomes a store code",
   not any(r["store_code"] not in ("B-1115", "B-1598") for r in out["rows"]))
ck("(a) a line missing a field is SKIPPED and says which field",
   any(l["verdict"] == "skipped" and "password" in l["reason"] for l in out["lines"]))
ck("(a) a store repeated in one paste is SKIPPED naming the earlier line",
   any(l["verdict"] == "skipped" and "already on line" in l["reason"] for l in out["lines"]))
ck("(a) every non-blank line is accounted for, none dropped in silence",
   len(out["lines"]) == 6)
ck("(a) resolve_store answers ('','') for an unplaced spelling, never a code",
   pli.resolve_store("Nowhere Mall", resolve) == ("", ""))
ck("(a) a resolver that RAISES answers ('',''), it does not place the store",
   pli.resolve_store("B-1115", lambda _: (_ for _ in ()).throw(RuntimeError("down"))) == ("", ""))
ck("(a) with no resolver at all nothing is placed",
   pli.parse(BLOCK, processor="epay", resolve=None)["ready"] == 0)

print("§B  a credential is never in an answer")
answer = repr({k: v for k, v in out.items() if k != "rows"})
for sec in SECRETS:
    ck("(b) %r is absent from every verdict, reason and echo" % sec, sec not in answer)
ck("(b) the password cell is quoted back as its LENGTH only",
   any("character value" in " ".join(map(str, l.get("cells", []))) for l in out["lines"]))
ck("(b) redacted_line replaces the password and keeps the named rest",
   pli.redacted_line(1, ["B-1115", "u", "abc"], ("store", "username", "password"))["cells"]
   == ["B-1115", "u", "3 character value"])
#: A cell the parser cannot NAME is redacted too. A paste can hold more cells than its header
#: declares — a password containing the separator, a header that omits the password column — and
#: then the credential sits under no name at all. "Echo what we could not identify" is the one
#: shape that would put a password back on screen.
ck("(b) a cell with NO field is redacted, not echoed",
   pli.redacted_line(1, ["B-1115", "u", "abc"], ("store", "username"))["cells"]
   == ["B-1115", "u", "3 character value"])
ck("(b) …and an unnamed cell of a real paste carries no value either",
   SECRETS[0] not in repr(pli.parse("Store\tUsername\nB-1115\tu\t" + SECRETS[0],
                                    processor="epay", resolve=resolve)["lines"]))
ck("(b) the rows DO carry the password (they are what gets sealed)",
   out["rows"][0].get("password") == SECRETS[0])

print("§C  one writer, one store resolver, no second opinion")
router_src = open(os.path.join(ROOT, "app/modules/commcalc/router.py"), encoding="utf-8").read()
tree = ast.parse(router_src)
fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
ck("(c) the one writer exists", "_save_source_row" in fns)
imp = fns.get("import_data_sources")
ck("(c) the import endpoint exists", imp is not None)
imp_src = ast.get_source_segment(router_src, imp) if imp else ""
ck("(c) the import stores through that one writer", "_save_source_row(" in imp_src)
ck("(c) the import does NOT write the table itself",
   'table("data_source").insert' not in imp_src and 'table("data_source").update' not in imp_src)
ck("(c) the single-login form goes through the same writer",
   "_save_source_row(" in (ast.get_source_segment(router_src, fns["save_data_source"]) or ""))
ck("(c) the writer is the only place the login row is sealed",
   router_src.count('_psec.seal_row("data_source"') == 2
   and all(m.start() > router_src.index("def _save_source_row")
           and m.start() < router_src.index("def save_data_source")
           for m in re.finditer(r'_psec\.seal_row\("data_source"', router_src)))
ck("(c) the import is gated the same way as changing a login",
   "_require_import_admin(" in imp_src)
ck("(c) the import is org-scoped", "require_org(" in imp_src)
ck("(d) the parser takes the platform resolver as an ARGUMENT",
   "resolve" in [a.arg for a in ast.parse(open(pli.__file__, encoding="utf-8").read()).body[-1].args.kwonlyargs])
ck("(d) the import passes the platform's own resolver, not one of its own",
   "_intake_store_resolver(" in imp_src)
pli_src = open(pli.__file__, encoding="utf-8").read()
pli_tree = ast.parse(pli_src)
#: every module the parser imports, from the AST — not from a string search over its own prose,
#: which would read the sentence "no `sb()`" as a database call. A check that trips on
#: documentation is a check that gets deleted.
pli_imports = sorted({(n.module or "") for n in ast.walk(pli_tree) if isinstance(n, ast.ImportFrom)}
                     | {a.name for n in ast.walk(pli_tree) if isinstance(n, ast.Import)
                        for a in n.names})
ck("(d) the parser imports NOTHING — no store matcher, no client, no second opinion",
   pli_imports == [], detail=repr(pli_imports))

print("§E  nothing is printed, logged or read from the world here")
#: every function the parser CALLS, by name, from the AST.
pli_calls = sorted({n.func.id for n in ast.walk(pli_tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)}
                   | {n.func.attr for n in ast.walk(pli_tree) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Attribute)})
ck("(e) the parser prints nothing", "print" not in pli_calls)
ck("(e) the parser logs nothing",
   not {c for c in pli_calls if c in ("debug", "info", "warning", "error", "exception",
                                      "getLogger", "basicConfig")})
#: `get`, `update` and `insert` are NOT on this list: they are `dict` methods a pure function uses
#: honestly, and a name that means two things cannot carry a lock. The names here mean one thing.
_REACH = ("sb", "execute", "table", "schema", "rpc", "connect", "urlopen", "request", "Session",
          "postgrest")
ck("(e) the parser reaches no database and no network",
   not {c for c in pli_calls if c in _REACH}, detail=repr(pli_calls))
ck("(f) …and that check would catch a parser that did reach one",
   bool({c for c in ("sb", "execute", "get") if c in _REACH}))
ck("(e) a failed read of the stored logins REFUSES the import rather than inserting duplicates",
   "second login" in imp_src and "Nothing was written" in imp_src)
ck("(e) a dry run writes nothing", "dry_run" in imp_src and imp_src.index("if body.dry_run") <
   imp_src.index("for row in parsed"))

print("§F  negative controls — each check fails on the shape it forbids")
ck("(f) a parser that placed an unknown store would be caught",
   pli.resolve_store("Nowhere Mall", lambda r: ("B-1115", "guess")) == ("B-1115", "guess"))
ck("(f) …and the real resolver does not do that",
   pli.resolve_store("Nowhere Mall", resolve) == ("", ""))
ck("(f) a verdict carrying the raw password would be caught",
   SECRETS[0] in repr({"reason": "stored " + SECRETS[0]}))
ck("(f) a header row that is really data is NOT read as a header",
   pli.header_fields(["B-1115", "user1", "abc"]) is None)
ck("(f) …and a real header row is",
   pli.header_fields(["Store", "Username", "Password"]) == ("store", "username", "password"))
ck("(f) a line with no separator is one cell, never silently split on spaces",
   pli.split_line("Jamaica Ave Store") == ["Jamaica Ave Store"])
ck("(f) a comma inside a tab-separated line does not split it",
   pli.split_line("4640 W Diversey, Unit A\tuser\tpw") == ["4640 W Diversey, Unit A", "user", "pw"])
ck("(f) a blank paste yields nothing and claims nothing",
   pli.parse("", processor="epay", resolve=resolve) == {"rows": [], "lines": [], "ready": 0,
                                                        "skipped": 0, "unresolved": 0})

print()
print("%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED: " + "; ".join(FAIL))
    sys.exit(1)
print("OK — a pasted credential reaches the sealed column and nothing else.")
