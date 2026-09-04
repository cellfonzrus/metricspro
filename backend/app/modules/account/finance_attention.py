"""FINANCE contributions to the central ADMIN ATTENTION feed (settings + imports audit, 2026-07-26).

OWNER DIRECTIVE 2026-07-26: every module audits its configurable settings, and any REQUIRED-BUT-MISSING
configuration must SURFACE to admins as a notification with plain-language fix instructions.

WHAT THIS IS
  A read-only diagnostics layer over the finance domain (account / payables / billing). It registers
  providers with platform-core's `core.import_health.register_provider()` so the existing login popup +
  /admin/import-health page pick the items up with NO change to any shared file. It computes NO money:
  every provider READS config tables and already-computed snapshots and never writes anything, never
  recomputes a statement, and never changes a booking rule, a COGS rate, a recon formula or a plan price.

WHY EACH ITEM EXISTS (a real SILENT failure — the tenant sees a plausible-looking $ with no error):
  finance_config      · the per-org accounting-config table is absent → the Accessory COGS % control on
                        Accounting → Accounting settings can't save (500) while the books quietly use 20%.
                      · NO company (legal entity) → the per-company P&L has nothing to scope to.
                      · stores unassigned while 2+ companies exist → their revenue/cost silently rolls
                        into the default company's statements.
                      · the AI narrative is off (no key) → statements are exact but unnarrated.
  finance_inventory   · Balance-Sheet "Inventory — on-hand device value" reads $0 because neither the POS
                        sweep nor a manual value has ever produced a number → assets understated, no error.
  finance_payables    · devices exist but no ACTIVE payable source map → Device Payables/Forecast is empty
                        and looks like "nothing is owed".
  finance_billing     · plan ↔ entitlement drift: the tenant's live module access does not match what its
                        subscription plan implies, so the next login SILENTLY changes what users can see
                        (incl. "plan switched off but its module restriction is still enforced").
  finance_books       · (heavy) a period has uploaded data but the books were NEVER computed, or the
                        snapshot is older than the newest upload → the P&L on screen is not the data.
  finance_integrity   · (heavy) manual journal entries filed under BOTH period spellings for the same
                        month ("June 2026" AND "2026-06") — `coa.build_inputs` reads both, so those
                        amounts are DOUBLE-COUNTED; journal labels that differ only by case/spacing split
                        one account into two lines; a Balance Sheet that does not balance.

COST DISCIPLINE (per core): cost='cheap' = config-table reads + bounded existence probes only, because
  cheap providers run on EVERY login. Anything that reads the ledger/statement payloads is cost='heavy'
  and therefore only runs for deep=1 ("Run full check" / the admin page).

MULTI-TENANT (contract §2): every read is `.eq("org_id", org_id)` with the org_id core passes in (which
  came from the tenant-middleware-rewritten query param). No house-org constant is used as a data scope,
  and no query can see another tenant's rows.

DEGRADES GRACEFULLY: if platform-core's import_health is absent, `register_provider` falls back to a
  no-op decorator and this module does nothing at all (finance never breaks because core moved). Every
  probe is individually wrapped, so an un-run migration (611 / 064 / 095 / 026) yields NO item rather
  than an exception. Never prints secret material — only the BOOLEAN presence of a credential/API key.

GROUPS: only 'import' | 'mapping' | 'duplicate' | 'other' render in the login popup (AdminAttention
  renders those four buckets), so finance items use 'other' (settings/books) and 'mapping' (source maps).
"""
from app.core.config import settings

# ── platform-core hook (guarded: a missing core module must never break finance) ─────────────────
try:                                                    # pragma: no cover - import shape, not logic
    from app.modules.core.import_health import register_provider
except Exception:                                       # pragma: no cover
    def register_provider(key, *, label, group="other", cost="cheap"):
        """No-op fallback so this module is inert when core.import_health is unavailable."""
        def _deco(fn):
            return fn
        return _deco


def _item(group, key, severity, label, detail, count, deep_link, deep_link_label):
    """The exact item shape core aggregates (built locally so we depend on no private core helper)."""
    return {"group": group, "key": key, "severity": severity, "label": label, "detail": detail,
            "count": int(count or 0), "deep_link": deep_link, "deep_link_label": deep_link_label}


def _looks_missing(err):
    """True when an exception says the TABLE/RELATION isn't there (un-run migration) rather than
    "the database is momentarily unreachable". Keeps a transient blip from telling an admin to run a
    migration that is already installed. PURE."""
    t = str(err or "").lower()
    return any(s in t for s in ("does not exist", "not find the table", "42p01", "pgrst205",
                                "schema cache", "undefined table", "unknown table"))


def _has_statements(client, org_id):
    """Bounded existence probe: has this tenant EVER produced a statement snapshot? Used to keep
    informational items off brand-new tenants that have not started using the books yet."""
    try:
        rows = (client.schema("commcalc").table("account_statements").select("org_id")
                .eq("org_id", org_id).limit(1).execute().data) or []
        return bool(rows)
    except Exception:
        return False


def _norm_label(s):
    return " ".join(str(s or "").strip().lower().split())


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# CHEAP — config tables + bounded existence probes only (these run on every login)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@register_provider("finance_config", label="Accounting configuration gaps", group="other", cost="cheap")
def _p_finance_config(client, org_id, ctx):
    """Config-only checks on the accounting settings a tenant is expected to have."""
    out = []

    # (a) per-org accounting config table (migration 611). Absent ⇒ the documented Accessory COGS %
    #     control cannot save. The books are NOT wrong (the code default 20% still applies) — the
    #     SETTING is simply unavailable, which is exactly the "configured-UI that silently fails" case.
    try:
        (client.schema("commcalc").table("account_config").select("org_id")
         .eq("org_id", org_id).limit(1).execute())
        missing_cfg = False
    except Exception as e:
        missing_cfg = _looks_missing(e)     # a transient read failure must NOT claim a missing migration
    if missing_cfg:
        out.append(_item(
            "other", "finance_account_config_missing", "info",
            "Accounting settings can't be saved yet",
            "The accounting settings table is not installed, so the Accessory COGS % on "
            "Accounting → Accounting settings cannot be changed — every company keeps using the "
            "built-in 20% default. Your current P&L numbers are unaffected. Fix: ask your MetricsPro "
            "administrator to run database migration 611_finance_account_config.sql in the Supabase "
            "SQL editor, then set the rate on the Accounting page.",
            1, "/accounts", "Open Accounting"))

    # (b) companies (multi-company books) + (c) store → company assignment
    companies = None
    try:
        from app.modules.account import coa as _coa
        companies = _coa.org_companies(client, org_id)   # canonical entity enumeration (fail closed)
    except Exception:
        companies = None

    if companies is not None and not companies:
        out.append(_item(
            "other", "finance_no_company", "warning",
            "No company (legal entity) is set up for the books",
            "The P&L and Balance Sheet are multi-company, but this tenant has no company on file, so "
            "there is no per-company view and manual journal entries cannot be attributed. Fix: open "
            "Accounting → Companies and add your operating company (legal name + EIN are optional), "
            "then assign each store to it.",
            1, "/accounts/companies", "Add a company"))
    elif companies and len(companies) > 1:
        try:
            mapped = [(r.get("store_address") or "").strip()
                      for r in ((client.schema("commcalc").table("store_mapping")
                                 .select("store_address").eq("org_id", org_id)
                                 .limit(5000).execute().data) or [])]
            assigned = {(r.get("store_address") or "").strip()
                        for r in ((client.schema("commcalc").table("store_companies")
                                   .select("store_address,company_id").eq("org_id", org_id)
                                   .limit(5000).execute().data) or [])
                        if r.get("company_id")}
        except Exception:
            mapped, assigned = [], set()
        missing = sorted({s for s in mapped if s and s not in assigned})
        if missing:
            default_name = next((c["name"] for c in companies if c.get("name") == "Default Company"),
                                companies[0].get("name") or "the first company")
            out.append(_item(
                "other", "finance_stores_unassigned", "warning",
                "Stores are not assigned to a company",
                f"{len(missing)} store(s) have no company assignment, so on the per-company P&L and "
                f"Balance Sheet their sales, costs and payroll all roll into \"{default_name}\" — the "
                f"other companies' statements are understated with no error shown. e.g. "
                + ", ".join(missing[:3])
                + ". Fix: open Accounting → Companies and pick the owning company for each store.",
                len(missing), "/accounts/companies", "Assign stores"))

    # (d) AI narrative (statements stay exact; only the written summary is missing). We report the
    #     PRESENCE of the key as a boolean and never read or print any key material. Only surfaced for
    #     a tenant that is entitled to Accounting AND already produces statements, so it is not noise
    #     for a tenant that never opens the books.
    if not settings.ANTHROPIC_API_KEY:
        entitled = True
        try:
            from app.modules.core.entitlements import module_enabled
            entitled = module_enabled(org_id, "account", client)
        except Exception:
            entitled = True
        if entitled and _has_statements(client, org_id):
            out.append(_item(
                "other", "finance_narrative_off", "info",
                "Financial narrative (AI summary) is switched off",
                "Your P&L and Balance Sheet numbers are exact and complete — only the written "
                "plain-English summary (and the credit-memo \"which days are short\" note) is missing, "
                "because the AI key is not configured on the server. Fix: an administrator sets the "
                "ANTHROPIC_API_KEY environment variable on the MetricsPro backend host and redeploys; "
                "no data changes and no recompute is needed.",
                1, "/accounts", "Open Accounting"))
    return out


@register_provider("finance_inventory", label="Balance-Sheet inventory has no value source",
                   group="other", cost="cheap")
def _p_finance_inventory(client, org_id, ctx):
    """The Balance Sheet's inventory line is $0 because neither the POS sweep nor a manual value has
    ever produced a number. Config-table reads only (inventory_value + b2b_sweep_config)."""
    try:
        rows = (client.schema("commcalc").table("inventory_value")
                .select("store,swept_value,manual_value").eq("org_id", org_id)
                .limit(5000).execute().data) or []
    except Exception:
        return []            # migration 026 not run → the feature isn't installed; nothing to report
    if any(r.get("manual_value") is not None or r.get("swept_value") is not None for r in rows):
        return []            # a value exists (manual or swept) → the line is sourced

    cfg = None
    try:
        cfg = ((client.schema("commcalc").table("b2b_sweep_config")
                .select("enabled,portal_user,last_run_at,last_status")
                .eq("org_id", org_id).limit(1).execute().data) or [None])[0]
    except Exception:
        cfg = None
    intends_sweep = bool(cfg and (cfg.get("enabled") or cfg.get("portal_user") or cfg.get("last_run_at")))
    if not intends_sweep and not _has_statements(client, org_id):
        return []            # tenant neither uses the books nor asked for the sweep → not a gap

    why = ""
    if cfg and cfg.get("enabled") and not cfg.get("portal_user"):
        why = ("The POS inventory sync is switched on but has no saved portal login, so it can never "
               "run. ")
    elif cfg and str(cfg.get("last_status") or "").lower() not in ("", "ok", "success"):
        why = f"The last POS inventory sync ended in \"{cfg.get('last_status')}\". "
    elif cfg and not cfg.get("last_run_at"):
        why = "The POS inventory sync has never completed a run. "
    return [_item(
        "other", "finance_inventory_unsourced", "warning" if intends_sweep else "info",
        "Inventory reads $0 on the Balance Sheet",
        why + "No on-hand inventory value has ever been swept from the POS or entered by hand, so the "
        "Balance Sheet's \"Inventory — on-hand device value\" line is $0 and total assets are "
        "understated. Fix (either one): enter the value per store on Accounting → Inventory Values, or "
        "save the POS (B2B Soft) portal username + password on the connectors page so the nightly sync "
        "can fill it in. Recompute the period afterwards so the stored Balance Sheet picks it up.",
        1, "/accounts/inventory", "Enter inventory values")]


@register_provider("finance_payables", label="Device payables source map missing",
                   group="mapping", cost="cheap")
def _p_finance_payables(client, org_id, ctx):
    """Devices exist but no ACTIVE payable source map → the Device Payables / Forecast reports are
    empty and read as "nothing is owed". Config read + one bounded existence probe."""
    try:
        maps = (client.schema("commcalc").table("payable_source_map").select("id")
                .eq("org_id", org_id).eq("is_active", True).limit(5).execute().data) or []
    except Exception:
        return []            # migration 095 not run → the module isn't installed here
    if maps:
        return []
    try:
        devices = (client.schema("commcalc").table("asset_ledger").select("org_id")
                   .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return []
    if not devices:
        return []            # no devices → an empty payables ledger is correct, not a gap
    return [_item(
        "mapping", "finance_payable_source_map", "warning",
        "Device payables are not mapped to any carrier source",
        "This tenant has devices on file but no active payable source mapping, so the Device Payables "
        "and Forecast reports are empty — it looks like nothing is owed to the distributor. Fix: open "
        "Payables → Source maps and add one row per carrier telling MetricsPro which column carries the "
        "amount owed, the sale, and the reimbursement.",
        1, "/commcalc/payables", "Map payable sources")]


@register_provider("finance_billing", label="Subscription plan vs module access",
                   group="other", cost="cheap")
def _p_finance_billing(client, org_id, ctx):
    """Plan ↔ entitlement drift. The entitlement engine is ALL-ACCESS by default and a plan RESTRICTS;
    it re-reconciles on every login, so any drift means module access is about to change silently."""
    try:
        plans = (client.schema("storeops").table("billing_plan")
                 .select("basis,unit_price,cycle,modules,is_active")
                 .eq("org_id", org_id).limit(1).execute().data) or []
    except Exception:
        return []            # migration 064 not run → tenant billing isn't installed
    plan = plans[0] if plans else None
    plan_modules = [m for m in ((plan or {}).get("modules") or []) if str(m or "").strip()]

    try:
        from app.modules.core.entitlements import (canonical_module_key, load_module_catalog,
                                                   effective_modules)
    except Exception:
        return []
    try:
        catalog = load_module_catalog(client)
        effective = effective_modules(client, org_id)
    except Exception:
        return []
    try:
        tm = (client.schema("storeops").table("tenant_modules").select("module_key,is_enabled")
              .eq("org_id", org_id).limit(500).execute().data) or []
    except Exception:
        tm = []

    out = []
    # (a) a plan switched OFF still restricts access — is_active only affects the MRR roll-up.
    if plan and plan.get("is_active") is False and plan_modules:
        out.append(_item(
            "other", "finance_plan_inactive_restricting", "warning",
            "Subscription plan is switched off but still limits module access",
            f"This tenant's plan is marked inactive (so it is excluded from revenue totals) but its "
            f"module list is still what decides access — the tenant remains limited to "
            f"{len(plan_modules)} module(s). Fix: to give full access again, clear the plan's module "
            f"list (all modules checked = full access) or delete the plan; to keep the restriction, "
            f"mark the plan active so billing and access agree.",
            1, "/admin/billing", "Review the plan"))

    # (b) module keys in the plan that do not exist → silently ignored, so the customer is missing a
    #     module it was priced for.
    unknown = sorted({str(m) for m in plan_modules if canonical_module_key(str(m)) not in catalog})
    if unknown:
        out.append(_item(
            "other", "finance_plan_unknown_modules", "warning",
            "Subscription plan lists modules that don't exist",
            f"{len(unknown)} entry in this tenant's plan does not match any MetricsPro module "
            f"({', '.join(unknown[:4])}), so it is ignored — the tenant is not getting what the plan "
            f"says it pays for. Fix: re-pick the modules on the billing page (use the checkboxes, "
            f"don't type keys).",
            len(unknown), "/admin/billing", "Re-pick modules"))

    # (c) DRIFT: what the tenant can see today vs what the plan implies. sync_tenant() overwrites
    #     tenant_modules with `effective` on the next login, so any difference = a silent change.
    if tm:
        current_on = {canonical_module_key(r.get("module_key")) for r in tm if r.get("is_enabled")}
        known_on = {k for k in current_on if k in catalog}
        will_enable = sorted(effective - known_on)
        will_disable = sorted(known_on - effective)
        if will_enable or will_disable:
            bits = []
            if will_disable:
                bits.append("will be TURNED OFF: " + ", ".join(catalog.get(k, k) for k in will_disable[:5]))
            if will_enable:
                bits.append("will be TURNED ON: " + ", ".join(catalog.get(k, k) for k in will_enable[:5]))
            reason = ("This tenant has no subscription plan, so MetricsPro treats it as full access and "
                      "any module you switched off by hand is switched back on."
                      if not plan else
                      "MetricsPro re-applies the subscription plan's module list on every login.")
            out.append(_item(
                "other", "finance_entitlement_drift", "warning",
                "Module access does not match the subscription plan",
                f"{reason} On the next login this tenant's access changes: " + "; ".join(bits) +
                ". Fix: set the intended modules on the plan (Admin → Billing) — that is the only "
                "setting that sticks.",
                len(will_enable) + len(will_disable), "/admin/billing", "Set plan modules"))

    # (d) a priced plan with no price generates $0 invoices.
    if plan and plan.get("is_active") is not False:
        try:
            price = float(plan.get("unit_price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price <= 0:
            out.append(_item(
                "other", "finance_plan_no_price", "info",
                "Subscription plan has no price",
                "This tenant's plan is active but its unit price is 0, so every invoice generated from "
                "it comes out at $0.00. Fix: set the price per "
                f"{(plan.get('basis') or 'flat').replace('_', ' ')} / {plan.get('cycle') or 'monthly'} "
                "on the billing page (or leave it at 0 deliberately for an internal/free tenant).",
                1, "/admin/billing", "Set the price"))
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# HEAVY — reads the ledger / statement snapshots (deep=1 only: a login must never pay for this)
# ══════════════════════════════════════════════════════════════════════════════════════════════════
@register_provider("finance_books", label="Books never computed / out of date",
                   group="other", cost="heavy")
def _p_finance_books(client, org_id, ctx):
    """A period has uploaded data but no statement snapshot, or the snapshot predates the newest
    upload. Reuses the module's OWN staleness probe so it can never disagree with the P&L banner."""
    try:
        from app.modules.account import autocompute
    except Exception:
        return []
    try:
        prior, current = autocompute.current_and_prior_periods()
    except Exception:
        return []
    out = []
    for period in (current, prior):
        try:
            newest = autocompute.newest_ingest_at(client, org_id, period)
        except Exception:
            continue
        if not newest:
            continue                     # no data for the period → nothing to compute (correct)
        try:
            computed = autocompute.statements_computed_at(client, org_id, period)
        except Exception:
            continue
        if computed is None:
            out.append(_item(
                "other", f"finance_books_never:{period}", "warning",
                f"The books for {period} have never been computed",
                f"{period} has uploaded data but no P&L or Balance Sheet has ever been built for it, so "
                f"those pages read as empty. Fix: open Accounting and press Compute for {period} (or "
                f"leave it to the scheduled rebuild if one is set up).",
                1, "/accounts", "Compute the books"))
        elif autocompute._later(newest, computed):
            hrs = None
            try:
                a = autocompute._parse_iso(newest)
                b = autocompute._parse_iso(computed)
                hrs = round((a - b).total_seconds() / 3600.0, 1) if a and b else None
            except Exception:
                hrs = None
            out.append(_item(
                "other", f"finance_books_stale:{period}", "info",
                f"The {period} statements are older than your newest data",
                (f"New data landed {hrs:g} hours after the {period} P&L / Balance Sheet were built, so "
                 if hrs is not None else
                 f"New data has landed since the {period} P&L / Balance Sheet were built, so ") +
                "the figures on screen are not your latest numbers. Fix: press Recompute on the P&L or "
                "Balance Sheet page (nothing else changes — the numbers are rebuilt from your data).",
                1, "/accounts/pl", "Recompute"))
    return out


@register_provider("finance_integrity", label="Journal / statement integrity",
                   group="other", cost="heavy")
def _p_finance_integrity(client, org_id, ctx):
    """Silent DOUBLE-COUNT and split-line conditions in the manual journal, plus a Balance Sheet that
    does not balance. Reports only — a money-moving correction is the owner's call, never automatic."""
    from app.modules.account import _period as fin_period
    out = []

    # (a) journal entries filed under TWO spellings of the same month. Uploaded data is read under BOTH
    #     spellings (coa._period.period_keys, by design), but the MANUAL journal is read for the ONE
    #     spelling the statements were computed with (engine.compute_and_store) — so whichever set does
    #     not match is silently LEFT OFF the statements. Proven offline 2026-07-26.
    rows = []
    try:
        rows = (client.schema("commcalc").table("journal_entries")
                .select("period,account_line,statement,amount")
                .eq("org_id", org_id).limit(20000).execute().data) or []
    except Exception:
        rows = []
    by_month = {}
    for r in rows:
        p = (r.get("period") or "").strip()
        if not p:
            continue
        pm, py = fin_period.parse_period(p)
        if not pm or not py:
            continue
        m = by_month.setdefault((py, pm), {})
        try:
            amt = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        s = m.setdefault(p, {"n": 0, "amt": 0.0})
        s["n"] += 1
        s["amt"] += amt
    dupes = {k: v for k, v in by_month.items() if len(v) > 1}
    if dupes:
        eg = []
        for (py, pm), spellings in sorted(dupes.items(), reverse=True)[:2]:
            eg.append(" + ".join(f"\"{k}\" ({v['n']} entries, ${v['amt']:,.2f})"
                                 for k, v in sorted(spellings.items())))
        out.append(_item(
            "duplicate", "finance_journal_period_spellings", "warning",
            "Manual journal entries exist under two spellings of the same month",
            f"{len(dupes)} month(s) have journal entries saved under more than one way of writing the "
            f"month (e.g. {'; '.join(eg)}). The statements pick up the manual journal for ONE of those "
            f"spellings only, so the other set is silently left OFF your P&L and Balance Sheet. Fix: "
            f"open Accounting → Journal for that month (the page always uses the \"June 2026\" style), "
            f"re-enter anything that is missing, and delete the set filed the other way — then "
            f"recompute. Check with your accountant first: this changes reported numbers.",
            len(dupes), "/accounts/journal", "Review the journal"))

    # (b) journal labels that differ only by case/spacing. Journal lines fold into a statement line by
    #     EXACT label match, so "Rent" and "rent " become two separate lines on the same statement.
    groups = {}
    for r in rows:
        lbl = (r.get("account_line") or "").strip()
        if not lbl:
            continue
        groups.setdefault((r.get("statement") or "", _norm_label(lbl)), set()).add(lbl)
    split = {k: v for k, v in groups.items() if len(v) > 1}
    if split:
        eg = "; ".join(" / ".join(sorted(v)) for v in list(split.values())[:3])
        out.append(_item(
            "other", "finance_journal_label_split", "info",
            "Journal account names differ only by capitalisation or spacing",
            f"{len(split)} account name(s) are entered more than one way ({eg}), and the statements "
            f"match these names exactly — so one account shows up as two separate lines. Fix: open "
            f"Accounting → Journal and make the spelling identical (copy the existing name).",
            len(split), "/accounts/journal", "Tidy the journal"))

    # (c) the SAME month computed twice under two period spellings. Snapshots are keyed by the literal
    #     period string, and the retained-earnings roll-up sums EVERY prior P&L snapshot for the scope —
    #     so a month present twice adds its net income twice to Retained Earnings, and the page (which
    #     always asks for the "June 2026" form) can even read "not computed" while a numeric-spelling
    #     snapshot exists. PROVEN offline 2026-07-26 (100 → 200). Report only: fixing it moves equity.
    try:
        srows = (client.schema("commcalc").table("account_statements").select("period")
                 .eq("org_id", org_id).eq("statement_type", "pl").eq("scope_key", "consolidated")
                 .limit(2000).execute().data) or []
    except Exception:
        srows = []
    smonths = {}
    for r in srows:
        p = (r.get("period") or "").strip()
        pm, py = fin_period.parse_period(p)
        if pm and py:
            smonths.setdefault((py, pm), set()).add(p)
    sdupe = {k: v for k, v in smonths.items() if len(v) > 1}
    if sdupe:
        eg = "; ".join(" + ".join(sorted(v)) for v in list(sdupe.values())[:2])
        out.append(_item(
            "duplicate", "finance_statement_period_spellings", "warning",
            "The same month's books were computed twice under different month spellings",
            f"{len(sdupe)} month(s) have TWO sets of statements because the month was written two "
            f"different ways (e.g. {eg}). Retained Earnings adds that month's profit twice, and the "
            f"Accounting pages only ever read the \"June 2026\" style — so the other set is invisible "
            f"while still distorting equity. Fix: ask your MetricsPro administrator to delete the "
            f"duplicate (numeric-style) set for that month and recompute using the month-name period "
            f"picker. This changes reported equity, so confirm before deleting.",
            len(sdupe), "/accounts/balance-sheet", "Open the Balance Sheet"))

    # (d) Balance Sheet that does not balance (the engine already computes this flag; we surface it).
    try:
        from app.modules.account import autocompute
        prior, current = autocompute.current_and_prior_periods()
        periods = [current, prior]
    except Exception:
        periods = []
    for period in periods:
        try:
            srows = (client.schema("commcalc").table("account_statements").select("payload")
                     .eq("org_id", org_id).eq("period", period)
                     .eq("statement_type", "balance_sheet").eq("scope_key", "consolidated")
                     .limit(1).execute().data) or []
        except Exception:
            continue
        if not srows:
            continue
        p = srows[0].get("payload") or {}
        if p.get("balanced") is False:
            try:
                imb = float(p.get("imbalance") or 0)
            except (TypeError, ValueError):
                imb = 0.0
            out.append(_item(
                "other", f"finance_bs_imbalance:{period}", "info",
                f"The {period} Balance Sheet does not balance",
                f"Assets are ${abs(imb):,.2f} "
                f"{'more' if imb > 0 else 'less'} than Liabilities + Equity. This is normal until the "
                f"hand-entered opening figures are in: cash / bank, fixtures & equipment and owner "
                f"capital are manual lines. Fix: enter them for {period} on Accounting → Journal and "
                f"recompute.",
                1, "/accounts/journal", "Enter opening balances"))
    return out
