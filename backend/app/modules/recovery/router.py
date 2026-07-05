"""Denied-Appeal Commission Recovery — API.

Rebuild the recovery ledger (scan denied appeals → find later payment/active evidence → bucket), read
the buckets, and generate a claim of the recoverable devices (with per-device rebuttals) to submit to
the carrier. Config-driven (window / look-back / evidence / categories / match keys / recipients).
"""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException

from app.core.database import get_supabase
from . import engine

router = APIRouter(prefix="/recovery", tags=["recovery"])
ORG_ID = "00000000-0000-0000-0000-000000000001"
_CFG_FIELDS = ("clawback_window_days", "lookback_days", "evidence_mode", "match_mdn", "match_imei",
               "recoverable_categories", "weekly_day_of_week", "weekly_hour", "enabled",
               "recipients", "payment_source")


def sb():
    return get_supabase()


def _today():
    return datetime.now(timezone.utc).date()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get_cfg(client, org_id):
    rows = (client.schema("commcalc").table("appeal_recovery_config").select("*")
            .eq("org_id", org_id).limit(1).execute().data) or []
    if rows:
        return rows[0]
    client.schema("commcalc").table("appeal_recovery_config").upsert(
        {"org_id": org_id}, on_conflict="org_id").execute()
    return (client.schema("commcalc").table("appeal_recovery_config").select("*")
            .eq("org_id", org_id).limit(1).execute().data or [{"org_id": org_id}])[0]


@router.get("/config")
def get_config(org_id: str = ORG_ID):
    return {"config": _get_cfg(sb(), org_id), "appeal_categories": engine.APPEAL_CATEGORIES}


@router.put("/config")
def put_config(body: dict, org_id: str = ORG_ID):
    client = sb()
    _get_cfg(client, org_id)  # ensure the row exists
    upd = {k: body[k] for k in _CFG_FIELDS if k in body}
    upd["updated_at"] = _now_iso()
    client.schema("commcalc").table("appeal_recovery_config").update(upd).eq("org_id", org_id).execute()
    return {"config": _get_cfg(client, org_id)}


@router.post("/rebuild")
def rebuild(org_id: str = ORG_ID):
    """Scan denied appeals + rebuild the recovery ledger as of today. May take a few seconds."""
    client = sb()
    cfg = _get_cfg(client, org_id)
    summary = engine.build_recovery_ledger(client, org_id, cfg, _today())
    client.schema("commcalc").table("appeal_recovery_config").update(
        {"last_run_at": _now_iso()}).eq("org_id", org_id).execute()
    return {"summary": summary}


@router.get("/ledger")
def ledger(status: str = "", org_id: str = ORG_ID):
    """The recovery ledger + bucket totals. Optional ?status= filters the returned rows; buckets always
    reflect ALL statuses. Recoverable/expired rows carry a carrier-facing rebuttal."""
    client = sb()
    rows_all = (client.schema("commcalc").table("appeal_recovery").select("*")
                .eq("org_id", org_id).limit(20000).execute().data) or []
    buckets = {}
    for r in rows_all:
        b = buckets.setdefault(r.get("status") or "unknown", {"count": 0, "owed": 0.0})
        b["count"] += 1
        b["owed"] += engine._safe_float(r.get("owed_amount"))
    for k in buckets:
        buckets[k]["owed"] = round(buckets[k]["owed"], 2)
    rows = [r for r in rows_all if (not status or r.get("status") == status)]
    rows.sort(key=lambda r: -engine._safe_float(r.get("owed_amount")))
    for r in rows:
        if r.get("status") in ("recoverable", "expired"):
            r["rebuttal"] = engine.rebuttal_for(r)
    return {"rows": rows[:2000], "buckets": buckets,
            "recoverable_amount": buckets.get("recoverable", {}).get("owed", 0.0)}


def _generate_claim(client, org_id, cfg, today):
    lookback = int(cfg.get("lookback_days") or 60)
    cutoff = (today - timedelta(days=lookback)).isoformat()
    rows = (client.schema("commcalc").table("appeal_recovery").select("*")
            .eq("org_id", org_id).eq("status", "recoverable").is_("claim_id", "null")
            .gte("denied_date", cutoff).limit(5000).execute().data) or []
    if not rows:
        return None, []
    total = round(sum(engine._safe_float(r.get("owed_amount")) for r in rows), 2)
    label = f"week of {today.isoformat()} ({lookback}d look-back)"
    claim = {"org_id": org_id, "generated_at": _now_iso(), "period_label": label,
             "lookback_days": lookback, "device_count": len(rows), "total_amount": total,
             "status": "draft"}
    res = client.schema("commcalc").table("appeal_claim").insert(claim).execute()
    cid = (res.data or [claim])[0].get("id")
    ids = [r["id"] for r in rows]
    for i in range(0, len(ids), 200):
        client.schema("commcalc").table("appeal_recovery").update(
            {"claim_id": cid}).in_("id", ids[i:i + 200]).execute()
    for r in rows:
        r["rebuttal"] = engine.rebuttal_for(r)
    return {**claim, "id": cid}, rows


@router.post("/claim")
def make_claim(org_id: str = ORG_ID):
    """Roll the currently-recoverable devices (denied within the look-back, not yet claimed) into a new
    claim batch with per-device rebuttals. Idempotent-ish: already-claimed rows are excluded."""
    client = sb()
    cfg = _get_cfg(client, org_id)
    claim, lines = _generate_claim(client, org_id, cfg, _today())
    if not claim:
        return {"claim": None, "message": "No new recoverable devices in the look-back window."}
    return {"claim": claim, "lines": lines}


@router.get("/claims")
def list_claims(org_id: str = ORG_ID):
    client = sb()
    claims = (client.schema("commcalc").table("appeal_claim").select("*")
              .eq("org_id", org_id).order("created_at", desc=True).limit(200).execute().data) or []
    return {"claims": claims}


@router.get("/claims/{claim_id}")
def get_claim(claim_id: str, org_id: str = ORG_ID):
    client = sb()
    rows = (client.schema("commcalc").table("appeal_claim").select("*")
            .eq("org_id", org_id).eq("id", claim_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    lines = (client.schema("commcalc").table("appeal_recovery").select("*")
             .eq("org_id", org_id).eq("claim_id", claim_id).limit(5000).execute().data) or []
    for r in lines:
        r["rebuttal"] = engine.rebuttal_for(r)
    return {"claim": rows[0], "lines": lines}


@router.patch("/claims/{claim_id}")
def update_claim(claim_id: str, body: dict, org_id: str = ORG_ID):
    """Move a claim through submitted/paid/rejected + notes."""
    client = sb()
    upd = {k: body[k] for k in ("status", "form_ref", "notes") if k in body}
    if not upd:
        raise HTTPException(400, "nothing to update")
    r = (client.schema("commcalc").table("appeal_claim").update(upd)
         .eq("org_id", org_id).eq("id", claim_id).execute())
    return {"claim": (r.data or [{}])[0]}
