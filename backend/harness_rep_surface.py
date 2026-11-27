"""PROOF — an individual contributor's reach is ONE declaration, the API refuses what is not in it,
and no tenant's answer can leak into another's.

OWNER (2026-11-04): *"All sales reps should log in to the clock in screen, on ny logistics Manan was
able to go past it and also see the adjustment hours, this should not be allowed for any sales reps,
fix the permissions accordingly … this is universal across the board."*

THE DEFECT, reproduced for real in `frontend/tools/rep-surface-nav-proof.mjs` §A over the compiled
`lib/rbac.ts`: the `scope === 'self'` branch admitted any page whose URL began with a menu row the
rep could see, so the `/storeops` row admitted `/storeops/timeclock` — Hours Adjustment — and 32
other manager pages. This harness proves the half that is the real fix: the SERVER's own refusal.

DB-free. The pure declaration, the REAL middleware verdict, and the REAL route handlers over the
in-memory double (`tests/fakes/supabase`) — the same double the integration suite uses, so there is
one semantics for "what does .eq() do" and not two.

  A. THE GRAMMAR. A declared page is EXACT or an explicit `/**` subtree; a page's PARENT prefix
     admits nothing (the defect); a `page_except` carves the manager screens out of a subtree.
  B. DEFAULT DENY on the API: every manager path refused, every declared path allowed, a read-only
     lookup refused for a write method.
  C. THE MIDDLEWARE VERDICT over each real role shape: a self-scoped caller is refused an undeclared
     path and served a declared one; a store manager and a company role are refused nothing; the
     break-glass lifts it; an unreadable roles table refuses nobody.
  D. THE FOUR WRITES that asked nothing before: hours adjustment (add + delete), time off, shift
     swap, onboarding paperwork by id — refused to a rep, allowed to the manager whose team the
     person is on, refused to a manager whose team they are not on.
  E. EVERY TENANT (owner 2026-10-30): the declaration takes no org_id at all, and the gate's answer
     for one tenant's role is never reachable from another tenant's request.
  F. NO LOCKOUT (index §22): the identity, shell and own-password paths can never be refused.

    python3 backend/harness_rep_surface.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("APP_ENV", "test")
for _v in ("SUPABASE_KEY", "SUPABASE_SERVICE_KEY", "DATABASE_URL"):
    os.environ[_v] = ""

from app.core import rep_surface as RS                      # noqa: E402
from tests.fakes.supabase import FakeSupabase               # noqa: E402

P = F = 0


def check(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print(f"  PASS  {name}")
    else:
        F += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nA. THE PAGE GRAMMAR — a parent prefix admits nothing")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("A1 the landing is reachable", RS.page_allowed("/my"))
check("A2 a declared subtree admits what is below it", RS.page_allowed("/my/anything"))
check("A3 the clock-in screen is reachable", RS.page_allowed("/portal"))
check("A4 THE DEFECT: the Hours Adjustment screen is NOT reachable", not RS.page_allowed("/storeops/timeclock"))
check("A5 …nor Hours Approval", not RS.page_allowed("/storeops/payroll/approvals"))
check("A6 …nor payroll, the employee database or store setup",
      not any(RS.page_allowed(p) for p in ("/storeops/payroll", "/storeops/employees",
                                           "/storeops/setup/stores", "/storeops/reports",
                                           "/storeops/attendance", "/storeops/admin")))
check("A7 a declared EXACT page admits only itself — Time Off does not admit its neighbours",
      RS.page_allowed("/storeops/timeoff") and not RS.page_allowed("/storeops/timeoff/settings"))
check("A8 the helpdesk subtree admits a ticket", RS.page_allowed("/helpdesk") and RS.page_allowed("/helpdesk/41"))
check("A9 …and the carve-out refuses its two manager screens",
      not RS.page_allowed("/helpdesk/settings") and not RS.page_allowed("/helpdesk/dashboard"))
check("A10 the Report Center is not an employee page", not RS.page_allowed("/reports"))
check("A11 a query string never changes the answer",
      RS.page_allowed("/helpdesk?tab=mine") and not RS.page_allowed("/storeops/timeclock?d=1"))
check("A12 a trailing slash never changes the answer",
      RS.page_allowed("/helpdesk/") and not RS.page_allowed("/storeops/timeclock/"))
# DECLARED, not counted. "dropoff" is the sixth, added 2026-11-20 on the owner's *"Grant the sales
# rep this"* — AFTER his original five, which keep their order. A tile is how a confined employee
# finds a surface at all (they have no menu row), so a surface arriving here is a deliberate act and
# names itself in this list rather than sliding under a count.
check("A13 every one of the owner's five has a tile, in his order — and nothing else has one "
      "without saying so here",
      [t["key"] for t in RS.tiles()] == ["clock", "commissions", "schedule", "action_plan",
                                         "targets", "helpdesk", "dropoff", "closing",
                                         "chat", "training", "password"],
      [t["key"] for t in RS.tiles()])
check("A14 every tile's page is a page the gate admits",
      all(RS.page_allowed(t["page"]) for t in RS.tiles()), [t["page"] for t in RS.tiles()])
# ── A15–A18 THE TILE'S PAGE IS AN ADDRESS, NOT A PATTERN ─────────────────────────────────────────
# THE DEFECT (reported 2026-11-27): a confined employee tapped "My commissions" on the landing and
# got the app's 404. The tile carried `pages[0]` VERBATIM, and `pages[0]` is written in the page
# GRAMMAR — `"/employee/**"` means "that page and everything below it". A browser cannot navigate to
# a pattern, so three of the six tiles (clock, commissions, helpdesk) linked to an address that
# cannot exist, for EVERY tenant.
#
# A14 above could not see it, and that is the lesson: `page_allowed("/employee/**")` is True, because
# the literal `/**` reads as a child of `/employee` and the subtree grant admits its children. A
# default-deny gate answers a wrong-SHAPED question with a confident yes. So the shape is pinned
# here, separately from the permission.
check("A15 THE 404: no tile publishes a grammar pattern as its link",
      not any("*" in str(t["page"] or "") for t in RS.tiles()),
      [t["page"] for t in RS.tiles()])
check("A16 the pattern → address conversion has ONE home that every tile dereferences",
      hasattr(RS, "tile_page") and RS.tile_page("/employee" + RS.SUBTREE) == "/employee"
      and RS.tile_page("/storeops/timeoff") == "/storeops/timeoff")
check("A17 …and the addresses it publishes are the real ones",
      [t["page"] for t in RS.tiles()] == ["/portal", "/employee", "/my/schedule",
                                          "/commcalc/targets/action-plan", "/commcalc/targets/my",
                                          "/helpdesk", "/storeops/dropoff-barcode",
                                          "/closing/submit", "/chat", "/training",
                                          "/account/password"],
      [t["page"] for t in RS.tiles()])
check("A18 …and each one is still a page the gate admits, asked of the ADDRESS",
      all(RS.page_allowed(t["page"]) and "*" not in t["page"] for t in RS.tiles()))
# The clock's destination is the SECOND grant, and that is the second half of the defect: the tile's
# page was being guessed from the first grant, which for the clock is the landing the tile sits on.
check("A19 THE OWNER'S SECOND REPORT: Clock in / out opens the punch kiosk, not the landing",
      next(t["page"] for t in RS.tiles() if t["key"] == "clock") == "/portal")
# ── A19b THE DAILY CLOSING TILE (owner 2026-11-27) ───────────────────────────────────────────────
# *"daily closing tile is missing from their landing page but on teh portal is seen."* The form was
# reachable only as a panel inside the kiosk. It gets a tile and ONE page — and the exactness of that
# page is the safety: `/closing` is a 30-screen subsystem and none of it moves.
check("A19b the Daily closing tile opens the submit page",
      next((t["page"] for t in RS.tiles() if t["key"] == "closing"), None) == "/closing/submit")
check("A19c …and that grant admits that page and NOTHING else under /closing",
      RS.page_allowed("/closing/submit")
      and not any(RS.page_allowed(p) for p in (
          "/closing", "/closing/recon", "/closing/verify", "/closing/management",
          "/closing/tender-config", "/closing/store-cash-on-hand", "/closing/deposit-recon",
          "/closing/submit/anything")))
check("A19d …and it needed no new API grant: every call the page makes was already granted",
      all(RS.api_allowed(r, "POST") for r in ("/api/v1/closing/row", "/api/v1/closing/metric-lines"))
      and RS.api_allowed("/api/v1/closing/days", "GET")
      and not RS.api_allowed("/api/v1/closing/tender-config", "PUT"))
# ── A19e THE MY TARGETS TILE, AND THE THREE THAT WERE GRANTED AND UNREACHABLE ────────────────────
# OWNER 2026-11-27: *"my targets are shown on the portal and not on the employee dashboad, a tile for
# that shoudl be there too."* Target-vs-achieved lived only inside the kiosk's My Dashboard tab.
check("A19e the My targets tile opens the rep's own targets screen",
      next((t["page"] for t in RS.tiles() if t["key"] == "targets"), None) == "/commcalc/targets/my")
check("A19f …granted EXACTLY: the rest of the targets subsystem stays refused",
      RS.page_allowed("/commcalc/targets/my")
      and not any(RS.page_allowed(p) for p in (
          "/commcalc/targets", "/commcalc/targets/settings", "/commcalc/targets/report-cards",
          "/commcalc/targets/rep-map", "/commcalc/targets/accessories", "/commcalc/targets/my/x")))
check("A19g …and it needed no new API grant: the targets reads were already granted",
      all(RS.api_allowed(p, "GET") for p in (
          "/api/v1/commcalc/targets/November%202026/summary",
          "/api/v1/commcalc/targets/November%202026/calendar",
          "/api/v1/commcalc/targets/November%202026/action-plan"))
      and not RS.api_allowed("/api/v1/commcalc/targets", "POST"))
# The same class, found by asking the question once rather than per report: a surface GRANTED a page
# and given no tile is reachable only by typing its address, because a confined employee has no menu.
check("A19h every surface that HAS a page has a tile — nothing is granted and unreachable",
      [s["key"] for s in RS.SURFACES if s["pages"] and not s.get("tile")] == [],
      [s["key"] for s in RS.SURFACES if s["pages"] and not s.get("tile")])
check("A19i …and the one surface with no page of its own is the paperwork inside the kiosk",
      [s["key"] for s in RS.SURFACES if not s["pages"]] == ["own_paperwork"],
      [s["key"] for s in RS.SURFACES if not s["pages"]])
# ── A21 THE KIOSK OFFERS NO SURFACE THE GATE REFUSES ─────────────────────────────────────────────
# OWNER 2026-11-27: *"device history look up is not rwequired ont eh portal but the bar code resue
# is."* The employee dashboard mounted the device-history lookup for EVERY employee, on the reasoning
# that doing so was "safe" because the money rows are gated server-side. Safety is not the test:
# `GET /commcalc/device-history` is not granted to a confined employee, so the kiosk showed a search
# box that answers 403 — the browser offering what the API refuses, which is the §CORE-33 defect
# pointing the other way. The barcode rescue is the contrast: asked for, granted, and tiled.
_CR = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "app/modules/core/router.py"), encoding="utf-8").read()
_WIDGETS = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "../frontend/src/components/EmployeeWidgets.impl.tsx"),
                encoding="utf-8").read()
check("A21 the device-history lookup is not granted to a confined employee",
      not RS.api_allowed("/api/v1/commcalc/device-history", "GET"))
check("A21b …so the employee dashboard does not render it",
      "DeviceHistoryLookup" not in _WIDGETS and "device_history" not in _WIDGETS)
check("A21c …and the widget list no longer offers a toggle for a widget that cannot render",
      '"device_history"' not in _CR.split("EMP_WIDGETS = [")[1].split("]")[0])
check("A21d THE CONTRAST: the barcode rescue IS granted, and tiled",
      RS.page_allowed("/storeops/dropoff-barcode")
      and any(t["key"] == "dropoff" for t in RS.tiles())
      and RS.api_allowed("/api/v1/storeops/dropoff-label/read", "POST"))

# ── A22 THE CARD THAT COULD NOT LOAD ─────────────────────────────────────────────────────────────
# OWNER 2026-11-27, opening the tile added for him the same morning: *"on employee dashboard it shows
# Inventory aging could not be loaded (This is not part of your access. GET
# /api/v1/commcalc/my-shelf belongs to a manager screen…)"*. Granting the PAGE is half a grant — the
# cards on it ask the server too, and a refused card is what the employee actually sees.
check("A22 THE REPORTED DEFECT: the rep's own shelf is granted to the screen that shows it",
      RS.api_allowed("/api/v1/commcalc/my-shelf", "GET"))
check("A22b …read only — nothing about a shelf is an employee's to write",
      not RS.api_allowed("/api/v1/commcalc/my-shelf", "POST")
      and not RS.api_allowed("/api/v1/commcalc/my-shelf", "DELETE"))
check("A22c …and it did not widen the module: the other commcalc reads stay refused",
      not any(RS.api_allowed(p, "GET") for p in (
          "/api/v1/commcalc/inventory-aging", "/api/v1/commcalc/device-history",
          "/api/v1/commcalc/commission-ledger")))
# A grant is a PREFIX, so it would carry a member route if one were ever added below it (§4g). There
# is none, and this fails the build if one appears without the grant being narrowed to the leaf.
_CC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "app/modules/commcalc/router.py"), encoding="utf-8").read()
check("A22c2 …and nothing is declared BELOW /my-shelf, which a prefix grant would carry",
      '"/my-shelf/' not in _CC)
# The same class on three more screens, found by asking the question of every granted page at once
# rather than waiting for the next report (locked by harness_rep_surface_lock §6d).
check("A22d the ticket form's own vocabulary is granted — raising a ticket is the feature",
      RS.api_allowed("/api/v1/helpdesk/config/bootstrap", "GET"))
check("A22e …and the settings writes it used to sit behind are still refused",
      not RS.api_allowed("/api/v1/helpdesk/config/bootstrap", "PUT")
      and not RS.api_allowed("/api/v1/helpdesk/config/settings", "PUT")
      and not RS.api_allowed("/api/v1/helpdesk/config/categories", "POST")
      and not RS.api_allowed("/api/v1/helpdesk/support/queue", "GET")
      and not RS.api_allowed("/api/v1/helpdesk/stats/dashboard", "GET"))
check("A22f the ticket itself is still granted, members included",
      RS.api_allowed("/api/v1/helpdesk/tickets", "GET")
      and RS.api_allowed("/api/v1/helpdesk/tickets/42/comments", "POST")
      and RS.api_allowed("/api/v1/helpdesk/tickets/42/attachments", "POST"))
check("A22g the org's time-off policy is a READ for everyone, and the write stays a manager's",
      RS.api_allowed("/api/v1/storeops/timeoff-conflict-mode", "GET")
      and not RS.api_allowed("/api/v1/storeops/timeoff-conflict-mode", "PUT"))
# The personnel roster stays refused — the two pages that used it as a name picker now ask the
# PICKER, which the declaration already granted and which carries no personnel record.
_TO = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "../frontend/src/app/(platform)/storeops/timeoff/page.tsx"),
           encoding="utf-8").read()
_SW = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "../frontend/src/app/(platform)/storeops/swaps/page.tsx"),
           encoding="utf-8").read()
check("A22h the personnel roster is still refused to an employee (it carries date of birth, home "
      "address and emergency contacts)",
      not RS.api_allowed("/api/v1/storeops/employees", "GET"))
check("A22i …and neither rep page asks for it any more — both ask the picker",
      "'/api/v1/storeops/employees'" not in _TO and "'/api/v1/storeops/employees'" not in _SW
      and "/api/v1/storeops/employees/visible" in _TO
      and "/api/v1/storeops/employees/visible" in _SW
      and RS.api_allowed("/api/v1/storeops/employees/visible", "GET"))

# ── A23 A GRANT HERE IS A MAXIMUM, NOT A MANDATE ─────────────────────────────────────────────────
# OWNER 2026-11-27: *"also i disaables my targets from ny logistics sales reps but it still shows
# up."* The per-function switch an admin ticks on the Roles screen was never consulted for a
# confined viewer — this declaration answered as the whole truth, so the tenant's own administrator
# could not take away what it gave.
_OFF = {"pages": {"/commcalc/targets/my": False}}
check("A23 THE REPORTED DEFECT: the role's switch closes the tile",
      "targets" in [t["key"] for t in RS.tiles()]
      and "targets" not in [t["key"] for t in RS.tiles(_OFF)])
check("A23b …the page", not RS.page_allowed("/commcalc/targets/my", _OFF))
check("A23c …and the API that page calls",
      not RS.api_allowed("/api/v1/commcalc/my-shelf", "GET", role_perms=_OFF))
check("A23d the refusal sentence then stops offering the screen it just closed",
      "My targets" in RS.refusal("/api/v1/commcalc/my-shelf", "GET")
      and "My targets" not in RS.refusal("/api/v1/commcalc/my-shelf", "GET", role_perms=_OFF))
check("A23e a switch only SUBTRACTS — ticking a refused page ON grants nothing",
      not RS.page_allowed("/commcalc/targets/settings",
                          {"pages": {"/commcalc/targets/settings": True}}))
check("A23f every other tile is untouched by one switch",
      len(RS.tiles(_OFF)) == len(RS.tiles()) - 1
      and RS.page_allowed("/employee", _OFF) and RS.api_allowed("/api/v1/storeops/shifts", "GET",
                                                                role_perms=_OFF))
check("A23g ★ NO LOCKOUT ★ the password page cannot be switched off",
      RS.page_allowed("/account/password", {"pages": {"/account/password": False}})
      and RS.api_allowed("/api/v1/core/me", "POST",
                         role_perms={"pages": {"/account/password": False}}))
check("A23h the shell is never subtracted, whatever is switched off",
      all(RS.api_allowed(u, "GET", role_perms={"pages": {RS.tile_page(p): False
                                                         for p in RS.pages()}})
          for u in RS.UNIVERSAL_API))
check("A23i only a surface call makes the gate read the role at all",
      not RS.api_switchable("/api/v1/core/me", "GET")
      and not RS.api_switchable("/api/v1/storeops/payroll", "GET")
      and RS.api_switchable("/api/v1/commcalc/my-shelf", "GET"))

check("A20 a tile that declares no destination publishes none — there is no fallback to pages[0]",
      "pages\"][0]" not in open(RS.__file__).read().split("def tiles(")[1])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nB. THE API — default deny")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
REFUSED = [("/api/v1/storeops/manual-hours", "POST"), ("/api/v1/storeops/manual-hours/7", "DELETE"),
           ("/api/v1/storeops/manual-hours", "GET"), ("/api/v1/storeops/payroll", "GET"),
           ("/api/v1/storeops/payroll/approvals", "GET"), ("/api/v1/storeops/employees", "POST"),
           ("/api/v1/storeops/hours-budgets", "PUT"), ("/api/v1/storeops/timeclock-permissions", "GET"),
           ("/api/v1/hr/onboarding/employee/E229", "GET"), ("/api/v1/hr/payroll-expenses", "GET"),
           ("/api/v1/commcalc/commission-ledger", "GET"), ("/api/v1/account/pl-statement", "GET"),
           ("/api/v1/core/tenants", "GET"), ("/api/v1/storeops/roles", "PUT")]
for p, m in REFUSED:
    check(f"B- {m} {p} is refused", not RS.api_allowed(p, m), RS.surface_for_api(p, m))
ALLOWED = [("/api/v1/storeops/timeclock/clock-in", "POST"), ("/api/v1/storeops/timeclock/status", "GET"),
           ("/api/v1/core/employee-dashboard", "GET"), ("/api/v1/storeops/my-chargebacks", "GET"),
           ("/api/v1/storeops/shifts", "GET"), ("/api/v1/storeops/time-off", "POST"),
           ("/api/v1/storeops/shift-swaps", "POST"), ("/api/v1/commcalc/targets/2026-09/action-plan", "GET"),
           ("/api/v1/helpdesk/tickets", "POST"), ("/api/v1/hr/onboarding/me/sign", "POST"),
           ("/api/v1/closing/row", "POST"), ("/api/v1/core/me", "GET"), ("/api/v1/core/bootstrap", "GET")]
for p, m in ALLOWED:
    check(f"B+ {m} {p} is allowed", RS.api_allowed(p, m), "refused")
# ── The barcode rescue, opened to a confined employee 2026-11-20. The three facts that matter: the
#    rescue works, the tenant's DECLARATIONS cannot be touched, and the organisation-wide record
#    cannot be read. The last two are the ones a widening would quietly take with it.
check("B19a the rescue itself works — read a photo, print a confirmed reference",
      all(RS.api_allowed(p, "POST") for p in (
          "/api/v1/storeops/dropoff-label/read", "/api/v1/storeops/dropoff-label/read-batch",
          "/api/v1/storeops/dropoff-label/barcode", "/api/v1/storeops/dropoff-label/barcodes")))
check("B19b the declared label types are READABLE (the screen matches printed text with them) "
      "and the renderer reloads for a print dialog",
      RS.api_allowed("/api/v1/storeops/dropoff-label/profiles", "GET")
      and RS.api_allowed("/api/v1/storeops/dropoff-label/barcode.svg", "GET"))
check("B19c DECLARING a label type is an admin act and is refused",
      not RS.api_allowed("/api/v1/storeops/dropoff-label/profiles", "POST"))
check("B19d RETIRING one is refused twice over — by method, and by the member carve-out",
      not RS.api_allowed("/api/v1/storeops/dropoff-label/profiles/abc", "DELETE")
      and not RS.api_allowed("/api/v1/storeops/dropoff-label/profiles/abc", "GET"))
check("B19e the organisation's whole rescue record is a report, and is NOT granted",
      not RS.api_allowed("/api/v1/storeops/dropoff-label/scans", "GET"))
check("B19f the module PREFIX itself is not granted — only the named routes are",
      not RS.api_allowed("/api/v1/storeops/dropoff-label", "GET")
      and not RS.api_allowed("/api/v1/storeops/dropoff-label/anything-new", "POST"))

check("B20 a read-only lookup is refused for a WRITE method",
      RS.api_allowed("/api/v1/storeops/stores", "GET") and not RS.api_allowed("/api/v1/storeops/stores", "POST"))
check("B21 the employee roster is readable (already narrowed server-side) and never writable",
      RS.api_allowed("/api/v1/storeops/employees/visible", "GET")
      and not RS.api_allowed("/api/v1/storeops/employees/visible", "POST"))
check("B22 the shift WRITES are not an employee's",
      not RS.api_allowed("/api/v1/storeops/shifts", "POST")
      and not RS.api_allowed("/api/v1/storeops/shift-templates/save-week", "POST"))
check("B23 a path that merely STARTS LIKE a declared one is refused",
      not RS.api_allowed("/api/v1/storeops/timeclock-permissions/9/decision", "POST")
      and not RS.api_allowed("/api/v1/storeops/time-off-balances", "GET"))
# ── §B2. THE ASK IS NOT THE ANSWER (the security pass on this change, 2026-11-04) ────────────────
# The first draft granted whole prefixes for every method, so the POST that ASKS for time off also
# carried the PATCH that APPROVES it, and those handlers had no authority of their own: a rep could
# approve their own leave. Each pair below REPRODUCES the hole (the ask stays allowed, which is what
# made the grant look reasonable) and then shows the answer refused.
print("\n§B2 a grant on a collection is not a grant on its members")
PAIRS = [
    # (the ask — must stay ALLOWED, or an employee cannot ask),  (the answer — must be REFUSED)
    (("/api/v1/storeops/time-off", "POST"), ("/api/v1/storeops/time-off/41", "PATCH")),
    (("/api/v1/storeops/shift-swaps", "POST"), ("/api/v1/storeops/shift-swaps/7", "PATCH")),
    (("/api/v1/storeops/shift-extensions", "POST"),
     ("/api/v1/storeops/shift-extensions/9/decision", "POST")),
    (("/api/v1/closing/row", "POST"), ("/api/v1/closing/row/9", "DELETE")),
    (("/api/v1/helpdesk/tickets", "POST"), ("/api/v1/helpdesk/config/teams", "POST")),
    (("/api/v1/chat/channels/1/messages", "POST"), ("/api/v1/chat/admin/retention", "POST")),
]
for (ap, am), (rp, rm) in PAIRS:
    check(f"B2+ the ask {am} {ap} is still allowed", RS.api_allowed(ap, am), "an employee cannot ask")
    check(f"B2- the answer {rm} {rp} is refused", not RS.api_allowed(rp, rm), RS.surface_for_api(rp, rm))
check("B2a the org-wide time-off cleanup is not an employee's",
      not RS.api_allowed("/api/v1/storeops/time-off/reconcile-duplicates", "POST"))
check("B2b the help desk's agent console is refused, not just its page",
      not RS.api_allowed("/api/v1/helpdesk/support/sla-policy", "PUT")
      and not RS.api_allowed("/api/v1/helpdesk/support/cases/3/assign", "POST"))
check("B2c the closing CONFIG writes are refused while their reads stay (the kiosk form reads them)",
      not RS.api_allowed("/api/v1/closing/tender-config", "PUT")
      and not RS.api_allowed("/api/v1/closing/cash-config", "PUT")
      and not RS.api_allowed("/api/v1/closing/sweep/run-now", "POST")
      and RS.api_allowed("/api/v1/closing/tender-config", "GET")
      and RS.api_allowed("/api/v1/closing/count-config", "GET"))
check("B2d the timeclock ADMIN writes are refused while the punch and the manager override stay",
      not RS.api_allowed("/api/v1/storeops/timeclock/geo-config", "PUT")
      and not RS.api_allowed("/api/v1/storeops/timeclock/attendance-config", "PUT")
      and not RS.api_allowed("/api/v1/storeops/timeclock/permissions/3/decision", "POST")
      and RS.api_allowed("/api/v1/storeops/timeclock/clock-in", "POST")
      and RS.api_allowed("/api/v1/storeops/timeclock/clock-out", "POST")
      and RS.api_allowed("/api/v1/storeops/timeclock/override", "POST"))
check("B2e a carve-out refuses every method, a READ included — it is not a method narrowing",
      not RS.api_allowed("/api/v1/storeops/time-off/41", "GET"))
check("B2f …and a trace names no surface for a carved route, so nothing can report it as granted",
      RS.surface_for_api("/api/v1/storeops/time-off/41", "PATCH") is None)
# A READ-ONLY grant is a grant, and its members are reachable too. This check used to read only
# `s["api"]`, so a carve-out below an `api_read` prefix was reported as a comment pretending to be a
# gate when it is nothing of the kind: `api_read` on a collection admits GET on its members, and a
# member of a collection of the TENANT'S OWN DECLARATIONS is exactly what must not be reachable
# (found 2026-11-20 by this check firing on the drop-off label types, correctly complaining about
# an incomplete notion of "granted"). Widening what counts as granted does not blunt the check —
# the control below shows a carve-out of something genuinely ungranted is still caught.
def _granted_prefixes():
    return [g for s in RS.SURFACES for key in ("api", "api_read") for g in s[key]]


def _vacuous(entries):
    """Which carve-out entries sit below NO granted prefix at all, for any method."""
    out = []
    for e in entries:
        base = e.split("/**")[0].rstrip("/")
        if not any(g.rstrip("/") == base or base.startswith(g.rstrip("/") + "/")
                   for g in _granted_prefixes()):
            out.append(e)
    return out


check("B2g every carved entry really is below a granted prefix — for ANY method, reads included — "
      "because a carve-out of something never granted is a comment pretending to be a gate",
      not _vacuous(RS.api_exceptions()), _vacuous(RS.api_exceptions()))
check("CONTROL B2g still catches a carve-out of something nobody was ever granted",
      _vacuous(("/api/v1/storeops/payroll/approvals/**",)) == ["/api/v1/storeops/payroll/approvals/**"])
check("B2g2 the drop-off label DECLARATIONS are carved below a READ grant, and the carve bites for "
      "the read as well as the write — a member of that collection is the tenant's own paperwork",
      "/api/v1/storeops/dropoff-label/profiles/**" in RS.api_exceptions()
      and RS.api_allowed("/api/v1/storeops/dropoff-label/profiles", "GET")
      and not RS.api_allowed("/api/v1/storeops/dropoff-label/profiles/abc", "GET"))

check("B24 every declared API prefix resolves to a surface (its own, or the universal set it is "
      "also in — the own-password path is deliberately both)",
      all(RS.surface_for_api(a, "POST") in (s["key"], "universal")
          for s in RS.SURFACES for a in s["api"]),
      [(s["key"], a, RS.surface_for_api(a, "POST")) for s in RS.SURFACES for a in s["api"]
       if RS.surface_for_api(a, "POST") not in (s["key"], "universal")])
check("B25 every declared read-only prefix resolves too",
      all(RS.surface_for_api(a, "GET") in (s["key"], "universal")
          for s in RS.SURFACES for a in s["api_read"]))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nC. THE MIDDLEWARE VERDICT — the real function, over the real role shapes")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
from app.core import tenant_middleware as TM                # noqa: E402

ORG_A, ORG_B = "org-aaaa", "org-bbbb"
SELF_SCOPED = {(ORG_A, "sales_rep"), (ORG_B, "sales_rep")}


def _stub_self(org, role, rbac_on=None):
    return (str(org), str(role)) in SELF_SCOPED


import app.modules.storeops.router as SR                    # noqa: E402
_real_self = SR.role_is_self_scoped
SR.role_is_self_scoped = _stub_self
TM._rep_cache.clear() if hasattr(TM._rep_cache, "clear") else None


def verdict(org, role, path, method="GET"):
    TM._rep_cache = type(TM._rep_cache)(ttl=0.0001, cap=16)   # fresh cache per question
    return TM._rep_surface_refusal(org, role, path, method)


check("C1 a rep is REFUSED the hours-adjustment write",
      bool(verdict(ORG_A, "sales_rep", "/api/v1/storeops/manual-hours", "POST")))
check("C2 the refusal names what they CAN use, not what they cannot",
      "Clock in / out" in (verdict(ORG_A, "sales_rep", "/api/v1/storeops/payroll", "GET") or ""))
check("C3 a rep is SERVED the clock-in write",
      verdict(ORG_A, "sales_rep", "/api/v1/storeops/timeclock/clock-in", "POST") is None)
check("C4 a store manager is refused NOTHING (not an individual contributor)",
      verdict(ORG_A, "store_manager", "/api/v1/storeops/manual-hours", "POST") is None)
check("C5 a market manager is refused nothing",
      verdict(ORG_A, "market_manager", "/api/v1/storeops/payroll", "GET") is None)
check("C6 an admin is refused nothing",
      verdict(ORG_A, "admin", "/api/v1/core/tenants", "GET") is None)
os.environ["REP_CONFINE"] = "0"
check("C7 the break-glass REP_CONFINE=0 lifts it entirely",
      verdict(ORG_A, "sales_rep", "/api/v1/storeops/manual-hours", "POST") is None)
os.environ.pop("REP_CONFINE")
check("C8 …and it is back on by default",
      bool(verdict(ORG_A, "sales_rep", "/api/v1/storeops/manual-hours", "POST")))


def _boom(org, role, rbac_on=None):
    raise RuntimeError("roles table unreadable")


SR.role_is_self_scoped = _boom
check("C9 an UNREADABLE roles table refuses nobody — unreadable is never the restrictive answer",
      verdict(ORG_A, "sales_rep", "/api/v1/storeops/manual-hours", "POST") is None)
SR.role_is_self_scoped = _stub_self
check("C10 a declared path never costs a role read at all",
      verdict(ORG_A, "anything-at-all", "/api/v1/core/me", "GET") is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nE. EVERY TENANT — the declaration is house policy, not a tenant's vocabulary")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
import inspect                                              # noqa: E402

import ast                                                  # noqa: E402

_src = inspect.getsource(RS)
_tree = ast.parse(_src)
# CODE only: the module docstring quotes the owner (who named a tenant) and the comments explain the
# defect, so the RULE TWO / no-org_id assertions are made against what actually EXECUTES.
_code = ast.unparse(ast.Module(body=[n for n in _tree.body
                                     if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                             and isinstance(n.value.value, str))],
                               type_ignores=[])).lower()
check("E1 nothing that executes takes an org_id — one answer for every tenant by construction",
      "org_id" not in _code and "org-" not in _code, _code[:200])
check("E2 no tenant, carrier or product is named in what executes (RULE TWO)",
      not any(w in _code for w in ("cellfonz", "luxelink", "vzone", "logistics", "boost")),
      [w for w in ("cellfonz", "luxelink", "vzone", "logistics", "boost") if w in _code])
check("E3a no public function of the declaration accepts an org at all",
      all("org" not in p for f in ("page_allowed", "api_allowed", "tiles", "pages", "refusal")
          for p in inspect.signature(getattr(RS, f)).parameters))
check("E3 tenant A's rep is confined and tenant B's company role is not, in the same process",
      bool(verdict(ORG_A, "sales_rep", "/api/v1/storeops/payroll", "GET"))
      and verdict(ORG_B, "admin", "/api/v1/storeops/payroll", "GET") is None)
check("E4 the SAME role name is answered per tenant, never once for the house",
      bool(verdict(ORG_B, "sales_rep", "/api/v1/storeops/payroll", "GET")))
SELF_SCOPED.discard((ORG_B, "sales_rep"))
check("E5 …so a tenant whose role is NOT self-scoped is not confined by another tenant's answer",
      verdict(ORG_B, "sales_rep", "/api/v1/storeops/payroll", "GET") is None
      and bool(verdict(ORG_A, "sales_rep", "/api/v1/storeops/payroll", "GET")))
SELF_SCOPED.add((ORG_B, "sales_rep"))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nF. NO LOCKOUT (index §22)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
for p in ("/api/v1/core/me", "/api/v1/core/bootstrap", "/api/v1/core/auth-config",
          "/api/v1/core/my-tenants", "/api/v1/commcalc/nav-config", "/api/v1/core/me/2fa/start"):
    check(f"F- {p} can never be refused", RS.api_allowed(p, "POST") or RS.api_allowed(p, "GET"))
check("F7 changing their own password is a declared page", RS.page_allowed("/account/password"))
check("F8 a confined viewer is always handed the landing to go to",
      (RS.viewer_payload(True, "self") or {}).get("landing") == RS.LANDING_PAGE)
check("F9 a store manager is landed but NOT handed a page allowlist (they are not confined)",
      (RS.viewer_payload(False, "store") or {}) == {"confined": False, "landing": RS.LANDING_PAGE,
                                                    "tiles": [dict(t) for t in RS.tiles()]})
check("F10 a market/company role is handed nothing at all — byte-identical to before",
      RS.viewer_payload(False, "all") is None and RS.viewer_payload(False, "market") is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
print("\nD. THE FOUR WRITES THAT ASKED NOTHING — the real handlers over the double")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
UID_REP, UID_MGR = "uid-rep", "uid-mgr"
REP_EID, MATE_EID, FOREIGN_EID = "E100", "E101", "E999"
fake = FakeSupabase()
fake.seed("storeops.app_config", [{"id": 1, "rbac_enabled": True}])
fake.seed("storeops.app_users", [
    {"id": "au1", "org_id": ORG_A, "auth_id": UID_REP, "role": "sales_rep", "employee_id": REP_EID},
    {"id": "au2", "org_id": ORG_A, "auth_id": UID_MGR, "role": "store_manager", "employee_id": "E200"}])
fake.seed("storeops.employees", [
    {"id": "1", "org_id": ORG_A, "employee_id": REP_EID, "name": "A Rep"},
    {"id": "2", "org_id": ORG_A, "employee_id": MATE_EID, "name": "A Teammate"},
    {"id": "3", "org_id": ORG_A, "employee_id": FOREIGN_EID, "name": "Another Store"}])
fake.seed("storeops.manual_hours", [{"id": "mh1", "org_id": ORG_A, "employee_id": MATE_EID,
                                     "work_date": "2026-11-03", "hours": 2.0, "reason": "forgot"}])

SR.sb = lambda: fake.schema("storeops")
SR.get_supabase = lambda: fake
# WHOSE PEOPLE THESE ARE is `schedule_emp_ids` — the §14w one home. Stubbed here at that ONE seam
# (not re-implemented) so the proof exercises the real handlers and the real `require_person_write`.
_PEOPLE = {UID_REP: {REP_EID}, UID_MGR: {"E200", REP_EID, MATE_EID}}
_who = {"auth": UID_REP}
SR.schedule_emp_ids = lambda authorization="", org_id=ORG_A, **kw: _PEOPLE.get(authorization)
SR._caller_app_user = lambda authorization="", org_id=ORG_A: next(
    (r for r in fake.schema("storeops").table("app_users").select("*").execute().data
     if r["auth_id"] == authorization and r["org_id"] == org_id), {})
SR.role_is_self_scoped = lambda org, role, rbac_on=None: (str(org), str(role).strip()) in SELF_SCOPED
SR._canonical_shift_employee_id = lambda org, eid: str(eid)


def refused(fn, *a, **kw):
    from fastapi import HTTPException
    try:
        fn(*a, **kw)
        return None
    except HTTPException as e:
        return f"{e.status_code}: {e.detail}"


body = SR.ManualHoursIn(employee_id=MATE_EID, hours=3, reason="x", work_date="2026-11-04")
r = refused(SR.add_manual_hours, body, authorization=UID_REP, org_id=ORG_A)
check("D1 THE OWNER'S REPORT: a rep cannot add an hours adjustment at all", r and r.startswith("403"), r)
check("D2 …and the refusal says it is a manager action", "manager action" in (r or ""))
r = refused(SR.add_manual_hours, SR.ManualHoursIn(employee_id=REP_EID, hours=3, reason="x"),
            authorization=UID_REP, org_id=ORG_A)
check("D3 …not even for themselves", r and r.startswith("403"), r)
r = refused(SR.add_manual_hours, SR.ManualHoursIn(employee_id=MATE_EID, hours=3, reason="x",
                                                 work_date="2026-11-04"),
            authorization=UID_MGR, org_id=ORG_A)
check("D4 the manager whose team they are on CAN", r is None, r)
r = refused(SR.add_manual_hours, SR.ManualHoursIn(employee_id=FOREIGN_EID, hours=3, reason="x"),
            authorization=UID_MGR, org_id=ORG_A)
check("D5 …and cannot for someone who is not on their team", r and r.startswith("403"), r)
check("D6 a manual-hours row did land for the allowed write",
      any(x["employee_id"] == MATE_EID and x.get("hours") == 3.0
          for x in fake.schema("storeops").table("manual_hours").select("*").execute().data))
r = refused(SR.delete_manual_hours, "mh1", authorization=UID_REP, org_id=ORG_A)
check("D7 a rep cannot DELETE an hours adjustment either", r and r.startswith("403"), r)
check("D8 …and the row is still there", any(x["id"] == "mh1" for x in
      fake.schema("storeops").table("manual_hours").select("*").execute().data))
r = refused(SR.delete_manual_hours, "mh1", authorization=UID_MGR, org_id=ORG_A)
check("D9 their manager can", r is None, r)

r = refused(SR.create_time_off, {"employee_id": REP_EID, "start_date": "2026-11-10",
                                 "end_date": "2026-11-11"}, authorization=UID_REP, org_id=ORG_A)
check("D10 a rep CAN file their own time off", r is None, r)
r = refused(SR.create_time_off, {"employee_id": FOREIGN_EID, "start_date": "2026-11-10",
                                 "end_date": "2026-11-11"}, authorization=UID_REP, org_id=ORG_A)
check("D11 …and cannot file it in a stranger's name (reported 2026-11-01)", r and r.startswith("403"), r)
r = refused(SR.create_time_off, {"employee_id": MATE_EID, "start_date": "2026-11-10",
                                 "end_date": "2026-11-11"}, authorization=UID_MGR, org_id=ORG_A)
check("D12 THE OWNER'S RULE: a manager CAN file time off for their own employee", r is None, r)
r = refused(SR.create_shift_swap, {"requester_id": FOREIGN_EID}, authorization=UID_REP, org_id=ORG_A)
check("D13 a swap cannot be raised in a stranger's name", r and r.startswith("403"), r)
r = refused(SR.create_shift_swap, {"requester_id": REP_EID}, authorization=UID_REP, org_id=ORG_A)
check("D14 …and an employee can raise their own", r is None, r)

import app.modules.hr.router as HR                          # noqa: E402

HR._so = lambda: fake.schema("storeops")
HR.onboarding_template = lambda org_id=ORG_A: {"ready": False, "categories": []}
r = refused(HR.onboarding_for_employee, FOREIGN_EID, authorization=UID_REP, org_id=ORG_A)
check("D15 THE OWNER'S REPORT: a rep cannot open anyone else's onboarding paperwork",
      r and r.startswith("403"), r)
r = refused(HR.onboarding_for_employee, REP_EID, authorization=UID_REP, org_id=ORG_A)
check("D16 …and can open their own", r is None, r)
r = refused(HR.onboarding_for_employee, MATE_EID, authorization=UID_MGR, org_id=ORG_A)
check("D17 …and their manager can open theirs", r is None, r)
r = refused(HR.onboarding_set_profile, FOREIGN_EID, HR.OnboardingSetProfileIn(work_state="NY"),
            authorization=UID_REP, org_id=ORG_A)
check("D18 …nor CHANGE a stranger's paperwork", r and r.startswith("403"), r)
r = refused(SR.create_time_off, {"employee_id": REP_EID, "start_date": "2026-11-10",
                                 "end_date": "2026-11-11"}, authorization="", org_id=ORG_A)
check("D19 an in-process / tokenless caller is UNRESTRICTED, exactly as before (a sweep, a cron)",
      r is None, r)

SR.role_is_self_scoped = _real_self
print("\n" + "=" * 100)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
