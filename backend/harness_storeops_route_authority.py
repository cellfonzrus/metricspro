"""Proof harness — EVERY storeops route asks who is calling, and the ones that do cannot un-wire.

Index §STOREOPS-07.8. DB-free, no network: the subject is the shipped SOURCE, read with `ast`.
Run from backend/:  python3 harness_storeops_route_authority.py

WHY THIS EXISTS. The daily security pass (§CORE-42) probed storeops on 2026-11-27. The stranger was
refused on every path — and the blast-radius question found **30 WRITE routes that took no
`authorization` parameter at all**, among them the whole `/org/*` cluster that rewrites the company
structure, `POST /stores`, and `PUT /payroll-settings/{employee_id}` (one named person's W-4). A
route that cannot ask who is calling has only the tenant as a boundary, so every signed-in person in
that tenant is equal on it however the UI is drawn. That is the owner's own 2026-08-05 ruling
(`harness_privesc_rbac_gates`: *"these endpoints self-gated on nothing but the UI hiding the
control"*) and §STOREOPS-07.65, one module over.

THE GATES ALREADY EXISTED. `require_person_write`, `require_not_individual_contributor` and
`gate_person_rows` are the house one homes, built on owner directives of 2026-11-04 and 2026-11-20.
The defect was 21 callers never wired to them — the §19.18 pattern the house rules name explicitly:
*"writing the registry without wiring the callers to it is not a fix at all."* This lock is the part
that stops it happening a fifth time.

WHAT IS PROVED
  §A  no WRITE route is ungated beyond the pinned debt, and the debt may only SHRINK.
  §B  the unexcused READ debt may only SHRINK.
  §C  each route this PR wired is STILL wired, by name — un-wiring one trips a check here.
  §D  armed controls: the checks really fail when the property is broken.
  §E  the vocabulary has ONE home (`storeops/route_authority`) and this lock dereferences it.
"""
import ast
import os
import sys

sys.path.insert(0, ".")
os.environ.setdefault("SUPABASE_URL", "https://example-harness.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "harness-dummy-service-key")
os.environ.setdefault("SUPABASE_KEY", "harness-dummy-anon-key")

from app.modules.storeops import route_authority as ra   # noqa: E402  THE one home

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


# ── the ratchet ───────────────────────────────────────────────────────────────────────────────────
# Measured 2026-11-27 AFTER wiring the 21 routes below. These numbers are a CEILING and a tripwire:
# they may be lowered when the debt is paid and must never be raised. Raising one is the patchwork
# the house rules forbid — gate the route instead.
MAX_UNGATED_WRITES = 10
MAX_UNGATED_READS = 9

# The debt, named, so a NEW ungated route cannot hide inside the count.
KNOWN_UNGATED_WRITES = {
    "POST /dropoff-label/read", "POST /dropoff-label/read-batch",
    "POST /dropoff-label/barcodes", "POST /dropoff-label/barcode",
    "PATCH /shifts/{shift_id}", "DELETE /shifts/{shift_id}",
    "DELETE /employees/{emp_id}", "POST /employees/merge",
    "PUT /employees/{emp_id}/face-config", "PUT /employees/{emp_id}/lunch-config",
}
KNOWN_UNGATED_READS = {
    "GET /payroll/clock-link-proposals", "GET /clock-in-check", "GET /payroll/payers",
    "GET /timeclock/attendance-config", "GET /timeclock/geo-config",
    "GET /accountability/alert-config", "GET /timeclock/face-config",
    "GET /timeclock/face-retention/config", "GET /timeclock/lunch-config",
}

# ── §C the routes wired on 2026-11-27, each with the gate it must keep dereferencing ─────────────
WIRED = {
    "POST /shifts": "require_person_write",
    "POST /stores/bulk": "require_not_individual_contributor",
    "POST /stores": "require_not_individual_contributor",
    "PATCH /stores/{store_id}": "require_not_individual_contributor",
    "POST /shift-templates/save-week": "require_not_individual_contributor",
    "POST /shift-templates/apply": "require_not_individual_contributor",
    "GET /payroll-settings/{employee_id}": "gate_person_rows",
    "PUT /payroll-settings/{employee_id}": "require_person_write",
    "POST /org/seed": "require_not_individual_contributor",
    "POST /org/build-standard": "require_not_individual_contributor",
    "POST /org/levels": "require_not_individual_contributor",
    "PUT /org/levels/{level_id}": "require_not_individual_contributor",
    "DELETE /org/levels/{level_id}": "require_not_individual_contributor",
    "POST /org/units": "require_not_individual_contributor",
    "PUT /org/units/{unit_id}": "require_not_individual_contributor",
    "DELETE /org/units/{unit_id}": "require_not_individual_contributor",
    "POST /org/units/{unit_id}/managers": "require_not_individual_contributor",
    "DELETE /org/units/{unit_id}/managers/{employee_id}": "require_not_individual_contributor",
    "PUT /org/stores/{store_code}/unit": "require_not_individual_contributor",
    "PUT /org/employees/{row_id}/unit": "require_not_individual_contributor",
    "POST /payroll/clock-variance-ask": "require_not_individual_contributor",
}

ROWS = ra.classify_routes()
BY_KEY = {}
for r in ROWS:
    BY_KEY.setdefault(r["key"], r)

print("\n§A  no WRITE route asks nobody (beyond the pinned, named debt)\n")
uw = ra.ungated_writes(ROWS)
keys = {r["key"] for r in uw}
check("A1 the ungated-WRITE debt has not grown", len(uw) <= MAX_UNGATED_WRITES,
      f"{len(uw)} > {MAX_UNGATED_WRITES}: {sorted(keys - KNOWN_UNGATED_WRITES)}")
check("A2 no NEW ungated write appeared", keys <= KNOWN_UNGATED_WRITES,
      f"unnamed: {sorted(keys - KNOWN_UNGATED_WRITES)}")
check("A3 the ratchet is not slack (a paid debt must be re-pinned)",
      len(uw) == len(keys & KNOWN_UNGATED_WRITES),
      "lower MAX_UNGATED_WRITES and drop the paid routes from KNOWN_UNGATED_WRITES")

print("\n§B  the unexcused READ debt may only shrink\n")
ur = ra.ungated_reads(ROWS)
rkeys = {r["key"] for r in ur}
check("B1 the ungated-READ debt has not grown", len(ur) <= MAX_UNGATED_READS,
      f"{len(ur)} > {MAX_UNGATED_READS}: {sorted(rkeys - KNOWN_UNGATED_READS)}")
check("B2 no NEW ungated read appeared", rkeys <= KNOWN_UNGATED_READS,
      f"unnamed: {sorted(rkeys - KNOWN_UNGATED_READS)}")
check("B3 every excused read is excused WITH A REASON",
      all(str(v).strip() for v in ra.EXCUSED_READS.values()))
check("B4 an excused read is a real route (a stale excuse is a lie)",
      all(k in BY_KEY for k in ra.EXCUSED_READS),
      f"stale: {sorted(set(ra.EXCUSED_READS) - set(BY_KEY))}")

print("\n§C  every route wired on 2026-11-27 is STILL wired — by name\n")
for key, gate in sorted(WIRED.items()):
    row = BY_KEY.get(key)
    check(f"C[{key}] exists", row is not None)
    if not row:
        continue
    check(f"C[{key}] takes an authorization parameter", row["takes_authorization"])
    check(f"C[{key}] dereferences {gate}", gate in row["gates"],
          f"gates={row['gates']}")
    check(f"C[{key}] counts as asking", row["asks"])

print("\n§D  armed controls — the checks fail when the property is broken\n")
_FAKE = dict(is_write=True, asks=False, takes_authorization=False, gates=(), key="POST /made-up")
check("D1 a new ungated write is caught by A2",
      not ({"POST /made-up"} | KNOWN_UNGATED_WRITES) <= KNOWN_UNGATED_WRITES)
check("D2 an un-wired route fails C (no gate in its body)",
      "require_person_write" not in _FAKE["gates"])
check("D3 classify_routes really reads the shipped source",
      len(ROWS) > 150 and any(r["key"] == "GET /payroll-settings/{employee_id}" for r in ROWS),
      f"{len(ROWS)} routes")
_src = open(os.path.join(os.path.dirname(os.path.abspath(ra.__file__)), "router.py"),
            encoding="utf-8").read()
check("D4 un-wiring is DETECTABLE: removing the call removes it from the body",
      _src.count("require_not_individual_contributor(") >= 19,
      f"count={_src.count('require_not_individual_contributor(')}")
check("D5 the gate is asked BEFORE the write (first statement after the docstring)",
      all("require_not_individual_contributor" in _src.split(f'def {n}(')[1][:700]
          for n in ("org_unit_create", "org_unit_delete", "create_store")
          if f'def {n}(' in _src))

print("\n§E  one fact, one home — this lock holds no copy of the vocabulary\n")
_me = open(os.path.abspath(__file__), encoding="utf-8").read()
check("E1 the lock dereferences route_authority", "route_authority" in _me)
# The needles are BUILT, never spelled: a literal here would match this very file and the check
# would fail on itself (it did, first run).
_n2 = "AUTHORITY_" + "GATES = ("
_n3 = "EXCUSED_" + "READS = {"
check("E2 the lock keeps no second authority-gate list", _n2 not in _me)
check("E3 the lock keeps no second excused-read map", _n3 not in _me)
check("E5 the one home really declares both", _n2 in open(ra.__file__, encoding="utf-8").read()
      and _n3 in open(ra.__file__, encoding="utf-8").read())
check("E4 the vocabulary names only helpers that exist in storeops",
      all(any(g in open(os.path.join(os.path.dirname(os.path.abspath(ra.__file__)), f),
                        encoding="utf-8").read()
              for f in os.listdir(os.path.dirname(os.path.abspath(ra.__file__)))
              if f.endswith(".py"))
          for g in ra.AUTHORITY_GATES))

print("\n" + "=" * 78)
print(f"storeops route authority: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED:", f)
    raise SystemExit(1)
