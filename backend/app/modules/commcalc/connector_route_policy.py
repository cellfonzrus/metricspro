"""WHICH INGEST ROUTE IS OPEN FOR A CONNECTOR — the per-org, per-connector route gate. PURE.

OWNER DIRECTIVE 2026-09-09, verbatim: *"we are not doing the 2FA login for b2b as they sent an email
out to not do it, so make that gated by default for all unless it opens up later, only option for b2b
is email ingested reports"*.

The vendor told the tenant to stop using their portal's browser/2FA login. That is NOT a fault in the
connector, not a dead session, and not something a human fixes by signing in — it is an instruction
that CLOSES one ingest route and leaves another open. Nothing in the platform could say that:

  · `data_source.enabled` is the operator's own on/off knob for ONE login row. Switching it off says
    "we don't use this", carries no reason, is per-row rather than per-org-per-connector, and the next
    operator switches it straight back on.
  · `portal_backoff` (mig 244) already answers "may we contact this portal RIGHT NOW?" — but its answer
    is a TEMPORARY cooldown with a clock, and its wording ("the portal has temporarily blocked us")
    frames a deliberate closure as a fault. A vendor instruction has no `blocked_until`.
  · `portal_session_health` (mig 955) answers "can this session still pull?" and every one of its
    actionable states prescribes exactly the remedy the vendor just forbade: sign in again.

So this module adds the ONE question none of them asked, at the layer the others already live on:
**is this ROUTE open for this CONNECTOR, for this org?** One answer out — allowed or not, why, and
which route to use instead.

RULE TWO — CONFIG, NEVER CODE. No vendor, carrier or tenant name appears anywhere in this module or in
any call site. The gate is keyed on:
  · CONNECTOR — the `data_source.processor` slug the platform already dispatches on
    (`router._SOURCE_SCRAPERS`), i.e. the connector's own identity, supplied as data by the caller.
  · ROUTE — the FEED-SHAPE vocabulary `core.import_feed.source_type` already uses:
    `pull` (a portal login pull) | `email_sweep` | `ftp` | `google_sa` | `manual_expected`.
The rows live in `commcalc.connector_route_policy` (migration 998). A house-org row is the PLATFORM
DEFAULT inherited by every tenant; a tenant row for the same (connector, route) overrides it — the
same inheritance shape `portal_block_marker` (mig 244) and `report_pull_map` (mig 207) use. Re-opening
a closed route is one UPDATE on one row; nothing is deleted and no code has to be re-written.

DEGRADES BOTH WAYS. Before migration 998 runs — or with no rows at all — `resolve()` returns OPEN and
every gate is inert: no 500s, no behaviour change. After it runs, the seeded house rows apply.

NEVER SILENT, NEVER FALSE-GREEN. A closed route is a STATED state, not an absence:
  · `health()` renders it as the `route_disabled` state of `portal_session_health` — which
    `core.control_box.LAMP_FROM_PORTAL_STATE` lights as `unmonitored`, deliberately NOT green
    ("an absent subsystem is not a healthy one") and deliberately not an incident lamp either.
  · `should_notify` never fires for it (it is not in `portal_session_health._ACTIONABLE`), so a
    connector closed on purpose stops emailing the owner — but the chip, the attention list and the
    connector row all keep SAYING why, so nobody spends an afternoon "fixing" it.

NOT MONEY-TOUCHING. Nothing here reads or writes a rate, plan, payout or any calculation input; it
only decides WHICH WAY data may arrive.

PURE: stdlib only. No DB, no network (the one IO helper, `load_rows`, is a thin best-effort read that
returns [] on any failure). `backend/harness_connector_route_policy.py` proves every branch.
"""

# The house/platform org. Its rows are the DEFAULT every tenant inherits — never a data scope.
HOUSE_ORG = "00000000-0000-0000-0000-000000000001"

# INGEST ROUTES — the feed-shape vocabulary, taken verbatim from `core.import_feed.source_type` (mig
# 717) so a route slug means the same thing in the import-health registry and here. A route is a SHAPE
# of arrival, never a vendor: `pull` is "a portal login pulls it", whichever portal that is.
ROUTES = ("pull", "email_sweep", "ftp", "google_sa", "manual_expected")
# The route this gate is asked about by default: the automated portal login.
DEFAULT_ROUTE = "pull"

# Human wording for a route, used when a policy row names a remedy route but no remedy label.
ROUTE_LABEL = {
    "pull": "the portal login",
    "email_sweep": "the email-ingested reports",
    "ftp": "the FTP/SFTP drop",
    "google_sa": "the Google service-account feed",
    "manual_expected": "a manual upload",
}

# The state name this policy contributes to the portal-session ladder. Defined HERE (the module that
# owns the concept) and imported by portal_session_health, so the two cannot drift.
DISABLED_STATE = "route_disabled"

# What "nothing is configured" means: the route is open and behaviour is exactly as before.
OPEN = {"allowed": True, "connector": None, "route": DEFAULT_ROUTE, "reason": "",
        "remedy_route": None, "remedy_label": "", "remedy_href": None, "source": "default"}


def normalize(v):
    """Connector/route slug → the canonical lower-case form used for matching. PURE."""
    return str(v or "").strip().lower()


def _row_policy(row, connector, route):
    """One config row → the policy dict. PURE."""
    allowed = row.get("allowed")
    allowed = True if allowed is None else bool(allowed)
    remedy = normalize(row.get("remedy_route")) or None
    return {
        "allowed": allowed,
        "connector": connector,
        "route": route,
        "reason": str(row.get("reason") or "").strip(),
        "remedy_route": remedy,
        "remedy_label": str(row.get("remedy_label") or "").strip()
                        or (ROUTE_LABEL.get(remedy, "") if remedy else ""),
        "remedy_href": (str(row.get("remedy_href") or "").strip() or None),
        "source": "tenant" if str(row.get("org_id") or "") not in ("", HOUSE_ORG) else "house",
    }


def resolve(rows, org_id, connector, route=DEFAULT_ROUTE):
    """Is `route` open for `connector` at `org_id`? PURE.

    `rows` is any iterable of connector_route_policy rows already read for this org AND the house org
    (see `load_rows`). The caller org's own row wins; otherwise the house row (the platform default);
    otherwise OPEN — an unconfigured connector behaves exactly as it always has.

    Returns {allowed, connector, route, reason, remedy_route, remedy_label, remedy_href, source}."""
    conn, rt = normalize(connector), normalize(route) or DEFAULT_ROUTE
    if not conn:
        return dict(OPEN, route=rt)
    mine = house = None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if normalize(r.get("connector")) != conn:
            continue
        if (normalize(r.get("route")) or DEFAULT_ROUTE) != rt:
            continue
        if str(r.get("org_id") or "") == str(org_id):
            mine = r
            break                       # the tenant's own row is final
        if str(r.get("org_id") or "") == HOUSE_ORG:
            house = r
    hit = mine if mine is not None else house
    if hit is None:
        return dict(OPEN, connector=conn, route=rt)
    return _row_policy(hit, conn, rt)


def is_open(policy):
    """True when the route may be used. An absent/garbage policy is OPEN (fail-inert). PURE."""
    if not isinstance(policy, dict):
        return True
    return bool(policy.get("allowed", True))


def is_closed(policy):
    """True only when a policy explicitly closes the route. PURE."""
    return not is_open(policy)


def headline(policy):
    """The one-line chip text for a closed route. PURE."""
    if is_open(policy):
        return ""
    return "Automatic %s is switched off for this connector." % (
        ROUTE_LABEL.get(normalize(policy.get("route")), "ingest route"))


def detail(policy):
    """The sentence that stops someone "fixing" a connector that is off ON PURPOSE. PURE.

    It states (a) that this is a decision, not a fault, (b) the recorded reason, and (c) the route that
    IS supported — so the reader's next action is the right one instead of another sign-in attempt."""
    if is_open(policy):
        return ""
    bits = ["This is a configured decision, not a fault — nothing here is broken and signing in will "
            "not help."]
    reason = str((policy or {}).get("reason") or "").strip()
    if reason:
        bits.append(reason)
    remedy = (policy or {}).get("remedy_label") or ROUTE_LABEL.get(
        normalize((policy or {}).get("remedy_route")), "")
    if remedy:
        bits.append("The supported route for this data is %s." % remedy)
    bits.append("Re-open it by setting this connector's route policy back to allowed.")
    return " ".join(bits)


def health(policy):
    """The closed route rendered in the portal-session-health shape, so ONE chip speaks for a source.

    Deliberately `needs_human=False` / `actionable=False`: nobody can fix this by signing in, and the
    notify-once gate (`portal_session_health.should_notify`) must not page for it. It is still NOT
    healthy — `control_box.LAMP_FROM_PORTAL_STATE` lights `route_disabled` as `unmonitored`. PURE."""
    return {"state": DISABLED_STATE, "headline": headline(policy), "detail": detail(policy),
            "hours_left": None, "actionable": False, "needs_human": False, "since": None,
            "route_policy": dict(policy or {})}


def refusal(policy, extra=None):
    """The body a gated endpoint returns INSTEAD of contacting the portal. PURE.

    `route_disabled` (not `blocked`) on purpose: the cooldown's `requires_confirm` escape MUST NOT
    apply here. A cooldown is a timer a human may knowingly override; a vendor instruction is not
    overridable from the UI at all — it is reversed by changing the config row."""
    out = {"ok": False, "route_disabled": True, "requires_confirm": False,
           "connector": (policy or {}).get("connector"), "route": (policy or {}).get("route"),
           "reason": (policy or {}).get("reason") or "",
           "remedy_route": (policy or {}).get("remedy_route"),
           "remedy_label": (policy or {}).get("remedy_label") or "",
           "remedy_href": (policy or {}).get("remedy_href"),
           "headline": headline(policy), "error": headline(policy),
           "message": (headline(policy) + " " + detail(policy)).strip()}
    if extra:
        out.update(extra)
    return out


def status_line(policy):
    """The stored-status-style sentence for a connector row, so the page never renders a closed route
    as a stale error. Capped like the platform's other status strings. PURE."""
    if is_open(policy):
        return ""
    return (headline(policy) + " " + detail(policy)).strip()[:300]


# ── the one IO helper (best-effort, org-scoped, never raises) ────────────────────────────────────
def load_rows(client, org_id):
    """This org's route-policy rows PLUS the house defaults, in one read. ORG-SCOPED by hand.

    NOTE (org-scope CI): the guard scans only commcalc/router.py, so this scoping is not CI-covered —
    it is `.in_("org_id", [org_id, HOUSE_ORG])`, exactly the inheritance shape `portal_backoff
    .load_markers` uses, and the house org appears ONLY as the default-row provider, never as a data
    scope. Returns [] on any failure (pre-migration-998 included) ⇒ every gate is inert."""
    try:
        return (client.schema("commcalc").table("connector_route_policy")
                .select("org_id,connector,route,allowed,reason,remedy_route,remedy_label,remedy_href")
                .in_("org_id", [str(org_id), HOUSE_ORG]).limit(500).execute().data) or []
    except Exception:
        return []


def load(client, org_id, connector, route=DEFAULT_ROUTE):
    """`resolve` over a fresh read — the convenience form for a single-source call site."""
    return resolve(load_rows(client, org_id), org_id, connector, route)
