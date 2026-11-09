"""SETUP FIX — the ONE home for "this isn't set up; here is how YOU fix it, and who to ask if you may not".

OWNER ASK 2026-11-09 (verbatim): *"to fix any such problem going forward shpow it on the screen hpow the
user can fix teh problem so it is taken care of right away and if it is not under tehoer role
permissionsm, have them send a link ot thier manager from the chat menu so it can be fixed"*.

THE INSTANCE was one financing row reading *"⚠️ Detection not configured"* with nothing to press.

THE CLASS (CLAUDE.md, "A fix is a DESIGN fix or it is not a fix") is bigger and is the thing fixed here:
**the platform knows a check could not run, and tells the person in front of it nothing they can act
on.** Dozens of surfaces across this app say "not configured" / "not measured" / "needs setup" — each
of them correct, each of them a dead end. Wording them one page at a time is the patchwork the house
rules forbid; the next page is written next week and reads the same.

THE DESIGN — one fact, one home, dereferenced:

  • WHAT CLOSES THE GAP lives HERE, once, per gap: the steps in plain words, and the page that closes
    it. A surface that detects a gap names its KEY and renders what this registry says. No page carries
    its own copy of the remedy, so the remedy cannot drift from the screen that closes it.
  • WHO MAY is NOT declared here. It is asked of the house one home — `self_lockout.can_edit_setting`,
    the same function the write endpoint itself refuses on — so the button a person is offered and the
    save the server will accept can never disagree. A gap declares only WHICH settings area to ask
    about (`gate`), and `ADMIN` means "no grantable area — a full-scope administrator", which is what
    that same function already answers for an unregistered area.
  • WHO TO ASK is DERIVED, never declared: the roles of THIS tenant whose own permissions satisfy that
    same check, and the people holding them, the asker's own manager first. A tenant whose roles
    satisfy nobody is told so (`support: True`) rather than shown a button that would 403 — and,
    because this module is handed one tenant's rows, it has no way to name anybody else's people.
  • A GAP THAT IS OURS declares `gate = PLATFORM`: no tenant can close it, so no in-app page and no
    manager is offered to anyone, including an administrator. Honest beats hopeful.

PURE. No I/O, no DB, no app import beyond the two house one-homes it dereferences. Proof:
`backend/tests/unit/test_setup_fix.py` (the registry contract) + `backend/harness_setup_fix.py` (the
lock: every page is a real route, every area is a grantable settings area, no customer-facing word
names a migration, a table or a carrier, and a surface cannot stop dereferencing this file).

RULE TWO. Every word here is the APP's own vocabulary — the name of a screen, a field, a button. No
carrier, tenant or product name appears, and none may: a tenant's own words are its declarations, read
at run time, never literals in this file.
"""
from app.modules.core import self_lockout

# ── the two gates that are not a grantable settings area ────────────────────────────────────────────
#
# ADMIN: there is no per-role settings area for this write, so the house default applies — a full-scope
# administrator (or the platform super admin). Deliberately spelled as an UNREGISTERED area key so the
# answer comes from `can_edit_setting`'s own default branch rather than from a second rule written here.
ADMIN = "admin"
_ADMIN_AREA = "__admin_only__"

# PLATFORM: closing this gap is ours (a server credential, a deployment switch). Nobody in any tenant
# can close it, so nobody is offered a page or a person.
PLATFORM = "platform"

# One question never pages the whole company.
MAX_RECIPIENTS = 3


# ── THE REGISTRY ────────────────────────────────────────────────────────────────────────────────────
# Each entry answers, for ONE gap: what is not set up (`title`), what the surface therefore cannot say
# (`consequence` — never "zero"), the steps that close it (`steps`), the page that closes it (`page`),
# and which settings area says who may (`gate`).
FIXES = {
    # ── commissions & financing ────────────────────────────────────────────────────────────────────
    "financing_vendor_detection": {
        "title": "This financing vendor has no way to recognise its own sales yet.",
        "consequence": "Until it does, the report counts nothing for this vendor — so a zero here means "
                       "“not set up”, not “nothing was financed”.",
        "steps": [
            "Open Financing Vendors.",
            "Press Edit on the vendor this row names.",
            "Under “How do we recognise this vendor’s sales”, either pick a commission-plan rule to "
            "inherit the matcher from, or add a match on the payment method the staff actually select "
            "at the register.",
            "Press Save. The report recounts the month as soon as the page reloads.",
        ],
        "page": "/commcalc/financing/vendors",
        "gate": ADMIN,
    },
    "commission_plan_rule": {
        "title": "No commission-plan rule says which sales this pay line is for.",
        "consequence": "A rule that matches nothing pays nothing, and the line reads as “not set up” "
                       "rather than as a sale nobody made.",
        "steps": [
            "Open Commission Plans & Payout Schedules.",
            "Open the plan this line belongs to and find the rule for it.",
            "Fill in what the rule should match — the product or service as it is spelled in the sales "
            "feed — and the rate it pays.",
            "Press Save, then press Run Calculation on the Commissions page to restate the month.",
        ],
        "page": "/commcalc/payout-plans",
        "gate": "commission_plans",
    },
    # ── sales vocabulary (each tenant's own words, so the gap is a DECLARATION, never a default) ────
    "accessory_vocabulary": {
        "title": "This company has not declared which of its own sales lines are accessories.",
        "consequence": "Nothing can be identified as an accessory until it is declared, so accessory "
                       "revenue and every accessory-per-box figure read as “not measured”.",
        "steps": [
            "Open Sales Classification.",
            "In “Accessory departments”, tick the department names exactly as they come through on your "
            "own sales report.",
            "Press Save, then press Run Calculation to restate the months you care about.",
        ],
        "page": "/commcalc/accessory-definition",
        "gate": "classification",
    },
    "box_departments": {
        "title": "This company has not declared which of its own sales lines count as a device unit.",
        "consequence": "Device units are the denominator of every per-box figure, so until they are "
                       "declared those figures are “not measured” rather than low.",
        "steps": [
            "Open Sales Classification.",
            "In “Device-unit departments”, tick the department names your own sales report uses for a "
            "handset or other device sale.",
            "Press Save, then press Run Calculation to restate the months you care about.",
        ],
        "page": "/commcalc/accessory-definition",
        "gate": "classification",
    },
    "closing_tender_fields": {
        "title": "The daily closing form has no tender fields set up for this company.",
        "consequence": "A closing with nothing to declare cannot be reconciled against the register, so "
                       "the day reads as unfinished rather than as balanced.",
        "steps": [
            "Open Daily Closing / Tender Fields.",
            "Add one field for each way your stores actually take money.",
            "Press Save. The next closing a store submits asks for them.",
        ],
        "page": "/closing/tender-config",
        "gate": "closing",
    },
    # ── targets, KPIs, org ──────────────────────────────────────────────────────────────────────────
    "store_targets": {
        "title": "No target is set for this store and month.",
        "consequence": "Attainment is a comparison, so with nothing to compare against the figure is "
                       "“no target set”, never 0%.",
        "steps": [
            "Open Target Settings.",
            "Pick the month, then enter the target for this store.",
            "Press Save. Every target and scorecard screen picks it up straight away.",
        ],
        "page": "/commcalc/targets/settings",
        "gate": "targets",
    },
    "kpi_metrics": {
        "title": "No performance metric is declared for this company yet.",
        "consequence": "A scorecard with no declared metric has nothing to score, so it reads as “not "
                       "set up” rather than as a bad month.",
        "steps": [
            "Open KPI Metrics.",
            "Add the metrics you manage stores on, with the direction that counts as good.",
            "Press Save.",
        ],
        "page": "/admin/kpi-metrics",
        "gate": "kpi",
    },
    "org_chart_manager": {
        "title": "No manager is assigned above this store in the company structure.",
        "consequence": "Anything keyed to a manager — a scorecard, a visit plan, an escalation — has "
                       "nobody to key to, and is reported as “no manager assigned”.",
        "steps": [
            "Open Company Structure.",
            "Find the district or market this store sits under. If the store sits under nothing, drag "
            "it into the right one first.",
            "Assign a manager to that level.",
            "Press Save.",
        ],
        "page": "/admin/org-chart",
        "gate": ADMIN,
    },
    "connector_credentials": {
        "title": "This automatic report pull has no sign-in details saved, so it cannot run on its own.",
        "consequence": "Nothing arrives until somebody uploads the report by hand, which is why the "
                       "figures stop on the last day that was uploaded.",
        "steps": [
            "Open Connectors.",
            "Press Set credentials on this row and enter the portal sign-in your company uses.",
            "Switch the schedule on, then press Run now once to prove it signs in.",
        ],
        "page": "/commcalc/connectors",
        "gate": ADMIN,
    },
    # ── genuinely ours (a server credential) — nobody in a tenant can close these ───────────────────
    "notify_email_sender": {
        "title": "Sending email is not switched on for this platform yet.",
        "consequence": "Reports and alerts that would be emailed are held rather than sent, and the "
                       "send log records them as not sent.",
        "steps": [
            "Contact support and ask for email sending to be switched on for your company.",
            "Nothing in your own settings can turn this on, so there is nothing to change here.",
        ],
        "page": None,
        "gate": PLATFORM,
    },
    "notify_whatsapp_sender": {
        "title": "Sending WhatsApp messages is not switched on for this platform yet.",
        "consequence": "Alerts that would go to WhatsApp are held rather than sent, and the send log "
                       "records them as not sent.",
        "steps": [
            "Contact support and ask for WhatsApp sending to be switched on for your company.",
            "Nothing in your own settings can turn this on, so there is nothing to change here.",
        ],
        "page": None,
        "gate": PLATFORM,
    },
}

FIX_KEYS = tuple(sorted(FIXES))


def fix(key):
    """The registry entry for `key`, or None. Never invents one: an unknown key is a caller's bug and
    must surface as the plain notice, not as a made-up remedy."""
    e = FIXES.get(key)
    return dict(e) if e else None


def gate_area(entry):
    """WHICH settings area to ask `self_lockout.can_edit_setting` about — None for a platform gap.

    `ADMIN` resolves to an area no tenant can grant, so that same function's default branch answers it
    ("a full-scope administrator, or the platform super admin"). That is deliberately not a second rule
    written here: one copy of the precedence, in the file the write endpoints already refuse on."""
    gate = (entry or {}).get("gate")
    if not gate or gate == PLATFORM:
        return None
    return _ADMIN_AREA if gate == ADMIN else gate


def fixer_roles(role_perms, area):
    """The names of THIS tenant's roles that may edit `area`, from {role name -> permissions}.

    Derived through the house check, so an explicit per-role deny beats full scope here exactly as it
    does at the write endpoint. Sorted, so the same tenant always produces the same list."""
    if not area:
        return []
    out = []
    for name, perms in (role_perms or {}).items():
        if not name:
            continue
        if self_lockout.can_edit_setting({"role": name, "perms": perms or {}}, area):
            out.append(name)
    return sorted(out)


def ask_recipients(role_names, members, *, manager_ids=(), asker_id=None):
    """Who to ask, best first: the asker's own manager (when they can actually close it), then the
    others by name. At most `MAX_RECIPIENTS`.

    `members` are ONE tenant's people — [{employee_id, name, role}] — which is what makes a
    cross-tenant recipient impossible by construction rather than by a filter somebody can forget.
    A manager who does not hold a qualifying role is not offered: sending them a link they cannot use
    is the dead end this whole mechanism exists to remove."""
    allowed = {str(r) for r in (role_names or []) if r}
    if not allowed:
        return []
    order = {str(m): i for i, m in enumerate(manager_ids or [])}
    rows = []
    for m in (members or []):
        eid = str((m or {}).get("employee_id") or "").strip()
        if not eid or (asker_id is not None and eid == str(asker_id)):
            continue
        if str((m or {}).get("role") or "") not in allowed:
            continue
        rows.append({"employee_id": eid, "name": (m.get("name") or eid), "role": m.get("role"),
                     "is_manager": eid in order})
    rows.sort(key=lambda r: (0, order[r["employee_id"]]) if r["is_manager"] else (1, 0))
    managers = [r for r in rows if r["is_manager"]]
    others = sorted([r for r in rows if not r["is_manager"]], key=lambda r: str(r["name"]).lower())
    seen, out = set(), []
    for r in managers + others:
        if r["employee_id"] in seen:
            continue
        seen.add(r["employee_id"])
        out.append(r)
    return out[:MAX_RECIPIENTS]


def ask_message(entry, *, asker_name=None, origin=None):
    """The chat message that travels to whoever can close the gap: who asked, where they hit it, the
    same steps they were shown, and the deep link to the exact page.

    The SAME steps on purpose. A relayed "something isn't configured" is how a request dies; the person
    who can fix it should need to read nothing else."""
    e = entry or {}
    who = (asker_name or "").strip()
    where = (origin or "").strip()
    head = f"{who or 'Someone'} needs something set up"
    if where:
        head += f" on {where}"
    lines = [f"{head}.", "", e.get("title", ""), "", (e.get("consequence") or ""), "", "How to fix it:"]
    lines += [f"{i}. {s}" for i, s in enumerate(e.get("steps") or [], start=1)]
    if e.get("page"):
        lines += ["", f"Go straight to it: {e['page']}"]
    return "\n".join(x for x in lines if x is not None).strip()


def guide(key, *, can_fix, fixers=(), asker_name=None, origin=None):
    """THE payload every surface serves for a gap. None for an unknown key.

    `can_fix` is the HOUSE answer (`can_edit_setting`) computed by the caller against the live request —
    this module never decides a permission. A platform gap is `can_fix: False` for everybody, offers no
    page and no person, and sets `support`.
    """
    e = fix(key)
    if e is None:
        return None
    platform = e["gate"] == PLATFORM
    permitted = bool(can_fix) and not platform
    recipients = [] if (platform or permitted) else [dict(r) for r in (fixers or [])]
    return {
        "key": key,
        "title": e["title"],
        "consequence": e["consequence"],
        "steps": list(e["steps"]),
        "page": (None if platform else e["page"]),
        "can_fix": permitted,
        "gate": e["gate"],
        "ask": ({"recipients": recipients,
                 "message": ask_message(e, asker_name=asker_name, origin=origin)}
                if recipients else None),
        # Nothing in this company can close it: a platform gap, or a tenant whose roles satisfy nobody.
        "support": bool(platform or (not permitted and not recipients)),
    }
