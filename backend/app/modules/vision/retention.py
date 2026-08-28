"""Vision data retention — the purge that makes the rest of the module defensible.

A camera-analytics feature is only as safe as its DELETE. Collecting occupancy samples and employee
transcripts is a defensible business practice; keeping them forever is how it becomes a liability,
because every one of those rows is discoverable and none of them is useful after the coaching
conversation it informed. So the retention windows are tenant-configured (migration 900 defaults:
7 days of per-sample occupancy, 30 of transcripts, 90 of visits, 400 of rolled-up aggregates) and
this module enforces them.

DESIGNED TO BE RUN REPEATEDLY AND OUT OF ORDER. `purge(dry_run=True)` reports what WOULD go without
touching anything, every delete is bounded by a cutoff timestamp rather than a row list, and the
whole thing is idempotent — running it twice deletes nothing the second time. Wire it to the same
scheduler that runs the other daily sweeps, or call POST /vision/retention/purge from an admin.

WHAT SURVIVES A PURGE, DELIBERATELY:
  * `core.vision_heat_cell` and `core.vision_behavior_score` — these are AGGREGATES with no per-person
    and no per-track detail. Keeping a year of them is what makes "this August vs last August" answer
    at all, and there is nothing in a person-seconds-per-grid-cell number to protect.
  * `core.vision_audit` — the record of who watched which camera. Purging the audit trail alongside
    the data it audits would defeat the purpose of having one.
"""
from datetime import datetime, timezone

from app.modules.vision import config as C

# (config key, table, timestamp column) — the order is oldest-and-cheapest first so a partial run
# under a timeout still clears the highest-volume table.
TARGETS = (
    ("presence", "vision_presence_sample", "sampled_at"),
    ("transcript", "vision_transcript", "started_at"),
    ("visit", "vision_visit", "entered_at"),
    # mig 910. Sits above the aggregates because it carries the most per-person detail in the module
    # and has the shortest window; a partial run under a timeout should have cleared it already.
    ("activity", "vision_activity_bucket", "bucket_start"),
    ("coverage", "vision_coverage_bucket", "bucket_start"),
    ("heat", "vision_heat_cell", "updated_at"),
    ("score", "vision_behavior_score", "computed_at"),
)

# key -> timestamp column, so the delete does not have to re-derive it from TARGETS.
TS_COL = {key: col for key, _table, col in TARGETS}


def plan(client, org_id: str, cfg: dict = None, now=None) -> dict:
    """What a purge would remove right now, per table, without removing it.

    Counts are best-effort: a missing table (migration 900 not run) reports `available: false` for
    that target rather than failing the whole plan, because an operator asking "what would this
    delete" during setup should get an answer, not a stack trace."""
    cfg = cfg or C.resolve_config(client, org_id)
    cutoffs = C.retention_cutoffs(cfg, now=now or datetime.now(timezone.utc))
    out = {"org_id": org_id, "targets": [], "total": 0}
    for key, table, col in TARGETS:
        cutoff = cutoffs.get(key)
        entry = {"key": key, "table": table, "cutoff": cutoff,
                 "retention_days": cfg.get(f"{key}_retention_days"), "count": 0, "available": True}
        if not cutoff:
            entry["skipped"] = "retention_disabled"     # operator set 0 days = keep indefinitely
            out["targets"].append(entry)
            continue
        try:
            res = (client.schema("core").table(table).select("id", count="exact")
                   .eq("org_id", org_id).lt(col, cutoff).limit(1).execute())
            entry["count"] = int(getattr(res, "count", 0) or 0)
            out["total"] += entry["count"]
        except Exception as e:
            entry["available"] = False
            entry["error"] = str(e)[:200]
        out["targets"].append(entry)
    return out


def purge(client, org_id: str, cfg: dict = None, dry_run: bool = True, actor: str = None,
          now=None) -> dict:
    """Delete everything past its retention window. Returns the same shape as `plan()` plus
    `deleted` per target. `dry_run=True` is the DEFAULT — a destructive operation should have to be
    asked for twice, and the router's endpoint requires an explicit `confirm`."""
    cfg = cfg or C.resolve_config(client, org_id)
    result = plan(client, org_id, cfg=cfg, now=now)
    result["dry_run"] = dry_run
    result["deleted_total"] = 0
    if dry_run:
        return result

    for entry in result["targets"]:
        entry["deleted"] = 0
        if not entry.get("available") or not entry.get("cutoff") or not entry.get("count"):
            continue
        try:
            (client.schema("core").table(entry["table"]).delete()
             .eq("org_id", org_id).lt(TS_COL[entry["key"]], entry["cutoff"]).execute())
            entry["deleted"] = entry["count"]
            result["deleted_total"] += entry["count"]
        except Exception as e:
            entry["error"] = str(e)[:200]

    _audit(client, org_id, actor, result)
    return result


def _audit(client, org_id, actor, result):
    """A purge is itself an auditable event — best-effort, and never allowed to fail the purge."""
    try:
        client.schema("core").table("vision_audit").insert({
            "org_id": org_id, "actor": actor or "system", "action": "purge",
            "target": "retention",
            "detail": {"deleted_total": result.get("deleted_total"),
                       "targets": [{k: t.get(k) for k in ("key", "cutoff", "deleted")}
                                   for t in result.get("targets") or []]},
        }).execute()
    except Exception:
        pass
