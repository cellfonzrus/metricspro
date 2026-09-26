"""PROOF + LOCK: a switched-off closing field is gone everywhere — and can be switched off at all (index §29.9).

Owner report 2026-09-26 (UPS Store): *"the tender type has been saved with external card / gift card etc as
inactive … but the submit closing still shows the same, if disabled it should not show anything platform wide"*.

WHAT WAS WRONG (verified on the live rows): the Tender Config page had NO active switch. The owner unticked
"In total" on Gift Card / Store Account / External Credit Card / ACIMA, believing it hid them; every row stayed
`is_active = true`. Two more holes behind it, both fixed here, same class on the sibling Count Config page:
  · the settings GET returned ACTIVE defs only, so a switched-off field vanished from its own editor — and the
    editor's full-replace save then DELETED it;
  · an all-off (or all-hidden) list read as "no list", so the closing form fell back to the BUILT-IN fields —
    showing exactly what the company switched off.

  A. loaders: default = active only (every money reader); include_inactive=True for the editor only
  B. settings GETs return all_defs (editor) + configured (form), defs stays active-only
  C. a tender save with nothing active is refused before any write
  D. the editors show an Active switch, load all_defs, and save is_active; tender mappings only to active tenders
  E. the closing form: a configured company gets exactly its active list — never the built-in fallback
  F. every backend read of closing_tender_def honours is_active (the loader, or selects is_active)
  G. registered in the index

PURE / DB-FREE: fake client + source text.
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.modules.closing import tender_config, count_config  # noqa: E402

FAILS = []


def check(name, ok, why=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  -- " + why))
    if not ok:
        FAILS.append(name)


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


class Q:
    def __init__(self, rows):
        self.rows, self.filters = rows, []

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        out = [r for r in self.rows if all(r.get(c) == v for c, v in self.filters)]
        return type("R", (), {"data": out})()


class Client:
    def __init__(self, rows):
        self.rows = rows

    def schema(self, _s):
        return self

    def table(self, _t):
        return Q(self.rows)


ORG = "org-1"
rows = [{"org_id": ORG, "tender_key": "cash", "field_key": "cash", "is_active": True, "sort_order": 0},
        {"org_id": ORG, "tender_key": "gift", "field_key": "gift", "is_active": False, "sort_order": 1}]

# ── A. loaders ───────────────────────────────────────────────────────────────────────────────────
print("A. loaders")
d, _ = tender_config.load_tender_config(Client(rows), ORG)
check("A1 tender loader default = active only", [r["tender_key"] for r in d] == ["cash"])
d, _ = tender_config.load_tender_config(Client(rows), ORG, include_inactive=True)
check("A2 tender loader include_inactive = all", [r["tender_key"] for r in d] == ["cash", "gift"])
check("A3 count loader default = active only", [r["field_key"] for r in count_config.load_count_config(Client(rows), ORG)] == ["cash"])
check("A4 count loader include_inactive = all",
      [r["field_key"] for r in count_config.load_count_config(Client(rows), ORG, include_inactive=True)] == ["cash", "gift"])

router = read("backend/app/modules/closing/router.py")


def fn(name):
    m = re.search(r"def %s\(.*?\n(?=@router\.|\ndef |\nclass )" % name, router, re.S)
    return m.group(0) if m else ""


# ── B. GETs ──────────────────────────────────────────────────────────────────────────────────────
print("B. settings GETs")
gt, gc = fn("get_tender_config"), fn("get_count_config")
check("B1 tender GET: defs active, all_defs include_inactive, configured",
      "load_tender_config(client, org_id)" in gt and "load_tender_config(client, org_id, include_inactive=True)" in gt
      and '"all_defs": all_defs, "configured": bool(all_defs)' in gt)
check("B2 count GET: same contract",
      "load_count_config(client, org_id)" in gc and "load_count_config(client, org_id, include_inactive=True)" in gc
      and '"all_defs": all_defs, "configured": bool(all_defs)' in gc)

# ── C. refuse all-off ────────────────────────────────────────────────────────────────────────────
print("C. an all-off tender list is refused")
pt = fn("put_tender_config")
refuse = pt.find('if rows and not any(r["is_active"] for r in rows):')
delete = pt.find('.table("closing_tender_def").delete()')
check("C1 refused before the delete/insert", 0 <= refuse < delete, "missing or after the write")

# ── D. editors ───────────────────────────────────────────────────────────────────────────────────
print("D. editors")
tp = read("frontend/src/app/(platform)/closing/tender-config/page.tsx")
cpg = read("frontend/src/app/(platform)/closing/count-config/page.tsx")
for name, src in (("tender", tp), ("count", cpg)):
    check("D1 %s editor loads all_defs" % name, "d?.all_defs?.length ? d.all_defs : d?.defs" in src)
    check("D2 %s editor has an Active column bound to is_active" % name,
          "'Active'" in src and "checked={d.is_active}" in src and "setDef(i, { is_active: e.target.checked })" in src)
    check("D3 %s editor keeps is_active on load" % name, "is_active: x.is_active !== false" in src)
check("D4 tender mappings only to ACTIVE tenders", "cleanDefs.filter(d => d.is_active).map(d => d.tender_key)" in tp
      and "defs.filter(d => d.is_active).map(d => <option" in tp)
check("D5 'In total' says it does not hide", "It does NOT hide the tender" in tp)

# ── E. the form ──────────────────────────────────────────────────────────────────────────────────
print("E. the closing form")
form = read("frontend/src/components/ClosingSubmitForm.tsx")
check("E1 tenders: configured → exactly the active list", "setTdefs(d?.configured ? (d.defs || [])" in form)
check("E2 counts: configured → exactly the active list", "setCdefs(d?.configured ? (d.defs || [])" in form)

# ── F. every backend reader honours is_active ────────────────────────────────────────────────────
print("F. every backend read of closing_tender_def honours is_active")
for dp, _dn, fns in os.walk(os.path.join(ROOT, "backend", "app")):
    for f in fns:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dp, f)
        src = open(p, encoding="utf-8").read()
        for m in re.finditer(r'table\("closing_tender_def"\)\s*\n?\s*\.select\(([^)]*)\)', src):
            rel = os.path.relpath(p, ROOT)
            ok = rel.endswith("closing/tender_config.py") or "is_active" in m.group(1)
            check("F1 %s selects with is_active (or is THE loader)" % rel, ok, "a reader that would show switched-off tenders")

# ── G. registered ────────────────────────────────────────────────────────────────────────────────
print("G. registered")
idx = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("G1 index §29.9", "### 29.9" in idx and "harness_closing_config_active.py" in idx)

print()
print("%d FAIL(s)" % len(FAILS) if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)
