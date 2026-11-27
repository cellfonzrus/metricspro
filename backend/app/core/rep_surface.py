"""THE INDIVIDUAL CONTRIBUTOR'S REACH — one declaration, read by the API and by the menu. PURE.

OWNER (2026-11-04, verbatim): *"All sales reps should log in to the clock in screen, on ny logistics
Manan was able to go past it and also see the adjustment hours, this should not be allowed for any
sales reps, fix the permissions accordingly. Sales reps should have Clock in clock out / Their own
commissions / Their own schedule / Their own action plan / Help desk. Create a tiled front end for
their clock in specifically so when the sales reps or the store manager logs in they land on that
landing page … this is universal across the board."*

THE INSTANCE, measured 2026-11-04 by RUNNING the real `frontend/src/lib/rbac.ts` over the real role
shapes: `canAccessPath`'s `scope === 'self'` branch admitted any page whose URL began with a menu row
the rep could see —

    for (const g of NAV) for (const it of g.items)
      if ((path === it.href || path.startsWith(it.href + '/')) && canSeeItem(perms, it)) return true

— so the `/storeops` "Workforce Dashboard" row admitted the WHOLE `/storeops/**` subtree, 33 pages,
including `/storeops/timeclock` (Hours Adjustment: add/delete manual hours for any employee) and
`/storeops/payroll/approvals` (Hours Approval). The child page's own `scopes: ['all','market']` was
never consulted because the parent matched first. A rep with NO workforce module reached
`/helpdesk/settings` and `/reports` by the same prefix. The branch was added to cure "the tab is
there but clicking it does nothing" and was written as widening-only on purpose; the PREFIX is the
defect, not the intent.

THE CLASS (CLAUDE.md "A fix is a DESIGN fix"): **whether a viewer may use a surface was decided in
the browser, and the API had no audience of its own.** `POST /storeops/manual-hours` and its DELETE
took no `authorization` at all — any signed-in person in the org could write or delete payroll hours
for any employee — so the menu was the only gate on payroll. Three sibling routes had the same shape
and were already REPORTED unfixed on 2026-11-01 (`POST /storeops/time-off`, `POST
/storeops/shift-swaps`, `GET /hr/onboarding/employee/{id}`); they are fixed here, in the same pass,
because they are the same fact wearing a different hat.

SO: ONE declaration, here, of every surface an individual contributor may reach — its pages AND the
API paths those pages call — dereferenced by

  · the SERVER: `app.core.tenant_middleware` refuses an undeclared API path to a self-scoped caller
    before any handler runs (break-glass `REP_CONFINE=0`);
  · the MENU: `core.router._me_payload` serves `permissions.rep_surface` and `lib/rbac.ts` reads THIS
    list — `SELF_ALLOWED` is deleted, the prefix branch is gone, and the two can no longer disagree.

WHO IS CONFINED is NOT decided here. It is the one answer the platform already has —
`storeops.router.role_is_self_scoped` (scope 'self' OR a declared `people_visibility = 'self'`,
RBAC on) — the same answer the payout surfaces and `/me` ask (index §6j, §14w). A store manager is
not an individual contributor, so a store manager is NOT confined and keeps every manager feature;
only their LANDING changes (`landing_applies`).

RULE TWO: no tenant, carrier or product is named here. Every page is a NAV href
(`frontend/src/lib/rbac.ts` — the one screen→href map) and the lock pins that each one exists there.

EVERY TENANT (owner 2026-10-30): this declaration is the HOUSE POLICY of what an employee may reach,
not a tenant's vocabulary, so a house default is correct here — and it is identical for every org, by
construction, because no org_id reaches this module at all.
"""

# ── 1. THE SURFACES ───────────────────────────────────────────────────────────────────────────────
# One entry per thing an individual contributor may do. `pages` are NAV hrefs the route guard admits;
# `api` are API path prefixes allowed for every method; `api_read` are allowed for GET/HEAD only (a
# lookup a page reads but must never write); `api_except` carves a MANAGER route out of a granted
# prefix, in the same two-form grammar as `page_except` and asked FIRST, exactly as on the page side.
# `tile` marks the ones the landing page offers as tiles, in this order. `what` is the plain-words
# reason, shown in a refusal and in the index.
#
# WHY api_except EXISTS (found by the security pass on this very change, 2026-11-04). The first draft
# granted whole prefixes for every method, so `/api/v1/storeops/time-off` — granted because an
# employee ASKS for time off with a POST — also admitted `PATCH /time-off/{id}`, the manager's
# APPROVE, whose handler had no authority check of its own: a rep could approve their own leave. The
# same shape admitted `/helpdesk/config/**` (the settings PAGE was carved out and its whole API was
# not) and every closing config write. The class: A GRANT MADE FOR ONE METHOD ON A COLLECTION IS NOT
# A GRANT ON ITS MEMBERS. Both halves are fixed — the declaration no longer offers those routes, AND
# the three handlers behind them now dereference an authority check, so the refusal does not depend
# on this file being the only gate.
SURFACES = (
    {
        "key": "clock", "tile": True, "label": "Clock in / out", "icon": "⏱",
        # THE TILE'S DESTINATION, declared (see TILE DESTINATIONS below). The punch lives at the
        # kiosk, NOT at this surface's first grant — `/my` is the landing the tile sits ON.
        "tile_page": "/portal",
        "pages": ("/my/**", "/portal/**"),
        "api": ("/api/v1/storeops/timeclock",),
        "api_read": (),
        # Every route carved here is a manager/admin one the kiosk never calls, and each already
        # dereferences `_require_manager`; the carve-out means the DECLARATION does not offer them
        # either, so neither gate is the only one. `/override` is NOT carved: the kiosk calls it for
        # a manager standing at the screen, and it is manager-gated.
        "api_except": ("/api/v1/storeops/timeclock/attendance-config",
                       "/api/v1/storeops/timeclock/geo-config",
                       "/api/v1/storeops/timeclock/face-config",
                       "/api/v1/storeops/timeclock/face-retention/**",
                       "/api/v1/storeops/timeclock/lunch-config",
                       "/api/v1/storeops/timeclock/permissions/**",
                       "/api/v1/storeops/timeclock/force-clockout/**"),
        "what": "punch in and out, with the selfie / GPS evidence the tenant has switched on",
    },
    {
        "key": "commissions", "tile": True, "label": "My commissions", "icon": "💵",
        "tile_page": "/employee",
        "pages": ("/employee/**",),
        "api": (),
        "api_read": ("/api/v1/core/employee-dashboard", "/api/v1/core/portal-reports",
                     "/api/v1/storeops/my-chargebacks", "/api/v1/commcalc/coaching",
                     "/api/v1/storeops/google-reviews/my"),
        "what": "their own earnings, chargebacks and coaching for a month they choose",
    },
    {
        "key": "schedule", "tile": True, "label": "My schedule", "icon": "📅",
        # OWNER 2026-11-27: *"under employee login it does not show the employee thier own schdule"*
        # … *"it shows your next shift at the bottom on the main page it shoudl also show in schedule
        # whic his made for teh next 2 weeks or one week"*.
        #
        # THE TILE OPENS `/my/schedule`, the employee's own read of the published schedule. It used
        # to open `/storeops/timeoff` — the time-off REQUEST FORM — because the tile's link was taken
        # from `pages[0]`, and guessing a destination from the first grant is exactly the defect
        # §CORE-33.3 fixed in this same PR: the destination is DECLARED here now, so the order of
        # `pages` carries no meaning at all. The two REQUEST pages stay declared behind it, because
        # asking for time off and asking for a swap are the other half of this surface, and
        # `/my/schedule` links to both (pinned by rep-surface-nav-proof.mjs §E).
        #
        # The manager's builder (`/storeops/schedule` — the whole-roster grid, the shift writes, the
        # hours-trend report) is still NOT here and must not be: this surface gained a READ of the
        # employee's own rows, not a grant of anyone's schedule.
        "tile_page": "/my/schedule",
        "pages": ("/my/schedule", "/storeops/timeoff", "/storeops/swaps"),
        # The shift WRITES are a manager surface and are not here; `/storeops/shifts` is read-only to
        # an individual contributor, and the rows it returns are already narrowed to the people they
        # may see by `schedule_emp_ids` (§14w). Time off and shift swaps are POSTs on purpose — they
        # are how an employee ASKS — and each route now checks whose name the request carries.
        "api": ("/api/v1/storeops/time-off", "/api/v1/storeops/shift-swaps",
                "/api/v1/storeops/shift-extensions"),
        # The three grants above are the COLLECTION — the POST that ASKS. Everything BELOW each one
        # is the manager's answer (`PATCH /time-off/{id}`, `PATCH /shift-swaps/{id}`, the extension
        # decision) or an org-wide cleanup, and is carved out. This is the hole the security pass
        # found on this change: a rep could PATCH their own request to `approved`.
        "api_except": ("/api/v1/storeops/time-off/**", "/api/v1/storeops/shift-swaps/**",
                       "/api/v1/storeops/shift-extensions/**"),
        # `/api/v1/storeops/schedule` was ALSO granted here, and `_matches` is a PREFIX match, so it
        # handed every confined employee `GET /storeops/schedule/hours-trend` — the manager's
        # week-over-week scheduled-hours REPORT across their whole span. No cross-person leak (that
        # handler applies the §14w gate too, so a rep got an aggregate of their own hours), but a
        # report is a manager's and nothing an employee opens calls it: the only route under
        # `/schedule/` is hours-trend, and `/my/schedule`, `/storeops/timeoff` and `/storeops/swaps`
        # call none of it. REMOVED rather than carved out, because this module's own rule is that
        # carving something never granted is "a comment pretending to be a gate" — default deny is
        # the real refusal. Found by the security pass on 2026-11-27 (§CORE-33.2).
        # `timeoff-conflict-mode` (GET) is the org's own policy — may a shift be scheduled over
        # approved time off, warn or block. Its handler says "any signed-in caller may read"; the
        # Time off page asks it on mount to decide whether to confirm() before posting, and a rep
        # was refused it. READ only: the PUT beside it is `_require_manager`, and api_read cannot
        # carry a write (§CORE-33.6).
        "api_read": ("/api/v1/storeops/shifts", "/api/v1/storeops/timeoff-conflict-mode"),
        "what": "their own shifts, and asking for time off, a swap or an extension",
    },
    {
        "key": "action_plan", "tile": True, "label": "My action plan", "icon": "🎯",
        "tile_page": "/commcalc/targets/action-plan",
        "pages": ("/commcalc/targets/action-plan",),
        "api": (),
        "api_read": ("/api/v1/commcalc/targets",),
        "what": "the focus areas the targets engine derives for them, already self-scoped by "
                "scope_keyset (§59.7)",
    },
    {
        # OWNER 2026-11-27: *"my targets are shown on the portal and not on the employee dashboad, a
        # tile for that shoudl be there too"*. Target-vs-achieved was visible only as part of the
        # kiosk's My Dashboard tab (`<EmployeeWidgets repTargets=…>`), so the standalone screen that
        # owns the question was unreachable without a menu. §CORE-33.4.
        #
        # NO API GRANT IS ADDED. The page reads `/commcalc/targets/{period}/summary|calendar|
        # action-plan`, every one of which the action-plan surface above already grants read on —
        # a rep can call them today. Only the PAGE was missing, and it is granted EXACTLY: the rest
        # of `/commcalc/targets` (settings, report cards, the rep map, the accessory plan) stays
        # refused. The screen is already in `rbac.SELF_OWN_STORE_SURFACES`, i.e. it resolves a
        # scope-'self' viewer to their OWN store and filters per-rep rows to their own employee id,
        # so this grants a view that was designed for exactly this viewer.
        # THE CARD THAT COULD NOT LOAD (owner 2026-11-27, §CORE-33.6). The page carries an Inventory
        # aging card, and `GET /commcalc/my-shelf` was not granted — so the one screen this tile
        # exists to open told the rep "This is not part of your access" in a red box. The read is
        # THEIR OWN shelf by construction: the handler resolves the devices through
        # `storeops.scope_keyset(authorization)`, the evidence-based span of the stores this login
        # actually works at, and it was built (owner 2026-10-26) for exactly this viewer — *"it
        # should be on every sales reps daSHBOARDS WHEN TEHY LOG IN"*. GET only.
        "key": "targets", "tile": True, "label": "My targets", "icon": "📈",
        "tile_page": "/commcalc/targets/my",
        "pages": ("/commcalc/targets/my",),
        "api": (),
        "api_read": ("/api/v1/commcalc/my-shelf",),
        "what": "their own target against what they have achieved this month, for their own store",
    },
    {
        "key": "helpdesk", "tile": True, "label": "Help desk", "icon": "🎫",
        "tile_page": "/helpdesk",
        "pages": ("/helpdesk/**",),
        # The ticket list, a ticket and raising one are an employee's; the two manager screens
        # inside the same subtree are NOT, and the carve-out is stated where the grant is made.
        "page_except": ("/helpdesk/settings", "/helpdesk/dashboard"),
        # GRANTED BY WHAT THE SCREENS CALL, not by the module with holes punched in it (§CORE-33.6).
        # This was `"/api/v1/helpdesk"` — the WHOLE module, every method — with `/config/**` and
        # `/support/**` carved back out. Two problems with that shape, and the second one was a live
        # defect: (a) every helpdesk route shipped from now on is granted to every employee until
        # somebody remembers to carve it, and (b) the carve-out is absolute and asked FIRST, so
        # `/config/bootstrap` — the ticket form's own vocabulary (categories, priorities, statuses)
        # which all three employee screens fetch on mount — was refused. Raising a ticket is the
        # feature; its drop-downs are part of it.
        #
        # So: the ticket itself (list, one ticket, its comments, attachments, escalate — the prefix
        # match covers the members) for every method, and the form vocabulary READ-ONLY. The
        # settings writes, the agent console and the manager dashboard stats are simply never
        # granted, which is a real refusal rather than a carve-out that reads like one.
        "api": ("/api/v1/helpdesk/tickets",),
        "api_read": ("/api/v1/helpdesk/config/bootstrap",),
        "what": "raising and following their own tickets",
    },
    {
        "key": "dropoff", "tile": True, "label": "Barcode rescue", "icon": "🏷",
        "tile_page": "/storeops/dropoff-barcode",
        # OWNER 2026-11-20: *"Grant the sales rep this and also give step by step instructions on
        # the page when the sales rep is ready to take a picture of the items."* The person who
        # takes a return over the counter and finds the carrier barcode unscannable IS the rep; a
        # rescue only a manager can run is a package that stays on the shelf. A sixth tile, after
        # the owner's original five, because a confined employee has no menu row to find it by.
        "pages": ("/storeops/dropoff-barcode",),
        # The rescue itself: read a photograph, and draw a barcode for a reference a PERSON has
        # confirmed. Each is an EXACT route with nothing below it in the router, and each writes
        # only its own audit row — whose whole purpose is to say which employee confirmed which
        # reference, so a rep writing one is the feature working, not a hole in it.
        "api": ("/api/v1/storeops/dropoff-label/read",
                "/api/v1/storeops/dropoff-label/read-batch",
                "/api/v1/storeops/dropoff-label/barcode",
                "/api/v1/storeops/dropoff-label/barcodes",
                ),
        # READ-ONLY: the tenant's declared label types, which the screen needs to match printed
        # text, and the renderer a print dialog reloads (it reads and writes nothing).
        "api_read": ("/api/v1/storeops/dropoff-label/profiles",
                     "/api/v1/storeops/dropoff-label/barcode.svg"),
        # `profiles` is a COLLECTION, and this module's own rule is that a grant on a collection is
        # not a grant on its members: `DELETE /profiles/{id}` retires one of the tenant's
        # declarations, which is an admin act. The read-only grant already refuses it by METHOD;
        # carved anyway so the refusal does not rest on that single fact, and so a later widening
        # of this prefix cannot quietly carry it. Both handlers behind it ALSO ask their own
        # authority (`dropoff_label_api._require_manager_act`), so neither gate is the only one.
        "api_except": ("/api/v1/storeops/dropoff-label/profiles/**",),
        # NOT GRANTED, and so refused by default deny rather than by a carve-out: `/scans`, the
        # whole organisation's rescue record across every store. A rep rescues the package in front
        # of them; reading what every other store took in is a report, and reports are a manager's.
        "what": "rescuing a drop-off package whose carrier barcode will not scan — photograph the "
                "label, confirm the reference, print a code that scans",
    },
    # ── Surfaces an employee already has that the owner did not list, kept DELIBERATELY and named
    #    here so the keeping is a decision on the record rather than an omission. Each is reported to
    #    the owner with the PR; removing one is a one-line edit of this tuple and nothing else.
    {
        "key": "own_paperwork", "tile": False, "label": "My paperwork", "icon": "📄",
        "pages": (),
        "api": ("/api/v1/hr/onboarding/me",),
        "api_read": (),
        "what": "their OWN onboarding record only — the /me family resolves the person from the "
                "token, so there is no employee id to pass (the by-id route is manager-gated)",
    },
    {
        "key": "closing", "tile": True, "label": "Daily closing", "icon": "🧾",
        # OWNER 2026-11-27: *"daily closing tile is missing from their landing page but on teh portal
        # is seen"*. The closing form was reachable only as a panel inside the `/portal` kiosk, so a
        # rep who had already clocked in had to go back to the clock screen to file the close — and a
        # confined employee has no menu to find it by. It gets a tile, and the ONE page that hosts
        # the same shared `<ClosingSubmitForm>`.
        #
        # EXACT, not a subtree, and that is the whole safety of it: `/closing` is a 30-screen
        # subsystem (recon, deposits, variances, every store's cash position, the tender config). The
        # grammar's exact form admits that page and NOTHING else, so nothing below or beside it moves.
        # No API grant changes either — the page makes exactly the calls already granted below, which
        # the kiosk panel has been making all along.
        "tile_page": "/closing/submit",
        "pages": ("/closing/submit",),
        # Named routes, not the `/api/v1/closing` prefix the first draft granted: that prefix also
        # admitted every tender/cash/deposit/sweep CONFIG write and the chargeback decisions. These
        # are exactly the calls `components/ClosingSubmitForm.tsx` makes, nothing more.
        "api": ("/api/v1/closing/row", "/api/v1/closing/envelope-photo",
                "/api/v1/closing/metric-lines"),
        # …and `/row/{id}` PATCH/DELETE is the manager's edit of a filed row, not the kiosk's submit.
        "api_except": ("/api/v1/closing/row/**",),
        "api_read": ("/api/v1/closing/days", "/api/v1/closing/resume", "/api/v1/closing/stores",
                     "/api/v1/closing/tender-config", "/api/v1/closing/count-config",
                     "/api/v1/closing/metric-catalog", "/api/v1/closing/metric-lines",
                     "/api/v1/closing/expense-categories", "/api/v1/closing/envelope-config",
                     "/api/v1/closing/entry-quality/me"),
        "what": "submitting the store's daily closing from the kiosk, which reps already do "
                "(ClosingSubmitForm is on the clock-in screen) — removing it would stop closings",
    },
    {
        # Tiled 2026-11-27 with the targets report above, for the same reason: it was GRANTED and
        # unreachable. A confined employee has no menu row, so a granted page with no tile can be
        # opened only by typing its address. No permission changes — the page and its API were
        # already granted; the tile is how a person finds them.
        "key": "chat", "tile": True, "label": "Chat", "icon": "💬",
        "tile_page": "/chat",
        "pages": ("/chat/**",),
        "api": ("/api/v1/chat",),
        "api_except": ("/api/v1/chat/admin/**",),
        "api_read": (),
        "what": "in-app messaging, which is how an employee reaches their manager",
    },
    {
        "key": "training", "tile": True, "label": "Training", "icon": "🎓",
        "tile_page": "/training",
        "pages": ("/training/**",),
        "api": ("/api/v1/core/training",),
        "api_read": (),
        "what": "the training assignments the platform gives them",
    },
    {
        # ★ NO LOCKOUT ★ — `never_off` keeps this one surface out of the admin's switch (see
        # `surface_switched_off`). A role whose password page is switched off cannot complete a
        # forced reset, so it cannot sign in to have the switch put back.
        "key": "password", "tile": True, "label": "My password", "icon": "🔑", "never_off": True,
        "tile_page": "/account/password",
        "pages": ("/account/password/**",),
        "api": ("/api/v1/core/me",),
        "api_read": (),
        "what": "changing their own password — never refusable, or a forced reset would strand them",
    },
)

# ── 2. UNIVERSAL — what ANY signed-in person's browser must reach whatever their role ─────────────
# The identity/shell calls every page makes before it can render anything. Refusing one of these
# would not confine a rep, it would lock them out, so they are declared apart from the surfaces and
# are never conditional. ★ NO LOCKOUT ★ (index §22).
UNIVERSAL_API = (
    "/api/v1/core/me",                      # identity, 2FA, own password
    "/api/v1/core/bootstrap",
    "/api/v1/core/auth-config",
    "/api/v1/core/my-tenants",
    "/api/v1/core/pending-connections",
    "/api/v1/core/connect-tenant",
    "/api/v1/core/disable-and-switch",
    "/api/v1/core/failures",                # the browser's own error report
    "/api/v1/health",
    # THE EXPORT GOVERNANCE LOG (Security Controls item 7). Saving a sheet as a PDF is an export, and
    # an export this platform does not record is the gap that control exists to close. `governExport`
    # fails OPEN, so without this grant the file still saves and simply goes UNLOGGED — the worst of
    # the two outcomes.
    #
    # DECLARED HERE, NOT ON A SURFACE, since 2026-11-27. It lived inside the `dropoff` surface's own
    # `api` tuple, beside a comment in this file promising that removing a surface is "a one-line
    # edit of this tuple and nothing else" — so retiring the barcode-rescue tile would have made
    # every OTHER granted page's exports save unlogged, silently. Export governance is not one
    # surface's business: it belongs to every granted page that carries an Export button, which is
    # what UNIVERSAL_API means. Raised by the security pass on PR #755; no viewer gains or loses
    # reach, because `api_allowed` already unions every surface for every caller.
    "/api/v1/core/export-event",
)
UNIVERSAL_API_READ = (
    "/api/v1/commcalc/nav-config",           # the menu itself
    "/api/v1/commcalc/setup-documents/gate",  # the setup confinement the shell asks about
    "/api/v1/storeops/payroll/clock-gate",    # the clock-variance confinement the shell asks about
    "/api/v1/core/tenant-settings",           # the tenant's work-week / pay period
    "/api/v1/storeops/stores",                # the store picker every surface above uses
    "/api/v1/storeops/employees/visible",     # already narrowed server-side to who they may see
    "/api/v1/storeops/org/my-span",
    "/api/v1/storeops/org/my-team",
)

# ── 3. THE LANDING ────────────────────────────────────────────────────────────────────────────────
# The page a confined employee AND a store manager land on (owner: "when the sales reps or the store
# manager logs in they land on that landing page"). A store manager is not confined — only their
# landing changes — so the two facts are deliberately separate names.
LANDING_PAGE = "/my"
# WHO LANDS THERE is derived from the role's own CONFIG, never from a role NAME, because a role name
# is a tenant's word and this must hold for every tenant: an individual contributor (the reps), and
# any role whose scope is a single store (the store managers — "the store manager logs in they land
# on that landing page"). A market/company role keeps the landing its role row declares.
LANDING_SCOPES = ("store",)


def landing_applies(is_individual_contributor, scope=None):
    """Does the tiled landing replace this viewer's configured home?"""
    return bool(is_individual_contributor) or str(scope or "") in LANDING_SCOPES

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _norm(path):
    p = str(path or "").split("?")[0].rstrip("/")
    return p or "/"


def _matches(path, prefixes):
    p = _norm(path)
    for pre in prefixes:
        pre = _norm(pre)
        if p == pre or p.startswith(pre + "/"):
            return True
    return False


# ── TILE DESTINATIONS ─────────────────────────────────────────────────────────────────────────────
# A `pages` entry is a PERMISSION PATTERN, not an address: `"/employee/**"` says "that page and
# everything below it". A browser cannot navigate to a pattern.
#
# THE DEFECT THIS EXISTS TO KILL (reported 2026-11-27 by the owner, on NY Logistics and then on every
# tenant): `tiles()` published `pages[0]` verbatim as the tile's link, so "My commissions" linked to
# the literal `/employee/**` and "Clock in / out" to `/my/**`. Both are the app's 404. The gate could
# not catch it — `page_allowed("/employee/**")` is TRUE, because the literal `/**` reads as a child
# of `/employee` and the subtree grant admits its children. A default-deny gate answers a
# wrong-SHAPED question with a confident yes.
#
# TWO facts were being guessed from one, and both are now declared:
#   1. the ADDRESS of a pattern — `tile_page()` below is its one home, dereferenced by `tiles()` AND
#      by the locks, so no caller carries its own `[:-3]`;
#   2. WHICH page a tile opens — never "the first grant". The clock's first grant is `/my`, the
#      landing the tile sits on; the punch is at `/portal`. Every tiled surface states its own
#      `tile_page`, and there is NO fallback to `pages[0]`: a tile that has not said where it goes
#      must not be able to answer.
def tile_page(entry):
    """The navigable address of a page-grammar entry. `"/helpdesk/**"` → `"/helpdesk"` (a page
    subtree includes its base, see `page_allowed`), an exact entry is already its own address."""
    e = str(entry or "")
    return _norm(e[: -len(SUBTREE)]) if e.endswith(SUBTREE) else _norm(e)


def tiles(role_perms=None):
    """The landing page's tiles, in declaration order. Label, icon, page and plain-words `what`.
    `page` is an ADDRESS a browser can open, never a permission pattern.

    `role_perms` is the role's own permissions row. A tile whose destination that role has switched
    off is not published — the same subtraction the page list and the API gate make, from the same
    one answer, so the landing page cannot offer a door the server has closed."""
    return tuple({"key": s["key"], "label": s["label"], "icon": s["icon"],
                  "page": tile_page(s["tile_page"]) if s.get("tile_page") else None,
                  "what": s["what"]}
                 for s in SURFACES
                 if s.get("tile") and (s.get("never_off")
                                       or not switched_off(role_perms, tile_page(s.get("tile_page") or ""))))


# ── THE ADMIN'S OWN SWITCH — A GRANT HERE IS A MAXIMUM, NEVER A MANDATE ──────────────────────────
# OWNER 2026-11-27: *"i disaables my targets from ny logistics sales reps but it still shows up"*.
# He had turned My Targets off for that role on the Roles screen, and the tile stayed, the page
# opened and the API answered.
#
# THE CLASS, stated so nobody re-derives it. This module was built to stop a confined employee
# reaching a manager screen, so it answers ONE question — "is this page/route part of an individual
# contributor's surface?" — and it answered it as the WHOLE truth: `canSeeItem` hands a confined
# viewer straight to `repPageAllowed` and the API gate asks `api_allowed` alone, so the per-function
# switches an admin ticks for that role were never consulted for the one kind of viewer whose menu
# is built from this file. A tenant's own administrator could not take away what this declaration
# gave. A ceiling read as a floor.
#
# DELIBERATELY ONE-DIRECTIONAL. A switch here can only SUBTRACT. An admin ticking a page ON for a
# confined role does not widen their reach past this declaration — widening by configuration is
# exactly what §CORE-33 exists to prevent, and the API gate would refuse what the menu offered. So
# `False` closes a surface and nothing else opens one.
#
# WHAT A SWITCH IS. `roles.permissions["pages"]["/commcalc/targets/my"] is False` — the per-function
# override the Roles screen already writes, keyed by the page's NAV href, which is the same key
# `canSeeItem` reads for every other viewer. No new storage, no new vocabulary, no migration.
ROLE_PAGE_SWITCH_KEY = "pages"


def switched_off(role_perms, address):
    """Has this role's own per-function switch turned this page address OFF? Only an explicit
    `False` counts: an absent key and a `True` both leave the declaration exactly as it is."""
    sw = (role_perms or {}).get(ROLE_PAGE_SWITCH_KEY)
    if not isinstance(sw, dict):
        return False
    return sw.get(_norm(address)) is False


def surface_switched_off(surface, role_perms):
    """Is this whole surface off for this role? Only when EVERY page it declares is switched off —
    denying the time-off REQUEST FORM must not also take away the schedule it sits beside. A
    surface marked `never_off` is never off (★ NO LOCKOUT ★: a forced password change must not be
    switchable, or the role it is switched off for cannot sign in and fix anything)."""
    if not role_perms or surface.get("never_off"):
        return False
    addrs = [tile_page(p) for p in (surface.get("pages") or ())]
    if not addrs:
        return False
    return all(switched_off(role_perms, a) for a in addrs)


# ── PAGE GRAMMAR ──────────────────────────────────────────────────────────────────────────────────
# A `pages` entry is one of exactly two things, and nothing is implied:
#   "/storeops/timeoff"   EXACT — that page and no other. A page's NEIGHBOURS are never implied.
#   "/helpdesk/**"        that page AND everything below it (a ticket's own page, a flowchart).
# `page_except` carves a manager screen out of a subtree, stated beside the grant that would
# otherwise admit it. There is no third form and no prefix match on a page's PARENT — the parent
# prefix is the defect this module exists to kill.
SUBTREE = "/**"


def pages(role_perms=None):
    """Every page entry a confined employee may open, deduped, in declaration order — in the grammar
    above. This is the list `/me` hands the browser; `lib/rbac.ts` holds no copy of it.

    With `role_perms`, a page this role has switched off is dropped — one page at a time, so an
    admin can close the time-off form without closing the schedule beside it."""
    out, seen = [], set()
    for s in SURFACES:
        for p in s["pages"]:
            if s.get("never_off") or not switched_off(role_perms, tile_page(p)):
                if p not in seen:
                    seen.add(p)
                    out.append(p)
    return tuple(out)


def page_exceptions():
    """Every manager screen carved out of a declared subtree."""
    out = []
    for s in SURFACES:
        out.extend(s.get("page_except") or ())
    return tuple(out)


def _page_entry_matches(path, entry):
    p, e = _norm(path), str(entry or "")
    if e.endswith(SUBTREE):
        base = _norm(e[: -len(SUBTREE)])
        return p == base or p.startswith(base + "/")
    return p == _norm(e)


def page_allowed(path, role_perms=None):
    """May a confined employee open this PAGE? One of the declared entries must match in the grammar
    above, and no carve-out may. `/my/**` admits `/my/anything`; `/helpdesk/**` does NOT admit
    `/helpdesk/settings`; `/storeops/timeoff` does NOT admit `/storeops/timeclock`. With
    `role_perms`, a page that role has switched off is not among the entries."""
    if any(_page_entry_matches(path, e) for e in page_exceptions()):
        return False
    return any(_page_entry_matches(path, e) for e in pages(role_perms))


def _api_except_matches(path, entry):
    """A carve-out entry against a path. Two forms, and the `/**` one differs from the page side ON
    PURPOSE: `"/api/v1/storeops/time-off/**"` is everything STRICTLY BELOW that collection, not the
    collection itself, because the grant being carved IS the collection — the POST that asks. A page
    subtree includes its base (`/helpdesk/**` is the ticket list too); an API member carve-out must
    not, or carving the approve would also refuse the ask. An exact entry is that path alone."""
    p, e = _norm(path), str(entry or "")
    if e.endswith(SUBTREE):
        return p.startswith(_norm(e[: -len(SUBTREE)]) + "/")
    return p == _norm(e)


def api_exceptions():
    """Every manager route carved out of a granted API prefix, in the grammar above."""
    out = []
    for s in SURFACES:
        out.extend(s.get("api_except") or ())
    return tuple(out)


def api_allowed(path, method="GET", role_perms=None):
    """May a confined employee's browser call this API path with this method? DEFAULT DENY.

    A carve-out is asked FIRST and refuses outright, exactly as `page_allowed` does — a grant made
    for the POST that ASKS must never carry the PATCH that ANSWERS. The carve-out is absolute
    rather than per-surface: a route one surface calls a manager's is not an employee's because a
    second grant happens to overlap it.

    With `role_perms`, a surface whose every page this role has switched off grants nothing: the
    screen is gone, so the calls it makes are not this role's either. The UNIVERSAL calls are never
    subtracted — they are the shell, and refusing one would lock the person out rather than confine
    them (★ NO LOCKOUT ★)."""
    m = (method or "GET").upper()
    if any(_api_except_matches(path, e) for e in api_exceptions()):
        return False
    if _matches(path, UNIVERSAL_API):
        return True
    if m in _READ_METHODS and _matches(path, UNIVERSAL_API_READ):
        return True
    for s in SURFACES:
        if surface_switched_off(s, role_perms):
            continue
        if _matches(path, s["api"]):
            return True
        if m in _READ_METHODS and _matches(path, s["api_read"]):
            return True
    return False


def surface_for_api(path, method="GET", role_perms=None):
    """Which declared surface admits this API path (for a trace / an explanation). None when none
    does, which is exactly when the gate refuses."""
    m = (method or "GET").upper()
    if any(_api_except_matches(path, e) for e in api_exceptions()):
        return None
    if _matches(path, UNIVERSAL_API) or (m in _READ_METHODS and _matches(path, UNIVERSAL_API_READ)):
        return "universal"
    for s in SURFACES:
        if surface_switched_off(s, role_perms):
            continue
        if _matches(path, s["api"]) or (m in _READ_METHODS and _matches(path, s["api_read"])):
            return s["key"]
    return None


def api_switchable(path, method="GET"):
    """Could a ROLE's own switch close this API path? True only when the path is granted by a
    surface that has pages and is not `never_off` — i.e. when the answer depends on the role's
    permissions row and the gate therefore has to read it.

    This exists for the API middleware's hot path, which asks `api_allowed` BEFORE it resolves any
    identity: a universal shell call stays I/O-free, and only a surface call pays for the role read.
    Without it, honouring the switch would have meant an identity resolution on every request."""
    if not api_allowed(path, method):
        return False                      # already refused; nothing left for a switch to close
    key = surface_for_api(path, method)
    if key in (None, "universal"):
        return False
    for s in SURFACES:
        if s["key"] == key:
            return bool(s.get("pages")) and not s.get("never_off")
    return False


def refusal(path, method="GET", role_perms=None):
    """The sentence a refused caller is told. It names what they CAN do rather than what they cannot,
    because the person reading it is an employee who has done nothing wrong."""
    labels = ", ".join(t["label"] for t in tiles(role_perms))
    return (f"This is not part of your access. {method} {_norm(path)} belongs to a manager screen. "
            f"What you can use: {labels}.")


def viewer_payload(is_individual_contributor, scope=None, role_perms=None):
    """What `/me` carries, or None for a viewer neither confined nor landed — so an older browser and
    a market/company role are byte-identical to today. The browser never re-derives either fact:
    `confined` says whether its reach is this list, `landing` says where it drops them."""
    confined = bool(is_individual_contributor)
    if not landing_applies(confined, scope):
        return None
    out = {"confined": confined, "landing": LANDING_PAGE, "tiles": [dict(t) for t in tiles(role_perms)]}
    if confined:
        out["page_except"] = list(page_exceptions())
    # A store manager lands here but is NOT confined, so handing them a page allowlist would be a
    # second, wrong answer about their reach. Only a confined viewer gets one.
    if confined:
        out["pages"] = list(pages(role_perms))
    return out
