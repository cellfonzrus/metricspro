"""THE ONE HOME for "may THIS tenant use THIS gateable thing". PURE core (no I/O in `decide`).

OWNER (2026-11-10), verbatim: *"why is vidapay connector showing in boost ? we need to get these
things under control before we roll out for outside customers — have an agent asses and create a
dashboard for the super admin whereby these things can be gated out for the respective tenants right
from the get go"*.

WHY HIS CASE HAPPENED, measured before building (the answer is NOT a bug in the VidaPay code):

  · The platform keeps its DEFAULTS on org `00000000-0000-0000-0000-000000000001`, which EVERY
    tenant inherits — the mig-207 / 998 / 1010 / 1014 shape. That org id IS a real tenant
    (`storeops.tenants` → "Cellfonz R Us"). So "the house default" and "that tenant's own row" are
    the SAME ROW, and a default that is really another carrier's vocabulary is indistinguishable
    from a row that tenant declared for itself. Five `commcalc.report_pull_map` rows carry
    `processor = 'vidapay'`; the carrier-vocabulary rows `report_term:total` spell the vendor.
  · The gate that WOULD have hidden them exists and is right: `commcalc.connector_registry`
    (mig 1014) says that connector applies to carrier `total`, and the tenant declares another one.
    Every connector surface asks it. `GET /commcalc/report-pull-map` DID NOT — connector-keyed
    config rows listed with no connector predicate — which is the surface he was looking at.
  · And underneath both: gating was ALL-ACCESS BY DEFAULT (`entitlements.effective_modules` —
    "a tenant with no billing_plan is all-access and automatically receives EVERY module, including
    every module that ships in the future"). Measured 2026-11-10: all four tenants hold all 15
    modules enabled and `storeops.billing_plan` is empty. A new tenant gets everything.

THE CLASS (CLAUDE.md "A fix is a DESIGN fix"), stated so nobody re-derives it: **inheritance is not
a decision.** Three mechanisms already answer "is this thing on for this tenant" — module
entitlement, the connector registry, the report-kind registry — and all three answered it by
INHERITING a house row, so nothing anywhere recorded that a human had DECIDED. That is why one
tenant could not be gated at all (its rows are the house rows) and why a new tenant arrives holding
everything. This module is where the decision lives, for all three, so there is one truth table and
not three.

NOT A FOURTH REGISTRY — the duplicate check, stated for the PR (CLAUDE.md build gate). What was
searched in `docs/SYSTEM_DATA_FLOW_INDEX.md` and why none of it is this module:
  · `core.module_catalog` (mig 700) + `storeops.tenant_modules` (mig 053) + `storeops.billing_plan`
    → `core/entitlements.py` — WHICH MODULES a tenant is entitled to. REUSED: `tenant_modules.
    is_enabled` IS this module's grant for kind `module`; no new grant table.
  · `commcalc.connector_registry` (mig 1014) + `commcalc.report_kind` (mig 1010) →
    `report_kinds.visible_kinds` — WHICH CONNECTORS / REPORT KINDS apply to a tenant's declaration.
    REUSED: `ui_label_override` scope `cap`, keys `connector:<key>` / `kind:<key>`, IS this module's
    grant for those two kinds — the super-admin override those registries already read.
  · `commcalc.connector_route_policy` (mig 998) — whether ONE ROUTE of a connector may run. A
    policy BELOW the question here (it presumes the connector is the tenant's at all).
  · `core/verticals.py` (mig 1020) — whether a module belongs to this KIND OF BUSINESS. A fact
    about the module, not a decision about the tenant; it feeds `by_rule` and is not re-derived here.
  · `core/rep_surface.py` (§CORE-33) — what a confined EMPLOYEE may reach. A different subject
    (person, not tenant) and deliberately not org-keyed.
None of them holds "a human decided this tenant may/may not have this". ONE new fact is needed and
exactly one is added: the tenant's POSTURE (`storeops.tenants.feature_gate_posture`, mig 1119).

THE TRUTH TABLE — `decide()`, and nothing else may re-derive it (locked by
`harness_feature_gate_lock.py`):

    grant  posture   result
    ─────  ───────   ──────────────────────────────────────────────────────────
    False  any       DENY  — a super admin gated it off; a declaration cannot re-open it
    True   any       ALLOW — a super admin granted it; the declaration need not agree
    None   gated     DENY  — DEFAULT OFF: not yet granted to this tenant ("from the get go")
    None   inherit   `by_rule` — exactly what the platform answered before this module existed

`inherit` is the DEGRADE DIRECTION, deliberately: an unknown, absent or misspelled posture, an
unapplied migration and an unreachable table all resolve to `inherit`, so the gate NEVER starts
refusing on its own. The owner's "gated from the get go" is carried by the COLUMN DEFAULT (`gated`,
with every tenant alive at apply time backfilled to `inherit`), not by a code fallback — a code
fallback that denies is how a release locks four live tenants out of their own platform.

RULE TWO. No tenant, carrier, vendor or POS name is decided on here. `STARTER_MODULES` is a POLICY
(which modules a brand-new tenant can be SET UP with before anything is granted), which CLAUDE.md
allows as a house default, and it is applied ONCE at provisioning as real grant rows — never
consulted at read time, so it can never silently re-open a module a super admin gated off.

NOT MONEY-TOUCHING. Nothing here reads or writes a rate, plan, payout or any calculation input.
"""

# ── THE POSTURE VOCABULARY ────────────────────────────────────────────────────────────────────────
POSTURE_INHERIT = "inherit"      # answer as the platform did before this module: declaration / all-access
POSTURE_GATED = "gated"          # default DENY — only what a super admin granted
POSTURES = (POSTURE_INHERIT, POSTURE_GATED)
POSTURE_DEFAULT = POSTURE_INHERIT   # the DEGRADE value, never the new-tenant value (see the docstring)

TABLE = "tenants"                # storeops.tenants
COLUMN = "feature_gate_posture"
MIGRATION = "1119_tenant_feature_gate_posture.sql"

# ── THE GATEABLE KINDS, each naming the mechanism that already holds its grant ────────────────────
KIND_MODULE = "module"
KIND_CONNECTOR = "connector"
KIND_REPORT_KIND = "report_kind"
KINDS = (KIND_MODULE, KIND_CONNECTOR, KIND_REPORT_KIND)
# Where a grant for each kind LIVES. The dashboard writes through these homes and nowhere else, so a
# toggle here is the same row the registry has always read.
# ONE grant home for all three kinds: `commcalc.ui_label_override` scope 'cap', which has been THE
# super-admin override table since mig 068 and already carries the `connector:` / `kind:` / `carrier:`
# / `pos:` namespaces. A super admin's decision is one more namespace in it, never a new table —
# and deliberately NOT `storeops.tenant_modules`, which `entitlements.sync_tenant` DERIVES from the
# answer and would therefore overwrite the decision it was supposed to record.
CAP_SCOPE = "cap"
CAP_PREFIX = {KIND_MODULE: "module:", KIND_CONNECTOR: "connector:", KIND_REPORT_KIND: "kind:"}
GRANT_HOME = {k: f"commcalc.ui_label_override scope '{CAP_SCOPE}' key '{p}<key>'"
              for k, p in CAP_PREFIX.items()}
# What a grant row's `label` column SAYS. Two words, because a row's absence is the third state and
# must stay absent rather than being spelled.
GRANT_ON = "show"
GRANT_OFF = "hide"

# ── WHO MAY WIDEN — THE one list of `cap` namespaces whose "show" is the PLATFORM'S decision ──────
# Owner directive 2026-09-13, which `POST /commcalc/nav-labels` already enforces: *"if they need them
# then the super admin should have a full role permission exclusively for super admin to assign to
# the new or existing tenants which have been gated out due to carrier or pos settings."* The rule is
# asymmetric on purpose and the asymmetry IS the safety property: NARROWING (`hide`, or clearing the
# row) is a tenant's own business and can only ever show LESS, so it stays open to a menu-layout
# admin; WIDENING past a gate re-grants something the tenant's own configuration says it does not
# have, so it is the platform's call.
#
# THIS LIST IS WHY IT LIVES HERE (found by the security pass this feature's own PR required). That
# writer ENUMERATED the namespaces inline, and the enumeration had fallen behind the mechanisms:
# `connector:` was already missing, and making `module:` a grant namespace would have let any
# menu-layout admin POST `cap` / `module:<key>` / `show` and hand their own tenant a module its
# billing plan or business type excludes. A list of "which gates may a tenant re-open for itself"
# with two copies is a privilege-escalation path waiting for the next namespace. One home, and the
# lock fails the build if the writer stops dereferencing it.
PLATFORM_WIDEN_PREFIXES = tuple(CAP_PREFIX[k] for k in KINDS) + (
    "carrier:",     # commcalc/report_kinds — the carrier a tenant declared
    "pos:",         # commcalc/report_kinds — the POS a tenant declared
    "vertical:",    # core/verticals (migs 1020/1024) — the kind of business, per PAGE
    "closing:",     # core/verticals — the kind of business, per closing-form input
)


def widening_is_platform_only(scope, key, label):
    """Does this `ui_label_override` write RE-GRANT something a gate withheld? True = super admin
    only. PURE, and the ONE answer: a writer that asks this cannot fall behind the namespaces.

    The label is judged by `grant_state`, NOT by comparing it to the one word `show` — the security
    pass on this feature pointed out that a `== "show"` test leaves every synonym a caller might
    send ('yes', 'on', '1', `true`) walking straight past a refusal. Asking the same function that
    decides what a value MEANS is the only version of this test that cannot drift from it."""
    if str(scope or "").strip().lower() != CAP_SCOPE:
        return False
    if grant_state(label) is not True:
        return False
    k = str(key or "").strip()
    return any(k.startswith(p) for p in PLATFORM_WIDEN_PREFIXES)

# ── THE BASIS VOCABULARY — WHY the answer is what it is. A surface prints these words. ────────────
BASIS_GRANT = "granted to this tenant by a super admin"
BASIS_DENY = "gated off for this tenant by a super admin"
BASIS_GATED_DEFAULT = "not granted yet — this tenant is gated by default"
BASIS_RULE = "follows this tenant's own declaration"
BASES = (BASIS_GRANT, BASIS_DENY, BASIS_GATED_DEFAULT, BASIS_RULE)

# POLICY (not a vocabulary): what a brand-new GATED tenant is provisioned able to do, so somebody can
# sign in and be set up before a super admin grants anything. Applied ONCE, as grant rows, by
# `starter_grants` at provisioning; never read when answering `decide`.
STARTER_MODULES = ("storeops", "hr", "training", "support")


def posture(value):
    """The posture a stored value NAMES — anything unknown, blank or absent is `inherit`.

    The degrade direction is the whole point: a typo, an unapplied migration or an unreachable
    `storeops.tenants` must leave the platform answering exactly as it did yesterday, never start
    refusing. PURE."""
    v = str(value or "").strip().lower()
    return v if v in POSTURES else POSTURE_DEFAULT


def gated(value):
    """Is this tenant in the default-deny posture? PURE."""
    return posture(value) == POSTURE_GATED


def stored_grant(value):
    """A grant as it was STORED, read strictly: only the two words this module writes (`show` /
    `hide`) are a decision; anything else is None, "nobody has decided".

    Deliberately stricter than `grant_state` below, and the strictness is a security property found
    by this feature's own security pass: the grant home is a shared display-config table with
    another writer, so a row whose label this module did not write must not be able to mean
    "granted" by being a synonym for yes. PURE."""
    v = str(value or "").strip().lower()
    return True if v == GRANT_ON else (False if v == GRANT_OFF else None)


def grant_state(value):
    """A grant as an API CALLER expressed it: True (granted) / False (gated off) / None (no
    decision). Lenient on purpose — it reads a bool, the two stored words, and the ordinary
    synonyms — because it is what validates an inbound request before `save_grant` normalises it to
    one of the two words. For a value READ BACK OUT of the grant home, use `stored_grant`. PURE."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in ("show", "true", "on", "enabled", "yes", "1"):
        return True
    if v in ("hide", "false", "off", "disabled", "no", "0"):
        return False
    return None


def decide(*, grant=None, posture=POSTURE_DEFAULT, by_rule=True):  # noqa: A002 — the words are the vocabulary
    """THE truth table (see the docstring). Returns {allowed, basis, decided}.

    `decided` is True when a human's decision produced the answer — so a surface can say "a super
    admin set this" rather than implying the tenant's declaration did. Keyword-only on purpose: a
    positional call site would be one swap away from gating a tenant out of its own platform. PURE."""
    g = grant_state(grant)
    if g is False:
        return {"allowed": False, "basis": BASIS_DENY, "decided": True}
    if g is True:
        return {"allowed": True, "basis": BASIS_GRANT, "decided": True}
    if gated(posture):
        return {"allowed": False, "basis": BASIS_GATED_DEFAULT, "decided": False}
    return {"allowed": bool(by_rule), "basis": BASIS_RULE, "decided": False}


def allowed(*, grant=None, posture=POSTURE_DEFAULT, by_rule=True):
    """`decide(...)['allowed']` — for a caller that only needs the verdict. PURE."""
    return decide(grant=grant, posture=posture, by_rule=by_rule)["allowed"]


def starter_grants(kind, catalog_keys):
    """The grant rows a brand-new GATED tenant is provisioned with — the POLICY above, intersected
    with what the registry actually holds, so a renamed module never becomes a phantom grant. Every
    other key is deliberately ABSENT rather than False: absent means "nobody has decided", which is
    what a fresh tenant is, and it keeps `decide` the only thing that turns absence into a refusal.
    PURE."""
    if kind != KIND_MODULE:
        return {}
    keys = list(catalog_keys or [])
    return {k: True for k in STARTER_MODULES if k in keys}


def row(kind, key, label, *, grant=None, posture=POSTURE_DEFAULT, by_rule=True, extra=None):
    """ONE row of the super-admin grid: the thing, the verdict, the basis, and where its grant lives.
    The dashboard renders these and computes nothing of its own. PURE."""
    d = decide(grant=grant, posture=posture, by_rule=by_rule)
    out = {
        "kind": kind, "key": key, "label": label or key,
        "allowed": d["allowed"], "basis": d["basis"], "decided": d["decided"],
        "grant": grant_state(grant), "by_rule": bool(by_rule),
        "grant_home": GRANT_HOME.get(kind),
    }
    if extra:
        out.update(extra)
    return out


def summary(rows):
    """What the grid says at a glance, folded from the rows themselves — never counted a second time
    by a page. PURE."""
    rows = list(rows or [])
    return {
        "total": len(rows),
        "on": sum(1 for r in rows if r.get("allowed")),
        "off": sum(1 for r in rows if not r.get("allowed")),
        "decided": sum(1 for r in rows if r.get("decided")),
        "by_kind": {k: sum(1 for r in rows if r.get("kind") == k) for k in KINDS},
    }


# ── I/O — the ONE reader/writer of the posture column AND of the grant rows. Org-scoped. ─────────
def load_grants(client, org_id, kind):
    """{key: True/False} — the super admin's decisions for one KIND, from the one grant home. A key
    with no row is ABSENT rather than False: "nobody decided" and "decided no" are different facts,
    and only `decide` may turn the first into a refusal. Degrades to {} (never to a refusal)."""
    prefix = CAP_PREFIX.get(kind)
    if not prefix:
        return {}
    try:
        rows = (client.schema("commcalc").table("ui_label_override").select("key,label")
                .eq("org_id", str(org_id)).eq("scope", CAP_SCOPE).execute().data) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        k = str(r.get("key") or "")
        if not k.startswith(prefix):
            continue
        g = stored_grant(r.get("label"))
        if g is not None:
            out[k[len(prefix):]] = g
    return out


def save_grant(client, org_id, kind, key, value):
    """Record (or clear) ONE decision, in the one grant home. `value` True = on, False = off,
    None = CLEAR the decision, which puts the thing back under the posture + the declaration rather
    than leaving a row that says the opposite of what was meant."""
    prefix = CAP_PREFIX.get(kind)
    if not prefix:
        raise ValueError(f"kind must be one of {KINDS}")
    k = prefix + str(key or "").strip()
    if not str(key or "").strip():
        raise ValueError("a grant needs a key")
    tbl = client.schema("commcalc").table("ui_label_override")
    if value is None:
        tbl.delete().eq("org_id", str(org_id)).eq("scope", CAP_SCOPE).eq("key", k).execute()
        return {"kind": kind, "key": key, "grant": None}
    label = GRANT_ON if grant_state(value) else GRANT_OFF
    tbl.upsert({"org_id": str(org_id), "scope": CAP_SCOPE, "key": k, "label": label},
               on_conflict="org_id,scope,key").execute()
    return {"kind": kind, "key": key, "grant": grant_state(label)}



def load_posture(client, org_id):
    """This tenant's posture — THE one place the column is read. Any failure (unapplied migration,
    unreachable table, no such tenant) is `inherit`: see the docstring on the degrade direction."""
    try:
        rows = (client.schema("storeops").table(TABLE).select(COLUMN)
                .eq("org_id", str(org_id)).limit(1).execute().data) or []
    except Exception:
        return POSTURE_DEFAULT
    if not rows:
        return POSTURE_DEFAULT
    return posture(rows[0].get(COLUMN))


def load_postures(client):
    """{org_id: posture} for every tenant — the dashboard's one read. Degrades to {}."""
    try:
        rows = (client.schema("storeops").table(TABLE).select("org_id,name," + COLUMN)
                .execute().data) or []
    except Exception:
        return {}
    return {str(r.get("org_id")): posture(r.get(COLUMN)) for r in rows if r.get("org_id")}


def save_posture(client, org_id, value):
    """Set one tenant's posture. Refuses a value the vocabulary does not name rather than silently
    storing `inherit` — a super admin who mistypes must be told, not quietly left wide open."""
    v = str(value or "").strip().lower()
    if v not in POSTURES:
        raise ValueError(f"posture must be one of {POSTURES}")
    (client.schema("storeops").table(TABLE).update({COLUMN: v})
     .eq("org_id", str(org_id)).execute())
    return v
