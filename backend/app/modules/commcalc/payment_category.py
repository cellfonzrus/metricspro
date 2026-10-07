"""THE ONE HOME: which pay CATEGORY did this org declare for this payment type?

OWNER ASK 2026-10-07, verbatim: *"in the EPay Daily Debits & Credits, we need one more filter for
the rebates and commssion categories as it is virtually impossible to check which item is commsison
or rebate by a user"*.

He is right, and the reason is worth writing down rather than re-deriving. The Processor Daily
Debits & Credits report groups by the carrier's own `payment_type` — and the carrier ships 40+ of
them per quarter ("Simplified SIM Loading Bounty - Month 4", "2026 Q3 Promo PIC Offer"). Which of
those is commission and which is a rebate is not in the string; it is in the org's OWN
`commcalc.payment_categories` map. The report never read that map, so a human had to know 154
mappings by heart.

WHY THIS MODULE EXISTS AT ALL — THE DUPLICATE THAT WAS ALREADY THERE
────────────────────────────────────────────────────────────────────
`commcalc.payment_categories` had **nine** separate readers before this file, each doing its own
`select("description,category")` and folding the key its own way (some `.strip()`, some
`.strip().lower()`, some neither). That is the duplicate the build gate exists to stop: nine copies
of one question, free to disagree about whether `"  Boost Auto Top-Up"` is the same payment type as
`"boost auto top-up"`. Adding a tenth for this filter would have been the defect, so the filter
reads THIS and `harness_payment_category_home_lock.py` fails the build if a tenth appears.

ONE FACT, ONE HOME, DEREFERENCED — what is NOT restated here
─────────────────────────────────────────────────────────────
  · the sentinel for "the org never mapped this"  → `pay_data_quality.UNCATEGORISED`
  · which categories the pay engine can actually place → `pay_data_quality.PLACEABLE_CATEGORIES`
  · why an unmapped type is money going nowhere → `pay_data_quality.UNPLACED_REASONS`
This module adds exactly one thing those do not have: the READ, and one folding rule for the key.

RULE TWO. No category name is written in code here. "Commission", "Re-imbursement", "MDF" are rows
in the tenant's own table, and this module returns whatever the tenant declared — including a
category this code has never heard of. A tenant with an empty map gets an honest "nothing is
declared", never a guess.

ABSENCE IS NOT A CATEGORY. `category_of` returns `None` when the org has not mapped a type. The
caller may LABEL that for a human (`label_of`), but it must never be folded into a real category:
measured live 2026-10-07 on the house org, **$573,241.39 over 60 days — 72% of the feed — carries a
payment type with no declared category**, and eight of those fourteen types have an exact
prior-year twin that IS declared (`2024 Q3 Promo PIC Offer` → `Re-imbursement`). Mapping them is a
money decision and the owner's, so this module makes the hole visible and maps nothing.

PURE except for `load_map`. `category_of` / `label_of` / `declared_categories` are stdlib-only and
proven DB-free by `harness_payment_category.py`.
"""


def _fold(value):
    """The ONE folding rule for a payment-type key: trimmed and case-flattened.

    Nine call sites folded this nine ways, which is how `'Boost Auto Top-Up '` could be categorised
    by one caller and unmapped by the next. Casing is not meaning here — the carrier's own feed
    spells the same type inconsistently across months."""
    return " ".join(str(value or "").split()).lower()


def load_map(client, org_id):
    """The org's declared payment-type → category map, folded by `_fold`. IO.

    Returns `{}` on any read failure — an empty map means "nothing is declared", which
    `category_of` already reports honestly as unmapped, so a transient failure degrades to "we
    cannot say" and never to a wrong category. Later rows win on a duplicate description, matching
    what a human editing the table last would expect."""
    try:
        rows = (client.schema("commcalc").table("payment_categories")
                .select("description,category").eq("org_id", org_id).execute().data) or []
    except Exception as e:                       # pragma: no cover - I/O guard
        print(f"WARN payment_category.load_map failed: {e}")
        return {}
    out = {}
    for r in rows:
        key = _fold(r.get("description"))
        if not key:
            continue
        cat = str(r.get("category") or "").strip()
        out[key] = cat or None
    return out


def category_of(cmap, payment_type):
    """The category this org declared for `payment_type`, or **None** when it declared none.

    None is the honest answer and the caller must keep it distinguishable from a real category —
    see the module docstring. A row whose `category` cell is blank is also None: a mapping that
    names no category has not categorised anything."""
    return (cmap or {}).get(_fold(payment_type)) or None


def label_of(cmap, payment_type):
    """`category_of`, but with the shared sentinel for a human-facing cell.

    The sentinel is `pay_data_quality.UNCATEGORISED` — dereferenced, not restated, so the ledger's
    "not classified" bucket and the pay reconciliation's cannot drift into two different words for
    one state."""
    from app.modules.commcalc.pay_data_quality import UNCATEGORISED
    return category_of(cmap, payment_type) or UNCATEGORISED


def declared_categories(cmap):
    """Every category this org actually declared, sorted — the option list for a filter.

    Derived from the tenant's rows, never from a list in code, so a category the house has never
    heard of is offered the moment the tenant declares it (RULE TWO). The unmapped sentinel is NOT
    in here: a caller that wants to offer it appends it, and only when its own rows need it — the
    same posture §13c takes for the "(no market)" market option."""
    return sorted({c for c in (cmap or {}).values() if c}, key=str.lower)


def placeable_categories():
    """The categories the pay engine can actually place, dereferenced from its one home.

    A declared category outside this set is money the engine has nowhere to put — the
    `unhandled_category` state `pay_data_quality.UNPLACED_REASONS` already names. Exposed here so a
    report can mark such a category without importing the reconciliation module itself."""
    from app.modules.commcalc.pay_data_quality import PLACEABLE_CATEGORIES
    return tuple(PLACEABLE_CATEGORIES)
