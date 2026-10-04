#!/usr/bin/env python3
"""PROOF — a scope's DISPLAY NAME is a dereference of the company registry, never a stored copy.

Owner report 2026-10-03 (verbatim): *"the app shows the company id not the name of the company in
the settings to choose the company to work in."*

THE DEFECT, row-level. The company dropdown shared by the Accounts hub, the P&L, the Balance Sheet
and the Cash Flow pages labelled each scope `scope_label || scope_key`. `scope_label` is a COPY
persisted into `commcalc.account_statements` at COMPUTE time; `scope_key` for a company scope is the
literal string `company:<uuid>`. So any snapshot whose stored label was null (computed before the
company was named, or written by hand) or stale (the company was renamed afterwards) rendered the
company's UUID as its name — on the picker, the page title, the export cover and the emailed copy.

THE CLASS, fixed once: `account/coa.scope_display_label` (index §13b.1) is the ONE home. It
dereferences the canonical entity inventory (§13b `coa.org_companies`), uses the stored label only as
a fallback, and NEVER returns the raw key. `coa.label_scopes` stamps `scope_display` on every scope
list an API ships, so no renderer carries the rule.

THIS HARNESS PROVES (pure, DB-free, stdlib only — `python3 backend/harness_scope_label.py`):
  R1  THE REGRESSION: a `company:<uuid>` scope whose stored label is NULL renders the company's
      CURRENT name, and the uuid never appears in the output.
  R2  THE REGRESSION, stale half: a stored label that disagrees with the registry loses — a renamed
      company relabels every surface at once.
  R3  no input renders the raw scope key: null label, blank label, label == key, unknown company,
      malformed key, empty key, no registry at all.
  R4  the non-company families (consolidated, store, profit_center, filtered) keep the names users
      already read, and a store/profit-center identifier (human by construction) may show.
  R5  an UNNAMED company (registry row with a blank name) falls through to the stored label, then to
      a generic word — never to the id.
  R6  `label_scopes` stamps every row, is idempotent, leaves non-dicts alone, and sorts cleanly.
  R7  RULE TWO: the one home carries no tenant / company / carrier name — only scope FAMILIES.
  R8  org isolation: another org's company name can never label this org's scope, because the only
      registry the home reads is the one the caller passes from `org_companies` (fail-closed §13b).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.account import coa   # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=None):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")
        for d in (detail or [])[:12]:
            print(f"          {d}")


# ── fixtures: two orgs, anonymised, shaped exactly like coa.org_companies rows ──────────────────
ORG_A = "00000000-0000-0000-0000-0000000000aa"
ORG_B = "00000000-0000-0000-0000-0000000000bb"
CID_1 = "9b22c0d8-1111-4444-8888-aaaaaaaaaaaa"
CID_2 = "b5993b9d-2222-4444-8888-bbbbbbbbbbbb"
CID_UNNAMED = "c0ffee00-3333-4444-8888-cccccccccccc"
A_COMPANIES = [
    {"id": CID_1, "name": "First Entity LLC", "org_id": ORG_A},
    {"id": CID_2, "name": "Second Entity LLC", "org_id": ORG_A},
    {"id": CID_UNNAMED, "name": "   ", "org_id": ORG_A},
]
B_COMPANIES = [{"id": CID_1, "name": "FOREIGN ENTITY LLC", "org_id": ORG_B}]

L = coa.scope_display_label

print("\nR1 — THE REGRESSION: a company scope with NO stored label renders the current name")
out = L(f"company:{CID_1}", None, A_COMPANIES)
check("null stored label ⇒ the registry's name", out == "First Entity LLC", [f"got {out!r}"])
check("the uuid is nowhere in the output", CID_1 not in out, [out])
check("blank stored label ⇒ the registry's name", L(f"company:{CID_1}", "", A_COMPANIES) == "First Entity LLC")
check("whitespace stored label ⇒ the registry's name",
      L(f"company:{CID_1}", "   ", A_COMPANIES) == "First Entity LLC")
check("a snapshot that stored the KEY as its own label still renders the name",
      L(f"company:{CID_1}", f"company:{CID_1}", A_COMPANIES) == "First Entity LLC")

print("\nR2 — THE REGRESSION, stale half: the registry beats the copy")
check("a renamed company relabels (stored copy ignored)",
      L(f"company:{CID_2}", "Second Entity LLC (old name)", A_COMPANIES) == "Second Entity LLC")
check("both company scopes resolve independently",
      [L(f"company:{c}", None, A_COMPANIES) for c in (CID_1, CID_2)]
      == ["First Entity LLC", "Second Entity LLC"])

print("\nR3 — the raw key is NEVER what a human sees")
cases = [
    (f"company:{CID_1}", None, A_COMPANIES),
    (f"company:{CID_1}", None, None),                    # no registry at hand at all
    (f"company:{CID_1}", None, []),                      # empty registry
    ("company:" + "d" * 36, None, A_COMPANIES),          # a company not in this org's inventory
    ("company:", None, A_COMPANIES),                     # malformed key, no id
    ("company", None, A_COMPANIES),                      # malformed key, no separator
    ("", None, A_COMPANIES),
    (None, None, A_COMPANIES),
    ("weird_family:0f0f0f0f-9999-4444-8888-dddddddddddd", None, A_COMPANIES),
]
bad = []
for key, lab, comps in cases:
    got = L(key, lab, comps)
    k = str(key or "")
    if (k and got == k) or (":" in k and got.startswith(k.split(":")[0] + ":")) or not str(got).strip():
        bad.append(f"{key!r} + {lab!r} ⇒ {got!r}")
check("no case renders the key (or an empty label)", not bad, bad)
uuidish = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
leaks = [f"{k!r} ⇒ {L(k, lb, c)!r}" for k, lb, c in cases if uuidish.search(str(L(k, lb, c)))]
check("no case leaks a uuid into the label", not leaks, leaks)

print("\nR4 — the other scope families keep the words users already read")
check("consolidated", L("consolidated", "Consolidated (all companies)", A_COMPANIES)
      == "Consolidated (all companies)")
check("consolidated with NO stored label still names itself",
      L("consolidated", None, A_COMPANIES) == "Consolidated (all companies)")
check("a store scope shows its address (human identifier, not an id)",
      L("store:123 MAIN ST", None, A_COMPANIES) == "123 MAIN ST")
check("a store scope prefers the stored label when it has one",
      L("store:123 MAIN ST", "Main St", A_COMPANIES) == "Main St")
check("a profit center shows its code when unlabelled",
      L("profit_center:NORTH", None, A_COMPANIES) == "NORTH")
check("a profit center prefers its stored name", L("profit_center:NORTH", "North Region", A_COMPANIES)
      == "North Region")
check("the store/market FILTERED pseudo-scope names itself",
      L("filtered", None, A_COMPANIES) == "Filtered")
check("a filtered scope keeps its computed description",
      L("filtered", "Filtered — 3 store(s)", A_COMPANIES) == "Filtered — 3 store(s)")

print("\nR5 — an unnamed company degrades, it does not leak its id")
check("blank registry name + no stored label ⇒ a generic word",
      L(f"company:{CID_UNNAMED}", None, A_COMPANIES) == "Unnamed company")
check("blank registry name + a stored label ⇒ the stored label",
      L(f"company:{CID_UNNAMED}", "Entity being set up", A_COMPANIES) == "Entity being set up")
check("the unnamed company's id never renders",
      CID_UNNAMED not in L(f"company:{CID_UNNAMED}", None, A_COMPANIES))
check("companies_by_id drops blank names and non-dicts",
      coa.companies_by_id(A_COMPANIES + ["junk", None]) == {CID_1: "First Entity LLC",
                                                            CID_2: "Second Entity LLC"})

print("\nR6 — label_scopes stamps every row an API ships")
rows = [
    {"scope_key": "consolidated", "scope_label": "Consolidated (all companies)"},
    {"scope_key": f"company:{CID_1}", "scope_label": None},
    {"scope_key": f"company:{CID_2}", "scope_label": "Stale Name LLC"},
    {"scope_key": "store:123 MAIN ST", "scope_label": "123 MAIN ST"},
    "not a dict",
]
coa.label_scopes(rows, A_COMPANIES)
check("every dict row carries scope_display",
      all("scope_display" in r for r in rows if isinstance(r, dict)))
check("the stamped names are the resolved ones",
      [r["scope_display"] for r in rows if isinstance(r, dict)]
      == ["Consolidated (all companies)", "First Entity LLC", "Second Entity LLC", "123 MAIN ST"])
check("a non-dict row is left alone", rows[-1] == "not a dict")
before = [r.get("scope_display") for r in rows if isinstance(r, dict)]
coa.label_scopes(rows, A_COMPANIES)
check("idempotent (stamping twice changes nothing)",
      before == [r.get("scope_display") for r in rows if isinstance(r, dict)])
check("label_scopes(None) is harmless", coa.label_scopes(None, A_COMPANIES) is None
      or coa.label_scopes([], A_COMPANIES) == [])
ordered = sorted((r for r in rows if isinstance(r, dict)), key=lambda x: x["scope_display"])
check("the stamped name sorts (the dropdown reads alphabetically by what the user sees)",
      [r["scope_display"] for r in ordered][0] == "123 MAIN ST")

print("\nR7 — RULE TWO: the one home knows scope FAMILIES, never tenants")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "app", "modules", "account", "coa.py"), encoding="utf-8").read()
home = src[src.index("# ── THE ONE HOME for a scope's DISPLAY NAME"):src.index("def store_company_map")]
check("the families table is keyed by family only",
      set(coa.SCOPE_FAMILIES) == {"company", "store", "profit_center"},
      [str(set(coa.SCOPE_FAMILIES))])
check("the singletons table is the two pseudo-scopes",
      set(coa.SCOPE_SINGLETONS) == {"consolidated", "filtered"})
check("a company's identifier is declared NON-human (its uuid may never render)",
      coa.SCOPE_FAMILIES["company"][0] is False)
check("store / profit-center identifiers are declared human",
      coa.SCOPE_FAMILIES["store"][0] is True and coa.SCOPE_FAMILIES["profit_center"][0] is True)
check("the home contains no tenant / carrier / company proper noun",
      not re.search(r"\b(cellfonz|luxelink|novawave|boost|metro|cricket|t-?mobile|at&t|verizon)\b",
                    home, re.I))

print("\nR8 — org isolation: the registry the caller passes is the only one read")
check("org B's name for the SAME id never labels an org-A scope read with org A's registry",
      L(f"company:{CID_1}", None, A_COMPANIES) == "First Entity LLC")
check("and org B's own read gets org B's name",
      L(f"company:{CID_1}", None, B_COMPANIES) == "FOREIGN ENTITY LLC")
check("a scope for an id absent from the passed registry falls back, never cross-reads",
      L(f"company:{CID_2}", None, B_COMPANIES) == "Unnamed company")
check("no global state: the home takes the registry as an argument",
      "companies" in coa.scope_display_label.__code__.co_varnames)

print(f"\n{'='*78}\n  {PASS} passed, {FAIL} failed\n{'='*78}")
sys.exit(1 if FAIL else 0)
