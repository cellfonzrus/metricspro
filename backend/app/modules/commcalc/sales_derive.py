"""MONTH-BOUNDARY SALES-DERIVATION GRACE WINDOW — mod-commission, 2026-08-01.

THE DEFECT THIS EXISTS FOR (owner-verified on production data, tenant luxelink, 2026-08-01)
-------------------------------------------------------------------------------------------
The hourly feed→raw_sales derivation picks its period from the WALL CLOCK and nothing else:

    router._ftp_current_period()  ->  datetime.now().strftime("%B %Y")

so at 00:09 on August 1 it derives "August 2026" and never looks at July again. The B2B daily email
feed, however, keeps FINALIZING the old month for a few days after midnight — the trace for that night
shows the July `daily_sales_feed` row-count climbing 283 → 313 → 317 across the 00:09–04:05 sweeps while
every derivation run processed only {"August 2026"} (the 04:08 run even logged "no feed or monthly rows
for this period" *for August* while July sat un-rederived). Owner SQL on the same morning:

    July daily_sales_feed : 3,787 distinct trans_ids
    July raw_sales        : 3,744 distinct trans_ids
    -> 45 transactions in the feed and NOT in the monthly basis

`raw_sales` is the authoritative basis for a CLOSED month, so those 45 are invisible to every July
report and would be UNPAID in a July recompute. It recurs at every month boundary.

THE FIX
-------
Keep deriving the current month exactly as today, and ALSO re-derive the PRIOR month for a short,
PER-TENANT-CONFIGURABLE grace window after rollover. This module is the pure part of that: the config
shape, its defaults, and the period arithmetic. It holds NO database access and NO side effects beyond
one optional best-effort config read (`load`), so both the router (which acts on it) and
`import_audit` (which reports on it) share ONE implementation.

CONTRACT COMPLIANCE
  RULE ONE  — `load()` takes the org_id from the caller and scopes the read with `.eq("org_id", ...)`.
              Nothing here writes; nothing here knows a house-org constant.
  RULE TWO  — the window length is CONFIG (`commcalc.commission_org_config.sales_derive_grace`,
              migration 266), never a hard-coded 3. No tenant or carrier name is branched on.
  DEGRADES  — migration 266 unapplied ⇒ the column read raises ⇒ `load()` returns the CODE DEFAULT
              below and the feature still works; a tenant simply cannot tune it yet.
  MONEY     — nothing here computes or moves a payout. Deriving does NOT recompute (money moves
              attended); it only refreshes the calculator's INPUT for a period a human later runs.
"""
from datetime import timedelta

# ── CODE DEFAULT (documented; every field overridable per tenant via migration 266) ──────────────────
#   enabled  — grace re-derive on/off for this tenant.
#   days     — how many days into the NEW month the PRIOR month is still re-derived. 3 = the 1st, 2nd
#              and 3rd. The observed luxelink finalization tail was under 5 hours; 3 days is ~14x that
#              and still well inside "nobody has paid the month yet".
#   retain   — the shrink guard for GRACE runs only (None = use the caller's normal guard, 0.85). A
#              tenant that hand-uploads the authoritative 78-column monthly file for a closed month can
#              set 1.0, which refuses any grace run that would end up with fewer lines than it found.
DEFAULT = {"enabled": True, "days": 3, "retain": None}

# A "grace window" longer than half a month is not a grace window — it is a standing re-derive of a
# month people are already being paid from. Clamped, and used by the sweep as a cheap short-circuit so
# there is ZERO extra work for the back half of every month.
MAX_GRACE_DAYS = 15

# What a grace-window upload_trace row says, so the trace stays self-explanatory (dispatch item 6).
GRACE_NOTE = "month-boundary grace re-derive"

CONFIG_TABLE = "commission_org_config"
CONFIG_COLUMN = "sales_derive_grace"


def _as_dict(raw):
    """A stored jsonb value that may arrive as a dict, a JSON string, or None → a dict or None. PURE."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            import json
            v = json.loads(raw)
            return v if isinstance(v, dict) else None
        except Exception:
            return None
    return None


def resolve(raw):
    """A stored `commission_org_config.sales_derive_grace` value → the EFFECTIVE config. PURE.

    None / missing / unparseable ⇒ the code DEFAULT above (grace ON, 3 days). An explicit
    {"enabled": false} — or any days <= 0 — turns the whole thing off, which restores today's behaviour
    exactly (current month only, forever)."""
    cfg = dict(DEFAULT)
    d = _as_dict(raw)
    if d is None:
        return cfg
    if "enabled" in d:
        cfg["enabled"] = bool(d.get("enabled"))
    if "days" in d:
        try:
            cfg["days"] = int(d.get("days"))
        except Exception:
            pass
    if "retain" in d:
        try:
            r = d.get("retain")
            cfg["retain"] = None if r in (None, "") else float(r)
        except Exception:
            cfg["retain"] = None
    # clamps — a typo can never make the sweep re-derive a month somebody is being paid from, and can
    # never make the shrink guard permissive (a retain BELOW the normal 0.85 would weaken it).
    cfg["days"] = max(0, min(MAX_GRACE_DAYS, cfg["days"]))
    if cfg["retain"] is not None:
        cfg["retain"] = max(0.85, min(1.0, cfg["retain"]))
    if cfg["days"] <= 0:
        cfg["enabled"] = False
    return cfg


def prior_period_label(now):
    """The month BEFORE `now`, in the 'Month YYYY' spelling raw_sales stores. PURE.

    Same idiom as import_audit.p_sales_export: back up to the 1st, step one day earlier."""
    return (now.replace(day=1) - timedelta(days=1)).strftime("%B %Y")


def current_period_label(now):
    """`now`'s own month in the 'Month YYYY' spelling — the value router._ftp_current_period() returns.
    PURE. Kept here only so the period arithmetic reads as one thing; the router still calls its own
    function on the current-month path so that path is byte-identical to before."""
    return now.strftime("%B %Y")


def window_open(now, cfg):
    """Is `now` inside this tenant's month-boundary grace window? PURE.

    True on day-of-month 1..days of any month (days=3 ⇒ the 1st, 2nd, 3rd). False when disabled."""
    c = cfg if isinstance(cfg, dict) and "enabled" in cfg else resolve(cfg)
    return bool(c.get("enabled")) and int(c.get("days") or 0) > 0 and now.day <= int(c["days"])


def periods(now, cfg):
    """The period(s) the AUTOMATIC derivation must cover for this tenant at `now`. PURE.

    Always [current]; plus [prior] while inside the grace window. The current month is ALWAYS FIRST and
    is always exactly what the code derived before this module existed, so the pre-existing path (and
    its pre-existing current-month auto-recompute) is untouched."""
    cur = current_period_label(now)
    return [cur, prior_period_label(now)] if window_open(now, cfg) else [cur]


def plan(now, cfg, base_retain=0.85):
    """The derivation PLAN: [(period, grace, retain), ...]. PURE.

    Entry 0 is the current month with grace=False and the caller's normal retain guard — identical to
    today. Any further entry is a grace re-derive of a CLOSED month and carries the tenant's grace
    retain (or the normal one when unset)."""
    ps = periods(now, cfg)
    c = cfg if isinstance(cfg, dict) and "enabled" in cfg else resolve(cfg)
    gret = c.get("retain") or base_retain
    return [(ps[0], False, base_retain)] + [(p, True, gret) for p in ps[1:]]


def enumeration_needed(now):
    """Cheap short-circuit for the org-agnostic sweep: is it even POSSIBLE that some tenant's grace
    window is open right now? PURE. False for the back half of every month, so the extra
    prior-period org enumeration costs nothing for ~half the year."""
    return now.day <= MAX_GRACE_DAYS


def load(client, org_id):
    """This tenant's EFFECTIVE grace config, org-scoped and best-effort.

    Migration 266 unapplied (or any transient read error) ⇒ the code DEFAULT, so the fix works before
    the SQL is run and the SQL only makes it tunable (RULE TWO / contract §5 graceful degradation)."""
    try:
        rows = (client.schema("commcalc").table(CONFIG_TABLE).select(CONFIG_COLUMN)
                .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return resolve(None)
    return resolve(rows[0].get(CONFIG_COLUMN) if rows else None)
