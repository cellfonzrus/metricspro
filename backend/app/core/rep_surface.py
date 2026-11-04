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
        "pages": ("/employee/**",),
        "api": (),
        "api_read": ("/api/v1/core/employee-dashboard", "/api/v1/core/portal-reports",
                     "/api/v1/storeops/my-chargebacks", "/api/v1/commcalc/coaching",
                     "/api/v1/storeops/google-reviews/my"),
        "what": "their own earnings, chargebacks and coaching for a month they choose",
    },
    {
        "key": "schedule", "tile": True, "label": "My schedule", "icon": "📅",
        "pages": ("/storeops/timeoff", "/storeops/swaps"),
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
        "api_read": ("/api/v1/storeops/shifts", "/api/v1/storeops/schedule"),
        "what": "their own shifts, and asking for time off, a swap or an extension",
    },
    {
        "key": "action_plan", "tile": True, "label": "My action plan", "icon": "🎯",
        "pages": ("/commcalc/targets/action-plan",),
        "api": (),
        "api_read": ("/api/v1/commcalc/targets",),
        "what": "the focus areas the targets engine derives for them, already self-scoped by "
                "scope_keyset (§59.7)",
    },
    {
        "key": "helpdesk", "tile": True, "label": "Help desk", "icon": "🎫",
        "pages": ("/helpdesk/**",),
        # The ticket list, a ticket and raising one are an employee's; the two manager screens
        # inside the same subtree are NOT, and the carve-out is stated where the grant is made.
        "page_except": ("/helpdesk/settings", "/helpdesk/dashboard"),
        "api": ("/api/v1/helpdesk",),
        # The carve-out the page side already had, now on the API too — carving the SCREEN and
        # granting its whole API is the browser-only gate this module exists to kill. `/config/**`
        # is the settings screen (categories, priorities, teams, SLA) and `/support/**` is the
        # agent console (assign, reply, canned responses, bulk review).
        "api_except": ("/api/v1/helpdesk/config/**", "/api/v1/helpdesk/support/**"),
        "api_read": (),
        "what": "raising and following their own tickets",
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
        "key": "closing", "tile": False, "label": "Daily closing", "icon": "🧾",
        "pages": (),
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
        "key": "chat", "tile": False, "label": "Chat", "icon": "💬",
        "pages": ("/chat/**",),
        "api": ("/api/v1/chat",),
        "api_except": ("/api/v1/chat/admin/**",),
        "api_read": (),
        "what": "in-app messaging, which is how an employee reaches their manager",
    },
    {
        "key": "training", "tile": False, "label": "Training", "icon": "🎓",
        "pages": ("/training/**",),
        "api": ("/api/v1/core/training",),
        "api_read": (),
        "what": "the training assignments the platform gives them",
    },
    {
        "key": "password", "tile": False, "label": "My password", "icon": "🔑",
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


def tiles():
    """The landing page's tiles, in declaration order. Label, icon, page and plain-words `what`."""
    return tuple({"key": s["key"], "label": s["label"], "icon": s["icon"],
                  "page": (s["pages"][0] if s["pages"] else None), "what": s["what"]}
                 for s in SURFACES if s.get("tile"))


# ── PAGE GRAMMAR ──────────────────────────────────────────────────────────────────────────────────
# A `pages` entry is one of exactly two things, and nothing is implied:
#   "/storeops/timeoff"   EXACT — that page and no other. A page's NEIGHBOURS are never implied.
#   "/helpdesk/**"        that page AND everything below it (a ticket's own page, a flowchart).
# `page_except` carves a manager screen out of a subtree, stated beside the grant that would
# otherwise admit it. There is no third form and no prefix match on a page's PARENT — the parent
# prefix is the defect this module exists to kill.
SUBTREE = "/**"


def pages():
    """Every page entry a confined employee may open, deduped, in declaration order — in the grammar
    above. This is the list `/me` hands the browser; `lib/rbac.ts` holds no copy of it."""
    out, seen = [], set()
    for s in SURFACES:
        for p in s["pages"]:
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


def page_allowed(path):
    """May a confined employee open this PAGE? One of the declared entries must match in the grammar
    above, and no carve-out may. `/my/**` admits `/my/anything`; `/helpdesk/**` does NOT admit
    `/helpdesk/settings`; `/storeops/timeoff` does NOT admit `/storeops/timeclock`."""
    if any(_page_entry_matches(path, e) for e in page_exceptions()):
        return False
    return any(_page_entry_matches(path, e) for e in pages())


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


def api_allowed(path, method="GET"):
    """May a confined employee's browser call this API path with this method? DEFAULT DENY.

    A carve-out is asked FIRST and refuses outright, exactly as `page_allowed` does — a grant made
    for the POST that ASKS must never carry the PATCH that ANSWERS. The carve-out is absolute
    rather than per-surface: a route one surface calls a manager's is not an employee's because a
    second grant happens to overlap it."""
    m = (method or "GET").upper()
    if any(_api_except_matches(path, e) for e in api_exceptions()):
        return False
    if _matches(path, UNIVERSAL_API):
        return True
    if m in _READ_METHODS and _matches(path, UNIVERSAL_API_READ):
        return True
    for s in SURFACES:
        if _matches(path, s["api"]):
            return True
        if m in _READ_METHODS and _matches(path, s["api_read"]):
            return True
    return False


def surface_for_api(path, method="GET"):
    """Which declared surface admits this API path (for a trace / an explanation). None when none
    does, which is exactly when the gate refuses."""
    m = (method or "GET").upper()
    if any(_api_except_matches(path, e) for e in api_exceptions()):
        return None
    if _matches(path, UNIVERSAL_API) or (m in _READ_METHODS and _matches(path, UNIVERSAL_API_READ)):
        return "universal"
    for s in SURFACES:
        if _matches(path, s["api"]) or (m in _READ_METHODS and _matches(path, s["api_read"])):
            return s["key"]
    return None


def refusal(path, method="GET"):
    """The sentence a refused caller is told. It names what they CAN do rather than what they cannot,
    because the person reading it is an employee who has done nothing wrong."""
    labels = ", ".join(t["label"] for t in tiles())
    return (f"This is not part of your access. {method} {_norm(path)} belongs to a manager screen. "
            f"What you can use: {labels}.")


def viewer_payload(is_individual_contributor, scope=None):
    """What `/me` carries, or None for a viewer neither confined nor landed — so an older browser and
    a market/company role are byte-identical to today. The browser never re-derives either fact:
    `confined` says whether its reach is this list, `landing` says where it drops them."""
    confined = bool(is_individual_contributor)
    if not landing_applies(confined, scope):
        return None
    out = {"confined": confined, "landing": LANDING_PAGE, "tiles": [dict(t) for t in tiles()]}
    if confined:
        out["page_except"] = list(page_exceptions())
    # A store manager lands here but is NOT confined, so handing them a page allowlist would be a
    # second, wrong answer about their reach. Only a confined viewer gets one.
    if confined:
        out["pages"] = list(pages())
    return out
