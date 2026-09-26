"""PROOF + LOCK: whose name a daily closing is submitted under (owner 2026-09-26, index §29.7).

Owner, verbatim: *"while submitting the daily closing the employee name should default to the person who is
signed in, only the DM and above should get the option to select any body other than themselves"*.

ONE rule — `app/modules/closing/closer_pick.py` — enforced by the server (`closing/router.create_row` →
`_closer_gate`) and mirrored for the screen by `rbac.canPickAnyCloser` (the form offers the picker only when
it says yes). This harness proves the rule and fails the build if the server stops asking it, the screen
stops mirroring it, or the two drift.

  A. the rule: rep / store manager → own name only (case + spacing insensitive); DM (market), region and
     company-wide → anyone; platform super admin → anyone; an explicit Roles grant wins either way;
     a caller with no name on file is refused with a reason, never waved through
  B. the server asks it: create_row calls _closer_gate before anything is written, and _closer_gate asks
     closer_pick.verdict with the login's full name + employee-record name
  C. the screen mirrors it: same grant key, same scope tiers; the form gates the picker on canPickAnyCloser
     and otherwise shows the signed-in name read-only (a saved draft cannot override it)
  D. the Roles page can grant/withdraw it (DATA_GRANTS carries the key)
  E. registered in the index

PURE / DB-FREE: imports the pure module and reads sources as text.
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from app.modules.closing import closer_pick as cp  # noqa: E402

FAILS = []


def check(name, ok, why=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  -- " + why))
    if not ok:
        FAILS.append(name)


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


# ── A. the rule ──────────────────────────────────────────────────────────────────────────────────
print("A. the rule")
me = cp.own_names("Ennio Rodas", "Rodas, Ennio")
rep = {"__resolved": True, "scope": "store"}
selfp = {"__resolved": True, "scope": "self"}
dm = {"__resolved": True, "scope": "market"}
region = {"__resolved": True, "scope": "region"}
admin = {"__resolved": True, "scope": "all"}
check("A1 rep submitting under own name passes", cp.verdict(rep, "Ennio Rodas", me)[0])
check("A2 own name is case/spacing insensitive", cp.verdict(rep, "  ennio   RODAS ", me)[0])
check("A3 the employee-record spelling is also own", cp.verdict(rep, "Rodas, Ennio", me)[0])
ok, why = cp.verdict(rep, "Someone Else", me)
check("A4 rep under someone else's name is REFUSED with a reason", not ok and "own name" in why)
check("A5 self-scope behaves like a rep", not cp.verdict(selfp, "Someone Else", me)[0])
check("A6 DM (market scope) may pick anyone", cp.verdict(dm, "Someone Else", me)[0])
check("A7 region scope may pick anyone", cp.verdict(region, "Someone Else", me)[0])
check("A8 company-wide may pick anyone", cp.verdict(admin, "Someone Else", me)[0])
check("A9 no scope set reads as company-wide (house default)", cp.may_pick_any({"__resolved": True}))
check("A10 platform super admin may pick anyone", cp.verdict({"__super_admin": True, "scope": "store"}, "X", me)[0])
check("A11 explicit grant lets a store manager pick anyone",
      cp.verdict({"scope": "store", "data": {cp.GRANT_KEY: True}}, "Someone Else", me)[0])
check("A12 explicit withdrawal locks a DM to their own name",
      not cp.verdict({"scope": "market", "data": {cp.GRANT_KEY: False}}, "Someone Else", me)[0])
ok, why = cp.verdict(rep, "Anyone", set())
check("A13 a rep with no name on file is refused, told why", not ok and "no name on file" in why)
check("A14 own_names drops blanks", cp.own_names("", None, "  ") == set())

# ── B. the server asks it ────────────────────────────────────────────────────────────────────────
print("B. the server asks it")
router = read("backend/app/modules/closing/router.py")
m = re.search(r"async def create_row\(.*?\n(?=@router\.|\ndef |\nasync def )", router, re.S)
body = m.group(0) if m else ""
check("B1 create_row takes the Authorization header", "authorization: str = Header(" in body[:300])
gate_at = body.find("_closer_gate(client, org_id, authorization, payload.get(\"employee_name\"))")
write_at = min([i for i in (body.find(".insert("), body.find(".update(")) if i >= 0] or [len(body)])
check("B2 create_row calls _closer_gate before any write", 0 <= gate_at < write_at, "gate missing or after the write")
g = re.search(r"def _closer_gate\(.*?\n(?=\ndef |\nasync def |@router\.)", router, re.S)
gs = g.group(0) if g else ""
check("B3 _closer_gate asks closer_pick.verdict", "closer_pick.verdict(perms, submitted_name, names)" in gs)
check("B4 own names = login full_name + employee record name",
      "full_name" in gs and "employee_id" in gs and "closer_pick.own_names(" in gs)
check("B5 a refusal is a 403 carrying the reason", "HTTPException(403, why)" in gs)
check("B6 no second copy of the tiers in the router", "PICK_ANY_SCOPES" not in router and "'market', 'region'" not in router)

# ── C. the screen mirrors it ─────────────────────────────────────────────────────────────────────
print("C. the screen mirrors it")
rbac = read("frontend/src/lib/rbac.ts")
mk = re.search(r"export const CLOSER_PICK_ANY_GRANT = '([^']+)'", rbac)
check("C1 same grant key", bool(mk) and mk.group(1) == cp.GRANT_KEY, "drift")
ms = re.search(r"export const CLOSER_PICK_ANY_SCOPES: readonly string\[\] = \[([^\]]*)\]", rbac)
fe_scopes = tuple(re.findall(r"'([^']+)'", ms.group(1))) if ms else ()
check("C2 same scope tiers %s" % (fe_scopes,), fe_scopes == tuple(cp.PICK_ANY_SCOPES), "drift")
form = read("frontend/src/components/ClosingSubmitForm.tsx")
check("C3 the form asks canPickAnyCloser", "canPickAnyCloser(permissions, isPlatformAdmin(user))" in form)
check("C4 the picker is offered only when allowed", "{pickAny ? (" in form and "<EntityPicker options={empOptions}" in form)
check("C5 otherwise the signed-in name, read-only, and never a draft's name",
      "const employeeName = pickAny ? (f.employee_name || (nameTouched ? '' : prefillName)) : lockedName" in form
      and "readOnly" in form)

# ── D. configurable per role ─────────────────────────────────────────────────────────────────────
print("D. configurable per role")
check("D1 DATA_GRANTS lists the key (Roles page tick box)", "{ key: '%s'" % cp.GRANT_KEY in rbac)

# ── E. registered ────────────────────────────────────────────────────────────────────────────────
print("E. registered")
idx = read("docs/SYSTEM_DATA_FLOW_INDEX.md")
check("E1 index §29.7 documents it", "### 29.7" in idx and "harness_closing_closer_pick.py" in idx)

print()
print("%d FAIL(s)" % len(FAILS) if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)
