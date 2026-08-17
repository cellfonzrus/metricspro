"""Denied-Appeal Commission Recovery — API.

Rebuild the recovery ledger (scan denied appeals → find later payment/active evidence → bucket), read
the buckets, and generate a claim of the recoverable devices (with per-device rebuttals) to submit to
the carrier. Config-driven (window / look-back / evidence / categories / match keys / recipients).
"""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.core.database import get_supabase
from app.core.config import settings
from app.core.run_secret import verify_notify_secret
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


# ── Claim document (server-rendered PDF/XLSX) + delivery ───────────────────────────────────────────
def _claim_lines(client, org_id, claim_id):
    lines = (client.schema("commcalc").table("appeal_recovery").select("*")
             .eq("org_id", org_id).eq("claim_id", claim_id).limit(5000).execute().data) or []
    for r in lines:
        r["rebuttal"] = engine.rebuttal_for(r)
    return lines


def _claim_payload(claim, lines):
    cols = [{"header": "Store", "key": "store"}, {"header": "Device", "key": "device_model"},
            {"header": "IMEI", "key": "imei"}, {"header": "MDN", "key": "mdn"},
            {"header": "Denial reason", "key": "category"}, {"header": "Denied", "key": "denied_date"},
            {"header": "Owed", "key": "owed_amount", "money": True, "align": "right"},
            {"header": "Why the commission is owed (rebuttal)", "key": "rebuttal"}]
    return {"title": "Appeal Commission Claim — carrier resubmission",
            "subtitle": f"{claim.get('period_label')} · {claim.get('device_count')} devices · "
                        f"${engine._safe_float(claim.get('total_amount')):,.2f} total",
            "filename": f"appeal-claim-{str(claim.get('id'))[:8]}",
            "sheets": [{"name": "Claim", "columns": cols, "rows": lines}]}


def _render_claim(claim, lines, fmt="pdf"):
    from app.modules.notify import render as notify_render
    return notify_render.render(_claim_payload(claim, lines), fmt)  # (bytes, filename, mime)


async def _deliver_claim(cfg, claim, lines):
    """Best-effort deliver the rendered claim (PDF) to the config recipients over email + WhatsApp."""
    recips = cfg.get("recipients") or []
    if not recips or not lines:
        return []
    try:
        data, filename, mime = _render_claim(claim, lines, "pdf")
    except Exception:
        data, filename, mime = None, "appeal-claim.pdf", "application/pdf"
    total = engine._safe_float(claim.get("total_amount"))
    subject = f"[MetricsPro] Appeal commission claim — {claim.get('device_count')} devices, ${total:,.2f}"
    html = (f"<p>Weekly appeal-recovery claim: <b>{claim.get('period_label')}</b>.</p>"
            f"<p>{claim.get('device_count')} recoverable devices, total <b>${total:,.2f}</b>. "
            f"Per-device rebuttals are in the attached claim.</p>")
    delivered = []
    for r in recips:
        email = (r.get("email") or "").strip()
        wa = (r.get("whatsapp") or "").strip()
        if email:
            try:
                from app.modules.notify.channels import email_resend
                if email_resend.is_configured():
                    att = [(filename, data, mime)] if data else None
                    await email_resend.send_email(email, subject, html, attachments=att)
                    delivered.append(f"email:{email}")
            except Exception:
                pass
        if wa and data:
            try:
                from app.modules.notify.channels import whatsapp_meta
                if whatsapp_meta.is_configured():
                    await whatsapp_meta.send_document(wa, data, mime, filename, subject)
                    delivered.append(f"whatsapp:{wa}")
            except Exception:
                pass
    return delivered


@router.get("/claims/{claim_id}/document")
def claim_document(claim_id: str, fmt: str = "pdf", org_id: str = ORG_ID):
    """Download the rendered claim form (pdf|xlsx)."""
    client = sb()
    rows = (client.schema("commcalc").table("appeal_claim").select("*")
            .eq("org_id", org_id).eq("id", claim_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    lines = _claim_lines(client, org_id, claim_id)
    data, filename, mime = _render_claim(rows[0], lines, "xlsx" if fmt == "xlsx" else "pdf")
    return Response(content=data, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/claims/{claim_id}/send")
async def send_claim(claim_id: str, body: dict = None, org_id: str = ORG_ID):
    """Deliver a claim now. Uses the config recipients, or override with body {recipients:[{email,whatsapp}]}."""
    client = sb()
    rows = (client.schema("commcalc").table("appeal_claim").select("*")
            .eq("org_id", org_id).eq("id", claim_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(404, "not found")
    cfg = _get_cfg(client, org_id)
    if body and body.get("recipients"):
        cfg = {**cfg, "recipients": body["recipients"]}
    lines = _claim_lines(client, org_id, claim_id)
    delivered = await _deliver_claim(cfg, rows[0], lines)
    return {"delivered": delivered, "count": len(delivered)}


# ── Weekly auto-claim (cron via pg_cron → POST /run-due with x-notify-secret) ──────────────────────
def _parse_dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _next_weekly(now, dow, hour):
    """Next datetime strictly after `now` on config weekday `dow` (0=Sun … 6=Sat) at `hour` (UTC)."""
    py_target = (int(dow) - 1) % 7           # config 0=Sun → python weekday() Sun=6
    cand = now.replace(hour=int(hour) % 24, minute=0, second=0, microsecond=0)
    cand += timedelta(days=(py_target - cand.weekday()) % 7)
    if cand <= now:
        cand += timedelta(days=7)
    return cand


@router.post("/run-due")
async def run_due(request: Request):
    """Weekly driver: for each enabled config whose next_run_at is due, rebuild the ledger, generate a
    claim, deliver it, and advance next_run_at. Scheduled by pg_cron with the x-notify-secret header."""
    # Fail CLOSED: the old form only checked when the secret was SET, so an unset secret let anyone hit
    # this sweep. verify_notify_secret returns False when nothing is configured (Spec §4, item 9).
    if not verify_notify_secret(request.headers.get("x-notify-secret", "")):
        raise HTTPException(403, "forbidden")
    client = sb()
    today, now = _today(), datetime.now(timezone.utc)
    cfgs = (client.schema("commcalc").table("appeal_recovery_config").select("*")
            .eq("enabled", True).limit(1000).execute().data) or []
    ran = []
    for cfg in cfgs:
        nr = _parse_dt(cfg.get("next_run_at"))
        if nr and nr > now:
            continue  # not due yet
        org = cfg["org_id"]
        summary = engine.build_recovery_ledger(client, org, cfg, today)
        claim, lines = _generate_claim(client, org, cfg, today)
        delivered = await _deliver_claim(cfg, claim, lines) if claim else []
        nxt = _next_weekly(now, cfg.get("weekly_day_of_week") or 1, cfg.get("weekly_hour") or 8)
        client.schema("commcalc").table("appeal_recovery_config").update(
            {"next_run_at": nxt.isoformat(), "last_run_at": now.isoformat()}).eq("org_id", org).execute()
        ran.append({"org_id": org, "summary": summary,
                    "claim_id": claim and claim.get("id"), "delivered": delivered})
    return {"ran": ran, "count": len(ran)}
