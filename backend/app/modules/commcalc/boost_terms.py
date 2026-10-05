"""THE Boost KPI-tier engine's TERMS — one home for "what does this tenant's Boost config actually say".

WHY THIS MODULE EXISTS (owner directive 2026-10-05: *"give the option to create this pdf on the
incentive payout module"*, and the design-fix rule — one fact, one home, dereferenced, never copied).

MetricsPro pays through TWO engines. The configurable Commission Plans / Payout Schedules engine
(`commission_engine`) already had an employee-facing document describing it — `payout_structure.py`,
the "Incentive Payout Structure" PDF. The OTHER engine, the Boost KPI-tier model in
`calculator.calc_rep_commissions`, had none: a Boost tenant has ZERO `commission_plan` rows (measured
live 2026-10-05: org `…0001` = Cellfonz R Us = Boost, 0 plans), so that PDF described nothing their
employees are actually paid by, and the endpoint 400'd. The class defect is not "Boost has no PDF" —
it is that *the employee payout-structure document knew only one of the two pay engines*.

Fixing that needs the Boost terms in a place BOTH the engine and the document can read. Writing them
out a second time inside the document is precisely the divergence this house forbids: the document
would print a rate the engine does not pay the moment either literal moved. So the `G` dict that
`calc_rep_commissions` built inline — every rate, every spiff, every tier threshold, with its exact
`or`/`is not None` fallback — moved HERE unchanged, and the engine now dereferences it.

BYTE-IDENTICAL BY CONSTRUCTION. `resolve_terms` reproduces the engine's former expression for every
key, character for character, INCLUDING the two known quirks it is not this change's business to fix:

  ① `cfg.get('acc_rate') or 0.10` — a stored 0 falls back to 10%, it does not mean "pay nothing".
  ② `cfg.get('setup_fee_rate') or 0.10` — the same, and already REPORTED rather than fixed in the
     engine's own comment (changing it would silently move money for any tenant who stored a 0).

The `or`-vs-`is not None` split is likewise preserved per key: `upgrade_flat`, `premium_flat`,
`byod_flat` and `trade_in_spiff`/`acima_spiff` honour a stored 0, while the rates above do not.
`harness_boost_terms.py` replays the retired inline expression over the live configs and a grid of
edge-case shapes and asserts equality; `harness_boost_terms_lock.py` fails the build if the engine
stops dereferencing this module or a second copy of the defaults reappears under `backend/app`.

PURITY: stdlib only, no DB client, no reportlab, no app imports beyond the KPI definition helper the
targets are resolved against. The document builder and the pay engine both import it freely.
"""

# ── THE DEFAULTS ──────────────────────────────────────────────────────────────────────────────────
# What a key means when the tenant's `commcalc.payout_config` row does not carry it. These are the
# engine's own literals, not a fresh opinion about what Boost pays. `frontend/.../commcalc/settings`
# holds a display copy for its editor's empty state; `GET /commcalc/payout-terms/{period}` serves
# THESE, so a surface that wants the real answer asks rather than guessing.
DEFAULTS = {
    "upgrade_flat": 20,
    "premium_flat": 5,
    "byod_flat": 3,
    "byod_extra": 0,
    "trade_in_spiff": 20,
    "acima_spiff": 25,
    "acc_rate": 0.10,
    "setup_rate": 0.10,
    "acc_target_pct": 0.10,
    "t100": 7,
    "t75": 5,
    "t75pct": 0.75,
    "t50pct": 0.50,
}


def resolve_terms(cfg):
    """The Boost engine's resolved terms for ONE period's `payout_config` row. PURE.

    Returns the dict `calc_rep_commissions` used to build inline as `G`. Same keys, same order, same
    fallbacks — see this module's header for why the two `or`-quirks are preserved deliberately.
    """
    cfg = cfg or {}
    return {
        'upgrade_flat':     cfg.get('upgrade_flat') if cfg.get('upgrade_flat') is not None else 20,
        'premium_flat':     cfg.get('premium_flat') if cfg.get('premium_flat') is not None else 5,
        'byod_flat':        cfg.get('byod_flat') if cfg.get('byod_flat') is not None else 3,
        'byod_extra':       cfg.get('byod_extra_spiff') or 0,
        'trade_in_spiff':   cfg.get('trade_in_spiff') if cfg.get('trade_in_spiff') is not None else 20,
        'acima_spiff':      cfg.get('acima_spiff') if cfg.get('acima_spiff') is not None else 25,
        'acc_rate':         cfg.get('acc_rate') or 0.10,
        # The employee's share of the set-up fee COLLECTED. `payout_config.setup_fee_rate` remains the
        # source of truth for this (Boost) engine and still wins, so Boost pay is unchanged; the
        # per-carrier `setup_fee_pay` config (mig 263) is what the PLAN engine reads for every other
        # carrier. `or 0.10` is preserved verbatim, including its known quirk that a stored 0 falls back
        # to 10% — changing that here would silently move money for any tenant who stored a 0, so it is
        # REPORTED in the park record instead of fixed in the same breath as everything else.
        'setup_rate':       cfg.get('setup_fee_rate') or 0.10,
        'acc_target_on':    bool(cfg.get('acc_target_enabled', False)),
        'acc_target_pct':   cfg.get('acc_target_pct') or 0.10,
        'custom_spiffs':    cfg.get('custom_spiffs') or [],
        'straight':         bool(cfg.get('straight_line', False)),
        't100':             int(cfg.get('tier_100_min_kpis') or 7),
        't75':              int(cfg.get('tier_75_min_kpis') or 5),
        't75pct':           float(cfg.get('tier_75_pct') or 0.75),
        't50pct':           float(cfg.get('tier_50_pct') or 0.50),
    }


def resolve_kpi_targets(cfg, kpi_defs):
    """{metric_key: target} for the metrics this tenant is SCORED on. PURE.

    Lifted verbatim from the pay engine, where it was the loop that built `KPI`. Its two rules matter
    enough to restate, because the employee document now prints the same map:

    * The per-period `payout_config` column wins over the KPI definition's own default. The `or dflt`
      fallback is preserved verbatim, including its known quirk that a stored 0 falls back to the
      default.
    * **A FALSY TARGET IS NO TARGET**, not a target of zero that every value "meets" — a metric with
      neither a stored target nor a default is left OUT of the map on purpose, and
      `kpi_failing.evaluate` then skips it, because a metric with no target cannot fail anyone. The
      document must leave it out for the same reason: printing "target 0" would tell an employee they
      have already met a bar nobody set.

    `kpi_defs` is `kpi_failing.resolve_defs(...)` output — (key, label, payout_config_col, default).
    """
    cfg = cfg or {}
    out = {}
    for (_k, _label, _col, _dflt) in (kpi_defs or ()):
        _t = cfg.get(_col)
        if not _t:
            _t = _dflt
        try:
            _t = float(_t) if _t is not None else None
        except (TypeError, ValueError):
            _t = None
        if _t:
            out[_k] = _t
    return out
