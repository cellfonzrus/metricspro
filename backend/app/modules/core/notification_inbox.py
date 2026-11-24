"""THE COMPANY NOTIFICATION INBOX — the ONE home for a recurring, company-level notification.

Owner, 2026-11-24, verbatim: *"Stop sending daily messages about data not uploading instead create a
[one] for each compay in their notifications inbox and overwrite if it is the same message everyday"*.

MEASURED FIRST (read-only, production `storeops.alert_log`, 2026-11-24 — 21 days): three companies
received 5-9 emails EVERY DAY for eight days straight on alert scope `connector`. Four senders write
that scope and each one had invented its own "don't repeat yourself" key:

  · `router._data_freshness_monitor`   `freshness:<org>:<feed>:stuck-at-<data date>`  → moves daily
  · `router._scan_connector_health`    `connector:<table>:<id>:since-<last success>:<kind>`
  · `router` unmatched-report notice   its own key per sweep run
  · `vision/router` camera health      its own key per episode
  · `commcalc/setup_documents`         `<feed_key>:<due date>:<cycle>` — one per period, by design

THE CLASS, NOT THE INSTANCE (CLAUDE.md "A fix is a DESIGN fix"). The instance is a stale payment-detail
feed mailed nine days running. The class is that **a recurrence had nowhere to go but a new message**:
every sender's only verb was "send", so "tell them again" and "tell them something new" were the same
act, and each sender fought that with a private key spelling. The fix is to give a recurrence somewhere
to LAND — one row per (company, scope, finding) that the next sighting UPDATES — and to put the choice
of route in ONE place that every sender already passes through (`closing.router._send_alert`).

ONE FACT, ONE HOME, DEREFERENCED:
  · WHICH route a scope takes      → `storeops.alert_route` (mig 1129), read here, nowhere else.
  · WHO a scope's recipients are   → `closing.router._alert_recipients` (unchanged).
  · THAT a scope was carried       → `storeops.alert_log` (mig 1051), channel `inbox` — no second
    audit table; an inbox item is a delivery, recorded like any other.

RULE TWO: a scope, a route and a severity are VALUES. No carrier, tenant, store or product name
appears here, and the route is a POLICY (a switch), which is where a house default is correct — not a
tenant's own vocabulary, which may never have one (CLAUDE.md, owner 2026-10-30).

PURE functions first (proven DB-free by `harness_notification_inbox.py`); thin I/O after.
"""
from datetime import datetime, timezone

HOUSE_ORG = "00000000-0000-0000-0000-000000000001"   # CLAUDE.md house org (RULE TWO defaults)
SCHEMA = "core"
TABLE = "notification_inbox"
ROUTE_SCHEMA = "storeops"
ROUTE_TABLE = "alert_route"
CHANNEL = "inbox"                   # the `storeops.alert_log.channel` value for an inbox delivery
ANY_SCOPE = "all"                   # the catch-all spelling `alert_recipient` already uses
ROUTES = ("message", "inbox", "both")
DEFAULT_ROUTE = "message"           # an undeclared scope behaves EXACTLY as it does today
SEVERITIES = ("info", "warning", "error")
DEFAULT_SEVERITY = "warning"
#: the body fields a sighting carries; `merge_row` compares these to decide "same message"
CONTENT_FIELDS = ("title", "body", "severity", "deep_link", "affected")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── PURE: which route does this company take for this scope? ───────────────────────────────────────
def route_for(rows, scope, org_id, house_org=HOUSE_ORG):
    """The declared delivery route for (company, scope): `message` | `inbox` | `both`.

    Precedence, most specific first: the tenant's own row for the named scope, the tenant's `all` row,
    the house row for the named scope, the house `all` row, then DEFAULT_ROUTE. A tenant's row can
    never be read for another tenant — the org is part of the match, not a fallback — which is the
    cross-tenant leak the 2026-10-30 rule exists to stop.

    A route the code cannot speak reads as DEFAULT_ROUTE. Guessing `inbox` from a typo would SILENCE
    an alert, and the safe direction for an unknown declaration is the loud one.
    """
    scope = (scope or "").strip()
    org_id = str(org_id or "") or house_org
    best, best_rank = DEFAULT_ROUTE, -1
    for r in rows or []:
        r_org, r_scope = str(r.get("org_id") or ""), (r.get("scope") or "").strip()
        if r_scope not in (scope, ANY_SCOPE):
            continue
        is_tenant = (r_org == org_id and org_id != house_org)
        if not is_tenant and r_org != house_org:
            continue
        route = (r.get("route") or "").strip().lower()
        if route not in ROUTES:
            continue
        rank = (2 if is_tenant else 0) + (1 if r_scope == scope else 0)
        if rank > best_rank:
            best, best_rank = route, rank
    return best


#: what `file_item` reports when the item is genuinely on the company's inbox
FILED_STATES = ("created", "changed", "unchanged")


def suppresses_message(route, inbox_key, filed=None):
    """Does this route mean "do NOT email or WhatsApp this"?

    Only `inbox` does, and only when the finding is ACTUALLY FILED under a STABLE key:
      · no key          → nothing to overwrite, so the item would be a new row every day, which is
        the daily-message defect wearing a hat;
      · no filed item   → the write failed (most often: migration 1129 has not been applied yet).

    In both cases the message goes out exactly as it did before and the caller says why. A finding
    is never dropped to make an inbox look tidy, and shipping this code ahead of its migration can
    therefore not silence a single alert. `both` keeps the message AND files the item.
    """
    if route != "inbox" or not (inbox_key or "").strip():
        return False
    return bool((filed or {}).get("state") in FILED_STATES)


def identity(scope, inbox_key):
    """The stable per-company key for one finding. REFUSES an empty key rather than invent one from
    the date — a key with today's date in it is exactly what made every recurrence a new message."""
    key = (inbox_key or "").strip()
    if not key:
        raise ValueError(f"an inbox item for scope {scope!r} needs a stable key, not a date")
    return key


def merge_row(existing, incoming, now=None):
    """The row to write for one sighting, and what the sighting MEANT.

    Returns `(row, state)` where state is `created` | `changed` | `unchanged`:
      · `created`   — nobody has seen this finding; one unread item.
      · `unchanged` — byte-identical to what is already filed: the age (`first_seen_at`) and the
        read state are KEPT and only `last_seen_at` / `occurrences` move. This is the whole point:
        an identical message must not nag, or the inbox is the daily email with extra steps.
      · `changed`   — the text moved on (2 days behind became 3), or the finding had been resolved
        and is back. Either way it is NEWS, so the item returns UNREAD.
    """
    now = now or _now_iso()
    body = {k: incoming.get(k) for k in CONTENT_FIELDS}
    body["severity"] = body.get("severity") if body.get("severity") in SEVERITIES else DEFAULT_SEVERITY
    if not existing:
        row = dict(body)
        row.update({"occurrences": 1, "first_seen_at": now, "last_seen_at": now,
                    "read_at": None, "resolved_at": None, "updated_at": now})
        return row, "created"
    same = all(existing.get(k) == body.get(k) for k in CONTENT_FIELDS) and not existing.get("resolved_at")
    row = dict(existing)
    row.update(body)
    row["occurrences"] = int(existing.get("occurrences") or 1) + 1
    row["first_seen_at"] = existing.get("first_seen_at") or now
    row["last_seen_at"] = now
    row["updated_at"] = now
    row["resolved_at"] = None
    if not same:
        row["read_at"] = None
    return row, ("unchanged" if same else "changed")


def stale_keys(rows, live_keys, prefix=None):
    """Which filed, still-open findings did the latest check NOT report — so they are fixed.

    Owner's standing rule (2026-07-26): *"a notification must disappear once the check is OK"*. A row
    already resolved is not resolved again, so a clear estate writes nothing at all.

    `prefix` is how a check that answers only PART of a scope stays honest: several senders share the
    scope `connector`, and the freshness monitor knows nothing about cameras. A caller passes the
    prefix it owns, and rows outside it are not its business to close.
    """
    live = {str(k) for k in (live_keys or [])}
    out = []
    for r in rows or []:
        key = str(r.get("inbox_key") or "")
        if r.get("resolved_at") or key in live:
            continue
        if prefix and not key.startswith(prefix):
            continue
        out.append(key)
    return out


# ── I/O (thin; every read is org-scoped) ───────────────────────────────────────────────────────────
def routes(client, org_id=None):
    """The declared route rows `route_for` needs, in ONE read.

    `org_id` NARROWS THE READ AT THE QUERY to that company plus the house defaults it inherits —
    which is every row `route_for` could legitimately use for it. Pass it on any path that answers a
    REQUEST: `route_for` already refuses to read one tenant's row as another's, but a handler that
    had fetched every company's rows could still disclose the SET of scopes other tenants have
    declared, and once `alert_route` gains an editor that set is their words, not ours (owner
    2026-10-30). Omitted (the hourly sweeps, which run for every tenant in turn) it reads all rows
    once instead of once per company.
    """
    try:
        q = (client.schema(ROUTE_SCHEMA).table(ROUTE_TABLE).select("org_id,scope,route"))
        if org_id:
            q = q.in_("org_id", sorted({str(org_id), HOUSE_ORG}))
        return (q.limit(2000).execute().data) or []
    except Exception as e:                                              # pragma: no cover - I/O guard
        print(f"WARN notification_inbox could not read {ROUTE_SCHEMA}.{ROUTE_TABLE}: {e}")
        return []


def _existing(client, org_id, scope, inbox_key):
    try:
        rows = (client.schema(SCHEMA).table(TABLE).select("*")
                .eq("org_id", org_id).eq("scope", scope).eq("inbox_key", inbox_key)
                .limit(1).execute().data) or []
        return rows[0] if rows else None
    except Exception as e:                                              # pragma: no cover - I/O guard
        print(f"WARN notification_inbox read failed ({org_id}/{scope}): {e}")
        return None


def file_item(client, org_id, scope, inbox_key, title, body="", severity=DEFAULT_SEVERITY,
              deep_link=None, affected=None, now=None):
    """File one sighting for one company: create it, or OVERWRITE the item it already has.

    Never raises — a notification surface may not be the thing that breaks an hourly sweep. Returns
    `{"state": created|changed|unchanged|error, "inbox_key": ...}`.
    """
    try:
        key = identity(scope, inbox_key)
    except ValueError as e:
        return {"state": "error", "detail": str(e)}
    incoming = {"title": title, "body": body, "severity": severity, "deep_link": deep_link,
                "affected": list(affected or [])}
    row, state = merge_row(_existing(client, org_id, scope, key), incoming, now)
    row.update({"org_id": org_id, "scope": scope, "inbox_key": key})
    row.pop("id", None)
    try:
        (client.schema(SCHEMA).table(TABLE)
         .upsert(row, on_conflict="org_id,scope,inbox_key").execute())
    except Exception as e:                                              # pragma: no cover - I/O guard
        print(f"WARN notification_inbox write failed ({org_id}/{scope}/{key}): {e}")
        return {"state": "error", "detail": str(e)[:160], "inbox_key": key}
    return {"state": state, "inbox_key": key, "occurrences": row["occurrences"]}


def resolve_missing(client, org_id, scope, live_keys, prefix=None, now=None):
    """Close every open item of this scope that the latest check did not report. The caller must pass
    the COMPLETE live key set for what it answers, and `prefix` when it answers only part of a scope
    — otherwise it would close findings that still hold but belong to another sender.
    """
    now = now or _now_iso()
    try:
        rows = (client.schema(SCHEMA).table(TABLE).select("inbox_key,resolved_at")
                .eq("org_id", org_id).eq("scope", scope).is_("resolved_at", None)
                .limit(2000).execute().data) or []
    except Exception as e:                                              # pragma: no cover - I/O guard
        print(f"WARN notification_inbox resolve read failed ({org_id}/{scope}): {e}")
        return {"resolved": 0}
    gone = stale_keys(rows, live_keys, prefix=prefix)
    for key in gone:
        try:
            (client.schema(SCHEMA).table(TABLE).update({"resolved_at": now, "updated_at": now})
             .eq("org_id", org_id).eq("scope", scope).eq("inbox_key", key).execute())
        except Exception as e:                                          # pragma: no cover - I/O guard
            print(f"WARN notification_inbox resolve failed ({org_id}/{scope}/{key}): {e}")
    return {"resolved": len(gone), "keys": gone}


def list_items(client, org_id, include_resolved=False, scope=None, since=None, limit=200):
    """This company's inbox, newest sighting first. ORG-SCOPED at the query, never filtered after."""
    try:
        q = (client.schema(SCHEMA).table(TABLE).select("*").eq("org_id", org_id))
        if scope:
            q = q.eq("scope", scope)
        if since:
            q = q.gte("last_seen_at", since)
        if not include_resolved:
            q = q.is_("resolved_at", None)
        return (q.order("last_seen_at", desc=True).limit(limit).execute().data) or []
    except Exception as e:                                              # pragma: no cover - I/O guard
        print(f"WARN notification_inbox list failed ({org_id}): {e}")
        return []


def unread_count(items):
    """PURE: how many open items this company has not read. A zero is only ever reported from a read
    that succeeded — the caller renders nothing before its first answer (§23e silent-zero rule)."""
    return sum(1 for i in items or [] if not i.get("read_at") and not i.get("resolved_at"))


def mark_read(client, org_id, ids=None, all_items=False, now=None):
    """Mark items read for ONE company. `ids` are this company's rows; `all_items` clears the badge."""
    now = now or _now_iso()
    done = 0
    try:
        if all_items:
            # Only the OPEN items, which is exactly what `unread_count` counts. "Mark all read" has
            # to clear the badge and nothing else; a resolved item is already out of the inbox, and
            # touching it would make the action's answer disagree with the number it cleared.
            r = (client.schema(SCHEMA).table(TABLE).update({"read_at": now, "updated_at": now})
                 .eq("org_id", org_id).is_("read_at", None).is_("resolved_at", None).execute())
            done = len(r.data or [])
        else:
            for i in ids or []:
                r = (client.schema(SCHEMA).table(TABLE).update({"read_at": now, "updated_at": now})
                     .eq("org_id", org_id).eq("id", i).execute())
                done += len(r.data or [])
    except Exception as e:                                              # pragma: no cover - I/O guard
        print(f"WARN notification_inbox mark_read failed ({org_id}): {e}")
    return {"read": done}
