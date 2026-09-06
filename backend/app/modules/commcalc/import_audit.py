"""COMMISSION IMPORT / SETTINGS ATTENTION PROVIDERS — mod-commission (settings-audit 2026-07-26).

OWNER DIRECTIVE (2026-07-26): "if the system is not importing any data to be current from VidaPay or ePay
or any of the configured imports it should show up in the notifications with clear instructions on how to
fix". platform-core's core/import_health.py already answers "is this feed LATE / has it NEVER delivered"
for every derived feed. What it cannot answer is WHY, and it never sees a misconfiguration that produces
silently-wrong output without any feed being late at all.

This module contributes the WHY + the FIX, as attention providers registered into that same aggregator, so
one login popup carries everything and no core file changes:

  commcalc_connectors   (cheap)  a connector that CANNOT import: switched on with no credentials, a login
                                 waiting on a human (2FA), a processor with no scraper, a mailbox with no
                                 filename rules, reports mapped for a processor with no login, a sweep
                                 whose SCHEDULER is silent (next_run_at stuck in the past), a report marked
                                 auto on a connector that is switched off.
  commcalc_pay_config   (cheap)  the carrier-mode trap: a non-Boost tenant pays ONLY from Commission Plans,
                                 so no plan / no rule / no assignment = every rep $0 with no error anywhere.
  commcalc_sales_basis  (cheap)  the daily feed arrives but the monthly commission basis is not allowed to
                                 auto-derive from it (report_definitions.auto=false for 'sales').
  commcalc_sales_export (heavy)  the degraded/legacy sales export: rows landed, but Ext Price / GP /
                                 Contract Type are empty, which pays $0 accessories and $0 commission and
                                 looks exactly like a calc bug.

CONTRACT COMPLIANCE
  RULE ONE  — every read is `.eq("org_id", org_id)` with the org passed in by the aggregator; nothing here
              writes, and no house-org constant is used as a data scope.
  RULE TWO  — no tenant/carrier name is branched on: the carrier mode comes from router._resolve_carrier_mode
              (the SAME resolver the calc uses, imported lazily so there is never a second implementation),
              and every threshold is read from commcalc.commission_org_config with a documented default.
  DEGRADES  — core.import_health missing ⇒ the decorator is a no-op and importing this module is harmless.
              Every query is best-effort: a table that doesn't exist contributes nothing (no false alarm).
  READ-ONLY — nothing here recomputes, promotes, or writes. It can never move a payout number.
"""
from datetime import datetime, timezone, timedelta

# ── register_provider, or a no-op if platform-core isn't there (never break commcalc on import) ──────
try:                                                          # pragma: no cover - import shape
    from app.modules.core.import_health import register_provider as _register_provider
except Exception:                                             # pragma: no cover
    _register_provider = None


def register_provider(key, *, label, group="other", cost="cheap"):
    if _register_provider is None:
        def _noop(fn):
            return fn
        return _noop
    return _register_provider(key, label=label, group=group, cost=cost)


# ── defaults for the tunables (each overridable per tenant in commission_org_config, mig 241) ────────
DEFAULT_STALE_HOURS = 30.0        # connector_stale_hours — also caps the scheduler-silence grace below
SCHED_GRACE_CAP_HOURS = 6.0       # a 30-minute poller has had 12 chances in 6h; longer is not "a blip"
SCHED_GRACE_FLOOR_HOURS = 2.0
DEFAULT_ZERO_PRICE_PCT = 0.95     # audit_zero_price_pct — share of $0 Ext Price rows that means "degraded"
DEFAULT_BLOCK_ALERT_FAILURES = 4  # portal_block_alert_failures — consecutive dud attempts before we shout
SALES_SAMPLE_ROWS = 5000
MIN_SAMPLE_ROWS = 50


def _now():
    return datetime.now(timezone.utc)


def _ts(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _item(group, key, severity, label, detail, count, deep_link, deep_link_label):
    return {"group": group, "key": key, "severity": severity, "label": label, "detail": detail,
            "count": int(count or 0), "deep_link": deep_link, "deep_link_label": deep_link_label}


def _rows(client, table, org_id, *, schema="commcalc", limit=500, select="*"):
    """One org-scoped best-effort read. A missing table/column yields [] — never a false alarm."""
    try:
        return (client.schema(schema).table(table).select(select)
                .eq("org_id", org_id).limit(limit).execute().data) or []
    except Exception:
        return []


def _org_config(client, org_id):
    """The tenant's commission posture row (mig 201 + the mig-241 audit thresholds). Never raises."""
    rows = _rows(client, "commission_org_config", org_id, limit=1)
    return rows[0] if rows else {}


def _num(v, default):
    try:
        f = float(v)
        return f if f > 0 else float(default)
    except Exception:
        return float(default)


def _pvariants(period):
    """'July 2026' → both spellings the database uses ('July 2026' and '2026-07').

    Period spelling is a standing bug class here: raw_sales stores 'June 2026' while other surfaces use
    '2026-06', and a query filtered on the wrong one silently returns zero rows — which would make this
    provider claim "no sales data" on a perfectly healthy month. PURE."""
    p = (period or "").strip()
    if not p:
        return []
    out = [p]
    try:
        if p[:1].isalpha():
            d = datetime.strptime(p, "%B %Y")
            out.append(d.strftime("%Y-%m"))
        else:
            d = datetime.strptime(p, "%Y-%m")
            out.append(d.strftime("%B %Y"))
    except Exception:
        pass
    return list(dict.fromkeys(out))


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# 1) CONNECTORS THAT CANNOT IMPORT  (cheap — config tables only)
# ════════════════════════════════════════════════════════════════════════════════════════════════════
# table → (human label, the page an admin fixes it on, what stops importing when it's broken)
_SWEEPS = [
    ("epay_sweep_config", "ePay portal sweep", "/commcalc/epay/sweep",
     "MI & ATU, Comprehensive Comp and Payment Detail (residual + carrier commission)"),
    ("dlar_sweep_config", "Carrier portal sweep (DLAR)", "/commcalc/dlar/sweep",
     "store and rep KPI metrics"),
    ("vip_sweep_config", "Distributor portal sweep (VIP)", "/commcalc/vip/sweep",
     "distributor invoices, PayGo billing, the asset ledger and chargebacks"),
    ("b2b_sweep_config", "POS portal sweep (B2B Soft)", "/commcalc/connectors",
     "on-hand inventory value"),
]


def _login_hint(processor, status, auth_status, auth_message):
    """Plain-language next step for a portal login, chosen from the trail the login itself recorded.
    PURE (unit-testable) and deliberately non-technical: the reader is an office admin, not an engineer."""
    s = (status or "").lower()
    a = (auth_status or "").lower()
    proc = processor or "the portal"
    if "not wired" in s or "not wired yet" in s:
        return (f"MetricsPro has no automated pull for {proc} yet, so its reports only arrive when someone "
                f"uploads them. Upload them at Data Imports → MA Upload (or have the vendor email them to "
                f"the import mailbox, which imports automatically).")
    if a in ("needs_2fa", "authenticating") or "needs login" in s:
        return (f"The {proc} login is waiting for a person: the portal asks for a 2FA code, and a scheduled "
                f"pull cannot answer it. Open Data Imports → the {proc} login → Live login, then type the "
                f"code the portal sends. The saved session is then reused until it expires.")
    if a == "error" or "error" in s or "fail" in s or "403" in s:
        base = (f"The last {proc} pull failed. Open Data Imports → the {proc} login and use Live login to "
                f"confirm the user name, password and (if used) the proxy still work; the portal may have "
                f"forced a password change or blocked the server's IP.")
        return f"{base} Reported: {(auth_message or status or '')[:180]}" if (auth_message or status) else base
    return (f"{proc} has not delivered any data. Open Data Imports → the {proc} login, press Pull now, and "
            f"read the status it reports.")


_ZERO_ROW_MARKERS = ("0 rows", "no reports", "0 report(s)", "nothing imported", "imported 0",
                     "calibration/diagnostic needed", "not calibrated", "0 report")


def _signed_in_never_delivered(s):
    """True when a portal login is AUTHENTICATED yet has never actually imported anything. PURE.

    Two independent signals, either is enough:
      • `last_run_at` (which since mig 241 advances ONLY on a run that imported data) is empty while an
        attempt HAS been recorded — the login has been exercised and delivered nothing; or
      • the last status literally reports a zero-row pull.
    Deliberately keyword-independent of the `bad` branch above: the owner's row said
    "pulled 0 rows across 0 report(s): —; calibration/diagnostic needed: …", which contains no
    error/fail/403 token and therefore matched nothing at all."""
    au = (s.get("auth_status") or "").strip().lower()
    st = (s.get("last_status") or "").strip().lower()
    if au != "authenticated":
        return False
    if any(m in st for m in _ZERO_ROW_MARKERS):
        return True
    return bool(not s.get("last_run_at") and (s.get("last_attempt_at") or st))


def _blocked_detail(name, state, fails):
    """Plain-language "the portal has blocked us" copy. PURE. Deliberately tells the reader NOT to
    retry: the 2026-07-27 incident escalated because every error message the module produced pointed a
    human at an action (re-login, re-map, pull again) that put MORE load on an already-refusing portal."""
    until = state.get("blocked_until")
    when = ""
    if until:
        try:
            when = until.strftime("%H:%M UTC on %d %b")
        except Exception:
            when = str(until)[:16]
    mins = int(round((state.get("remaining_s") or 0) / 60.0))
    return (f"The {name} portal has temporarily blocked us — it answered that we are making too many "
            f"requests. MetricsPro has stopped contacting it and will try again automatically at "
            f"{when or 'the end of the cooldown'} (about {mins} minutes). "
            f"DO NOT press Log in or Pull now in the meantime: another attempt during a block usually "
            f"makes the portal extend it. Nothing is lost — the reports will import on the next "
            f"automatic attempt."
            + (f" The portal said: {state.get('reason')}" if state.get("reason") else "")
            + (f" ({fails} failed attempts in a row.)" if fails and fails > 1 else ""))


@register_provider("commcalc_connectors", label="Imports that cannot run (credentials / login / schedule)",
                   group="import", cost="cheap")
def p_connectors(client, org_id, ctx):
    """Every commission-side import channel that CANNOT deliver, with the reason and the fix.

    This is deliberately NOT a staleness check (core's `imports` provider owns that). It fires on the
    CONFIGURATION being unable to work — which is what an admin can actually act on, and which used to be
    invisible because a failed run stamped last_run_at and made the feed look fresh."""
    now = ctx.get("now") or _now()
    cfg = _org_config(client, org_id)
    stale_h = _num(cfg.get("connector_stale_hours"), DEFAULT_STALE_HOURS)
    sched_grace = max(SCHED_GRACE_FLOOR_HOURS, min(stale_h, SCHED_GRACE_CAP_HOURS))
    # Portal cooldown (mig 244). Imported lazily and best-effort: pre-migration there are no columns,
    # read_state() reports blocked=False and this whole branch is inert.
    try:
        from app.modules.commcalc import portal_backoff as _pb
    except Exception:
        _pb = None
    try:
        alert_fails = int(cfg.get("portal_block_alert_failures") or 0) or DEFAULT_BLOCK_ALERT_FAILURES
    except Exception:
        alert_fails = DEFAULT_BLOCK_ALERT_FAILURES
    out = []

    def sched_silent(row, slug, label, page, what):
        """next_run_at stuck in the PAST means nothing is calling the /run-due entrypoint: every run-due
        handler advances next_run_at BEFORE it dispatches, so a past-due timestamp cannot survive a firing
        scheduler. This is the check that would have caught the 2026-07 'sweeps idle' incident (pg_cron was
        fine; the endpoint was 403ing on a secret mismatch, and nothing surfaced it)."""
        nxt = _ts(row.get("next_run_at"))
        if not row.get("enabled") or not nxt:
            return None
        late_h = (now - nxt).total_seconds() / 3600.0
        if late_h < sched_grace:
            return None
        return _item("import", f"commcalc:sched:{slug}", "error",
                     f"{label} is switched on but its schedule is not firing",
                     (f"It was due {late_h:.0f}h ago and never started, so this is not being updated: "
                      f"{what}. The scheduled trigger is not reaching the server. Use the button below "
                      f"and press 'Run now' to import today's data, and ask whoever runs the platform to "
                      f"check the import scheduler (the run-due job and its shared secret)."),
                     1, page, "Open the sweep")

    # ── (a) portal sweeps ────────────────────────────────────────────────────────────────────────────
    for table, label, page, what in _SWEEPS:
        rows = _rows(client, table, org_id, limit=5)
        if not rows:
            continue                                  # never configured ⇒ the tenant doesn't use it
        r = rows[0]
        enabled = bool(r.get("enabled"))
        has_creds = bool((r.get("portal_user") or "").strip() and (r.get("portal_pass") or "").strip())
        status = (r.get("last_status") or "")
        if enabled and not has_creds:
            out.append(_item("import", f"commcalc:creds:{table}", "error",
                             f"{label} is switched on but has no saved login",
                             (f"Without portal credentials none of this can import: {what}. Use the "
                              f"button below, enter the portal user name and password, and Save."),
                             1, page, "Add credentials"))
        elif enabled and status and any(k in status.lower() for k in ("error", "fail", "403", "no ")):
            out.append(_item("import", f"commcalc:failed:{table}", "error",
                             f"{label}: the last run failed",
                             (f"This is not being updated: {what}. The sweep reported: {status[:220]}. "
                              f"Use the button below, fix what it reports (usually the portal password or "
                              f"a report that moved), then press 'Run now'."),
                             1, page, "Open the sweep"))
        elif enabled and (status or "").strip().lower() == "idle":
            out.append(_item("import", f"commcalc:idle:{table}", "warning",
                             f"{label} runs but has nothing to import",
                             (f"The sweep is on and the login works, but no report is ticked, so it "
                              f"imports nothing. Use the button below to tick the reports you want, or "
                              f"switch the sweep off if this tenant doesn't use that portal."),
                             1, page, "Choose reports"))
        it = sched_silent(r, table, label, page, what)
        if it:
            out.append(it)

    # ── (b) portal-login pulls (VidaPay / T-CETRA / b2bsoft — the owner's example) ───────────────────
    sources = _rows(client, "data_source", org_id, limit=200,
                    select="id,label,processor,enabled,username,account_id,password,auth_status,"
                           "auth_message,last_status,last_run_at,last_attempt_at,next_run_at,"
                           "frequency,session_expires_at,blocked_until,block_reason,"
                           "consecutive_failures")
    if not sources:
        # Pre-migration-244 the SELECT above fails on the unknown columns and _rows returns []. Fall
        # back to the pre-244 column list so this provider keeps reporting everything it used to.
        sources = _rows(client, "data_source", org_id, limit=200,
                        select="id,label,processor,enabled,username,account_id,password,auth_status,"
                               "auth_message,last_status,last_run_at,last_attempt_at,next_run_at,"
                               "frequency,session_expires_at")
    procs = set()
    for s in sources:
        proc = (s.get("processor") or "").strip()
        procs.add(proc.lower())
        name = (s.get("label") or proc or "portal login").strip()
        if not s.get("enabled"):
            continue
        if not ((s.get("username") or s.get("account_id")) and s.get("password")):
            out.append(_item("import", f"commcalc:src_creds:{s.get('id')}", "error",
                             f"{name}: the login is incomplete",
                             ("Enter the Account ID, User ID and Password on this login (Data Imports → "
                              "Processor logins) and Save, then use Live login once so the portal trusts "
                              "this device. Until then nothing can be pulled from it."),
                             1, "/commcalc/email-imports", "Open Data Imports"))
            continue
        # ── THE PORTAL BLOCKED US (owner report 2026-07-27) ─────────────────────────────────────
        # Checked FIRST and terminal for this source: while a cooldown is active every other symptom
        # on the row (auth_status='error', a zero-row last_status, a past-due next_run_at) is a
        # CONSEQUENCE of the block, and stacking four alarms on one cause helps nobody. It is also the
        # only item whose correct instruction is "do nothing".
        state = _pb.read_state(s, now=now) if _pb is not None else {"blocked": False}
        if state.get("blocked"):
            out.append(_item("import", f"commcalc:src_blocked:{s.get('id')}", "error",
                             f"{name}: the portal has temporarily blocked us",
                             _blocked_detail(proc or name, state,
                                             state.get("consecutive_failures")),
                             1, "/commcalc/email-imports", "See the login"))
            continue
        fails = 0
        try:
            fails = int(s.get("consecutive_failures") or 0)
        except Exception:
            fails = 0
        st, au = (s.get("last_status") or ""), (s.get("auth_status") or "")
        bad = (au.lower() in ("needs_2fa", "error", "authenticating")
               or any(k in st.lower() for k in ("error", "fail", "needs login", "not wired", "403")))
        if fails >= alert_fails and not bad:
            # Repeated dud attempts with no error keyword anywhere — the shape that stayed invisible
            # for weeks. Warning, not error: the login still works, it just keeps delivering nothing.
            out.append(_item("import", f"commcalc:src_repeat_fail:{s.get('id')}", "warning",
                             f"{name}: {fails} attempts in a row have imported nothing",
                             (f"Every recent attempt reached the portal and came back empty. Open Data "
                              f"Imports → this login → 🔧 What the pull saw to see which report failed "
                              f"and what the portal's own Reports list offers; correct any mismatched "
                              f"name on Report mapping. Counting stops the moment one pull imports rows."
                              + (f" It last reported: {st[:180]}" if st else "")),
                             fails, "/commcalc/email-imports", "See what the pull saw"))
        if bad:
            sev = "warning" if "not wired" in st.lower() else "error"
            out.append(_item("import", f"commcalc:src:{s.get('id')}", sev,
                             f"{name} is not importing",
                             _login_hint(proc or name, st, au, s.get("auth_message")),
                             1, "/commcalc/email-imports", "Fix the login"))
        elif _signed_in_never_delivered(s):
            # THE SILENT CASE (owner report 2026-07-27). The login is green — auth_status
            # 'authenticated', a saved session, an attempt recorded — and the last pull imported
            # NOTHING ("pulled 0 rows across 0 report(s)"). None of the keywords above match that
            # sentence, so this connector used to pass every check while delivering nothing, forever.
            out.append(_item("import", f"commcalc:src_nodata:{s.get('id')}", "error",
                             f"{name} signs in, but has never imported a report",
                             ("The portal login works — the last attempt reached the site and came back "
                              "with no data at all. Nothing from this processor is reaching MetricsPro. "
                              "Open Data Imports → this login → 🔧 What the pull saw: it lists every "
                              "report the pull tried and the names the portal itself offers. Where a name "
                              "doesn't match, correct it on Report mapping; then sign in again (the "
                              "reports are pulled automatically) or press ▶ Pull now."
                              + (f" It last reported: {st[:180]}" if st else "")),
                             1, "/commcalc/email-imports", "See what the pull saw"))
        it = sched_silent(s, f"src:{s.get('id')}", f"{name} scheduled pull",
                          "/commcalc/email-imports", f"the reports pulled from {proc or name}")
        if it:
            out.append(it)

    # ── (c) reports mapped for a processor that has no login at all ──────────────────────────────────
    rmap = [r for r in _rows(client, "report_pull_map", org_id, limit=300) if r.get("enabled", True)]
    orphan = sorted({(r.get("display_name") or r.get("report_key") or "?")
                     for r in rmap
                     if (r.get("processor") or "vidapay").strip().lower() not in procs})
    if orphan:
        out.append(_item("import", "commcalc:pull_no_login", "warning",
                         "Reports are configured to pull, but no portal login is saved",
                         (f"{len(orphan)} report(s) — {', '.join(orphan[:4])} — are set up to be pulled "
                          f"automatically, and there is no login for their processor, so nothing pulls "
                          f"them. Add the portal login under Data Imports → Processor logins, or upload "
                          f"these reports by hand each period."),
                         len(orphan), "/commcalc/email-imports", "Add the login"))

    # ── (d) mailboxes that can never match anything ──────────────────────────────────────────────────
    for m in _rows(client, "email_sweep_config", org_id, limit=50):
        if not (m.get("imap_host") or m.get("username")):
            continue
        pats = m.get("patterns")
        if isinstance(pats, str):
            import json
            try:
                pats = json.loads(pats)
            except Exception:
                pats = []
        live = [p for p in (pats or []) if isinstance(p, dict) and (p.get("pattern") or "").strip()]
        if not live:
            acct = m.get("label") or m.get("account") or m.get("username") or "the import mailbox"
            out.append(_item("import", f"commcalc:mailbox_norules:{m.get('account') or 'default'}", "error",
                             f"Import mailbox '{acct}' has no filename rules",
                             ("Reports can arrive in this mailbox and still be ignored: with no filename "
                              "rule nothing can match. Open Data Imports → the mailbox → add a rule (for "
                              "example *Sales*Transaction*Details* → daily sales) → Save, then press Test "
                              "connection."),
                             1, "/commcalc/email-imports", "Add a rule"))

    # ── (e) a report marked AUTO on a connector whose sweep is switched off ──────────────────────────
    defs = _rows(client, "report_definitions", org_id, limit=300)
    auto_keys = {(d.get("report_key") or "").strip() for d in defs if d.get("auto")}
    _SWEEP_KEYS = {
        "epay_sweep_config": ("mi_report", "comp_report", "payment_detail"),
        "dlar_sweep_config": ("dlar_rep", "dlar_store"),
        "vip_sweep_config": ("vip_workbook", "asset_ledger", "vip_chargebacks"),
    }
    for table, label, page, what in _SWEEPS:
        keys = _SWEEP_KEYS.get(table) or ()
        expected = sorted(k for k in keys if k in auto_keys)
        if not expected:
            continue
        rows = _rows(client, table, org_id, limit=5)
        if rows and not rows[0].get("enabled"):
            out.append(_item("import", f"commcalc:auto_off:{table}", "warning",
                             f"{label} is switched OFF but its reports are marked automatic",
                             (f"{len(expected)} report(s) ({', '.join(expected)}) are set to import "
                              f"automatically, and the sweep that fetches them is disabled — so they will "
                              f"never arrive on their own. Either switch the sweep on (button below), or "
                              f"mark those reports manual on the Connectors page so people know to "
                              f"upload them."),
                             len(expected), page, "Turn the sweep on"))
    return out


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# 2) THE CARRIER-MODE / PLAN TRAP  (cheap — config tables only)
# ════════════════════════════════════════════════════════════════════════════════════════════════════
@register_provider("commcalc_pay_config", label="Commission set-up incomplete (pays $0)",
                   group="mapping", cost="cheap")
def p_pay_config(client, org_id, ctx):
    """A non-Boost tenant is paid ONLY from Commission Plans. With no plan, no rule, or nobody assigned,
    the calculation completes successfully and pays every rep $0 — the single most common "commissions are
    broken" report, and nothing surfaces it today.

    Carrier mode comes from the calc's OWN resolver (lazy import) so this can never disagree with what the
    calculation actually does. Cheap: four small config reads, no sales scan."""
    out = []
    carriers = _rows(client, "carrier", org_id, limit=100, select="id,name,code,is_default")
    try:
        from app.modules.commcalc.router import _resolve_carrier_mode
        mode = _resolve_carrier_mode(carriers)
    except Exception:
        return out                                    # can't resolve ⇒ say nothing rather than guess

    cfg = _org_config(client, org_id)
    if cfg.get("pay_disabled"):
        out.append(_item("mapping", "commcalc:pay_disabled", "info",
                         "Commission pay is switched off for this tenant",
                         ("Every commission calculation will report $0 on purpose while 'pay disabled' is "
                          "set on the commission settings. Turn it off when this tenant should be paid."),
                         1, "/commcalc/settings", "Open commission settings"))

    if mode != "plan":
        return out

    plans = _rows(client, "commission_plan", org_id, limit=500, select="id,name,is_active")
    active = [p for p in plans if p.get("is_active", True)]
    carrier_name = next((c.get("name") for c in carriers if c.get("is_default")), None) \
        or (carriers[0].get("name") if carriers else "this carrier")
    why = (f"This tenant's carrier is {carrier_name}, so pay comes ONLY from Commission Plans "
           f"(the built-in Boost rates do not apply). ")
    if not active:
        out.append(_item("mapping", "commcalc:no_plan", "error",
                         "No commission plan exists — every rep calculates to $0",
                         (why + "There is no active plan yet, so every calculation returns $0 with no "
                                "error. Open Commission Plans → New plan, add at least one rule (what it "
                                "pays on and how much), then assign your reps to it."),
                         1, "/commcalc/commission-plans", "Create a plan"))
        return out

    ids = {p.get("id") for p in active}
    rules = [r for r in _rows(client, "commission_rule", org_id, limit=2000, select="id,plan_id")
             if r.get("plan_id") in ids]
    if not rules:
        out.append(_item("mapping", "commcalc:no_rule", "error",
                         "Your commission plan has no rules — it pays $0",
                         (why + f"{len(active)} plan(s) exist but none has a single rule, so there is "
                                f"nothing to pay on. Open the plan and add rules (for example: activations "
                                f"→ $ per unit, accessories → % of GP)."),
                         len(active), "/commcalc/commission-plans", "Add rules"))
        return out

    assigns = [a for a in _rows(client, "commission_plan_assignment", org_id, limit=2000,
                                select="id,plan_id,scope,scope_value")
               if a.get("plan_id") in ids]
    if not assigns:
        out.append(_item("mapping", "commcalc:no_assignment", "error",
                         "Nobody is assigned to a commission plan — every rep calculates to $0",
                         (why + "The plan and its rules are ready, but no rep, store, market or default "
                                "assignment points at it, so no sale can find a plan. Open Commission "
                                "Plans → Assignments and either assign each rep, or add ONE 'default' "
                                "assignment that covers everyone."),
                         len(active), "/commcalc/commission-plans", "Assign reps"))
    return out


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# 3) THE MONTHLY BASIS IS NOT ALLOWED TO AUTO-DERIVE  (cheap — config tables only)
# ════════════════════════════════════════════════════════════════════════════════════════════════════
@register_provider("commcalc_sales_basis", label="Monthly sales basis will not build itself",
                   group="import", cost="cheap")
def p_sales_basis(client, org_id, ctx):
    """The daily feed (daily_sales_feed) is the CURRENT month's source; a CLOSED month is paid from
    raw_sales, which is built by promoting the feed — and that promotion is gated on the tenant's
    report_definitions row for report_key='sales' having auto=true. A tenant receiving the daily feed with
    auto=false gets a correct-looking current month and an EMPTY closed month (silent $0), which is exactly
    how the luxelink July 2026 zero happened. Cheap: two config reads."""
    defs = _rows(client, "report_definitions", org_id, limit=300, select="report_key,auto,label")
    row = next((d for d in defs if (d.get("report_key") or "").strip() == "sales"), None)
    if not row or row.get("auto"):
        return []                                     # missing row defaults to ON — nothing to warn about

    feeds_daily = False
    for tbl in ("email_sweep_config", "ftp_sweep_config"):
        for c in _rows(client, tbl, org_id, limit=50):
            pats = c.get("patterns")
            if isinstance(pats, str):
                import json
                try:
                    pats = json.loads(pats)
                except Exception:
                    pats = []
            for p in (pats or []):
                if isinstance(p, dict) and (p.get("upload_type") or "").strip() == "daily_sales":
                    feeds_daily = True
    if not feeds_daily:
        return []                                     # no daily feed ⇒ manual monthly upload is the plan
    return [_item("import", "commcalc:basis_manual", "warning",
                  "The monthly sales basis is set to manual while the daily feed is running",
                  ("Daily sales are importing, but the monthly file that commissions are calculated from "
                   "is set to 'manual', so it will NOT be built from those daily imports. The current "
                   "month will look right and a closed month can come out $0. Open Connectors → Sales "
                   "Transactions and switch it to automatic, or upload the full monthly sales file by hand "
                   "before you calculate a closed month."),
                  1, "/commcalc/connectors", "Switch to automatic")]


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# 4) A DEGRADED SALES EXPORT  (heavy — samples the sales rows)
# ════════════════════════════════════════════════════════════════════════════════════════════════════
_REQUIRED_EXPORT = "Ext Price, GP, Contract Type (and Customer #)"


def _sample_sales(client, org_id, period):
    """(rows, source) — sample the CURRENT month's sales lines from raw_sales, falling back to the daily
    feed. Bounded (SALES_SAMPLE_ROWS, 3 columns) and read-only; both spellings of the period are tried."""
    for table in ("raw_sales", "daily_sales_feed"):
        for p in _pvariants(period):
            try:
                rows = (client.schema("commcalc").table(table)
                        .select("ext_price,gp,contract_type")
                        .eq("org_id", org_id).eq("period", p)
                        .limit(SALES_SAMPLE_ROWS).execute().data) or []
            except Exception:
                rows = []
            if rows:
                return rows, table
    return [], None


@register_provider("commcalc_sales_export", label="Sales export missing its money columns",
                   group="import", cost="heavy")
def p_sales_export(client, org_id, ctx):
    """A reduced / legacy sales export ingests every dollar as 0: commissions and accessories come out $0
    and it looks exactly like a calculation bug (it is not — the file is wrong). Flags it in plain language
    with the exact columns the export must carry.

    HEAVY on purpose (samples up to 5,000 sales rows), so it runs only on the admin page's full check, not
    on every login."""
    now = ctx.get("now") or _now()
    cfg = _org_config(client, org_id)
    pct = _num(cfg.get("audit_zero_price_pct"), DEFAULT_ZERO_PRICE_PCT)
    pct = min(max(pct, 0.5), 1.0)
    out = []
    for period in (now.strftime("%B %Y"), (now.replace(day=1) - timedelta(days=1)).strftime("%B %Y")):
        rows, table = _sample_sales(client, org_id, period)
        if len(rows) < MIN_SAMPLE_ROWS:
            continue                                  # too little data to judge (or nothing imported yet)
        n = len(rows)

        def _zero(v):
            try:
                return abs(float(v or 0)) < 0.005
            except Exception:
                return True
        zero_price = sum(1 for r in rows if _zero(r.get("ext_price")))
        blank_ct = sum(1 for r in rows if not (r.get("contract_type") or "").strip())
        if zero_price / n >= pct:
            out.append(_item("import", f"commcalc:sales_no_price:{period}", "error",
                             f"{period} sales imported with no prices",
                             (f"{zero_price} of the {n} sampled sales lines for {period} have a $0 Ext "
                              f"Price, which means the export that was imported is missing its money "
                              f"columns. Commissions, accessory pay and GP will all read $0 until it is "
                              f"replaced. Re-run the POS report as 'Sales Transaction Details' including "
                              f"{_REQUIRED_EXPORT}, upload it on the Data Imports page, then re-calculate "
                              f"{period}."),
                             zero_price, "/commcalc/upload", "Re-upload the sales file"))
        elif blank_ct / n >= pct:
            out.append(_item("import", f"commcalc:sales_no_ct:{period}", "warning",
                             f"{period} sales imported without Contract Type",
                             (f"{blank_ct} of the {n} sampled sales lines for {period} have no Contract "
                              f"Type, so activations, upgrades and ports cannot be told apart and any pay "
                              f"rule based on them resolves to $0. Re-run the POS export as 'Sales "
                              f"Transaction Details' (it includes Contract Type), upload it, then "
                              f"re-calculate {period}."),
                             blank_ct, "/commcalc/upload", "Re-upload the sales file"))
        break                                         # judge the newest month that actually has data
    return out


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# 5) THE MONTH JUST CLOSED AND ITS BASIS WAS NEVER RE-DERIVED  (heavy — counts trans_ids on two tables)
# ════════════════════════════════════════════════════════════════════════════════════════════════════
@register_provider("commcalc_sales_derive_gap",
                   label="A closed month's sales basis is behind the daily feed",
                   group="import", cost="heavy")
def p_sales_derive_gap(client, org_id, ctx):
    """THE MONTH-BOUNDARY GAP, said out loud.

    Provider 3 above catches the tenant who switched auto-derive OFF. This one catches the tenant whose
    auto-derive is ON and working and whose closed month is short anyway — because the derivation asked
    the WALL CLOCK for its period and moved to the new month at 00:00 while the B2B feed carried on
    finalizing the old one. Owner-verified 2026-08-01: 45 luxelink transactions sat in the July feed and
    not in the July basis, which makes every July report short and would UNDERPAY a July recompute, with
    no error anywhere and every connector showing green.

    Judges the month that JUST CLOSED (the one the boundary puts at risk) and says nothing at all when
    the feed for it is empty — a tenant with no daily feed has nothing to be behind.

    Severity is honest about whether anything will fix it by itself:
      • grace window still OPEN  → warning; the next hourly derivation run is expected to close it.
      • window closed / disabled → error; nothing automatic will ever pick these transactions up.

    HEAVY (two bounded trans_id scans), so it runs on the admin page's full check, never on login.
    READ-ONLY: it counts, it does not derive and it does not recompute."""
    now = ctx.get("now") or _now()
    prior = (now.replace(day=1) - timedelta(days=1)).strftime("%B %Y")
    try:
        from app.modules.commcalc import sales_recon as _recon
        from app.modules.commcalc import sales_derive as _derive
    except Exception:                                 # pragma: no cover - import shape
        return []

    try:
        gap = _recon.derive_gap(prior, org_id=org_id, client=client)
    except Exception:
        return []                                     # never a false alarm on a read error
    if not gap.get("has_feed"):
        return []                                     # no daily feed for that month ⇒ nothing to compare
    missing = int(gap.get("missing_in_monthly") or 0)
    if missing <= 0:
        return []                                     # in step — this is the healthy answer

    cfg = _org_config(client, org_id)
    grace = _derive.resolve(cfg.get(_derive.CONFIG_COLUMN))
    open_now = _derive.window_open(now, grace)
    auto_ok = True
    for d in _rows(client, "report_definitions", org_id, limit=300, select="report_key,auto"):
        if (d.get("report_key") or "").strip() == "sales" and not d.get("auto"):
            auto_ok = False

    if open_now and auto_ok:
        sev = "warning"
        tail = (f"The month-boundary grace window is still open ({grace.get('days')} day(s) after "
                f"rollover), so the next automatic run should pick them up. If this is still here "
                f"tomorrow, re-derive {prior} by hand before anyone calculates it.")
    elif not auto_ok:
        sev = "error"
        tail = ("This tenant's monthly sales basis is set to MANUAL, so nothing will ever derive these "
                f"automatically. Switch Sales Transactions to automatic on Connectors, or re-derive "
                f"{prior} by hand — then re-calculate {prior}.")
    else:
        sev = "error"
        tail = (f"The month-boundary grace window is closed"
                f"{' (switched off for this tenant)' if not grace.get('enabled') else ''}, so nothing "
                f"will pick these up on its own. Re-derive {prior} from the daily feed, then "
                f"re-calculate {prior} — otherwise those sales are unpaid.")

    return [_item("import", f"commcalc:derive_gap:{prior}", sev,
                  f"{prior} sales basis is {missing} transaction(s) behind the daily feed",
                  (f"The daily sales feed delivered {gap.get('feed_trans')} transactions for {prior} but "
                   f"the monthly basis commissions are calculated from only has "
                   f"{gap.get('monthly_trans')} — {missing} transaction(s) are in the feed and not in the "
                   f"basis. Late-finalizing transactions land in the feed after the month has already "
                   f"rolled over. Until the basis is re-derived they are missing from every {prior} "
                   f"report and would not be paid. " + tail),
                  missing, f"/commcalc/sales-derive?period={prior}", f"Re-derive {prior}")]


@register_provider("commcalc_portal_sessions",
                   label="Merchant portal logins that need a human (2FA session)",
                   group="import", cost="cheap")
def p_portal_sessions(client, org_id, ctx):
    """Merchant-processor portal logins whose DURABLE SESSION can no longer pull (mig 955).

    WHY THIS IS AN ATTENTION ITEM, not just a chip. The whole 2FA approach for these portals is: a human
    satisfies the challenge ONCE on the live-login screencast, and the saved session drives every daily
    pull. The failure that makes it worthless is a session dying quietly — the connector still looks
    configured, the nightly scrape returns nothing, and the gap is found weeks later as a hole in the
    daily-closing card recon. The status chip on the data-source row only helps someone already looking
    at that page; this provider puts it in front of an admin at login, which is the platform's existing
    answer for "a connector cannot deliver" (p_connectors above, same group and shape).

    Deliberately narrow: it fires ONLY on the states a human can fix by signing in (never_linked,
    expired, needs_login) — `expiring_soon` is a chip, not a popup, and a non-auth pull failure is
    already p_connectors' job. Pre-migration (no portal sources, or no session columns) it is inert.
    """
    try:
        from app.modules.commcalc import merchant_portals as _mp
        from app.modules.commcalc import portal_session_health as _psh
    except Exception:
        return []
    now = ctx.get("now") or _now()
    try:
        rows = (client.schema("commcalc").table("data_source")
                .select("id,label,username,processor,enabled,auth_status,auth_message,last_status,"
                        "session_expires_at,session_linked_at,session_warn_hours,session_state")
                .eq("org_id", org_id).eq("enabled", True).limit(500).execute().data) or []
    except Exception:
        return []                       # table/columns not there yet — nothing to report, never a 500
    out = []
    for r in rows:
        if not _mp.is_portal((r.get("processor") or "").strip().lower()):
            continue
        # Never let session material reach the item: swap the blob for the boolean the evaluator wants.
        probe = {k: v for k, v in r.items() if k != "session_state"}
        probe["has_session"] = bool(r.get("session_state"))
        h = _psh.evaluate(probe, now=now)
        if not h.get("needs_human"):
            continue
        name = r.get("label") or r.get("username") or r.get("processor") or "portal login"
        out.append(_item(
            "import", f"commcalc:portal_session:{r.get('id')}", "error",
            f"{name}: {h['headline']}",
            (f"{h['detail']} Until this is done, nothing is pulled from this processor and the daily "
             f"closing card tally has no figure to check the declared amount against."),
            1, "/commcalc/email-imports", "Open the live login"))
    return out
