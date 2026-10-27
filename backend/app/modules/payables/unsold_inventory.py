"""ONE home for "which devices are still unsold, and what is each one's purchase date, cost and
due date" — resolved per carrier from the config spine, never assumed.

OWNER REPORT 2026-10-22, verbatim: *"under Luxelink it is using Boost - it should not"* and *"there
is no data appearing for the phones which were purchased as per handset ordering and not sold ... to
be apparing in the aging, the aging report should show whatever is not sold after purchasing with how
many days they have been on the inventory"*.

THE CLASS. Inventory Aging read `commcalc.asset_ledger` and nothing else — the Boost/VIP consignment
snapshot — and Upcoming Invoice Payment Due read `commcalc.vip_invoices`. Both are one carrier's
plumbing, and a tenant on a different carrier therefore saw a card explaining that the program "does
not apply to your account". MEASURED 2026-10-22: Luxelink has **0** `asset_ledger` rows and **0**
`vip_invoices` — and **4,288** rows in `commcalc.device_payable_ledger`, with real IMEIs, carrying
exactly the facts the aging report needs. The empty screens were not missing data. They were the
wrong question.

WHY THIS IS NOT A THIRD SOURCE. `device_payable_ledger` (mig 095) is already the carrier-AGNOSTIC
per-IMEI materialization this module builds from every carrier's `payable_source_map` — IMEI, store,
model, invoice date, due date, sold flag, owed. Building another unsold-device derivation beside it
is the duplicate defect the house rules forbid, so this module READS it and shapes it into the
response the aging report already returns. Which feed answers is a DECLARED column
(`payable_source_map.unsold_source`, mig 1091): `source_table` keeps Boost on its own snapshot
byte-for-byte, `payable_ledger` is for a carrier that has no snapshot of its own.

THE WINDOW AND THE VERDICT ARE NOT DECIDED HERE either — they are `device_aging_policy`'s
(§COMMCALC-26), so a 12-day carrier and a 60-day carrier are one comparison with two declared
numbers, and "unsold and payable = out of pocket" means the same thing on every screen that shows it.
"""
from app.modules.commcalc import device_aging_policy as _aging
from app.modules.payables import ledger_status as _ls
from app.modules.payables import recovered_amount as _rec

PAGE = 1000

SOURCE_TABLE = "source_table"
SOURCE_LEDGER = "payable_ledger"

#: THE FIELD THIS MODULE STAMPS ITS ANSWER INTO, named once so a reader of a shaped row does not
#: carry a second copy of the key. `asset/device_asset_drilldown` dereferences it to decide WHICH
#: relation each aging row was read out of (§CORE-17.3y) — a literal there would be the copied fact
#: the house rules forbid, and it would silently stop matching the day this key changed.
SOURCE_FIELD = "_source"


def load_policies(client, org_id):
    """{carrier_id: resolved policy} for the org, plus the maps themselves.

    Returns (policies, maps). Fail-soft: an unreadable config yields ({}, []) and every caller then
    uses the house default, which is what the platform showed before any of this was declarable."""
    try:
        maps = (client.schema("commcalc").table("payable_source_map").select("*")
                .eq("org_id", org_id).eq("is_active", True).limit(200).execute().data) or []
    except Exception as e:
        print(f"WARN payables load_policies failed (house default applies): {e}")
        return {}, []
    return {m.get("carrier_id"): _aging.resolve_policy(m) for m in maps}, maps


def policy_for(policies, carrier_id):
    """The policy for a row's carrier, or the house default. A row whose carrier has no map is never
    refused — it is simply held to the default window, and `declared` says so."""
    return policies.get(carrier_id) or _aging.resolve_policy()


def ledger_sourced_maps(maps):
    """The maps that declare the per-IMEI ledger as their unsold-device source."""
    return [m for m in maps or []
            if str(m.get("unsold_source") or SOURCE_TABLE).strip().lower() == SOURCE_LEDGER]


def fetch_unsold_from_ledger(client, org_id, carrier_ids=(), date_from="", date_to=""):
    """Unsold devices out of `device_payable_ledger`, shaped like an `asset_ledger` aging row.

    The shape is deliberate, not incidental: the aging report's response, its export columns and its
    frontend all speak `acquired_date` / `owed_to_vip` / `date_sold`, so presenting the ledger in that
    vocabulary means ONE report renders both carriers instead of a parallel screen for the second one.
    `acquired_date` is the ledger's `invoice_date` — for a Total device that is the activation/order
    date the handset ordering sheet dates, which is what "how many days has it been on inventory"
    counts from.

    `sold_flag` is the ledger's own, built from each carrier's declared sold source; a row the build
    could not judge is NOT treated as unsold, because claiming a sold handset is still on the shelf
    invents an exposure. `date_from`/`date_to` narrow by invoice date through the one window home.
    """
    from app.modules.commcalc import date_window as _dw
    from app.modules.core import feed_read as _feed_read

    def _make():
        # A FRESH builder per page (postgrest builders carry their last range), and the paging
        # order is applied by the one home — an unordered paged read serves some rows twice and
        # others never (§19.48 / core/feed_read.py).
        # A line that bought no device is not unsold INVENTORY — there is no device to hold, so it
        # can never age and can never be paid out of pocket (§COMMCALC-32). Excluded through the one
        # home, the same filter the payables list makes.
        q = _ls.exclude_non_payable(
            client.schema("commcalc").table("device_payable_ledger")
            .select("imei,store,device_model,carrier_id,owed,net_owed,invoice_date,due_date,"
                    "sold_flag,sold_date,status,owed_source,due_source")
            .eq("org_id", org_id).eq("sold_flag", False))
        if carrier_ids:
            q = q.in_("carrier_id", list(carrier_ids))
        if date_from or date_to:
            q = _dw.apply_window(q, "invoice_date",
                                 _dw.resolve_window(date_from, date_to, 0))
        return q

    out = []
    for r in _feed_read.read_all(_make):
        out.append({
            "esn_imei": r.get("imei"),
            "store": r.get("store"),
            "device_model": r.get("device_model"),
            "carrier_id": r.get("carrier_id"),
            "category": "On Inventory",        # the vocabulary the report filters on
            "status": r.get("status"),
            "acquired_date": r.get("invoice_date"),
            "due_date": r.get("due_date"),
            "date_sold": r.get("sold_date"),
            "owed_to_vip": r.get("owed"),
            "selling_price": None,
            "reimbursement": None,
            # PROVENANCE, carried so the screen can say where each figure came from instead of
            # implying it came from a consignment ledger this tenant does not have.
            SOURCE_FIELD: SOURCE_LEDGER,
            "_owed_source": r.get("owed_source"),
            "_due_source": r.get("due_source"),
        })
    return out


def stamp_aging(rows, policies, today=None):
    """Stamp `days_held`, `aging_bucket`, `out_of_pocket`, `days_overdue` and the verdict's BASIS on
    rows that already carry `acquired_date` / `due_date` / a sold signal.

    THE UNIVERSAL HIGHLIGHT (owner 2026-10-22: *"if the imei is not sold then the device has to be
    paid out of pocket whch needs to be highlighted universally"*). Every surface that shows a device
    calls this, so the flag cannot mean one thing on the aging report and another on payables. The
    verdict itself is `device_aging_policy.out_of_pocket` — `None` where neither a due date nor a
    holding period exists, which a screen must render as unknown rather than as settled.

    Returns a summary {devices, out_of_pocket, out_of_pocket_amount, not_measured,
    out_of_pocket_unpriced, amount_incomplete} so a caller can headline the exposure without
    re-walking the rows.

    WHY THE AMOUNT CARRIES ITS OWN HONESTY FLAG (§COMMCALC-29). A device can be out of pocket and
    carry no cost, and the sum then understates the exposure while LOOKING like a total. On
    2026-10-23 that was the whole of it: a mis-declared cost source priced nothing, so a real
    $595,125.59 of exposure headlined as $0.00 with no hint that the number was incomplete.
    `out_of_pocket_unpriced` counts the flagged devices with no amount, and `amount_incomplete` is
    the one boolean a screen reads before presenting the sum as a total.
    """
    n_oop, amt, n_unknown, n_unpriced = 0, 0.0, 0, 0
    recovered, n_netted, n_unmeasured, unmeasured_amt = 0.0, 0, 0, 0.0
    for r in rows or []:
        pol = policy_for(policies, r.get("carrier_id"))
        held = _aging.days_held(r.get("acquired_date"), today)
        sold = bool(r.get("date_sold")) or bool(r.get("sold_flag"))
        v = _aging.out_of_pocket(sold=sold, due_date=r.get("due_date"), days_held=held,
                                 policy=pol, today=today)
        r["days_held"] = held
        r["days_aged"] = held                  # the aging report's existing field name, kept
        r["aging_bucket"] = _aging.bucket(held, pol)
        r["sell_within_days"] = pol["sell_within_days"]
        r["window_declared"] = pol["declared"]
        r["out_of_pocket"] = v["out_of_pocket"]
        r["out_of_pocket_basis"] = v["basis"]
        r["days_overdue"] = v["days_overdue"]
        if v["out_of_pocket"] is None:
            n_unknown += 1
        elif v["out_of_pocket"]:
            n_oop += 1
            try:
                one = float(r.get("owed_to_vip") or r.get("owed") or 0)
            except (TypeError, ValueError):
                one = 0.0
            # WHAT THE CARRIER ALREADY RECOVERED COMES OFF THE BILL (§COMMCALC-33). Owner, 2026-10-26:
            # "the payments for all phones automatically are done from commission and rebates
            # received and auto adjusted". This summed the GROSS cost, so on Luxelink it headlined
            # $544,447.78 of "your own cash" against about $38,374 genuinely unrecovered. The
            # recovery and its basis are the ledger row's, read through the one home — never
            # re-derived here, and never assumed to be zero because a column was blank.
            net = _rec.out_of_pocket(one, r.get("rebate_amount"), r.get("rebate_basis"))
            r["recovered_amount"] = net["recovered"]
            r["recovered_basis"] = net["basis"]
            r["recovered_measured"] = net["measured"]
            r["recovery_netted"] = net["netted"]
            if net["measured"]:
                recovered += net["recovered"] or 0.0
                if net["covered"]:
                    n_netted += 1
            else:
                # The liability stays in the figure at its GROSS and is FLAGGED. Dropping it would
                # be the same defect in the other direction — and the Boost/VIP rows carry no
                # recovery basis at all, so dropping them would zero a real exposure.
                n_unmeasured += 1
                unmeasured_amt += one
            one = net["amount"] if net["amount"] is not None else one
            amt += one
            if not one and not net["covered"]:
                n_unpriced += 1
    return {"devices": len(rows or []), "out_of_pocket": n_oop,
            "out_of_pocket_amount": round(amt, 2), "not_measured": n_unknown,
            "out_of_pocket_unpriced": n_unpriced,
            # The netting, said out loud rather than silently subtracted: how much the carrier has
            # already taken back, how many devices that settles outright, and how much nobody
            # measured — which is NOT in `out_of_pocket_amount` and must be shown beside it.
            "recovered_amount": round(recovered, 2), "netted_devices": n_netted,
            "recovery_unmeasured": n_unmeasured,
            "recovery_unmeasured_amount": round(unmeasured_amt, 2),
            # The figure is not netted where this is set: the sentence has one home.
            "recovery_note": (_rec.UNMEASURED_NOTE if n_unmeasured else None),
            # TWO flags, because they are two different doubts and one boolean cannot carry both.
            # `amount_incomplete` keeps its original meaning exactly — flagged devices with NO price,
            # the §COMMCALC-29 defect — and `amount_not_netted` is the new one: the figure has not
            # been reduced by what the carrier already recovered, because nothing measured it.
            # Overloading the first would have quietly changed what every existing reader of it
            # believes, and two of its proofs caught that when I tried.
            "amount_not_netted": bool(n_unmeasured),
            "amount_incomplete": bool(n_unpriced)}
