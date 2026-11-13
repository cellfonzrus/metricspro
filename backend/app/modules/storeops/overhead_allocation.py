"""storeops OVERHEAD ALLOCATION — salaried staff who belong to no store (proof:
backend/harness_overhead_allocation.py).

OWNER DIRECTIVE 2026-09-09 (verbatim): "payroll needs to be attributed to each store rather than teh
company as that will give the real picture of the store perfromace, the company will only calculate
as a result of all store combined together.  All employees who are salaried and not attached to
stores like the DM and market manager shoudl have thier won seaprat line and thier salary will be
divided amongs he store they handle , if th market manager is 78000 and he handles all markets then
his salary will be 78000/ the number of stores in each market, simialry thier commission will also
be a seaprate line item so at the end of the year we know who got paid how much with clear
distinction , all these will be a seaprate box in the p&l under wages"

WHAT THIS MODULE IS AND IS NOT
------------------------------
It is NOT a second wage derivation and it computes NO new dollar of pay. The platform already has:

  • `account/coa.derive_wage_cells`  — THE per-store wage estimate (hourly hours x rate; salaried
    monthly-equivalent allocated across the stores the person actually worked). Untouched.
  • `account/coa.monthly_salary_equivalent` — THE weekly/monthly/annual -> monthly conversion. This
    module never re-implements it; the caller injects it (`monthly_equivalent=`) so there is exactly
    one conversion table in the codebase.
  • `storeops/router.get_payroll_by_store` — THE per-store salary figure. Untouched.
  • `storeops/salary_expense.build_store_folder` / `fold_store_rows` — THE store-code fold. Reused
    by the IO layer below rather than re-derived.
  • `storeops.org_span_for_manager` (mig 050, index §13) — THE canonical "which stores does this
    manager cover" answer. Consulted FIRST and used AS-IS.

What it ADDS is the one thing none of those can express: a salaried person who is attached to NO
store at all. `derive_wage_cells` gives such a person the key `None` — "company-wide" — and under
`payroll_authority_grain='store'` (live for the reporting tenant) `coa.build_inputs` deliberately
does NOT book a company-wide cell, because it cannot prove the dollars are absent from the entered
per-store figures. Correct, and it means their pay lands NOWHERE. The owner's answer is not to book
them company-wide, it is to SPREAD them over the stores they cover and give them their own line.

THE ALLOCATION BASIS — the ambiguity in the directive, stated rather than silently resolved
--------------------------------------------------------------------------------------------
The directive gives two readings that are NOT the same arithmetic:

  (A) "their salary will be divided among the stores they handle"
  (B) "if the market manager is 78000 and he handles all markets then his salary will be
      78000 / the number of stores in EACH MARKET"

(A) is one division over the whole covered set. (B) reads as a per-market division, which for a
person covering several markets of UNEQUAL size gives each market's stores a different amount.
They coincide only when every covered market has the same store count.

  BASIS_EQUAL_STORES (the DEFAULT, reading A) — monthly salary / |covered stores|. Every store the
      person covers carries the same share of them. This is the defensible reading: it is what "the
      stores they handle" says without adding a grouping the sentence never asks for, it is stable
      when a market gains or loses a store, and it is what the reporting tenant is already doing by
      hand (twenty equal per-store rows).
  BASIS_MARKET_THEN_STORE (reading B) — the salary splits equally across the covered MARKETS first,
      then equally across the stores inside each market. A person covering a 13-store market and a
      7-store market gives the 7-store market's stores nearly twice the per-store load.
  BASIS_WEIGHTED (neither reading; offered because it is the one accountants ask for) — pro-rata on
      a caller-supplied weight per store (revenue, gross profit, labour hours). A big store carries
      more of the overhead that serves it.

The basis is CONFIG (`overhead_config.basis`), so switching readings is a row, never a deploy.
`allocate()` returns the basis it ACTUALLY used, and degrades LOUDLY: a weighted basis with no
usable weights falls back to an equal split and says so in `basis_used`, so a report can never
present an equal split as though it were revenue-weighted.

COMPANY IS THE SUM OF STORES, NEVER ITS OWN ALLOCATION
------------------------------------------------------
The owner is explicit: "the company will only calculate as a result of all store combined together."
Every function here emits PER-STORE cells only. There is no company-wide bucket to book into, and
`company_total()` exists solely to prove the identity company == sum(stores) rather than to compute
a second figure. A dollar this module cannot attribute to a store is REPORTED in `unallocated` — it
is never quietly moved to a company line, and it is never dropped.

CENTS. Each person's per-store shares are cents-rounded with the LAST store (deterministic sort
order) absorbing the residue, so every individual's stores sum EXACTLY to their monthly figure and
the org total is exact by construction. Same convention as `derive_wage_cells`.

RULE TWO. No tenant, store, role, employee or carrier name appears in this file. Which roles count
as overhead beyond the structural test, the line labels, the basis, the coverage fallback and
whether commission is derived at all are per-org config rows over the house defaults below. The
house default is `mode='off'`: with no config row this module books nothing and every org's
statement is byte-identical.
"""
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ── allocation bases ─────────────────────────────────────────────────────────────────────────────
BASIS_EQUAL_STORES = "equal_stores"
BASIS_MARKET_THEN_STORE = "equal_market_then_store"
BASIS_WEIGHTED = "weighted"
BASES = (BASIS_EQUAL_STORES, BASIS_MARKET_THEN_STORE, BASIS_WEIGHTED)

# ── how an overhead person's covered store set was resolved (most authoritative first) ───────────
COVER_ORG_SPAN = "org_span"      # storeops.org_span_for_manager — the canonical manager->stores RPC
COVER_ORG_UNIT = "org_unit"      # the subtree of the unit the employee is PLACED in
COVER_MARKET = "market"          # a unit in that subtree IS a market: core.scope's store->market answer
COVER_ORG_UNIT_MARKET = "org_unit_market"   # both rungs contributed (the union, named honestly)
COVER_ORG_WIDE = "org_wide"      # no unit at all: every active store in the org
COVER_NONE = "none"              # nothing resolved — REPORTED, never guessed
COVERS = (COVER_ORG_SPAN, COVER_ORG_UNIT, COVER_MARKET, COVER_ORG_UNIT_MARKET,
          COVER_ORG_WIDE, COVER_NONE)

# ── which DECLARED org level a person sits at (owner ask 2026-11-13) ─────────────────────────────
LEVEL_UNIT = "unit_level"        # the level of the unit in employees.org_unit_id
LEVEL_MANAGED = "managed_level"  # the highest level among the units they MANAGE (org_managers)
LEVEL_NONE = "none"              # the org declared no level that fits — SAID, never guessed

# The P&L line key family for the per-level lines. The key is derived from the level's RANK, which
# `storeops.org_levels` makes unique per org — deliberately NOT from the level's NAME, because a
# tenant's own words must never become a stored line key that another tenant's statement is read
# against. The tenant's words appear in the LABEL only. §STOREOPS-09.
LEVEL_LINE_PREFIX = "overhead_wages_l"
# The anchor the per-level lines are inserted AFTER in the chart, so the wages box reads as one box,
# and the chart attributes they inherit from it: operating expense, materialise only when they carry
# value, STORE grain (the 2026-09-09 directive forbids a company-wide overhead bucket). They are
# named here, outside the functions that build a line, so those functions hold no vocabulary literal
# at all — the lock in harness_overhead_level_lines.py §6(a) asserts exactly that.
LEVEL_LINE_AFTER = "overhead_wages"
LEVEL_LINE_SECTION = "opex"
LEVEL_LINE_KIND = "auto_opt"
LEVEL_LINE_GRAIN = "store"

# ── why an employee is (or is not) overhead ──────────────────────────────────────────────────────
WHY_UNATTACHED = "salaried_unattached"   # structural: salaried, active, no home store
WHY_ROLE = "configured_role"             # the org listed this role as overhead
WHY_INACTIVE = "inactive"
WHY_NOT_SALARIED = "not_salaried"
WHY_STORE_ATTACHED = "store_attached"

# ── config (RULE TWO — house defaults; per-org JSONB `account_config.overhead_config`, mig 997) ───
DEFAULT_CONFIG = {
    # 'off' = HOUSE DEFAULT. Nothing is derived, nothing is booked, every org byte-identical.
    # 'derive' = build the two overhead lines from the roster.
    "mode": "off",
    "basis": BASIS_EQUAL_STORES,
    # How far to look for "the stores they handle" when the canonical manager RPC returns nothing.
    # 'org_span' = the RPC only (strictest); 'org_unit' = also the subtree of the unit they sit in;
    # 'org_wide' = also every active store when they sit at no unit at all.
    "span_fallback": COVER_ORG_UNIT,
    # EXTRA roles to treat as overhead even when they DO carry a home store (a DM pinned to a store
    # for scheduling). Empty default = the structural test alone, so no role string lives in code.
    "roles": [],
    # Include inactive stores in the covered set? A closed store should not carry next month's DM.
    "include_inactive_stores": False,
    "wages_label": "Overhead salaries (allocated to stores)",
    "commission_label": "Overhead commission (allocated to stores)",
    # ── ONE LINE PER ORG LEVEL (owner ask 2026-11-13) ───────────────────────────────────────────
    # "add a line in the expenses with the name dm salary and rm salary or mm salary , if we add
    # another level the expense report should automatically add they level"
    #
    # 'on' (default) = one wages line per level the tenant DECLARES in storeops.org_levels, named
    # from that tenant's own level name. Adding a level makes its line appear with no deploy and no
    # config edit — that is the ask. 'off' = the single combined `wages_label` line only.
    #
    # Defaulting this ON moves nothing: the module's own `mode` default is 'off', so no org derives
    # anything at all until its owner opts in, and byte-identity rests there, not here.
    "level_lines": "on",
    # The POLICY template the tenant's own level NAME is rendered into. A format string is a house
    # policy and is allowed a default; the level NAME is tenant vocabulary and is NOT — it is a
    # required argument with no house words behind it (owner directive 2026-10-30). `{level}` is
    # substituted literally; a template missing it is refused back to this default rather than
    # producing an unnamed line.
    "level_label_template": "{level} salaries (allocated to stores)",
    # 'off' = HOUSE DEFAULT: no commission line is derived. 'management_incentive' = read the
    # ALREADY-COMPUTED payout from commcalc.management_incentive_payout. This module never computes
    # a commission amount; it only places one that another engine already decided.
    "commission_source": "off",
    # management_incentive_payout.status values that count as owed. Draft is deliberately excluded.
    "commission_statuses": ["approved", "paid"],
    # store_expenses.expense_name values that carry the SAME overhead salary the org already enters
    # by hand. Empty default = nothing is claimed. Used ONLY to REPORT the overlap (see
    # `reconcile_manual`); this module never deletes an expense row.
    "manual_expense_names": [],
}
_CONFIG_FIELDS = tuple(DEFAULT_CONFIG.keys())


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _s(v) -> str:
    return str(v or "").strip()


def resolve_config(org_row: Optional[dict] = None) -> dict:
    """Merge a (possibly partial / absent / malformed) per-org config over the house defaults.

    Same posture as `salary_expense.resolve_config` and `payroll_expenses.resolve_tax_config`: an org
    with no row behaves EXACTLY as the house default, and an unknown value for an enumerated field
    falls back to the default rather than inventing a fourth basis. NEVER raises."""
    eff = dict(DEFAULT_CONFIG)
    row = org_row if isinstance(org_row, dict) else {}
    for f in _CONFIG_FIELDS:
        v = row.get(f)
        if v is not None:
            eff[f] = v
    mode = _s(eff["mode"]).lower()
    eff["mode"] = mode if mode in ("off", "derive") else "off"
    basis = _s(eff["basis"]).lower()
    eff["basis"] = basis if basis in BASES else BASIS_EQUAL_STORES
    fb = _s(eff["span_fallback"]).lower()
    eff["span_fallback"] = fb if fb in (COVER_ORG_SPAN, COVER_ORG_UNIT, COVER_ORG_WIDE) else COVER_ORG_UNIT
    src = _s(eff["commission_source"]).lower()
    eff["commission_source"] = src if src in ("off", "management_incentive") else "off"
    eff["roles"] = [r for r in (_s(x) for x in (eff["roles"] or [])) if r]
    eff["commission_statuses"] = [r for r in (_s(x).lower() for x in (eff["commission_statuses"] or [])) if r]
    eff["manual_expense_names"] = [r for r in (_s(x) for x in (eff["manual_expense_names"] or [])) if r]
    eff["include_inactive_stores"] = bool(eff["include_inactive_stores"])
    eff["wages_label"] = _s(eff["wages_label"]) or DEFAULT_CONFIG["wages_label"]
    eff["commission_label"] = _s(eff["commission_label"]) or DEFAULT_CONFIG["commission_label"]
    ll = _s(eff["level_lines"]).lower()
    eff["level_lines"] = ll if ll in ("on", "off") else "on"
    tpl = _s(eff["level_label_template"])
    eff["level_label_template"] = tpl if "{level}" in tpl else DEFAULT_CONFIG["level_label_template"]
    return eff


# ── 1. who is overhead ───────────────────────────────────────────────────────────────────────────
def classify_employee(emp: dict, cfg: dict, monthly_equivalent: Callable[[object, object], Optional[float]]
                      ) -> Tuple[bool, str, float]:
    """(is_overhead, why, monthly_salary) for ONE roster row.

    `monthly_equivalent(pay_basis, pay_amount)` is INJECTED — in production it is
    `account.coa.monthly_salary_equivalent`, the platform's ONE weekly/monthly/annual conversion.
    Passing it in keeps this module pure and keeps the conversion table single-sourced.

    THE STRUCTURAL TEST, which needs no role string and therefore no RULE TWO violation:
      active  AND  salaried (a usable monthly equivalent)  AND  no home_store
    That is exactly the person `derive_wage_cells` can only key as company-wide, i.e. exactly the
    person whose pay currently lands nowhere. An org may WIDEN it with `roles` (a DM who is pinned
    to a store for scheduling but is paid to cover a market); it can never be narrowed, because a
    salaried person attached to no store has no other home in the books.

    An INACTIVE employee is never overhead: they are no longer earning, and booking a leaver's
    salary across stores would overstate every store's cost. Mirrors `derive_wage_cells`'s own
    inactive-and-no-hours rule."""
    if emp.get("is_active") is False:
        return False, WHY_INACTIVE, 0.0
    meq = None
    try:
        meq = monthly_equivalent(emp.get("pay_basis"), emp.get("pay_amount"))
    except Exception:
        meq = None
    if meq is None or _f(meq) <= 0:
        return False, WHY_NOT_SALARIED, 0.0
    role = _s(emp.get("role")).lower()
    if role and role in {r.lower() for r in (cfg.get("roles") or [])}:
        return True, WHY_ROLE, round(_f(meq), 2)
    if not _s(emp.get("home_store")):
        return True, WHY_UNATTACHED, round(_f(meq), 2)
    return False, WHY_STORE_ATTACHED, round(_f(meq), 2)


# ── 2. which stores they cover ───────────────────────────────────────────────────────────────────
def market_unit_stores(unit_ids: Optional[Iterable[str]],
                       unit_by_id: Optional[Dict[str, dict]],
                       codes_by_market: Optional[Dict[str, Iterable[str]]]) -> List[str]:
    """PURE: the store codes of every MARKET that a unit in `unit_ids` corresponds to.

    RUNG 2b OF THE SAME LADDER, dereferencing the platform's ONE store→market answer rather than
    adding a second resolver. `codes_by_market` is built by the caller from
    `core.scope.market_by_code` (§13a, the canonical union index) — this function never reads a
    table and never decides what a market is.

    A unit CORRESPONDS to a market when, case-insensitively, its NAME equals a declared market, or
    its CODE's tail after the last ':' does (`mkt:chicago`, the shape mig 050's seeder writes).
    That is a DATA-to-DATA match between two things the TENANT declared: no market name, level name
    or tenant word appears in this file.

    WHY THIS RUNG EXISTS, measured 2026-11-13. A market node can hold its stores through
    `stores.org_unit_id` OR through `stores.market` alone, and live orgs do both at once: in one
    measured tenant a market node has SIX active stores placed under it and FOUR more that carry
    that market and no unit at all. The placement walk can only see the six, so without this rung
    the manager's salary divides by six and four stores they actually cover carry none of it — a
    wrong figure that looks right. Requiring every store to be hand-placed to fix that is a wiring
    demand, not a design. The tenant and its words stay in the index and the harness, not here."""
    by_market = {_s(k).lower(): [_s(c) for c in (v or []) if _s(c)]
                 for k, v in (codes_by_market or {}).items() if _s(k)}
    if not by_market:
        return []
    out = set()
    for uid in (unit_ids or []):
        u = (unit_by_id or {}).get(uid) or (unit_by_id or {}).get(_s(uid)) or {}
        name = _s(u.get("name")).lower()
        tail = _s(u.get("code")).lower().rsplit(":", 1)[-1]
        for cand in (name, tail):
            if cand and cand in by_market:
                out.update(by_market[cand])
    return sorted(out)


def covered_stores(span_codes: Optional[Iterable[str]],
                   unit_codes: Optional[Iterable[str]],
                   org_codes: Optional[Iterable[str]],
                   cfg: dict,
                   market_codes: Optional[Iterable[str]] = None) -> Tuple[List[str], str]:
    """(sorted covered store codes, how) for ONE overhead person.

    A LADDER, most authoritative first, and it stops at the first rung that answers:

      1. `span_codes`  — the result of `storeops.org_span_for_manager` (index §13, mig 050): the
         org's own statement of which stores this person MANAGES. Used as-is; never second-guessed.
      2. `unit_codes`  — the stores under the org unit the employee is PLACED in
         (`employees.org_unit_id` -> that unit's subtree). This rung exists because the RPC keys on
         `storeops.org_managers`, and an org that has placed its people in the tree but never
         written a manager row gets an EMPTY span from rung 1 — which is a wiring gap, not a person
         who covers nothing. Gated by `span_fallback`.
      2b. `market_codes` — the stores of any MARKET that a unit in that same subtree IS
         (`market_unit_stores`, dereferencing `core.scope.market_by_code`). UNIONED with rung 2
         rather than tried after it, because a market node holds its stores through
         `stores.org_unit_id` and through `stores.market` AT ONCE in live data, and taking only the
         placed half divides a salary by the wrong store count. Gated by the same
         `span_fallback`: it is the SAME unit answered through the canonical market vocabulary, so
         it is exactly as strong a claim as rung 2 and no stronger. `span_fallback='org_span'`
         still refuses both.
      3. `org_codes`   — every (active) store, for a person sitting at no unit at all. Gated by
         `span_fallback='org_wide'` ONLY, because "covers everything" is a strong claim to make on
         a person's behalf.

    Returns ([], COVER_NONE) when nothing resolves. AN UNRESOLVED PERSON IS REPORTED, NEVER SPREAD:
    guessing a coverage set moves real salary onto stores that never had that manager, and a store
    wearing another market's overhead is worse than a gap somebody can see and fix."""
    fb = cfg.get("span_fallback", COVER_ORG_UNIT)
    span = sorted({_s(c) for c in (span_codes or []) if _s(c)})
    if span:
        return span, COVER_ORG_SPAN
    if fb in (COVER_ORG_UNIT, COVER_ORG_WIDE):
        unit = {_s(c) for c in (unit_codes or []) if _s(c)}
        mkt = {_s(c) for c in (market_codes or []) if _s(c)}
        both = unit | mkt
        if both:
            # The reason code NAMES which rung(s) answered, so every cell still says how it was
            # resolved. `org_unit_market` is not a fourth mechanism — it is the UNION of 2a and 2b
            # over the SAME subtree, reported as the union it is rather than as either half.
            how = (COVER_ORG_UNIT if not mkt else
                   COVER_MARKET if not unit else COVER_ORG_UNIT_MARKET)
            return sorted(both), how
    if fb == COVER_ORG_WIDE:
        every = sorted({_s(c) for c in (org_codes or []) if _s(c)})
        if every:
            return every, COVER_ORG_WIDE
    return [], COVER_NONE


# ── 2c. the org's DECLARED level vocabulary — a required argument, never a default ──────────────
# OWNER ASK 2026-11-13 (verbatim): "Anybody who is a dm their salary is divided among the stores they
# are responsible for , similarly for the regional manager or market manager whatever stores they
# cover their salary is divided amount their stores , add a line in the expenses with the name dm
# salary and rm salary or mm salary , if we add another level the expense report should automatically
# add they level and then divide the salary based on the people who report to them"
#
# THE TRAP THIS AVOIDS (§COMMCALC-49.10, owner directive 2026-10-30). The level names are the
# TENANT'S OWN WORDS and they differ in every tenant: measured 2026-11-13 the four live orgs declare
# FOUR DIFFERENT vocabularies, of six, two, three and six levels, agreeing on not one rung — the
# names themselves are deliberately not written here, and the cross-tenant harness reads them from
# each org's own rows. A house default behind this vocabulary would label one tenant's line with
# another tenant's word and look entirely plausible doing it. So the rows are a REQUIRED argument:
# nothing declared means nothing identified, the people fall to the combined line, and the surface
# SAYS SO.
def level_vocabulary(level_rows: Optional[Iterable[dict]]) -> List[dict]:
    """THE org's levels: [{level_id, name, rank, key}] in rank order. [] in ⇒ [] out, always.

    `level_rows` are `storeops.org_levels` rows for ONE org (`id`/`level_id`, `name`, `rank`). There
    is no fallback list: this function cannot answer for an org that has declared nothing, which is
    the whole point — a caller that has not asked the org must not be able to produce a line.

    `key` is `overhead_wages_l<rank>`: `org_levels` is UNIQUE (org_id, rank), so rank is the stable
    per-org handle, and it carries none of the tenant's vocabulary into a stored line key."""
    out: List[dict] = []
    seen = set()
    for r in (level_rows or []):
        if not isinstance(r, dict):
            continue
        name = _s(r.get("name"))
        lid = _s(r.get("id") if r.get("id") is not None else r.get("level_id"))
        try:
            rank = int(r.get("rank"))
        except (TypeError, ValueError):
            continue
        if not name or not lid or lid in seen or rank < 0:
            continue
        seen.add(lid)
        out.append({"level_id": lid, "name": name, "rank": rank,
                    "key": "%s%d" % (LEVEL_LINE_PREFIX, rank)})
    out.sort(key=lambda d: (d["rank"], d["name"]))
    return out


def level_label(level_name: str, cfg: dict) -> str:
    """The P&L label for one level's line: the TENANT'S OWN level name rendered into the house
    POLICY template (`level_label_template`) — "<the org's own level name> salaries (allocated to
    stores)" with the house template, and whatever the org configured otherwise.

    RAISES on a blank name instead of substituting anything. That refusal IS the lock the 2026-10-30
    directive asks for, expressed at runtime: there is no word this function could put in a tenant's
    label that the tenant did not declare, so a caller that has not asked the org gets an error
    rather than a plausible-looking line."""
    name = _s(level_name)
    if not name:
        # Business words only: this sentence can reach a tenant, and the infra-names lock (§19.39)
        # rightly refuses a message that names a schema or a table. WHERE the levels are stored is
        # documented in this module's header and in index §STOREOPS-09, not in an error a user sees.
        raise ValueError(
            "this organisation's own org-chart level name is required to name an overhead expense "
            "line; there is no house default for a tenant's vocabulary")
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    return cfg["level_label_template"].replace("{level}", name)


def level_of_person(unit_id: Optional[str],
                    managed_unit_ids: Optional[Iterable[str]],
                    unit_by_id: Optional[Dict[str, dict]],
                    levels: Optional[Iterable[dict]]) -> Tuple[Optional[dict], str]:
    """(the level dict from `levels`, how) for ONE overhead person — or (None, LEVEL_NONE).

    A LADDER mirroring the coverage ladder's own order of authority:
      1. the level of the unit the person is PLACED in (`employees.org_unit_id`);
      2. failing that, the HIGHEST level (lowest rank) among the units they MANAGE
         (`storeops.org_managers`) — a person who manages three districts is a district-level cost
         wherever they personally sit.

    Nothing resolves ⇒ (None, LEVEL_NONE). That person keeps the combined overhead line and the
    note says their level is unknown. Guessing a level would put real salary on a line the tenant
    reads as a different job."""
    by_id = {_s(l.get("level_id")): l for l in (levels or []) if _s((l or {}).get("level_id"))}
    if not by_id:
        return None, LEVEL_NONE
    units = unit_by_id or {}

    def _lvl(uid):
        u = units.get(uid) or units.get(_s(uid)) or {}
        return by_id.get(_s(u.get("level_id")))

    own = _lvl(_s(unit_id)) if _s(unit_id) else None
    if own:
        return own, LEVEL_UNIT
    managed = [l for l in (_lvl(_s(u)) for u in (managed_unit_ids or [])) if l]
    if managed:
        return sorted(managed, key=lambda l: (l["rank"], l["name"]))[0], LEVEL_MANAGED
    return None, LEVEL_NONE


# ── 3. the allocation itself ─────────────────────────────────────────────────────────────────────
def _spread(amount: float, codes: Sequence[str], weights: Optional[Dict[str, float]] = None
            ) -> Dict[str, float]:
    """Cents-exact spread of `amount` over `codes`. `weights` None/degenerate ⇒ equal shares.

    Deterministic: codes are taken in the order given (callers pass them sorted) and the LAST one
    absorbs the rounding residue, so sum(result.values()) == round(amount, 2) EXACTLY. Same
    convention as `coa.derive_wage_cells`, so the two allocations round alike."""
    n = len(codes)
    if n <= 0:
        return {}
    amt = round(_f(amount), 2)
    if not amt:
        return {}
    tot_w = sum(max(0.0, _f((weights or {}).get(c))) for c in codes) if weights else 0.0
    out: Dict[str, float] = {}
    allocated = 0.0
    for i, c in enumerate(codes):
        if i == n - 1:
            share = round(amt - allocated, 2)
        elif weights and tot_w > 0:
            share = round(amt * max(0.0, _f(weights.get(c))) / tot_w, 2)
        else:
            share = round(amt / n, 2)
        allocated = round(allocated + share, 2)
        out[c] = round(out.get(c, 0.0) + share, 2)
    return out


def allocate(amount: float, codes: Sequence[str], cfg: dict,
             market_of: Optional[Callable[[str], str]] = None,
             weight_of: Optional[Dict[str, float]] = None) -> Tuple[Dict[str, float], str, str]:
    """({store_code: amount}, basis_used, note) — the basis the org configured, or an honest
    degradation to the equal split with `basis_used` naming what actually happened.

    NEVER silently substitutes a basis. A weighted allocation whose weights are all zero/absent is
    NOT a revenue allocation, and a report that showed it as one would be a lie about how the money
    was spread; `basis_used` comes back as the equal split and `note` says why."""
    codes = [c for c in (codes or []) if _s(c)]
    if not codes:
        return {}, cfg.get("basis", BASIS_EQUAL_STORES), "no covered stores"
    basis = cfg.get("basis", BASIS_EQUAL_STORES)
    amt = round(_f(amount), 2)
    if basis == BASIS_MARKET_THEN_STORE and market_of is not None:
        by_market: Dict[str, List[str]] = {}
        for c in codes:
            by_market.setdefault(_s(market_of(c)) or "", []).append(c)
        markets = sorted(by_market)
        if len(markets) > 1:
            per_market = _spread(amt, markets)
            out: Dict[str, float] = {}
            for m in markets:
                for c, v in _spread(per_market.get(m, 0.0), sorted(by_market[m])).items():
                    out[c] = round(out.get(c, 0.0) + v, 2)
            return out, BASIS_MARKET_THEN_STORE, (
                "split equally across %d market(s) first, then equally within each market"
                % len(markets))
        # ONE market (or no market vocabulary): market-then-store and equal-stores are the SAME
        # arithmetic. Reported as the equal split, because that is what it is.
        return _spread(amt, sorted(codes)), BASIS_EQUAL_STORES, (
            "one market covered, so the per-market split is the equal per-store split")
    if basis == BASIS_WEIGHTED:
        tot = sum(max(0.0, _f((weight_of or {}).get(c))) for c in codes)
        if tot > 0:
            return _spread(amt, sorted(codes), weight_of), BASIS_WEIGHTED, "weighted per store"
        return _spread(amt, sorted(codes)), BASIS_EQUAL_STORES, (
            "a weighted basis was configured but no covered store has a usable weight for this "
            "period, so this is an EQUAL split — not a weighted one")
    return _spread(amt, sorted(codes)), BASIS_EQUAL_STORES, "equal across the covered stores"


# ── 4. the two lines ─────────────────────────────────────────────────────────────────────────────
def build_overhead(people: Iterable[dict], cfg: dict,
                   market_of: Optional[Callable[[str], str]] = None,
                   weight_of: Optional[Dict[str, float]] = None) -> dict:
    """The engine's answer for one period.

    `people`: one entry per OVERHEAD employee —
        {'employee_id', 'label', 'role', 'salary_month', 'commission', 'covered': [codes],
         'coverage': COVER_*, 'why': WHY_*}
    `market_of(code) -> market` is only consulted for BASIS_MARKET_THEN_STORE.
    `weight_of` is only consulted for BASIS_WEIGHTED.

    Returns {wages_by_store, commission_by_store, people, unallocated, totals, config}:
      wages_by_store       — what to add to the P&L `overhead_wages` line, per STORE. No company key.
      commission_by_store  — the same for `overhead_comm`.
      people               — the per-person audit row the owner asked for ("at the end of the year we
                             know who got paid how much with clear distinction"): every person, their
                             monthly salary and commission, how many stores they cover, how that
                             coverage was resolved, the basis actually used, and their per-store split.
      unallocated          — every person whose coverage resolved to NOTHING, with their dollars.
                             REPORTED, never spread and never folded into a company line.
      totals               — allocated/unallocated sums + the identity check inputs.
    """
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    wages: Dict[str, float] = {}
    comm: Dict[str, float] = {}
    rows: List[dict] = []
    unallocated: List[dict] = []
    # Per-level accumulation rides ALONGSIDE `wages_by_store`, keyed by the level_id the caller
    # resolved with `level_of_person`. It is the SAME cells, grouped — never a second allocation —
    # so `sum(by_level) == wages_by_store` holds cell for cell and is asserted in the harness.
    by_level: Dict[str, Dict[str, float]] = {}
    for p in (people or []):
        codes = [c for c in (p.get("covered") or []) if _s(c)]
        sal = round(_f(p.get("salary_month")), 2)
        cm = round(_f(p.get("commission")), 2)
        if not codes:
            unallocated.append({
                "employee_id": p.get("employee_id"), "label": p.get("label"), "role": p.get("role"),
                "salary_month": sal, "commission": cm, "coverage": COVER_NONE,
                "reason": ("no covered stores could be resolved for this person — they manage no unit "
                           "in the org tree, sit at no unit, and have no home store. Their pay is "
                           "REPORTED here and booked NOWHERE: spreading it over a guessed store set "
                           "would move real salary onto stores this person never covered."),
            })
            continue
        w_cells, basis_used, note = allocate(sal, codes, cfg, market_of, weight_of)
        c_cells, c_basis, _c_note = allocate(cm, codes, cfg, market_of, weight_of)
        for k, v in w_cells.items():
            wages[k] = round(wages.get(k, 0.0) + v, 2)
        for k, v in c_cells.items():
            comm[k] = round(comm.get(k, 0.0) + v, 2)
        lvl_id = _s(p.get("level_id"))
        bucket = by_level.setdefault(lvl_id, {})
        for k, v in w_cells.items():
            bucket[k] = round(bucket.get(k, 0.0) + v, 2)
        rows.append({
            "employee_id": p.get("employee_id"), "label": p.get("label"), "role": p.get("role"),
            "why": p.get("why"), "coverage": p.get("coverage") or COVER_NONE,
            "level_id": lvl_id, "level_name": _s(p.get("level_name")),
            "level_rank": p.get("level_rank"), "level_how": _s(p.get("level_how")) or LEVEL_NONE,
            "store_count": len(codes), "covered": sorted(codes),
            "salary_month": sal, "commission": cm,
            "basis_used": basis_used if sal else c_basis, "basis_note": note,
            "salary_by_store": w_cells, "commission_by_store": c_cells,
            "per_store_salary": round(sal / len(codes), 2) if sal else 0.0,
        })
    rows.sort(key=lambda r: (str(r.get("employee_id") or ""),))
    alloc_w = round(sum(wages.values()), 2)
    alloc_c = round(sum(comm.values()), 2)
    return {
        "wages_by_store": wages,
        "commission_by_store": comm,
        "wages_by_level": by_level,
        "people": rows,
        "unallocated": unallocated,
        "config": cfg,
        "totals": {
            "salary_allocated": alloc_w,
            "commission_allocated": alloc_c,
            "salary_unallocated": round(sum(_f(u["salary_month"]) for u in unallocated), 2),
            "commission_unallocated": round(sum(_f(u["commission"]) for u in unallocated), 2),
            "people": len(rows),
            "people_unallocated": len(unallocated),
            "stores_touched": len(set(wages) | set(comm)),
        },
    }


# ── 4b. ONE LINE PER DECLARED LEVEL (owner ask 2026-11-13) ──────────────────────────────────────
def level_lines(result: dict, cfg: dict, levels: Optional[Iterable[dict]]) -> dict:
    """The per-LEVEL wage lines for one period:

        {"lines": [{key, label, level_id, level_name, rank, by_store, total}, …]   # rank order
         "combined_by_store": {store: amount},        # people whose level did not resolve
         "combined_total": float,
         "levels_declared": int,
         "note": str}                                  # "" when there is nothing to explain

    `levels` is `level_vocabulary`'s output and is REQUIRED. [] ⇒ no per-level line exists, EVERY
    dollar stays on the combined line, and `note` says the org has declared no levels. That is the
    "nothing declared means nothing identified, and the surface says so" posture: there is no list
    of level names in this file to fall back to.

    `level_lines='off'` returns the same shape with no lines and everything combined, so a tenant
    can go back to the single line by changing a config row.

    THE SUM IS PRESERVED EXACTLY. Every cell here comes from `build_overhead`'s already-allocated
    `wages_by_level`; nothing is re-divided, so
        Σ lines + combined == result['wages_by_store']
    cell for cell, and the per-level view can never disagree with the total the statement shows.
    Asserted in harness_overhead_level_lines.py §4.

    THE OWNER'S "automatically add the level" IS THIS: the lines come from whatever
    `storeops.org_levels` holds at read time. A tenant that adds a level and places (or assigns) a
    person at it gets that line on the next read — no deploy, no config edit, no code change."""
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    by_level = dict((result or {}).get("wages_by_level") or {})
    declared = [l for l in (levels or []) if isinstance(l, dict)]
    combined: Dict[str, float] = {}

    def _fold(dst: Dict[str, float], src: Dict[str, float]) -> None:
        for k, v in (src or {}).items():
            dst[k] = round(dst.get(k, 0.0) + _f(v), 2)

    if cfg["level_lines"] != "on" or not declared:
        for cells in by_level.values():
            _fold(combined, cells)
        total = round(sum(combined.values()), 2)
        if cfg["level_lines"] != "on":
            note = ("overhead salary is booked on ONE combined line because `level_lines` is off "
                    "for this org; switch it on to get a line per declared org level.")
        elif total:
            note = ("$%s of overhead salary could not be split per org level because this org has "
                    "declared NO levels in its org chart — it is booked on the single combined "
                    "line. Declaring the levels (Settings → Org Chart) is what names the lines; "
                    "no level names are assumed on an org's behalf." % f"{total:,.2f}")
        else:
            note = ""
        return {"lines": [], "combined_by_store": combined, "combined_total": total,
                "levels_declared": len(declared), "note": note}

    lines: List[dict] = []
    for lvl in sorted(declared, key=lambda l: (l.get("rank", 0), _s(l.get("name")))):
        cells = by_level.get(_s(lvl.get("level_id")))
        if not cells:
            continue            # a declared level nobody is paid at books nothing — no empty line
        lines.append({"key": _s(lvl.get("key")) or "%s%s" % (LEVEL_LINE_PREFIX, lvl.get("rank")),
                      "label": level_label(lvl.get("name"), cfg),
                      "level_id": _s(lvl.get("level_id")), "level_name": _s(lvl.get("name")),
                      "rank": lvl.get("rank"),
                      "by_store": {k: round(_f(v), 2) for k, v in cells.items()},
                      "total": round(sum(_f(v) for v in cells.values()), 2)})
    known = {_s(l.get("level_id")) for l in declared}
    for lid, cells in by_level.items():
        if _s(lid) not in known:
            _fold(combined, cells)
    ctotal = round(sum(combined.values()), 2)
    bits: List[str] = []
    if lines:
        bits.append("Overhead salary is split into %d line(s), one per org level the org chart "
                    "declares: %s." % (len(lines), ", ".join(l["level_name"] for l in lines)))
    if ctotal:
        bits.append("$%s belongs to %s whose org level could not be identified — they sit at no org "
                    "unit and manage none, so they stay on the combined line rather than being "
                    "assigned to a level they may not hold." % (f"{ctotal:,.2f}", "people"))
    return {"lines": lines, "combined_by_store": combined, "combined_total": ctotal,
            "levels_declared": len(declared), "note": " ".join(bits)}


def level_spec_rows(lines: Optional[Iterable[dict]]) -> List[tuple]:
    """The P&L chart rows for the per-level lines, in the 6-tuple shape `coa.pl_spec_for` inserts:
    (key, label, section, kind, grain, after).

    `opex` / `auto_opt` / `store` grain and anchored after `overhead_wages`, matching the two lines
    the wages box already carries — `auto_opt` so a level that books nothing renders no line, and
    `store` grain because the directive forbids a company-wide overhead bucket."""
    return [(_s(l.get("key")), _s(l.get("label")), LEVEL_LINE_SECTION, LEVEL_LINE_KIND,
             LEVEL_LINE_GRAIN, LEVEL_LINE_AFTER)
            for l in (lines or []) if _s(l.get("key")) and _s(l.get("label"))]


# ── 5. company == sum of stores (the owner's second sentence, as an assertion) ────────────────────
def company_total(by_store: Dict[str, float]) -> float:
    """The company figure. It is DEFINED as the sum of the stores and is computed no other way —
    "the company will only calculate as a result of all store combined together". This function
    exists so that identity can be asserted in a harness, not so a second figure can be derived."""
    return round(sum(_f(v) for v in (by_store or {}).values()), 2)


def rollup_by_market(by_store: Dict[str, float], market_of: Callable[[str], str]) -> Dict[str, float]:
    """Market subtotals, also a pure SUM of stores. A store whose market is unknown folds under ''
    and stays visible in the total — never dropped, so market subtotals always re-sum to company."""
    out: Dict[str, float] = {}
    for code, amt in (by_store or {}).items():
        m = _s(market_of(code))
        out[m] = round(out.get(m, 0.0) + _f(amt), 2)
    return out


# ── 6. what the org ALREADY enters by hand — reported, never deleted ─────────────────────────────
def reconcile_manual(derived_by_store: Dict[str, float], manual_rows: Iterable[dict], cfg: dict,
                     key_of: Optional[Callable[[object], Optional[str]]] = None) -> dict:
    """Compare the DERIVED overhead allocation against the overhead salary the org already types
    into `commcalc.store_expenses` under the names it listed in `manual_expense_names`.

    This function SUPPRESSES NOTHING and DELETES NOTHING. It answers one question — "does the
    roster explain the overhead cost this tenant is already booking?" — and hands back the gap.

    WHY IT IS NOT A SUPPRESSION PLAN (unlike `labour_coverage.suppression_plan`, which it otherwise
    resembles): that plan may suppress because `rep_comm` demonstrably books the SAME dollars from a
    more authoritative source. Here the derived figure comes from the ROSTER, and a roster that is
    missing an overhead employee derives LESS than the truth. Switching the manual rows off on the
    strength of a smaller derived number would delete real cost and book nothing back — precisely
    the failure the house forbids. So: report the gap, name it, and let a human close it by
    completing the roster.

    Returns {manual_total, derived_total, gap, by_store, names, note}. `gap` > 0 means the tenant
    books MORE overhead by hand than the roster explains — i.e. the roster is incomplete."""
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    names = {n.lower() for n in (cfg.get("manual_expense_names") or [])}
    manual: Dict[str, float] = {}
    if names:
        for r in (manual_rows or []):
            if _s(r.get("expense_name")).lower() not in names:
                continue
            raw = r.get("store_code")
            k = (key_of(raw) if key_of else _s(raw)) or ""
            manual[k] = round(manual.get(k, 0.0) + _f(r.get("amount")), 2)
    derived = {k: round(_f(v), 2) for k, v in (derived_by_store or {}).items()}
    by_store = []
    for k in sorted(set(manual) | set(derived)):
        m, d = manual.get(k, 0.0), derived.get(k, 0.0)
        by_store.append({"store": k, "manual": m, "derived": d, "gap": round(m - d, 2)})
    mt, dt = round(sum(manual.values()), 2), round(sum(derived.values()), 2)
    gap = round(mt - dt, 2)
    if not names:
        note = ""
    elif gap > 0:
        note = ("$%s of overhead salary is entered by hand under %s while the roster explains only "
                "$%s of it — a gap of $%s. Both are shown; NEITHER is suppressed, because the "
                "hand-entered rows are the larger and more complete figure. Close the gap by giving "
                "the missing overhead staff a roster row with their pay basis and amount."
                % (f"{mt:,.2f}", ", ".join(sorted(cfg.get('manual_expense_names') or [])),
                   f"{dt:,.2f}", f"{gap:,.2f}"))
    elif gap < 0:
        note = ("the roster derives $%s of overhead salary against $%s entered by hand — $%s MORE "
                "than the manual rows. Both are shown and neither is suppressed; check for an "
                "overhead employee counted twice before switching either off."
                % (f"{dt:,.2f}", f"{mt:,.2f}", f"{abs(gap):,.2f}"))
    else:
        note = ("the roster's overhead allocation and the hand-entered overhead rows agree at "
                "$%s. Only one of them should book; the tenant chooses which." % f"{mt:,.2f}")
    return {"manual_total": mt, "derived_total": dt, "gap": gap, "by_store": by_store,
            "names": sorted(cfg.get("manual_expense_names") or []), "note": note}


def line_note(result: dict, recon: Optional[dict] = None) -> str:
    """The one-sentence honesty note the P&L wage box carries. Same `note` passthrough the wages and
    device-cost lines already speak through (`engine._assemble`) — one mechanism, not a third."""
    t = (result or {}).get("totals") or {}
    people = (result or {}).get("people") or []
    bits: List[str] = []
    if t.get("people"):
        bases = sorted({str(p.get("basis_used")) for p in people if p.get("basis_used")})
        bits.append("%d overhead employee(s) attached to no store are allocated across %d store(s) "
                    "on a %s basis." % (t["people"], t.get("stores_touched", 0),
                                        " / ".join(bases) or BASIS_EQUAL_STORES))
    if t.get("people_unallocated"):
        bits.append("$%s of overhead pay for %d person(s) could NOT be attributed to any store and is "
                    "NOT booked — no org-tree coverage and no home store. Place them in the org tree "
                    "to book it." % (f"{_f(t.get('salary_unallocated')) + _f(t.get('commission_unallocated')):,.2f}",
                                     t["people_unallocated"]))
    lv = (result or {}).get("levels") or {}
    if lv.get("note"):
        bits.append(lv["note"])
    if recon and recon.get("note"):
        bits.append(recon["note"])
    return " ".join(bits)


# ── 7. IO layer — org-scoped, thin, and the ONLY part that touches a client ──────────────────────
# NOTE ON ORG SCOPING: the CI org-scope guard scans ONLY commcalc/router.py, so nothing here is
# covered by it. Every read below therefore carries an explicit `.eq("org_id", org_id)` by hand, and
# the org_id is the caller's — never a value read out of a row.
def gather(client, org_id: str, period: str, cfg: dict, code_to_key=None) -> dict:
    """Build the overhead allocation for one period. Returns `build_overhead`'s result plus
    `reconciliation`, or a `{'skipped': reason}` shell when the org has not enabled it.

    `code_to_key(store_code)` maps a canonical store_code onto the caller's own key space (the P&L
    keys by canonical store ADDRESS), so what this returns is directly addable by the caller. It is
    applied LAST, after every allocation, so the cents-exact per-person split is computed in code
    space and can never be disturbed by an address collision.

    Every derivation it needs already exists and is REUSED, never re-derived:
      • `account.coa.monthly_salary_equivalent`  — the salary conversion table
      • `storeops.org_span_for_manager` (RPC)    — the canonical manager -> stores span (index §13)
      • `commcalc.management_incentive_payout`   — the ALREADY-COMPUTED manager commission (§9)
    NEVER raises: any failure degrades to a skipped shell and the statement is byte-identical."""
    cfg = resolve_config(cfg if isinstance(cfg, dict) else None)
    if cfg["mode"] != "derive":
        return {"skipped": "overhead allocation is off for this org (house default)", "config": cfg}
    try:
        from app.modules.account.coa import monthly_salary_equivalent
    except Exception:
        return {"skipped": "salary conversion unavailable", "config": cfg}

    so = client.schema("storeops")
    try:
        stores = (so.table("stores").select("store_code,is_active,org_unit_id")
                  .eq("org_id", org_id).limit(50000).execute().data) or []
    except Exception:
        return {"skipped": "store roster unreadable", "config": cfg}
    if not cfg["include_inactive_stores"]:
        stores = [s for s in stores if s.get("is_active") is not False]
    org_codes = [_s(s.get("store_code")) for s in stores if _s(s.get("store_code"))]
    # STORE -> MARKET is resolved through the ONE canonical union index (§13a, owner directive
    # 2026-09-03), NEVER off `stores.market` directly: these rows already carry a store_code, so
    # `core.scope.market_by_code` is the helper for them. Reading one vocabulary here would make a
    # store that exists only in `store_mapping`/`store_aliases` marketless, and under the
    # market-then-store basis a marketless store silently changes everybody else's share.
    _mkt: Dict[str, str] = {}
    try:
        from app.core.scope import market_by_code as _market_by_code
        _mkt = {_s(k).upper(): _s(v) for k, v in (_market_by_code(client, org_id) or {}).items()}
    except Exception:
        _mkt = {}
    market_by_code = {c: _mkt.get(c.upper(), "") for c in org_codes}
    # The INVERSE index, for the market rung of the coverage ladder: {market: [store_code, …]}. It is
    # the same canonical answer read the other way round, not a second resolution.
    codes_by_market: Dict[str, List[str]] = {}
    for _c, _m in market_by_code.items():
        if _s(_m):
            codes_by_market.setdefault(_s(_m), []).append(_c)
    # THE ORG TREE. `name` and `code` are read because the market rung matches a unit against the
    # org's own market vocabulary; `level_id` because the per-level lines are named from the level
    # the person sits at. The DOWNWARD walk itself is `org_chain.subtree_ids` — the platform's one
    # home for "everything beneath this node", which is precisely the union rule the owner's
    # 2026-11-13 ask rests on. A local re-walk here would be the second traversal the index forbids.
    try:
        units = (so.table("org_units").select("id,parent_id,level_id,name,code")
                 .eq("org_id", org_id).limit(50000).execute().data) or []
    except Exception:
        units = []
    try:
        levels = (so.table("org_levels").select("id,name,rank").eq("org_id", org_id)
                  .limit(50000).execute().data) or []
    except Exception:
        levels = []
    try:
        from app.modules.storeops import org_chain as _oc
        _lvl_names, unit_by_id = _oc.index_tree(levels, units)
        _children = _oc.children_index(unit_by_id)
        _subtree = lambda uid: _oc.subtree_ids(uid, unit_by_id, _children)   # noqa: E731
    except Exception:
        unit_by_id, _children = {}, {}
        _subtree = lambda uid: set()                                         # noqa: E731
    # Which units each person MANAGES — the second rung of `level_of_person`. Org-scoped by hand.
    managed_by_emp: Dict[str, List[str]] = {}
    try:
        for m in ((so.table("org_managers").select("unit_id,employee_id").eq("org_id", org_id)
                   .limit(50000).execute().data) or []):
            managed_by_emp.setdefault(_s(m.get("employee_id")), []).append(_s(m.get("unit_id")))
    except Exception:
        managed_by_emp = {}
    vocab = level_vocabulary(levels)

    def subtree_codes(unit_id: str) -> List[str]:
        seen = _subtree(unit_id)
        return [_s(s.get("store_code")) for s in stores
                if _s(s.get("org_unit_id")) in seen and _s(s.get("store_code"))]

    try:
        emps = (so.table("employees")
                .select("employee_id,name,home_store,role,pay_basis,pay_amount,is_active,org_unit_id")
                .eq("org_id", org_id).limit(50000).execute().data) or []
    except Exception:
        return {"skipped": "employee roster unreadable", "config": cfg}

    comm_by_emp: Dict[str, float] = {}
    if cfg["commission_source"] == "management_incentive":
        try:
            want = set(cfg["commission_statuses"])
            rows = (client.schema("commcalc").table("management_incentive_payout")
                    .select("employee_id,period,total,status").eq("org_id", org_id)
                    .eq("period", period).limit(50000).execute().data) or []
            for r in rows:
                if want and _s(r.get("status")).lower() not in want:
                    continue
                eid = _s(r.get("employee_id"))
                if eid:
                    comm_by_emp[eid] = round(comm_by_emp.get(eid, 0.0) + _f(r.get("total")), 2)
        except Exception:
            comm_by_emp = {}

    people = []
    for e in emps:
        is_oh, why, sal = classify_employee(e, cfg, monthly_salary_equivalent)
        if not is_oh:
            continue
        eid = _s(e.get("employee_id"))
        span: List[str] = []
        try:
            rows = (client.rpc("org_span_for_manager",
                               {"p_org_id": str(org_id), "p_employee_id": eid}).execute().data) or []
            span = [_s(r.get("store_code") if isinstance(r, dict) else r) for r in rows]
        except Exception:
            span = []
        span = [c for c in span if c in set(org_codes)]
        _uid = _s(e.get("org_unit_id"))
        _managed = managed_by_emp.get(eid) or []
        # THE UNION RULE, owner 2026-11-13: a higher level covers the union of everything beneath it.
        # `_subtree` IS that union — the one downward walk — so a regional unit's set already
        # contains every district unit's set beneath it with no per-level arithmetic of its own.
        _sub = set(_subtree(_uid)) if _uid else set()
        for _mu in _managed:
            _sub |= set(_subtree(_mu))
        unit = [_s(s.get("store_code")) for s in stores
                if _s(s.get("org_unit_id")) in _sub and _s(s.get("store_code"))]
        mkt_codes = [c for c in market_unit_stores(_sub, unit_by_id, codes_by_market)
                     if c in set(org_codes)]
        codes, how = covered_stores(span, unit, org_codes, cfg, market_codes=mkt_codes)
        lvl, lvl_how = level_of_person(_uid, _managed, unit_by_id, vocab)
        people.append({"employee_id": eid, "label": _s(e.get("name")) or eid,
                       "role": _s(e.get("role")), "why": why, "salary_month": sal,
                       "commission": comm_by_emp.get(eid, 0.0),
                       "covered": codes, "coverage": how,
                       "level_id": (lvl or {}).get("level_id", ""),
                       "level_name": (lvl or {}).get("name", ""),
                       "level_rank": (lvl or {}).get("rank"), "level_how": lvl_how})

    res = build_overhead(people, cfg, market_of=lambda c: market_by_code.get(c, ""))
    # The per-LEVEL lines, named from THIS org's own `org_levels` rows and from nothing else.
    res["levels"] = level_lines(res, cfg, vocab)
    res["level_vocabulary"] = vocab
    # Market subtotals are taken in STORE-CODE space, before any re-keying, because that is the only
    # space the market vocabulary is keyed in. They are a pure sum of stores either way.
    res["market_rollup"] = rollup_by_market(res["wages_by_store"], lambda c: market_by_code.get(c, ""))
    if code_to_key is not None:
        def _refold(cells):
            folded: Dict[str, float] = {}
            for c, v in (cells or {}).items():
                k = code_to_key(c) or c
                folded[k] = round(folded.get(k, 0.0) + _f(v), 2)
            return folded

        for key in ("wages_by_store", "commission_by_store"):
            res[key] = _refold(res.get(key))
        # The per-level cells are re-keyed by the SAME function, in the same pass, so the identity
        # Σ lines + combined == wages_by_store survives the fold even when two codes share an
        # address (the collision the fold exists for).
        for ln in ((res.get("levels") or {}).get("lines") or []):
            ln["by_store"] = _refold(ln.get("by_store"))
        if res.get("levels"):
            res["levels"]["combined_by_store"] = _refold(res["levels"].get("combined_by_store"))
    return res
