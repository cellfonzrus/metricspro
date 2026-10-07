"""THE ONE HOME for the platform's own LABOUR-ROW vocabulary (owner report 2026-10-07).

Owner, verbatim: *"the finance module is doubling the salaries, it is appearing in the store
expenses and also in separate line as the wages/ hourly payroll"*.

THE CLASS, stated once so nobody re-derives it
──────────────────────────────────────────────
**The platform AUTO-FILLS a figure into one surface and then RE-DERIVES the same figure from the
same source on another surface, and the only guard against the resulting double-count is opt-in
config that ships EMPTY — so the default is wrong and every new tenant starts broken.**

It is not hypothetical. The Expenses sheet ships its OWN default category list and auto-fills two
of those rows from the platform's own data:

  · `Employee Salaries`   ← `GET /storeops/payroll-by-store` (StoreOps worked/scheduled hours)
  · `Employee Commission` ← `GET /commcalc/commission-by-store` (the calculated rep commissions)

`account/coa.build_inputs` then books the SAME two sources again, on their own P&L lines:

  · the `wages` line  ← `coa.wages_by_store` → `derive_wage_cells` (the same StoreOps hours)
  · the `rep_comm` line ← `commcalc.rep_commissions.total_payout` (the same rep commissions)

Both copies were subtracted from gross profit. The only thing standing between a tenant and that
double-count was a tenant typing those row names into `commcalc.account_config
.payroll_expense_names` / `.labour_commission_expense_names`, which ship `'{}'`. Measured live
2026-10-07: 2 of 3 tenants were double-counting, in two different ways.

THE FIX: the house default is CORRECT instead of inert, and lives in ONE place
─────────────────────────────────────────────────────────────────────────────
Those row names are the PLATFORM'S OWN shipped defaults, not tenant vocabulary — the platform
writes them, the platform auto-fills them, so the platform must also know not to count them twice.
Naming them in a shared default therefore does NOT violate RULE TWO, which bans carrier / tenant /
product BRANCHES in code. RULE TWO still holds here in full:

  · no `if org == …`, no carrier, tenant, company or store name anywhere below;
  · a tenant's own explicit vocabulary still WINS, wholesale, over the house default;
  · a tenant can still name an expense row this module has never heard of and have it respected;
  · a tenant can still switch the whole mechanism off (`mode='off'`), per org, as a config row.

ONE FACT, ONE HOME, DEREFERENCED. Before this module the same labour-row vocabulary existed in
THREE copies, which is the duplicate defect the index rules forbid:

  1. `frontend/.../commcalc/expenses/page.tsx` — `DEFAULT_CATS` + `SALARY_ROW` + `COMMISSION_ROW`
     (the rows the platform SHIPS and AUTO-FILLS);
  2. each tenant's `account_config.payroll_expense_names` / `.labour_commission_expense_names`
     (the rows the P&L must not count twice) — empty by default, i.e. the copy that was MISSING;
  3. `commcalc/router._EXPENSE_APPLY_DEFAULT_TOKENS` = `['commission','salary','salaries']`
     (the rows that must never be copied across months — a THIRD spelling of "this row is labour",
     and the only one whose default was already correct).

All three now dereference the constants below. `backend/harness_labour_vocabulary.py` FAILS THE
BUILD if any caller stops dereferencing this module, or if a second copy of the vocabulary appears
in `backend/app` or `frontend/src`.

PURE: stdlib only, no I/O, no DB. Every function is a total function of its arguments.
"""

# ── THE PLATFORM'S OWN LABOUR ROWS ───────────────────────────────────────────────────────────────
# The expense rows the PLATFORM ships in the Expenses sheet's default category list AND auto-fills
# from its own data. A row here is, by construction, already booked on a dedicated P&L line from
# the same source — so its expense-side copy must not be counted a second time.
#
# A tenant-invented labour row (a hand-typed 'Dm Salary', say) is deliberately NOT here: the
# platform does not auto-fill it, nothing re-derives it, and it is therefore NOT a duplicate. It
# books as the ordinary store expense it is, exactly as before. A tenant that DOES want such a row
# treated as payroll names it in its own config, which wins (see `resolve`).

#: Rows that carry SALARY/PAYROLL already derived onto the `wages` P&L line from StoreOps hours.
DEFAULT_PAYROLL_ROWS = ("Employee Salaries", "Owner / Mgmt Salaries")

#: Rows that carry REP COMMISSION already booked onto the `rep_comm` P&L line from
#: `commcalc.rep_commissions.total_payout` (owner decision 2026-09-08: "Rep commision should go in
#: p&l" — `rep_commissions` is the authoritative route).
DEFAULT_COMMISSION_ROWS = ("Employee Commission",)

#: The P&L line a payroll-named expense row books on under the house default. Salary belongs on the
#: salary line, not inside "rent / utilities / supplies" — that is what made the owner's drill-down
#: unreadable (index §4e). Routing moves a dollar between two OPEX lines; NET INCOME IS UNCHANGED
#: by it, and only the suppression above changes a bottom line.
DEFAULT_PAYROLL_LINE = "wages"

# ── how the house default and a tenant's own vocabulary compose ───────────────────────────────────
HOUSE = "house"          # no explicit vocabulary ⇒ the platform's own default rows apply
EXPLICIT = "explicit"    # the tenant named its own rows ⇒ they win, wholesale
OFF = "off"              # the tenant switched the mechanism off ⇒ nothing is suppressed

#: The per-org opt-out. 'house' (absent/unknown ⇒ this) applies the defaults above where the tenant
#: named nothing; 'off' suppresses nothing at all and reproduces the pre-2026-10-07 behaviour.
MODES = (HOUSE, OFF)

#: Under the HOUSE vocabulary the payroll authority grain is forced to per-STORE. Rationale, which
#: is the whole reason this is derived and not a fourth config knob: grain 'org' means "one
#: authoritative payroll row ANYWHERE suppresses EVERY store's hours estimate". That is safe when a
#: tenant deliberately listed its own payroll rows (it knows it enters payroll for all its stores),
#: but the house default cannot know that — and a store with no salary row would then book $0.00 of
#: labour, which reads exactly like "this store paid nobody". Per-store authority is the only grain
#: under which switching the default ON cannot invent a silent zero. A tenant with an EXPLICIT
#: vocabulary keeps whatever grain it stored.
HOUSE_AUTHORITY_GRAIN = "store"


def _clean(names):
    """PURE: a config value (list/tuple/None/garbage) → a de-duplicated list of non-blank strings,
    original spelling preserved, first occurrence wins. Never raises."""
    out, seen = [], set()
    if not isinstance(names, (list, tuple, set, frozenset)):
        return out
    for n in names:
        s = " ".join(str(n or "").strip().split())
        if not s:
            continue
        low = s.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(s)
    return out


def resolve_mode(mode):
    """PURE: a stored `labour_vocabulary_mode` → one of `MODES`. Anything unrecognised (including
    None, and every row written before the column existed) resolves to `HOUSE`, so the correct
    default applies without a migration having to run first."""
    m = str(mode or "").strip().lower()
    return m if m in MODES else HOUSE


def resolve(payroll_cfg=None, commission_cfg=None, grain_cfg=None, mode=None):
    """PURE: the ONE resolution of "which expense rows are labour already booked elsewhere".

    `payroll_cfg` / `commission_cfg` — the tenant's `account_config.payroll_expense_names` /
    `.labour_commission_expense_names` as stored (list, empty list, or None).
    `grain_cfg`  — its stored `payroll_authority_grain` ('org' | 'store' | None).
    `mode`       — its stored `labour_vocabulary_mode` (see `MODES`); absent ⇒ `HOUSE`.

    THE RULE, one sentence: a NON-EMPTY tenant list wins wholesale; an empty or absent list takes
    the platform's own default rows; `mode='off'` takes nothing.

    Wholesale, never merged — deliberately. Merging the house rows into a tenant's own list would
    change the statements of a tenant that already configured itself, unasked, which is exactly the
    kind of silent money movement this house does not ship. A tenant that wants a house row added
    types it into its own list.

    An empty list cannot be told apart from "never configured" (the column default is `'{}'`), so
    empty is read as "not configured" and the honest opt-out is `mode='off'` — stated here rather
    than inferred, because an inferred opt-out is how a correct default becomes inert again.

    Returns a JSON-safe dict:
      {'mode', 'payroll_names', 'commission_names', 'payroll_source', 'commission_source',
       'payroll_routes', 'grain', 'grain_source', 'apply_protection_tokens'}
    """
    m = resolve_mode(mode)
    pay_explicit = _clean(payroll_cfg)
    com_explicit = _clean(commission_cfg)
    stored_grain = str(grain_cfg or "").strip().lower()
    stored_grain = stored_grain if stored_grain in ("org", "store") else "org"

    if m == OFF:
        return {"mode": OFF, "payroll_names": [], "commission_names": [],
                "payroll_source": OFF, "commission_source": OFF, "payroll_routes": {},
                "grain": stored_grain, "grain_source": EXPLICIT,
                "apply_protection_tokens": apply_protection_tokens([], [])}

    pay = pay_explicit or list(DEFAULT_PAYROLL_ROWS)
    com = com_explicit or list(DEFAULT_COMMISSION_ROWS)
    pay_src = EXPLICIT if pay_explicit else HOUSE
    com_src = EXPLICIT if com_explicit else HOUSE
    # See HOUSE_AUTHORITY_GRAIN: the house vocabulary can only be switched on safely at store grain.
    grain = stored_grain if pay_src == EXPLICIT else HOUSE_AUTHORITY_GRAIN
    return {"mode": m, "payroll_names": pay, "commission_names": com,
            "payroll_source": pay_src, "commission_source": com_src,
            "payroll_routes": default_payroll_routes(pay) if pay_src == HOUSE else {},
            "grain": grain, "grain_source": EXPLICIT if pay_src == EXPLICIT else HOUSE,
            "apply_protection_tokens": apply_protection_tokens(pay, com)}


def default_payroll_routes(payroll_names):
    """PURE: the house `payroll_expense_routes` — every house payroll row onto `DEFAULT_PAYROLL_LINE`.

    Only ever applied when the payroll vocabulary itself came from the house default; a tenant with
    an explicit vocabulary keeps its own (possibly empty) route map, so its presentation does not
    move under it. Routing changes WHICH opex line holds a dollar, never the bottom line."""
    return {n.lower(): DEFAULT_PAYROLL_LINE for n in _clean(payroll_names)}


def apply_protection_tokens(payroll_names, commission_names):
    """PURE: the case-insensitive SUBSTRING tokens that protect a labour row from being copied
    across months (`commcalc/router._apply_to_months_expand`).

    DERIVED from the resolved vocabulary rather than hand-listed, which is what removes the third
    copy. The vocabulary's own words are tokens; a plural row name also yields its singular stem so
    a substring match still catches a tenant's 'Employee Salary' alongside 'Employee Salaries'
    (the pre-2026-10-07 hand-written default carried both 'salary' and 'salaries' for exactly this
    reason, and dropping either would silently widen what gets copied).

    An EMPTY vocabulary (mode 'off') yields NO tokens: a tenant that switched labour handling off
    has said it does not want the platform deciding which of its rows are labour.
    """
    toks, seen = [], set()
    for n in _clean(payroll_names) + _clean(commission_names):
        for t in _tokens_for(n):
            if t not in seen:
                seen.add(t)
                toks.append(t)
    return toks


def _tokens_for(name):
    """PURE: the substring tokens one row name contributes. The row's LAST word is the noun that
    identifies the kind of labour ('Employee Salaries' → 'salaries' → stem 'salary'); the whole
    lowercased name is kept too so an exact match always holds."""
    low = " ".join(str(name or "").strip().split()).lower()
    if not low:
        return []
    out = [low]
    words = [w for w in low.replace("/", " ").split() if w]
    if words:
        last = words[-1]
        if last not in out:
            out.append(last)
        # 'salaries' → 'salary' (and 'commissions' → 'commission'): the singular stem, so a
        # substring match catches either spelling of the same noun.
        if last.endswith("ies") and len(last) > 4:
            stem = last[:-3] + "y"
            if stem not in out:
                out.append(stem)
        elif last.endswith("s") and len(last) > 3:
            stem = last[:-1]
            if stem not in out:
                out.append(stem)
    return out
