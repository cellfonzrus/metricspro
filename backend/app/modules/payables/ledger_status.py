"""WHAT A DEVICE-PAYABLE LEDGER ROW'S STATUS MEANS — the ONE home for that vocabulary.

Index §COMMCALC-31. Lock: `backend/harness_payables_device_rows.py`.

OWNER REPORT 2026-10-26, on the payables list: *"product not available , it shoudl show the
product"*. There is no product to show, and that was the finding. Measured on the live Luxelink feed
the same day: `activation_type2` declares `branded` on 3,567 rows (all of them carrying a model name)
and `byop` on 869 (all of them reading "Product Not Available"), with 59 declaring nothing. Not one
of those 928 rows carries any device money — device margin $0, financing $0, `is_financed` No, on
every single one.

So "Product Not Available" never meant missing data. It means **the dealer bought no phone on that
line** — the customer brought their own. Such a row cannot be priced, cannot be owed for and can
never be paid out of pocket, and two of them were being presented to the owner as `discrepancy`,
which is a claim that he is carrying a cost he never had.

THE CLASS, NOT THE INSTANCE. The ledger was built as "one row per activation" when what it means is
**one row per device the dealer bought**. Nothing in it asked whether a device was bought at all, so
every surface downstream inherited the wrong population: the payables list, the aging report, the
out-of-pocket exposure, the discrepancy flags. The fix is one question asked once, at the build, with
the answer carried on the row as a status every reader dereferences.

WHY A STATUS AND NOT A DROPPED ROW. A row that vanishes cannot be audited, and a defect found in live
data is reported, never hidden. The line stays in the ledger, stamped for what it is, and every
reader excludes it BY NAME through this module while the count of what was excluded is reported.

WHY THE DECLARATION AND NOTHING ELSE. The question is answered by
`commcalc/product_mix.no_device_declared` — the one home for "did this line sell a device" since
§COMMCALC-03.21 — and only on its `declared_no_device` basis. A class read off a product description
is NOT evidence (§66), and acting on a keyword here would drop real handsets whose names happen to
read oddly. A carrier that declares no such column keeps every row, byte-identical to before.

PURE — no imports at all, which is what lets every reader and the engine dereference it without a
cycle (`engine` imports `asset.router`, which imports `unsold_inventory`).
"""

#: A device the dealer bought, inside its window, nothing to do yet.
STATUS_OPEN = "open"
#: Unsold and past its due date.
STATUS_DUE = "due"
#: Sold, but no equipment rebate came back — the dealer is still carrying the cost.
STATUS_DISCREPANCY = "discrepancy"
#: A rebate at least as large as the amount owed has landed.
STATUS_OFFSET = "offset"
#: NO DEVICE WAS BOUGHT on this line, declared by the feed itself (bring-your-own-phone, SIM only).
#: Not a payable at all, and the one status every reader excludes.
STATUS_NO_DEVICE = "no_device"

#: The statuses that ARE device payables. A reader showing "the payables" shows these.
PAYABLE_STATUSES = (STATUS_OPEN, STATUS_DUE, STATUS_DISCREPANCY, STATUS_OFFSET)
#: The statuses that are NOT. Kept as a tuple so a second non-payable status can be added here once
#: rather than in every reader — which is the failure mode this module exists to prevent.
NON_PAYABLE_STATUSES = (STATUS_NO_DEVICE,)

#: `owed_source` on such a row. Distinct from both 'unconfigured' (no cost source is set up) and
#: 'price_source_unreadable' (one is, and it is broken, §COMMCALC-29): here there is nothing to price.
OWED_SOURCE_NO_DEVICE = "no_device_declared"

#: The one sentence a screen shows about what it left out. Names no column and no feed, so it is safe
#: to put in front of any customer (§19.36).
EXCLUDED_NOTE = ("Lines where the customer brought their own phone are not device payables — no "
                 "handset was bought, so nothing can be owed. They are counted here rather than "
                 "dropped.")


def is_payable(status) -> bool:
    """PURE: whether a ledger row's status is a device payable at all."""
    return str(status or "").strip().lower() not in NON_PAYABLE_STATUSES


def payable_only(rows, status_of=None):
    """PURE: `(kept, excluded_count)` — the device payables, and how many lines were not one.

    The in-memory half of the same test the query-side `.neq()` makes, so a caller that has already
    fetched rows cannot get a different population than one that filtered in the database."""
    get = status_of or (lambda r: (r or {}).get("status"))
    kept = [r for r in (rows or []) if is_payable(get(r))]
    return kept, len(rows or []) - len(kept)


def exclude_non_payable(q, column="status"):
    """The QUERY-side half of `payable_only`, so the database filter and the in-memory one cannot
    drift: both read `NON_PAYABLE_STATUSES`, and a second non-payable status reaches every reader by
    being added to that tuple. `q` is any postgrest builder — passed in, never imported, which keeps
    this module free of dependencies."""
    return q.not_.in_(column, list(NON_PAYABLE_STATUSES))
