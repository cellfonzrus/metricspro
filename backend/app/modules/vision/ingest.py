"""Edge-analyzer ingest — authenticating the analyzer, and deciding what it is allowed to send.

THE TRUST BOUNDARY
──────────────────
The edge analyzer runs on hardware this backend does not control, on a store's network, and it posts
numbers that end up in a manager's coaching conversation. So it authenticates like a machine, not like
a person: a per-agent secret, an HMAC-SHA256 over `timestamp.raw_body`, and a bounded clock skew.
Not a bearer token in a header — a replayable string on a box sitting in a stockroom is a bad idea,
and a signature over the body means a tampered payload fails even if the string leaks.

WHAT GETS REFUSED, AND WHY THAT MATTERS MORE THAN WHAT GETS ACCEPTED
────────────────────────────────────────────────────────────────────
`normalize_batch` is the choke point where a well-behaved analyzer and a misconfigured one are told
apart. It drops, with a counted reason:

  * anything for a camera this tenant does not own (cross-tenant, by construction)
  * traffic from a camera the operator did not mark as the entrance
  * ANY transcript segment whose speaker is not the employee — the customer's speech never lands
  * ANY transcript segment for an employee without recorded consent
  * ANY transcript at all when the deployment audio kill switch is off, or the tenant's audio switch
    is off, or the camera's audio switch is off

The rejects are returned with counts rather than silently swallowed, so the settings page can show
"1,240 segments rejected: consent_missing" — which is how an operator discovers they enabled audio
before collecting consent, instead of discovering it in a deposition.

Redaction runs HERE, before the row is built, so the unredacted transcript never reaches storage.
"""
import hashlib
import hmac
import time
from datetime import datetime, timezone

from app.modules.vision import behavior as B
from app.modules.vision import config as C

MAX_SKEW_SECONDS = 300          # 5 minutes each way — a store box with no NTP still works
MAX_EVENTS_PER_BATCH = 5000


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """The signature the analyzer computes and this backend recomputes. Signing `timestamp.body`
    rather than the body alone is what makes a captured request unreplayable once the skew lapses."""
    msg = f"{timestamp}.".encode() + (body or b"")
    return hmac.new((secret or "").encode(), msg, hashlib.sha256).hexdigest()


def verify(secret: str, timestamp: str, body: bytes, signature: str,
           max_skew: int = MAX_SKEW_SECONDS, now=None) -> tuple:
    """(ok, reason). Constant-time compare — a timing oracle on an HMAC check is a solved problem and
    `==` on a hex digest is how you reopen it."""
    if not secret or not signature or not timestamp:
        return False, "missing_signature"
    try:
        ts = int(float(timestamp))
    except (TypeError, ValueError):
        return False, "bad_timestamp"
    now = int(now if now is not None else time.time())
    if abs(now - ts) > max_skew:
        return False, "stale_timestamp"
    if not hmac.compare_digest(sign(secret, timestamp, body), (signature or "").strip().lower()):
        return False, "bad_signature"
    return True, "ok"


def _local_parts(value, fallback_iso=None):
    """(local_date, local_hour) — the analyzer sends the STORE's local date/hour because it knows the
    store's timezone and this backend would have to look it up per row. Falling back to the UTC
    timestamp is wrong past midnight, so it is only ever a last resort for a malformed payload."""
    if isinstance(value, dict):
        d, h = value.get("local_date"), value.get("local_hour")
        if d is not None and h is not None:
            try:
                return str(d)[:10], int(h)
            except (TypeError, ValueError):
                pass
    try:
        dt = datetime.fromisoformat(str(fallback_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        dt = datetime.now(timezone.utc)
    return dt.date().isoformat(), dt.hour


def normalize_batch(payload: dict, cameras_by_name: dict, cfg: dict, consent_by_employee: dict,
                    org_id: str, agent: dict) -> dict:
    """Turn one analyzer POST into the rows to insert, plus a reject tally.

    `cameras_by_name` maps SDM device_name -> the tenant's camera row (this is also the ownership
    check — a device this tenant has not registered simply is not in the map).
    `consent_by_employee` maps employee_id -> the consent row.
    """
    rejects = {}

    def reject(reason):
        rejects[reason] = rejects.get(reason, 0) + 1

    agent_store = (agent or {}).get("store_code")
    traffic, presence, transcripts = [], [], []
    events = (payload or {}).get("events") or []
    if len(events) > MAX_EVENTS_PER_BATCH:
        events = events[:MAX_EVENTS_PER_BATCH]
        rejects["batch_truncated"] = 1

    for ev in events:
        if not isinstance(ev, dict):
            reject("malformed")
            continue
        kind = (ev.get("kind") or "").strip().lower()
        cam = cameras_by_name.get((ev.get("device_name") or "").strip())
        if not cam:
            reject("unknown_camera")
            continue
        store = cam.get("store_code") or agent_store
        if not store:
            reject("camera_has_no_store")
            continue
        # An agent pinned to a store may only speak for that store. Without this, one compromised
        # store box could write traffic and coaching numbers for every store in the tenant.
        if agent_store and store != agent_store:
            reject("agent_store_mismatch")
            continue

        if kind == "traffic":
            if not C.camera_allows(cfg, cam, "traffic"):
                reject("traffic_not_enabled")
                continue
            direction = (ev.get("direction") or "").strip().lower()
            if direction not in ("in", "out"):
                reject("bad_direction")
                continue
            d, h = _local_parts(ev, ev.get("occurred_at"))
            traffic.append({
                "org_id": org_id, "store_code": store, "camera_id": cam.get("id"),
                "occurred_at": ev.get("occurred_at"), "local_date": d, "local_hour": h,
                "direction": direction, "track_key": ev.get("track_key"),
                "confidence": _f(ev.get("confidence")),
            })

        elif kind == "presence":
            if not C.camera_allows(cfg, cam, "heatmap"):
                reject("heatmap_not_enabled")
                continue
            d, h = _local_parts(ev, ev.get("sampled_at"))
            for cell in ev.get("cells") or []:
                try:
                    cx, cy = int(cell.get("x")), int(cell.get("y"))
                except (TypeError, ValueError):
                    reject("bad_cell")
                    continue
                presence.append({
                    "org_id": org_id, "store_code": store, "camera_id": cam.get("id"),
                    "sampled_at": ev.get("sampled_at"), "local_date": d, "local_hour": h,
                    "cell_x": cx, "cell_y": cy,
                    "occupancy": _f(cell.get("occupancy")) or 0.0,
                })

        elif kind == "transcript":
            # Five gates, in the order that fails cheapest first. Every one of them is a "no".
            if C.AUDIO_GLOBALLY_DISABLED:
                reject("audio_kill_switch")
                continue
            if not C.camera_allows(cfg, cam, "audio_analytics"):
                reject("audio_not_enabled")
                continue
            speaker = (ev.get("speaker") or "employee").strip().lower()
            if speaker != "employee":
                reject("not_employee_speech")     # the customer's half is never stored
                continue
            emp = ev.get("employee_id")
            if not emp:
                reject("no_employee")
                continue
            allowed, reason = C.consent_ok(cfg, consent_by_employee.get(str(emp)))
            if not allowed:
                reject(reason)
                continue
            text, redactions = B.redact(ev.get("text") or "")
            if not text.strip():
                reject("empty_text")
                continue
            d, h = _local_parts(ev, ev.get("started_at"))
            transcripts.append({
                "org_id": org_id, "store_code": store, "camera_id": cam.get("id"),
                "employee_id": str(emp), "started_at": ev.get("started_at"),
                "ended_at": ev.get("ended_at"), "local_date": d,
                "duration_s": _f(ev.get("duration_s")), "speaker": "employee",
                "text": text[:4000], "language": (ev.get("language") or "en")[:8],
                "asr_confidence": _f(ev.get("asr_confidence")), "redactions": redactions,
                "visit_id": ev.get("visit_id"),
                "signals": {"elapsed_s": _f(ev.get("elapsed_s"))},
            })

        else:
            reject("unknown_kind")

    return {"traffic": traffic, "presence": presence, "transcripts": transcripts,
            "rejected": rejects,
            "accepted": len(traffic) + len(presence) + len(transcripts)}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
