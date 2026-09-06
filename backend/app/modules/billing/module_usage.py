"""PER-MODULE USAGE METERING — "it should bill each call on all modules, nothing is for free".

OWNER DIRECTIVE 2026-09-05 (sanjot@): *"For the billing, it should bill each call on all modules,
nothing is for free, and have an itemized statement for the tenant… the billing engine should list all
the modules and an option to assign price against them, a drop down menu to assign what kind of plan
could belong to like free, starter, premium etc"*.

This module answers the METERING half: which tenant used which module, how many times, on which day.
`billing/statement.py` turns that into money.

══ THE THROUGHPUT DECISION (stated up front, because it is the whole design) ══════════════════════
"Bill each call on all modules" is a volume problem of a completely different order from the AI meter:
AI calls are a handful per tenant per day, API calls are thousands per tenant per hour. Two shapes
were considered:

  · A ROW PER CALL. Truthful and maximally detailed, but it puts a database write in the path of every
    request and grows without bound. This platform already took a SEV-1 (2026-07-30) from work done
    inline on the request path, and the access log — the one existing per-request writer — only gets
    away with it by detaching the write. Rejected for billing.
  · COUNTERS ROLLED UP PER (org, module, day)  ← CHOSEN.
    An in-process accumulator counts in memory; a periodic flush sends the whole batch in ONE round
    trip to `core.bump_module_usage`, which does INSERT … ON CONFLICT DO UPDATE SET calls = calls +
    excluded.calls. So the request path pays a dict increment (sub-microsecond, no I/O, no lock), and
    the database sees roughly one call per flush interval instead of one per request.

    Cost of the choice, stated honestly: a process that dies between flushes loses at most one
    interval of counts, so this UNDER-counts slightly under a hard crash. For a usage bill that is the
    right direction to be wrong (never bill for calls we cannot evidence), and the flush interval
    bounds the loss. Per-call forensic detail is not retained — `core.access_log` already keeps
    per-request rows for that purpose, and duplicating it here would be a second derivation of the
    same fact.

══ WHAT IS BILLED, AND WHAT IS DELIBERATELY NOT ══════════════════════════════════════════════════
"Nothing is for free" means no module is exempt from HAVING a price. It does not mean a tenant should
be charged for work the tenant did not ask for. Silently billing a tenant for OUR retry storm is both
wrong and the fastest way to make an invoice untrustworthy, so calls are classified, not lumped:

  · `tenant`  — a signed-in human (or their browser) acting in that tenant. BILLABLE.
  · `system`  — the platform's own machinery: pg_cron ticks and `*/run-due` sweeps (secret-gated, no
                JWT), webhook deliveries, internal service-role calls, health checks. COUNTED AND
                SHOWN, never billed. The operator can see exactly what the platform did on a tenant's
                behalf without it landing on their invoice.
  · `anonymous`— unauthenticated public endpoints (the marketing price list, signup status). Not
                attributable to a tenant, so not billed to one.

Both counts are stored, so the decision is visible and reversible: if the owner decides sweeps SHOULD
be billed, that is a config change to the statement, not a re-instrumentation — the numbers are
already there.

══ HONESTY ═══════════════════════════════════════════════════════════════════════════════════════
A path whose module cannot be determined is counted under `unmapped`, never silently dropped and
never guessed onto a neighbouring module. `unmapped` surfaces on the operator grid the same way an
unpriced module and an unmetered AI call site do — the platform has already been bitten by a
hardcoded module list going stale (`main.py:_mounted_modules` was written precisely because a literal
list "CONFIDENTLY MISREPRESENTS the deployment"), so the route map is derived and its gaps are shown.

PURE: stdlib only. No DB, no network, no FastAPI. `backend/harness_module_billing.py` proves it.
"""
import threading
from datetime import datetime, timezone

# ── Route prefix → billable module key ───────────────────────────────────────────────────────────
# API paths are /api/v1/<prefix>/…, and <prefix> is the ROUTER's prefix, which is not always the
# entitlement key (`commcalc` serves the `commissions` module; `storeops` serves several).
# This map is the code DEFAULT; `core.module_route_map` rows override it per deployment (RULE TWO —
# a new module is registered, not hard-coded), and anything unmapped is reported as `unmapped`
# rather than being attributed to whatever looks closest.
#
# Keys on the right MUST be entitlement keys from core.entitlements.MODULE_CATALOG, so the pricing
# grid, the entitlement gate and the invoice all name the same thing. `validate_route_map` proves it.
DEFAULT_ROUTE_MODULE = {
    "commcalc": "commissions",
    "account": "account",
    "closing": "closing",
    "storeops": "storeops",
    "asset": "asset",
    "notify": "notify",
    "helpdesk": "helpdesk",
    "hr": "hr",
    "crm": "crm",
    "vision": "vision",
    "pos": "pos",
    "approvals": "approvals",
    "chat": "chat",
    "payables": "payables",
    "recovery": "recovery",
    "referral": "referral",
    "remediation": "remediation",
    "storevisit": "storevisit",
}
# Prefixes that are PLATFORM INFRASTRUCTURE, not a billable tenant module. Excluded deliberately and
# by name, so their absence from the invoice is a decision on the record rather than an oversight:
#   core       — auth, RBAC, tenant admin, the attention popup. Using the platform is not a module.
#   billing    — reading one's own invoice must never itself be billable.
#   vendor-api — the POS vendor-facing token API: the caller is a VENDOR, not the tenant.
INFRA_PREFIXES = frozenset({"core", "billing", "vendor-api"})

# Call classes. Only `tenant` reaches an invoice (see the module docstring).
CALL_CLASSES = ("tenant", "system", "anonymous")

# Paths that are the platform talking to itself. A cron tick authenticates with the shared run secret
# and carries no JWT, so it would otherwise look like an anonymous call; naming the shape keeps the
# classification honest instead of accidental.
_SYSTEM_PATH_MARKERS = ("/run-due", "/sweep/run-due", "/webhook", "-webhook", "/cron")


def module_for_path(path, route_map=None):
    """'/api/v1/commcalc/commissions/July 2026' → ('commissions', 'commcalc'). PURE.

    Returns (module_key | None, prefix | None). module_key None with a prefix means the prefix is
    infrastructure or unmapped — the caller decides which bucket; `classify` does that below."""
    p = str(path or "").strip()
    if not p.startswith("/api/v1/"):
        return None, None
    rest = p[len("/api/v1/"):]
    prefix = rest.split("/", 1)[0].split("?", 1)[0].strip().lower()
    if not prefix:
        return None, None
    rmap = dict(DEFAULT_ROUTE_MODULE)
    rmap.update(route_map or {})
    return rmap.get(prefix), prefix


def classify(path, *, org_id=None, has_actor=False, is_system=False, route_map=None):
    """One request → what to count it as. PURE.

    Returns {module, prefix, call_class, billable, bucket} where `bucket` is what the counter keys on:
    a real module key, or one of the honest catch-alls 'infra' / 'unmapped'."""
    module, prefix = module_for_path(path, route_map)
    p = str(path or "").lower()
    system = bool(is_system) or any(mk in p for mk in _SYSTEM_PATH_MARKERS)

    if prefix is None:
        bucket, module = "unmapped", None
    elif prefix in INFRA_PREFIXES:
        bucket, module = "infra", None
    elif module is None:
        # A live route prefix nobody has mapped to a billable module. NOT guessed onto a neighbour and
        # NOT dropped: surfaced so the operator can map or exempt it.
        bucket = "unmapped"
    else:
        bucket = module

    if system:
        call_class = "system"
    elif org_id and has_actor:
        call_class = "tenant"
    else:
        call_class = "anonymous"

    # Billable = a tenant-initiated call, on a real module, attributable to a tenant. Everything else
    # is counted and shown, never charged.
    billable = bool(call_class == "tenant" and module and org_id)
    return {"module": module, "prefix": prefix, "call_class": call_class,
            "billable": billable, "bucket": bucket}


def validate_route_map(catalog_keys, route_map=None):
    """Every mapped module key must exist in the entitlement catalog. PURE.

    This is the guard against the `/health` failure mode: a route map that names a module the
    entitlement system does not know would produce invoice lines for a module the operator cannot
    price or grant. Returns {ok, unknown_targets, unmapped_prefixes}."""
    rmap = dict(DEFAULT_ROUTE_MODULE)
    rmap.update(route_map or {})
    known = set(catalog_keys or ())
    unknown = sorted({v for v in rmap.values() if v not in known})
    return {"ok": not unknown, "unknown_targets": unknown,
            "mapped_prefixes": sorted(rmap), "infra_prefixes": sorted(INFRA_PREFIXES)}


def unmapped_prefixes(mounted_prefixes, route_map=None):
    """Which LIVE route prefixes have no billable-module mapping and are not declared infrastructure.

    Fed from `main.py:_mounted_modules()` — the list DERIVED from the running app's routes — so a new
    module mounted without being mapped shows up here instead of quietly billing nothing. PURE."""
    rmap = dict(DEFAULT_ROUTE_MODULE)
    rmap.update(route_map or {})
    return sorted({p for p in (mounted_prefixes or ())
                   if p not in rmap and p not in INFRA_PREFIXES})


# ══════════════════════════════════════════════════════════════════════════════════════════════
# THE ACCUMULATOR — the reason the request path pays no I/O
# ══════════════════════════════════════════════════════════════════════════════════════════════
class UsageAccumulator:
    """In-memory per-(org, bucket, day) counters, drained periodically by the flusher.

    THREAD-SAFE because uvicorn serves requests on an event loop AND hands blocking work to a thread
    pool, so `add` can genuinely be reached from several threads. The lock is held only for a dict
    update — no I/O inside it, ever, or this becomes the very stall it exists to avoid.

    `drain()` atomically swaps the buffer out and returns it, so counting continues while a flush is
    in flight and a failed flush can be merged back with `restore()` rather than lost."""

    def __init__(self):
        self._counts = {}
        self._lock = threading.Lock()

    def add(self, org_id, bucket, day, *, call_class="tenant", billable=False, n=1):
        if not org_id or not bucket or not day:
            return False
        key = (str(org_id), str(bucket), str(day)[:10])
        with self._lock:
            c = self._counts.get(key)
            if c is None:
                c = {"calls": 0, "billable_calls": 0, "system_calls": 0, "anonymous_calls": 0}
                self._counts[key] = c
            c["calls"] += n
            if billable:
                c["billable_calls"] += n
            if call_class == "system":
                c["system_calls"] += n
            elif call_class == "anonymous":
                c["anonymous_calls"] += n
        return True

    def size(self):
        with self._lock:
            return len(self._counts)

    def drain(self):
        """Swap out and return the accumulated rows as a flat list ready for the bump RPC."""
        with self._lock:
            counts, self._counts = self._counts, {}
        return [{"org_id": o, "module": b, "usage_date": d, **c}
                for (o, b, d), c in sorted(counts.items())]

    def restore(self, rows):
        """Merge drained rows back after a FAILED flush, so a database blip loses no counts."""
        with self._lock:
            for r in rows or []:
                key = (r.get("org_id"), r.get("module"), r.get("usage_date"))
                if not all(key):
                    continue
                c = self._counts.setdefault(key, {"calls": 0, "billable_calls": 0,
                                                  "system_calls": 0, "anonymous_calls": 0})
                for f in ("calls", "billable_calls", "system_calls", "anonymous_calls"):
                    c[f] += int(r.get(f) or 0)
        return True


def today_utc(now=None):
    return (now or datetime.now(timezone.utc)).date().isoformat()


def rollup_by_module(rows):
    """Daily counter rows → per-module totals for a period. PURE.

    `rows` are core.module_usage_daily rows already filtered to the period and the tenant."""
    out = {}
    for r in rows or []:
        m = r.get("module") or "unmapped"
        o = out.setdefault(m, {"calls": 0, "billable_calls": 0, "system_calls": 0,
                               "anonymous_calls": 0, "days": 0})
        for f in ("calls", "billable_calls", "system_calls", "anonymous_calls"):
            o[f] += int(r.get(f) or 0)
        o["days"] += 1
    return dict(sorted(out.items()))
