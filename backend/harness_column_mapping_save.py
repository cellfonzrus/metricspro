"""PROOF: saving a column mapping actually SAVES (owner bug report 2026-09-19).

THE DEFECT. `POST /commcalc/column-mapping` persisted with

    .upsert(row, on_conflict="org_id,report_key,carrier_id,target_field")

and Postgres refuses that with 42P10 — "there is no unique or exclusion constraint matching the ON
CONFLICT specification" — because mig 042's uniqueness is an EXPRESSION index:

    CREATE UNIQUE INDEX column_mapping_uq ON commcalc.column_mapping
      (org_id, report_key, COALESCE(carrier_id, '000…0'::uuid), target_field);

`carrier_id` is not `COALESCE(carrier_id, …)`, and ON CONFLICT requires an EXACT index match. So every
save through this endpoint failed.

MEASURED CONSEQUENCE, not a hypothetical: `commcalc.column_mapping` was EMPTY across the whole platform,
and the Commission Ledger setup wizard's "Re-check with these columns" reported success while persisting
nothing. The importer then fell back to the built-in MA Daily Tx layout, which matched NONE of the
tenant's headers (file had Gross / Report Section / Report Heading / AgentSSOID / Master Service Date;
defaults wanted Account ID / Product Name / Retail Cost / Order Type / Date of Transaction — zero
overlap), so the preview read $0.00 across 0 lines and six import attempts each returned
400 "No usable rows — check the column mapping for this file."

WHY A READ-THEN-WRITE AND NOT A NEW INDEX. A plain unique index on the four BARE columns cannot replace
the expression one: SQL NULLs are DISTINCT, so `carrier_id IS NULL` rows would stop colliding and a
tenant could accumulate unlimited duplicate "global" mappings for the same field — precisely what mig
042 exists to prevent. The look-up handles that NULL slot explicitly and needs no migration.

DB-FREE by default (§A parses the real source). §B additionally round-trips against a THROWAWAY
report_key when SUPABASE_URL/SUPABASE_SERVICE_KEY are present, and deletes what it wrote; without
credentials it is skipped, never failed.

Run:  python3 backend/harness_column_mapping_save.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS, FAIL, SKIP = [], [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name if ok else f"{name} :: {detail}")


# ── A. THE DEFECT IS GONE FROM THE SOURCE ───────────────────────────────────────────────────────
SRC = open(os.path.join(HERE, "app/modules/commcalc/router.py"), encoding="utf-8").read()
_i = SRC.index("def upsert_column_mapping(")
# THE WHOLE FUNCTION, to the next top-level definition — not a fixed character window. A window
# silently truncates the moment the function grows (it did, when the amount column's sign convention
# was added on 2026-09-20), and a guard that stops reading half way through reports a defect that is
# not there while being blind to one that is.
_j = re.search(r"(?m)^(@router\.|def |class )", SRC[_i + 10:])
FN = SRC[_i:_i + 10 + _j.start()] if _j else SRC[_i:]
# Strip comments FIRST: the fix documents the broken call by name, and a guard that grepped raw
# source would fail on its own explanation — and, far worse, would PASS on a defect merely commented
# out. Same lesson as harness_wizard_feedback_guard and harness_screen_link_guard §G.
CODE = re.sub(r"(?m)^\s*#.*$", "", FN)

check("A1 the endpoint no longer names an ON CONFLICT target that does not exist",
      'on_conflict="org_id,report_key,carrier_id,target_field"' not in CODE)
check("A2 …and does not reach for ON CONFLICT on this table at all", "on_conflict" not in CODE)
check("A3 it looks the existing row up before writing", '.eq("target_field", tf)' in CODE)
check("A4 …scoped to the org and report", '.eq("org_id", org_id)' in CODE and '.eq("report_key", rk)' in CODE)
check("A5 …handling the NULL (global) carrier slot explicitly",
      'is_("carrier_id", "null")' in CODE)
check("A6 …and a specific carrier as its own slot", '.eq("carrier_id", cid)' in CODE)
check("A7 it UPDATES when a row exists", '.update(row).eq("id", rid)' in CODE)
check("A8 …and INSERTS when it does not", '.insert(row)' in CODE)
check("A9 an explicit id still takes the direct update path, unchanged", "if body.id:" in CODE)

# ── B. LIVE ROUND TRIP on a throwaway key (skipped without credentials) ─────────────────────────
URL, KEY = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
if not (URL and KEY):
    SKIP.append("B live round-trip (no SUPABASE_URL / SUPABASE_SERVICE_KEY in the environment)")
else:
    from supabase import create_client
    sb = create_client(URL, KEY)
    ORG = "00000000-0000-0000-0000-000000000001"
    RK = "__harness_column_mapping_save"
    tbl = lambda: sb.schema("commcalc").table("column_mapping")

    def row_for(header):
        return {"org_id": ORG, "report_key": RK, "carrier_id": None, "target_field": "probe",
                "source_header": header, "transform": "text", "is_active": True, "priority": 100}

    try:
        tbl().delete().eq("org_id", ORG).eq("report_key", RK).execute()

        # B1 — REPRODUCE THE CAUSE: the old call still fails, so the fix is not cosmetic.
        reproduced = False
        try:
            tbl().upsert(row_for("First"),
                         on_conflict="org_id,report_key,carrier_id,target_field").execute()
        except Exception as e:
            reproduced = "42P10" in str(e) or "ON CONFLICT" in str(e)
        check("B1 REPRODUCE: the old ON CONFLICT call still raises 42P10 against the real table",
              reproduced)

        # B2/B3 — the new path: insert, then update the SAME slot rather than duplicating it.
        tbl().insert(row_for("First")).execute()
        n1 = len(tbl().select("id").eq("org_id", ORG).eq("report_key", RK).execute().data)
        check("B2 a first save lands one row", n1 == 1, f"got {n1}")

        found = (tbl().select("id").eq("org_id", ORG).eq("report_key", RK)
                 .eq("target_field", "probe").is_("carrier_id", "null").limit(1).execute().data)
        check("B3 the NULL-carrier slot is findable by look-up (what the fix relies on)", len(found) == 1)
        tbl().update(row_for("Second")).eq("id", found[0]["id"]).execute()

        after = tbl().select("source_header").eq("org_id", ORG).eq("report_key", RK).execute().data
        check("B4 re-saving UPDATES in place — no duplicate global row", len(after) == 1, f"got {len(after)}")
        check("B5 …and the new value is what stuck",
              after and after[0]["source_header"] == "Second", str(after))
    finally:
        tbl().delete().eq("org_id", ORG).eq("report_key", RK).execute()
        left = len(tbl().select("id").eq("org_id", ORG).eq("report_key", RK).execute().data)
        check("B6 the harness cleans up after itself", left == 0, f"{left} left behind")

for p in PASS:
    print(f"  PASS  {p}")
for s in SKIP:
    print(f"  SKIP  {s}")
for f in FAIL:
    print(f"  FAIL  {f}")
print(f"\nharness_column_mapping_save: {len(PASS)} passed, {len(FAIL)} failed, {len(SKIP)} skipped")
sys.exit(1 if FAIL else 0)
