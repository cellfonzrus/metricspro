"""LOCK — a number that names ONE thing is claimed ONCE (2026-09-28).

THE DEFECT. #315 (auto-calculation) and #317 (the port-out fraud report) were built in parallel and merged
the same day. Both took migration number 1030 and both took index section §19.32. Nothing noticed: CI
ran each branch against a main that did not yet have the other. So the owner, told "run 1030", had two
files by that name — and one of them (the fraud report's Block 2) sends daily WhatsApp and email to
every market manager and above.

THE CLASS. Migration numbers and index section numbers are identifiers. Two branches cut from the same
main each see the next free number as free, so a collision is the NORMAL outcome of parallel work, not
a slip. The only place both claims are visible together is the merged tree — so the check runs on every
tree CI builds, including main after the merge.

WHAT THIS FAILS ON:
  A. two files under database/migrations/ with the same number (the letter suffix counts: 268 and 268b
     are different numbers, as the migration runner already orders them);
  B. two index sections (a `##`/`###`/`####` heading, a `§N.M **…**` paragraph, or a table-of-contents
     row) with the same number in docs/SYSTEM_DATA_FLOW_INDEX.md.
Collisions that predate this lock and are already APPLIED (renaming an applied migration would break the
record of what ran) are listed by exact file name / exact count in GRANDFATHERED_* below. The list may
only shrink: a third file on a grandfathered number fails, and an entry that no longer collides fails
until it is removed from the list.

Negative controls (C) plant each kind of collision and require this lock to catch it.
Stdlib only, no DB. Run: python3 harness_unique_numbers_lock.py
"""
import collections
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MIGRATIONS = os.path.join(ROOT, "database", "migrations")
INDEX = os.path.join(ROOT, "docs", "SYSTEM_DATA_FLOW_INDEX.md")

# Applied long ago under a shared number; each set is exact. Never add to this list — renumber the new file.
GRANDFATHERED_MIGRATIONS = {
    "223": {"223_commission_installment_gate_source.sql", "223_rollback_commission_installment_gate_source.sql"},
    "420": {"420_storeops_face_recognition_toggle.sql", "420_storeops_google_reviews_lookback.sql"},
    "724": {"724_core_security_anon_function_lockdown.sql", "724_pos_core.sql"},
    "864": {"864_pos_receipt_import.sql", "864_pos_special_order_catalog.sql"},
    "865": {"865_pos_special_orders.sql", "865_receipt_import_encrypt.sql"},
    "866": {"866_pos_vendor_connectors.sql", "866_receipt_import_structured.sql"},
    "867": {"867_activation_rebate_ledger.sql", "867_approvals_engine.sql"},
}
# (kind, id) -> how many times it may appear. Never add to this list — renumber the new section.
GRANDFATHERED_SECTIONS = {
    ("heading", "23s"): 2,
    ("heading", "23s.8"): 2,
    ("toc", "12"): 2,
}

_MIG = re.compile(r"^(\d+[a-z]?)_.+\.sql$")
_HEADING = re.compile(r"^#{2,4}\s+(\d+[a-z]?(?:\.\d+[a-z]?)*)\.?\s")
_PARA = re.compile(r"^§(\d+\.\d+[a-z]?)\s+\*\*")
_TOC = re.compile(r"^\|\s*(\d+[a-z]?(?:\.\d+[a-z]?)?)\s*\|\s*\*\*")
TOC_LINES = 200  # the table of contents sits at the top of the index

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def migration_collisions(names, grandfathered):
    """-> list of problems. `names` = file names in the migrations folder."""
    by_num = collections.defaultdict(set)
    for n in names:
        m = _MIG.match(n)
        if m:
            by_num[m.group(1)].add(n)
    problems = []
    for num, files in sorted(by_num.items()):
        allowed = grandfathered.get(num)
        if len(files) > 1 and files != allowed:
            problems.append(f"migration {num} is claimed by {len(files)} files: {sorted(files)}")
    for num, files in sorted(grandfathered.items()):
        if by_num.get(num) != files:
            problems.append(f"grandfathered migration {num} no longer matches {sorted(files)} — "
                            f"found {sorted(by_num.get(num, set()))}; shrink GRANDFATHERED_MIGRATIONS")
    return problems


def section_collisions(lines, grandfathered):
    """-> list of problems. `lines` = the index, one string per line."""
    seen = collections.defaultdict(list)
    for i, line in enumerate(lines, 1):
        for kind, rx in (("heading", _HEADING), ("para", _PARA)):
            m = rx.match(line)
            if m:
                seen[(kind, m.group(1))].append(i)
        if i <= TOC_LINES:
            m = _TOC.match(line)
            if m:
                seen[("toc", m.group(1))].append(i)
    problems = []
    for key, where in sorted(seen.items()):
        allowed = grandfathered.get(key, 1)
        if len(where) > allowed:
            problems.append(f"index {key[0]} §{key[1]} appears {len(where)} times (lines {where})")
    for key, n in sorted(grandfathered.items()):
        if len(seen.get(key, [])) != n:
            problems.append(f"grandfathered index {key[0]} §{key[1]} no longer appears {n} times — "
                            f"found {len(seen.get(key, []))}; shrink GRANDFATHERED_SECTIONS")
    return problems


print("A. migration numbers are unique")
mig_names = sorted(os.listdir(MIGRATIONS))
check("the migrations folder was read", len(mig_names) > 400 and "001_core.sql" in mig_names, len(mig_names))
probs = migration_collisions(mig_names, GRANDFATHERED_MIGRATIONS)
check("no migration number is claimed twice (outside the grandfathered, already-applied set)", not probs,
      "\n        " + "\n        ".join(probs))
check("1030 is the auto-calculation migration alone",
      [n for n in mig_names if n.startswith("1030_")] == ["1030_auto_calc_on_landing.sql"],
      [n for n in mig_names if n.startswith("1030_")])
check("the port-out fraud report migration is 1031",
      "1031_portout_fraud_report.sql" in mig_names)

print("B. index section numbers are unique")
with open(INDEX, encoding="utf-8") as f:
    index_lines = f.read().split("\n")
probs = section_collisions(index_lines, GRANDFATHERED_SECTIONS)
check("no index section number is claimed twice (outside the grandfathered set)", not probs,
      "\n        " + "\n        ".join(probs))
para = {m.group(1): l for l in index_lines for m in [_PARA.match(l)] if m}
check("§19.32 is the port-out fraud report", "PORT-OUT" in para.get("19.32", ""), para.get("19.32", "")[:80])
check("§19.33 is the missing period lock (auto-calculation)", "PERIOD LOCK" in para.get("19.33", ""),
      para.get("19.33", "")[:80])

print("C. negative controls — each planted collision is caught")
check("control: two new files on one number",
      migration_collisions(["1040_a.sql", "1040_b.sql"], {}) != [])
check("control: a letter suffix is its own number (268 / 268b)",
      migration_collisions(["268_a.sql", "268b_b.sql"], {}) == [])
check("control: a third file on a grandfathered number",
      migration_collisions(["223_x.sql", "223_y.sql", "223_z.sql"], {"223": {"223_x.sql", "223_y.sql"}}) != [])
check("control: a grandfathered entry that no longer collides must be removed",
      migration_collisions(["223_x.sql"], {"223": {"223_x.sql", "223_y.sql"}}) != [])
check("control: two `§N.M **` paragraphs with one number",
      section_collisions(["§19.40 **A**", "§19.40 **B**"], {}) != [])
check("control: two `###` headings with one number",
      section_collisions(["### 6x. ONE", "### 6x. TWO"], {}) != [])
check("control: two table-of-contents rows with one number",
      section_collisions(["| 41 | **A** | x |", "| 41 | **B** | y |"], {}) != [])
check("control: distinct numbers pass",
      section_collisions(["### 6x. ONE", "### 6y. TWO", "§19.40 **A**", "§19.41 **B**"], {}) == [])
with tempfile.TemporaryDirectory() as d:
    for n in ("1030_auto_calc_on_landing.sql", "1030_portout_fraud_report.sql"):
        open(os.path.join(d, n), "w").close()
    check("control: the 2026-09-28 tree (two 1030s) would have failed",
          migration_collisions(sorted(os.listdir(d)), {}) != [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
