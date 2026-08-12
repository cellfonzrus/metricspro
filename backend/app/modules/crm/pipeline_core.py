"""CRM pipeline core — the PURE logic behind the sales pipeline and follow-up engine.

Everything here is a plain function over plain dicts: no database, no network, no clock of its own
(callers pass `now`). That is deliberate — the reminder sweep, the assignment router and the
disposition rules are exactly the code that must not be "verified" by eyeballing production, so they
live here and are proven offline by `backend/harness_crm_pipeline.py`.

The router (`crm/router.py`) does the I/O and calls into this module for every decision.

Conventions:
  • `now` is always an aware UTC datetime.
  • Business-hours `days` use the JavaScript getDay() convention: 0 = Sunday … 6 = Saturday, which
    is what the crm_config.business_hours seed writes.
  • employee ids are the BUSINESS ids (`storeops.employees.employee_id`, text), not row uuids —
    the same identifier targets/closing/payroll already key on.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone

try:                                    # py3.9+ stdlib; falls back to UTC if tzdata is missing
    from zoneinfo import ZoneInfo
except Exception:                       # pragma: no cover - environment without tzdata
    ZoneInfo = None


# ── defaults (mirrored by the crm_config column defaults in migration 800) ────────────────────────
DEFAULT_CONFIG = {
    "timezone": "America/New_York",
    "business_hours": {"start": "09:00", "end": "20:00", "days": [1, 2, 3, 4, 5, 6, 0]},
    "stale_lead_hours": 48,
    "escalate_after_hours": 24,
    "miss_grace_hours": 4,
    "require_disposition_on_close": 1,
    "duplicate_match": "phone",
    "reminder_channels": ["in_app", "email"],
    "auto_convert_on_won": True,
    "max_open_leads_per_rep": None,
    "daily_logging_reminder_hour": 18,
    "lookup_requires_grant": True,
}


def resolve_config(row) -> dict:
    """Merge a crm_config row onto the defaults. A missing table / missing row (the migration has
    not run yet) yields the pure defaults — the module still works, it just isn't tunable."""
    cfg = dict(DEFAULT_CONFIG)
    for k, v in (row or {}).items():
        if v is None:
            continue
        cfg[k] = v
    # ints that arrive as strings from a JSON body should not poison the arithmetic later
    for k in ("stale_lead_hours", "escalate_after_hours", "miss_grace_hours",
              "daily_logging_reminder_hour"):
        try:
            cfg[k] = max(0, int(cfg.get(k) or 0))
        except (TypeError, ValueError):
            cfg[k] = DEFAULT_CONFIG[k]
    if not isinstance(cfg.get("business_hours"), dict):
        cfg["business_hours"] = dict(DEFAULT_CONFIG["business_hours"])
    if not isinstance(cfg.get("reminder_channels"), list):
        cfg["reminder_channels"] = list(DEFAULT_CONFIG["reminder_channels"])
    return cfg


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Identity normalization — the join key for dedupe AND for Customer 360
# ══════════════════════════════════════════════════════════════════════════════════════════════

_DIGITS = re.compile(r"[^0-9]")


def normalize_phone(value) -> str:
    """The 10-digit national number — the ONE key every phone-bearing source in this system agrees on.

    `raw_sales.mdn`, `pos.customers.phone_primary`, `pos.activations.cell_number` and
    `asset_ledger.phone_number` are typed and imported by different hands: "(516) 555-0134",
    "+1 516 555 0134", "1-516-555-0134", "5165550134 x22".

    Rules, in order:
      • strip to digits;
      • fewer than 7 digits → "" (refuse to half-match rather than collide on a fragment);
      • exactly 11 starting with 1 → drop the US country code;
      • MORE than 10 → keep the FIRST 10 (after any leading 1). An extension is written at the END,
        so a naive "last 10" turns 5165550134 x22 into 6555013422 — a key that matches nothing and
        fails silently, which on a lookup reads as "we have never seen this customer".

    ⚠️ The generated column `core.crm_lead.phone_norm` (migration 800) implements this SAME rule in
    SQL. The two MUST stay in step: a lead is stored with the SQL key and looked up with this one.
    """
    digits = _DIGITS.sub("", str(value or ""))
    if len(digits) < 7:          # too short to be a real number — refuse rather than half-match
        return ""
    if len(digits) == 11 and digits[0] == "1":
        return digits[1:]
    if len(digits) > 10:
        return digits[1:11] if digits[0] == "1" else digits[:10]
    return digits


def mask_phone(value) -> str:
    """'••••0134' — what the audit log stores. The full number is never written to the trail."""
    n = normalize_phone(value)
    return f"••••{n[-4:]}" if n else "••••"


def normalize_email(value) -> str:
    return str(value or "").strip().lower()


def display_name(lead: dict) -> str:
    parts = [str(lead.get("first_name") or "").strip(), str(lead.get("last_name") or "").strip()]
    name = " ".join(p for p in parts if p).strip()
    return name or str(lead.get("company_name") or "").strip() or (lead.get("phone") or "Unknown")


def is_duplicate(lead: dict, existing: dict, mode: str) -> bool:
    """Duplicate policy, tenant-configurable. 'none' disables dedupe entirely (some tenants run
    outbound lists where the same number legitimately re-enters the pipeline)."""
    mode = (mode or "phone").lower()
    if mode == "none":
        return False
    p_new, p_old = normalize_phone(lead.get("phone")), normalize_phone(existing.get("phone"))
    e_new, e_old = normalize_email(lead.get("email")), normalize_email(existing.get("email"))
    phone_hit = bool(p_new) and p_new == p_old
    email_hit = bool(e_new) and e_new == e_old
    if mode == "phone":
        return phone_hit
    if mode == "email":
        return email_hit
    if mode == "both":           # "both" = either identifier matches (a stricter AND would let a
        return phone_hit or email_hit   # same-person-different-email walk-in slip through)
    return phone_hit


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Business hours
# ══════════════════════════════════════════════════════════════════════════════════════════════

def _tz(name):
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(str(name or "UTC"))
    except Exception:
        return timezone.utc


def _parse_hhmm(value, fallback: time) -> time:
    try:
        h, m = str(value).split(":")[:2]
        return time(int(h) % 24, int(m) % 60)
    except Exception:
        return fallback


def shift_to_business_hours(when: datetime, cfg: dict) -> datetime:
    """Move a moment forward to the next instant the store is actually open.

    A cadence step that lands at 03:00 is not a 3 a.m. phone call — it is a 9 a.m. phone call. This
    only ever moves time FORWARD, never earlier, so a step can't jump ahead of the one before it.
    Returns UTC. A malformed business_hours block degrades to "leave it alone" rather than raising.
    """
    hours = cfg.get("business_hours") or {}
    days = hours.get("days")
    if not isinstance(days, list) or not days:
        return when
    try:
        days = sorted({int(d) % 7 for d in days})
    except (TypeError, ValueError):
        return when
    tz = _tz(cfg.get("timezone"))
    start = _parse_hhmm(hours.get("start"), time(9, 0))
    end = _parse_hhmm(hours.get("end"), time(20, 0))
    if start >= end:                       # nonsense window — don't invent a schedule
        return when

    local = when.astimezone(tz)
    for _ in range(8):                     # at most a week of skipping, then give up gracefully
        dow = (local.weekday() + 1) % 7    # python Mon=0 → JS Sun=0
        if dow in days:
            if local.time() < start:
                local = local.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
                return local.astimezone(timezone.utc)
            if local.time() <= end:
                return local.astimezone(timezone.utc)
        # closed (or past close) → try the start of the next day
        local = (local + timedelta(days=1)).replace(
            hour=start.hour, minute=start.minute, second=0, microsecond=0)
    return when


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Lead scoring
# ══════════════════════════════════════════════════════════════════════════════════════════════

def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def score_lead(lead: dict, rules: list) -> int:
    """Sum the points of every matching active rule, clamped to 0..100.

    `lead` here is the ENRICHED dict the router builds: it carries `source_key` / `interest_key`
    alongside the raw ids, because a rule an admin writes says "referral", not a uuid.
    """
    total = 0
    for r in rules or []:
        if not r.get("is_active", True):
            continue
        field = r.get("field")
        if not field:
            continue
        actual = lead.get(field)
        op = (r.get("op") or "eq").lower()
        expected = r.get("value")
        hit = False
        if op == "exists":
            hit = actual not in (None, "", [], {})
        elif op == "in":
            wanted = {w.strip().lower() for w in str(expected or "").split(",") if w.strip()}
            hit = str(actual or "").lower() in wanted
        elif op == "contains":
            hit = str(expected or "").lower() in str(actual or "").lower()
        elif op in ("gt", "gte", "lt", "lte"):
            a, b = _as_float(actual), _as_float(expected)
            if a is not None and b is not None:
                hit = {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
        elif op == "ne":
            hit = str(actual or "").lower() != str(expected or "").lower()
        else:                                  # eq
            hit = str(actual or "").lower() == str(expected or "").lower()
        if hit:
            try:
                total += int(r.get("points") or 0)
            except (TypeError, ValueError):
                pass
    return max(0, min(100, total))


def priority_from_score(score: int) -> str:
    return "hot" if score >= 60 else ("warm" if score >= 25 else "cold")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Assignment — teammates, queues, agencies
# ══════════════════════════════════════════════════════════════════════════════════════════════

def rule_matches(lead: dict, match: dict) -> bool:
    """Every key present in `match` must be satisfied (AND). An empty match matches everything —
    that is how the seeded catch-all "the lead's store owns it" rule works."""
    if not isinstance(match, dict):
        return False
    for key, want in match.items():
        if want in (None, "", [], {}):
            continue
        if key == "min_value":
            v, w = _as_float(lead.get("value_estimate")), _as_float(want)
            if v is None or w is None or v < w:
                return False
            continue
        if key == "min_lines":
            v, w = _as_float(lead.get("lines_estimate")), _as_float(want)
            if v is None or w is None or v < w:
                return False
            continue
        actual = str(lead.get(key) or "").strip().lower()
        if isinstance(want, list):
            if actual not in {str(w).strip().lower() for w in want}:
                return False
        elif actual != str(want).strip().lower():
            return False
    return True


def next_round_robin(members: list, cursor: int) -> tuple:
    """(employee_id, next_cursor) over the ACTIVE members in sort order.

    The cursor is persisted on the queue so the rotation survives a restart — an in-memory cursor
    silently resets to member #1 on every deploy, which in practice means the top of the list gets
    every lead and nobody notices until a rep complains.
    """
    active = [m for m in sorted(members or [], key=lambda m: (m.get("sort_order") or 0,
                                                             str(m.get("employee_id") or "")))
              if m.get("is_active", True) and m.get("employee_id")]
    if not active:
        return None, cursor
    idx = (int(cursor or 0)) % len(active)
    return active[idx].get("employee_id"), (idx + 1) % len(active)


def pick_assignee(lead: dict, rules: list, ctx: dict) -> dict:
    """Resolve WHO gets this lead. Returns
    {employee_id, queue_id, agency_id, rule_id, strategy, rr_cursor_update:(queue_id,cursor)|None}.

    Rules are evaluated in `priority` order, first match wins. No match → nobody is assigned and the
    lead sits in the pool, which is a legitimate state (and visible on the dashboard) rather than a
    silent default to whoever happens to be first alphabetically.

    ctx: {"queue_members": {queue_id: [member,...]}, "queues": {queue_id: queue},
          "store_owner": {store_code: employee_id}}
    """
    out = {"employee_id": None, "queue_id": None, "agency_id": None, "rule_id": None,
           "strategy": None, "rr_cursor_update": None}
    ordered = sorted([r for r in (rules or []) if r.get("is_active", True)],
                     key=lambda r: (r.get("priority") if r.get("priority") is not None else 100,
                                    str(r.get("name") or "")))
    for r in ordered:
        if not rule_matches(lead, r.get("match") or {}):
            continue
        strategy = (r.get("strategy") or "store_owner").lower()
        out["rule_id"], out["strategy"] = r.get("id"), strategy
        if strategy == "specific_user":
            out["employee_id"] = r.get("target_employee_id")
        elif strategy == "store_owner":
            out["employee_id"] = (ctx.get("store_owner") or {}).get(
                str(lead.get("store_code") or "").strip().upper())
        elif strategy == "agency":
            out["agency_id"] = r.get("target_agency_id")
        elif strategy == "queue":
            out["queue_id"] = r.get("target_queue_id")
        elif strategy == "round_robin":
            qid = r.get("target_queue_id")
            members = (ctx.get("queue_members") or {}).get(qid) or []
            cursor = ((ctx.get("queues") or {}).get(qid) or {}).get("rr_cursor") or 0
            emp, nxt = next_round_robin(members, cursor)
            out["employee_id"], out["queue_id"] = emp, qid
            if emp is not None:
                out["rr_cursor_update"] = (qid, nxt)
        return out
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Disposition — "dispose them"
# ══════════════════════════════════════════════════════════════════════════════════════════════

def apply_disposition(lead: dict, disp: dict, cfg: dict, now: datetime,
                      reason_code_id=None, note: str = "", followup_at=None) -> dict:
    """What a disposition does to a lead. Returns
    {"errors": [...], "lead_updates": {...}, "followup": {...}|None, "activity": {...}}.

    Refusals are returned, not raised, so the caller can answer with a 400 carrying every problem at
    once instead of making the rep discover them one at a time.
    """
    errors = []
    if not disp:
        return {"errors": ["Unknown disposition."], "lead_updates": {}, "followup": None,
                "activity": {}}
    if disp.get("requires_reason") and not reason_code_id:
        errors.append(f"'{disp.get('name')}' requires a reason — pick one from the list.")

    outcome = (disp.get("outcome") or "connected").lower()
    upd = {
        "disposition_id": disp.get("id"),
        "reason_code_id": reason_code_id,
        "last_activity_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    if note:
        upd["lost_note"] = note[:2000]
    if not lead.get("first_contacted_at") and outcome in ("connected", "won"):
        upd["first_contacted_at"] = now.isoformat()
    if disp.get("sets_stage_id"):
        upd["stage_id"] = disp["sets_stage_id"]
        upd["stage_entered_at"] = now.isoformat()
    if disp.get("closes_lead"):
        upd["status"] = "won" if outcome == "won" else "lost"
        upd["closed_at"] = now.isoformat()
        upd["next_action_at"] = None

    followup = None
    if disp.get("requires_followup") and not disp.get("closes_lead"):
        if followup_at:
            due = followup_at
        else:
            hrs = disp.get("default_followup_hours")
            try:
                hrs = int(hrs) if hrs is not None else 24
            except (TypeError, ValueError):
                hrs = 24
            due = shift_to_business_hours(now + timedelta(hours=hrs), cfg)
        followup = {
            "title": f"Follow up — {disp.get('name')}",
            "type": "call" if outcome == "no_contact" else "other",
            "due_at": due.isoformat() if hasattr(due, "isoformat") else due,
            "remind_at": due.isoformat() if hasattr(due, "isoformat") else due,
        }
        upd["next_action_at"] = followup["due_at"]

    activity = {
        "kind": "disposition",
        "body": f"{disp.get('name')}" + (f" — {note}" if note else ""),
        "meta": {"disposition_key": disp.get("key"), "outcome": outcome,
                 "reason_code_id": reason_code_id, "closed": bool(disp.get("closes_lead"))},
    }
    return {"errors": errors, "lead_updates": upd, "followup": followup, "activity": activity}


def stage_close_requires_disposition(stage: dict, cfg: dict) -> bool:
    """A won/lost stage the tenant marked `requires_disposition` cannot be entered blind — the whole
    point of the pipeline is knowing WHY a deal ended."""
    if not stage:
        return False
    if not int(cfg.get("require_disposition_on_close") or 0):
        return False
    return bool(stage.get("requires_disposition")) or bool(stage.get("is_won")) or bool(stage.get("is_lost"))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# The follow-up sweep — cadence materialization, reminders, misses, escalation
# ══════════════════════════════════════════════════════════════════════════════════════════════

def _dt(value):
    """Parse a timestamp from the DB (or a body) into aware UTC. Returns None on anything unusable —
    a row with a garbage date must be SKIPPED by the sweep, never crash it."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        try:
            d = datetime.fromisoformat(s[:19])
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def due_cadence_steps(lead: dict, cadence: dict, steps: list, existing_keys: set,
                      cfg: dict, now: datetime, anchor=None) -> list:
    """Cadence steps that have come due and are not already materialized.

    `existing_keys` is the set of (cadence_id, step_no) already on the lead — the same pair the
    partial unique index in migration 800 enforces. Checking it here means a sweep that runs twice,
    or retries after a gateway timeout, cannot double-book the rep.
    """
    trigger = (cadence.get("trigger") or "on_create").lower()
    if anchor is None:
        if trigger == "on_create":
            anchor = _dt(lead.get("created_at"))
        elif trigger == "on_stage_enter":
            anchor = _dt(lead.get("stage_entered_at")) or _dt(lead.get("created_at"))
        else:                                     # no_activity
            anchor = _dt(lead.get("last_activity_at")) or _dt(lead.get("created_at"))
    if anchor is None:
        return []
    if trigger == "on_stage_enter" and cadence.get("stage_id") \
            and lead.get("stage_id") != cadence.get("stage_id"):
        return []
    if trigger == "no_activity":
        try:
            idle = int(cadence.get("idle_hours") or 0)
        except (TypeError, ValueError):
            idle = 0
        if idle and now < anchor + timedelta(hours=idle):
            return []

    out = []
    for s in sorted(steps or [], key=lambda x: int(x.get("step_no") or 0)):
        if not s.get("is_active", True):
            continue
        step_no = int(s.get("step_no") or 0)
        if (cadence.get("id"), step_no) in existing_keys:
            continue
        try:
            offset = int(s.get("offset_hours") or 0)
        except (TypeError, ValueError):
            offset = 0
        due = shift_to_business_hours(anchor + timedelta(hours=offset), cfg)
        if due > now:                              # not yet — a later sweep will pick it up
            continue
        out.append({
            "lead_id": lead.get("id"),
            "cadence_id": cadence.get("id"),
            "cadence_step_no": step_no,
            "title": s.get("title") or "Follow up",
            "body": s.get("body"),
            "type": s.get("task_type") or "call",
            "due_at": due.isoformat(),
            "remind_at": due.isoformat(),
            "assign_to": s.get("assign_to") or "owner",
            "channel": s.get("channel") or "task",
        })
    return out


def tasks_to_remind(tasks: list, sent_keys: set, now: datetime) -> list:
    """Open tasks whose reminder moment has passed and that have not been reminded for THIS window.

    The window key is `<task_id>:<due_at>` — so moving a task's due date legitimately re-arms the
    reminder, while a sweep re-run for the same due date does not re-send.
    """
    out = []
    for t in tasks or []:
        if (t.get("status") or "open") != "open":
            continue
        remind = _dt(t.get("remind_at")) or _dt(t.get("due_at"))
        if remind is None or remind > now:
            continue
        snooze = _dt(t.get("snooze_until"))
        if snooze and snooze > now:
            continue
        key = reminder_window_key(t)
        if key in sent_keys:
            continue
        out.append({**t, "window_key": key})
    return out


def reminder_window_key(task: dict) -> str:
    return f"{task.get('id')}:{task.get('due_at')}"


def tasks_to_miss(tasks: list, cfg: dict, now: datetime) -> list:
    """Open tasks past due + grace. These flip to `missed` — they are NOT deleted, because a missed
    follow-up is the single most useful thing a manager can see."""
    try:
        grace = timedelta(hours=int(cfg.get("miss_grace_hours") or 0))
    except (TypeError, ValueError):
        grace = timedelta(hours=4)
    out = []
    for t in tasks or []:
        if (t.get("status") or "open") != "open":
            continue
        due = _dt(t.get("due_at"))
        if due is None or now < due + grace:
            continue
        snooze = _dt(t.get("snooze_until"))
        if snooze and snooze > now:
            continue
        out.append(t)
    return out


def leads_to_escalate(leads: list, cfg: dict, now: datetime, already: set = None) -> list:
    """Open leads that have gone quiet past stale + escalate windows.

    Routed to the rep's MANAGER, not broadcast — per the standing DM-review-gate directive, the
    market manager sees their people's problems before anyone else does.
    """
    already = already or set()
    stale = int(cfg.get("stale_lead_hours") or 0)
    esc = int(cfg.get("escalate_after_hours") or 0)
    if stale <= 0 and esc <= 0:
        return []
    cutoff = now - timedelta(hours=stale + esc)
    out = []
    for lead in leads or []:
        if (lead.get("status") or "open") != "open":
            continue
        last = _dt(lead.get("last_activity_at")) or _dt(lead.get("created_at"))
        if last is None or last > cutoff:
            continue
        if lead.get("id") in already:
            continue
        out.append({**lead, "quiet_hours": int((now - last).total_seconds() // 3600)})
    return out


def stale_leads(leads: list, cfg: dict, now: datetime) -> list:
    """Open leads past the stale window — the dashboard's "needs attention" set (a superset of the
    escalation set: stale first, escalated only if it stays stale)."""
    stale = int(cfg.get("stale_lead_hours") or 0)
    if stale <= 0:
        return []
    cutoff = now - timedelta(hours=stale)
    out = []
    for lead in leads or []:
        if (lead.get("status") or "open") != "open":
            continue
        last = _dt(lead.get("last_activity_at")) or _dt(lead.get("created_at"))
        if last is not None and last <= cutoff:
            out.append(lead)
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Dashboard math
# ══════════════════════════════════════════════════════════════════════════════════════════════

def funnel(leads: list, stages: list) -> list:
    """Count + value per stage, in stage order. Stages with nothing in them are still returned —
    an empty stage is information (that is where the pipeline is dry)."""
    by_id = {s.get("id"): s for s in stages or []}
    buckets = {s.get("id"): {"stage_id": s.get("id"), "stage": s.get("name"),
                             "sort_order": s.get("sort_order") or 0,
                             "probability": float(s.get("probability") or 0),
                             "count": 0, "value": 0.0}
               for s in stages or []}
    for lead in leads or []:
        b = buckets.get(lead.get("stage_id"))
        if b is None:
            continue
        b["count"] += 1
        b["value"] += _as_float(lead.get("value_estimate")) or 0.0
    rows = sorted(buckets.values(), key=lambda r: r["sort_order"])
    for r in rows:
        st = by_id.get(r["stage_id"]) or {}
        r["is_won"], r["is_lost"] = bool(st.get("is_won")), bool(st.get("is_lost"))
    return rows


def weighted_forecast(leads: list, stages: list) -> float:
    """Σ value × stage probability over OPEN leads only. Closed deals are results, not forecast."""
    prob = {s.get("id"): float(s.get("probability") or 0) / 100.0 for s in stages or []}
    total = 0.0
    for lead in leads or []:
        if (lead.get("status") or "open") != "open":
            continue
        total += (_as_float(lead.get("value_estimate")) or 0.0) * prob.get(lead.get("stage_id"), 0.0)
    return round(total, 2)


def conversion_rates(leads: list) -> dict:
    total = len(leads or [])
    won = sum(1 for l in leads or [] if (l.get("status") or "") == "won")
    lost = sum(1 for l in leads or [] if (l.get("status") or "") == "lost")
    open_ = sum(1 for l in leads or [] if (l.get("status") or "open") == "open")
    closed = won + lost
    return {
        "total": total, "open": open_, "won": won, "lost": lost,
        "win_rate": round(100.0 * won / closed, 1) if closed else 0.0,
        "close_rate": round(100.0 * closed / total, 1) if total else 0.0,
    }
