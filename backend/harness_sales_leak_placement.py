"""Offline proof (no DB/network) for WHERE A SALES LEAK BELONGS — owner directive 2026-09-10:

    "sales leak should not be in employee dashbaord, it shoudl be in management overview under all
     flags tile which is not here right now"

WHAT A SALES LEAK IS, AND WHY IT WAS ON THE WRONG SCREEN. `commcalc/sales_recon.sync_recon_flags`
writes flag_type 'sales_leak' (severity critical) for a sale that IS in the daily B2B feed and is
MISSING from the monthly statement — money the carrier has not paid us yet. The row carries the rep
who made the sale because that is how the sale is identified, NOT because the rep did anything. On
their own dashboard it read as a red critical mark against them for a reconciliation gap between two
of OUR OWN feeds, about which they can do exactly nothing.

WITHHELD FROM ONE AUDIENCE, NOT SUPPRESSED. §A pins that the flag still exists, still has its amount,
and is still returned to management — this hides it from the people it maligns, it does not make a
finding disappear. That distinction is the whole point: the house rule is that a defect found in live
data is REPORTED, never hidden, and a rep's dashboard is simply not the report.

THE COUNT MUST AGREE WITH THE LIST. §A4 is the trap this class of fix usually springs: the bundle also
carries `report_card.flags_count`, and a filter applied to the list but not the count leaves the card
reading "3 flags" above a list of two. A number that disagrees with what is under it is a new defect,
not a fix.

§B is STATIC over the shipped config/nav, because "the tile is not here right now" is a placement
fact: no test can see a missing tile by running code, and neither tsc nor a build can see a dashboard
with nothing on it (index §24, the proof-harness audit).

Run: `cd backend && python3 harness_sales_leak_placement.py`
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# ══ A. THE REP'S BUNDLE — the filter, and the count that must move with it ═══════════════════════
core = read("backend/app/modules/core/router.py")
check("A1 there is a NAMED set of flag types withheld from the employee dashboard, not a bare "
      "string buried in a comprehension",
      "REP_HIDDEN_FLAG_TYPES = (" in core and '"sales_leak"' in
      core.split("REP_HIDDEN_FLAG_TYPES = (")[1][:80])

dash = core.split('@router.get("/employee-dashboard")')[1]
flags_line = dash.split('out["flags"] = myf')[0].split("myf = [")[1]
check("A2 the rep's flag list applies it", "REP_HIDDEN_FLAG_TYPES" in flags_line
      and "not in" in flags_line, flags_line[:120])
check("A3 ... case- and whitespace-insensitively, so a feed writing 'Sales_Leak ' is still caught",
      ".strip().lower()" in flags_line, flags_line[:120])

# The count is derived from the SAME list — the only shape that cannot drift.
card = dash.split('"flags_count"')[1][:40]
check("A4 report_card.flags_count counts the FILTERED list, so the number on the card can never "
      "disagree with the flags shown under it",
      "len(myf)" in card, card)

# Withheld from the rep, NOT deleted and NOT stopped being written.
recon = read("backend/app/modules/commcalc/sales_recon.py")
check("A5 sync_recon_flags still WRITES the sales_leak flag — nothing about detection changed",
      '"flag_type": "sales_leak"' in recon)
check("A6 ... still critical, so it still ranks as one on the management queues",
      '"severity": "critical"' in recon.split('"flag_type": "sales_leak"')[1][:200])
check("A7 the employee dashboard is the ONLY place that withholds it",
      core.count("REP_HIDDEN_FLAG_TYPES") == 2)   # the definition + the one use

# ══ B. WHERE IT NOW LIVES — the tile that "is not here right now" ════════════════════════════════
mig = read("database/migrations/1002_management_overview_all_flags_tile.sql")
check("B1 the tile is seeded into the HOUSE management-overview tile layout",
      "key     = 'management-overview'" in mig
      and "'00000000-0000-0000-0000-000000000001'" in mig)
check("B2 it is an UPDATE that APPENDS, because mig 948 already created that row",
      "UPDATE commcalc.ui_label_override" in mig and "-> 'tiles') ||" in mig)
check("B3 it is idempotent — a second run appends nothing",
      "NOT (label::jsonb -> 'tiles') @> '[{\"title\":\"All Flags\"}]'::jsonb" in mig)
check("B4 it carries a -- REVERT: note", "-- REVERT:" in mig)
check("B5 it is one transaction with a post-flight check, so it cannot half-land a dashboard",
      "BEGIN;" in mig and "COMMIT;" in mig and "RAISE EXCEPTION" in mig)
check("B6 it touches the HOUSE row only — a tenant's own designed layout is left alone",
      mig.count("org_id = '00000000-0000-0000-0000-000000000001'") >= 1
      and "WHERE org_id" in mig)

# The tile must name a page that EXISTS and that actually shows this flag type.
tile_json = re.search(r"\|\| '(\[[\s\S]*?\])'::jsonb", mig)
check("B7 the appended tile parses as JSON", tile_json is not None)
tiles = json.loads(tile_json.group(1)) if tile_json else []
hrefs = [i["href"] for t in tiles for i in t.get("items", [])]
check("B8 it is titled 'All Flags', as the owner named it",
      len(tiles) == 1 and tiles[0].get("title") == "All Flags", [t.get("title") for t in tiles])
check("B9 it points at the EXISTING all-flags page — no new page was built",
      "/commcalc/flags" in hrefs, hrefs)
check("B10 ... which exists on disk", os.path.exists(
    os.path.join(ROOT, "frontend/src/app/(platform)/commcalc/flags/page.tsx")))
check("B11 ... and lists flags by TYPE with no type excluded, so a sales_leak lands there",
      "f.flag_type" in read("frontend/src/app/(platform)/commcalc/flags/page.tsx"))

# ══ C. REACHABLE FROM THE HUB, WITH NO RBAC CHANGE ═══════════════════════════════════════════════
rbac = read("frontend/src/lib/rbac.ts")
mo = rbac.split("{ group: 'Management Overview'")[1].split("]},")[0]
check("C1 the flags page is reachable from Management Overview", "'/commcalc/flags'" in mo)
check("C2 ... as a tileOnly DUPLICATE, so it does not gain a second nav row",
      "'/commcalc/flags'" in mo and "tileOnly: true" in mo)
fc = rbac.split("{ group: 'Flags & Compliance'")[1].split("]},")[0]


def entry(block, href):
    m = re.search(r"\{ href: '" + re.escape(href) + r"'.*?\}", block)
    return m.group(0) if m else ""


for href in ("/commcalc/flags", "/compliance"):
    a, b = entry(mo, href), entry(fc, href)
    check(f"C3 {href} carries the SAME module as its original entry — zero access change",
          bool(a) and bool(b)
          and re.search(r"module: '(\w+)'", a).group(1) == re.search(r"module: '(\w+)'", b).group(1),
          f"{a} vs {b}")
    check(f"C4 {href} carries the SAME scopes as its original entry",
          bool(a) and bool(b)
          and re.search(r"scopes: \[([^\]]*)\]", a).group(1)
          == re.search(r"scopes: \[([^\]]*)\]", b).group(1),
          f"{a} vs {b}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
