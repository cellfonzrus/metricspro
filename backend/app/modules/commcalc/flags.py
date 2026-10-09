"""
Flags Calculator — the EIGHT flag types this module actually emits:
CHARGEBACK, UNMAPPED_PAYMENT_TYPE, DUPLICATE_IMEI, SETUP_FEE_MISSING,
RSK_ACTIVATIONS, LOW_PORT_IN_RATE, MISSING_STORE_PAYMENT, MISSING_STORE_SALES

CORRECTED 2026-10-05. This header used to claim "all 13 flag types" and list
MRC_IMEI_MISMATCH, ACCESSORY_LOSS, IMEI_FRAUD, IMEI_MULTI_MDN, RSK_NON_PAYMENT and
HIGH_CHURN_RATE — none of which appears anywhere in the code below, while DUPLICATE_IMEI,
which it does emit, was missing. The list was wrong in both directions and had been for
long enough that the registry work had to re-derive it from the code.

Those unemitted names are NOT being implemented here to make the docstring true: they are
recorded in `flag_registry.DOCUMENTED_NOT_EMITTED` and left unregistered on purpose, because
registering a type nobody writes would put a permanently-empty row on the Management Watchdog
board — the fake-zero this house forbids (index §53.2). If a tenant wants one of them, it is a
new detector with its own proof, not a docstring to satisfy.
"""
from typing import Any
from collections import defaultdict

# ONE home for what a finding's severity means (index §53). This module keeps its own HIGH/MEDIUM/
# LOW judgements; `flag_registry.stamp` only puts them on the shared scale, so the Management
# Watchdog can order by severity across every writer of the table.
from app.modules.commcalc import flag_registry as _reg

# ONE home for "is this feed row money the carrier TOOK BACK" (index §54). This detector used to ask
# `row['category'] == 'Chargeback'` — a category `commcalc.payment_categories` has never been able to
# contain, so in four years it fired ZERO times while the house feed carried 474 withholding rows
# ($7,123.39 taken back, measured live 2026-10-06). The test now dereferences the shared one, which
# recognises a clawback by the DIRECTION the processor moved the money. See clawback.py for the class.
from app.modules.commcalc import clawback as _clawback

def safe_float(v) -> float:
    try: return float(v or 0)
    except: return 0.0

def street_num(addr: str) -> str:
    return str(addr or '').strip().split(' ')[0]

def _days_since(date_str):
    """Whole days from an activation/acquired date string to today (None if unparseable)."""
    s = str(date_str or '').strip()
    if not s:
        return None
    try:
        from datetime import date
        from dateutil import parser as _dp
        return (date.today() - _dp.parse(s).date()).days
    except Exception:
        return None


def calc_flags(
    sales: list[dict],
    pay_detail: list[dict],
    mi_rows: list[dict],
    dlar_store: list[dict],
    store_mapping: list[dict],
    period: str,
    period_month: int,
    period_year: int,
    asset_by_imei: dict | None = None,
) -> list[dict]:
    """Returns list of flag dicts ready to insert into commcalc.flags. asset_by_imei maps an IMEI
    (upper, no '.0') → its asset_ledger row, used to show a chargeback's REBATE LOST + device + age."""

    flags = []
    asset_by_imei = asset_by_imei or {}
    base = {'period': period, 'period_month': period_month, 'period_year': period_year}

    # VOIDED: SHARED token set (owner 2026-07-25) so flags see exactly the lines that pay.
    from app.modules.commcalc.gp_report import is_voided as _is_voided
    valid_sales = [
        r for r in sales
        if not _is_voided(r.get('voided'))
        and str(r.get('trans_type', '')).strip() != 'Return'
    ]

    # Device model BY IMEI from the SALES report: the sales line carrying a serial IS the phone, so its
    # product description is the DEVICE model — not the plan / accessory / service lines on the same
    # transaction. Flags show the phone sold, not the plan activated. Prefer a device-looking description.
    def _device_like(pd):
        p = (pd or '').lower()
        return bool(p) and not any(b in p for b in (
            'unlimited', 'plan', ' rtr', 'refill', 'recharge', 'autopay', 'protect', 'setup charge',
            'service charge', 'sim ', 'sim card', 'wall charger', 'access charge', 'wallet funding',
            'insurance', 'warranty', 'boost rtr', 'top up', 'topup'))
    model_by_imei: dict[str, str] = {}
    for r in valid_sales:
        ser = str(r.get('serial_1', '') or '').replace('.0', '').strip().upper()
        pd = str(r.get('product_desc', '') or '').strip()
        if not (ser and pd):
            continue
        if ser not in model_by_imei or (_device_like(pd) and not _device_like(model_by_imei[ser])):
            model_by_imei[ser] = pd

    # ── 1. CHARGEBACK — show the REBATE LOST for that phone, not the bill-pay amount ──────
    # The org's OWN declaration map, rebuilt from the rows the caller already stamped (router
    # `_run_calculation` / `_compute_gp` both set `category` from commcalc.payment_categories). It is
    # handed to the shared test rather than a category literal being compared here: RULE TWO, and the
    # reason this detector was silent for four years (see the import note).
    _cat_by_type = {}
    for r in pay_detail:
        pt = str(r.get('payment_type', '') or '').strip()
        if pt and pt not in _cat_by_type:
            _cat_by_type[pt] = str(r.get('category', '') or '').strip()
    _pay_cats = _clawback.pay_categories()
    for r in pay_detail:
        _cl = _clawback.classify_row(r, _cat_by_type, feed='epay', pay_cats=_pay_cats)
        if _cl['clawback']:
            # The DEBITED magnitude, always positive — the feed's sign convention is the shared
            # module's business, not this detector's.
            amt = -_cl['amount']
            if amt != 0:
                imei = str(r.get('imei', '') or '').replace('.0', '').strip()
                a = asset_by_imei.get(imei.upper()) if imei else None
                rebate = safe_float(a.get('reimbursement')) if a else 0.0
                # Prefer the device model resolved from the SALES report by IMEI; fall back to the asset row.
                model = (model_by_imei.get(imei.upper()) or (a.get('device_model') if a else '') or '').strip()
                acq = (a.get('acquired_date') if a else None) or (a.get('payg_date') if a else None)
                disp = rebate if rebate > 0 else abs(amt)   # rebate lost; fall back to bill-pay if no asset match
                txn = str(r.get('payment_date') or r.get('trans_date') or r.get('date') or '')[:10] or acq
                desc = f"Chargeback — rebate lost ${disp:,.2f}"
                if rebate > 0:
                    desc += f" (bill-pay ${abs(amt):,.2f})"
                if model:
                    desc += f" · {model}"
                flags.append({**base,
                    'flag_type': 'CHARGEBACK', 'source': 'payment_detail', 'severity': 'HIGH',
                    'store_address': r.get('business_address', ''),
                    'epay_salesperson': r.get('rep_username', ''),
                    'mdn': r.get('mdn', ''), 'imei': imei,
                    # mig 287 identity fallback: a chargeback row with neither IMEI nor MDN still has
                    # a payment type + date, which is what makes it re-findable on the next run.
                    'source_ref': f"{str(r.get('payment_type') or '').strip()}|{txn}",
                    'amount': round(disp, 2),
                    'rebate_lost': round(rebate, 2) if a else None,
                    'phone_model': model, 'days_active': _days_since(acq),
                    'activation_date': acq, 'transaction_date': txn,
                    'customer_plan': ((a.get('contract_type') if a else '') or '').strip(),
                    'description': desc,
                    'coaching_note': 'Rebate clawed back — review activation quality (port-out / dispute / early cancel).',
                })

    # ── 2. UNMAPPED PAYMENT TYPE ──────────────────────────────────
    unmapped_types: set[str] = set()
    for r in pay_detail:
        cat = str(r.get('category', '') or '').strip()
        if not cat or cat == 'Unknown':
            pt = str(r.get('payment_type', '') or '').strip()
            if pt: unmapped_types.add(pt)
    for pt in unmapped_types:
        rows = [r for r in pay_detail if str(r.get('payment_type', '') or '').strip() == pt]
        total = sum(safe_float(r.get('amount')) for r in rows)
        flags.append({**base,
            'flag_type': 'UNMAPPED_PAYMENT_TYPE', 'source': 'payment_detail',
            'severity': 'LOW',
            # mig 287: the payment type IS this flag's identity. It cannot come from `description`,
            # which embeds a row count and a dollar total that change on every run.
            'source_ref': pt,
            'amount': total,
            'description': f"Payment type '{pt}' not in category master ({len(rows)} rows, ${total:.2f})",
            'coaching_note': 'Add this payment type to Commission Categories in Management > Settings.',
        })

    # ── 3. IMEI FRAUD (same IMEI, different MDNs) ─────────────────
    imei_mdns: dict[str, set] = defaultdict(set)
    imei_reps: dict[str, set] = defaultdict(set)
    for r in valid_sales:
        imei = str(r.get('serial_1', '') or '').replace('.0', '').strip()
        mdn  = str(r.get('mdn', '') or '').replace('.0', '').strip()
        rep  = str(r.get('salesperson', '') or '').strip()
        if imei and mdn:
            imei_mdns[imei].add(mdn)
            imei_reps[imei].add(rep)

    for imei, mdns in imei_mdns.items():
        if len(mdns) > 1:
            reps = [rp for rp in imei_reps.get(imei, set()) if rp]
            for rep in (reps or ['']):
                rep_sale = next((s for s in valid_sales
                                 if str(s.get('serial_1','') or '').replace('.0','').strip() == imei
                                 and str(s.get('salesperson','') or '').strip() == rep), {})
                flags.append({**base,
                    'flag_type': 'DUPLICATE_IMEI', 'source': 'sales',
                    'severity': 'HIGH',
                    'imei': imei,
                    'mdn': str(rep_sale.get('mdn','') or '').replace('.0','').strip(),
                    'store_address': str(rep_sale.get('store','') or ''),
                    'epay_salesperson': rep,
                    'description': f"IMEI {imei} used on {len(mdns)} MDNs: {', '.join(list(mdns)[:3])}",
                    'coaching_note': 'Duplicate IMEI across multiple lines. Review which rep(s) are responsible before charging back.',
                })

    # ── 4. SETUP FEE MISSING ──────────────────────────────────────
    act_trans_ids: set[str] = set()
    setup_trans_ids: set[str] = set()
    for r in valid_sales:
        tid = str(r.get('trans_id', '') or '').replace('.0', '').strip()
        ct  = str(r.get('contract_type', '') or '').strip()
        pd  = str(r.get('product_desc', '') or '')
        if ct in {'Activation', 'Port-In', 'Add A Line', 'BYOD', 'BYOD Port-In'}:
            act_trans_ids.add(tid)
        if 'Device Setup Charge' in pd:
            setup_trans_ids.add(tid)

    missing_setup = act_trans_ids - setup_trans_ids
    if missing_setup:
        # Sample a few for the flag
        sample_rows = [r for r in valid_sales
                       if str(r.get('trans_id','') or '').replace('.0','').strip() in list(missing_setup)[:5]]
        rep_counts: dict[str, int] = defaultdict(int)
        for r in [s for s in valid_sales
                  if str(s.get('trans_id','') or '').replace('.0','').strip() in missing_setup]:
            rep_counts[str(r.get('salesperson','') or '')] += 1
        for rep, cnt in sorted(rep_counts.items(), key=lambda x: -x[1])[:10]:
            if rep:
                flags.append({**base,
                    'flag_type': 'SETUP_FEE_MISSING', 'source': 'sales',
                    'severity': 'MEDIUM',
                    'epay_salesperson': rep,
                    'description': f"{rep} has {cnt} activation(s) with no setup fee charged",
                    'coaching_note': 'Rep may be waiving setup fee to close sale. Review with rep and coach on fee compliance.',
                })

    # ── 5. RSK ACTIVATIONS ────────────────────────────────────────
    # THE register predicate is shared with the Sales-from-Events reports (§23s) — extracted to
    # commcalc/sales_register.py so the flag and the report can never disagree about what an event
    # register sale is (CLAUDE.md duplicate-check gate). Byte-identical to the expression that was
    # inline here: the HOUSE default list is the single value this flag has always used, and this
    # call site deliberately passes the HOUSE default rather than per-org config — a commission
    # FLAG must not change its meaning because a reporting setting was edited.
    from app.modules.commcalc.sales_register import (
        filter_by_register as _filter_by_register, HOUSE_EVENT_REGISTERS as _HOUSE_REGISTERS)
    rsk_rows = _filter_by_register(valid_sales, _HOUSE_REGISTERS)
    if rsk_rows:
        rsk_by_rep: dict[str, int] = defaultdict(int)
        for r in rsk_rows:
            rsk_by_rep[str(r.get('salesperson', '') or '')] += 1
        for rep, cnt in rsk_by_rep.items():
            if rep:
                flags.append({**base,
                    'flag_type': 'RSK_ACTIVATIONS', 'source': 'sales',
                    'severity': 'HIGH',
                    'epay_salesperson': rep,
                    'description': f"{cnt} RSK register activation(s) by {rep}",
                    'coaching_note': 'RSK activations require monitoring per Carrier policy. Verify each activation is legitimate.',
                })

    # ── 6. LOW PORT-IN RATE (from DLAR store) ─────────────────────
    # CORRECTED 2026-10-09 (index §61). This detector read the same column and got both its DIRECTION
    # and its SCALE wrong: `raw_dlar_store.port_pct` is the carrier's PORT-IN share (filled from the
    # portal's `port_ins` / the upload's 'Port %' column) and it is already a percent, 0-100. The old
    # test `safe_float(port_pct) * 100 > 15` therefore turned 66.67% into 6667 and fired on EVERY
    # store that had so much as one port-in, under the name HIGH_PORT_OUT_RATE — accusing the best
    # porting doors in the estate of churn, off a feed that carries no port-out figure at all.
    #
    # Neither the meaning nor the threshold lives here any more: `kpi_failing.port_in_rate` /
    # `low_port_in` is the ONE home for what that column says (the index's "one fact, one home,
    # dereferenced" rule), and `harness_product_mix.py` fails the build if a second reader of the raw
    # column appears. The finding this column CAN support is the one the owner asked for on
    # 2026-10-09: which stores are porting in LESS.
    from app.modules.commcalc import kpi_failing as _kpi
    for r in dlar_store:
        rate, is_low = _kpi.low_port_in(r)
        if is_low:
            flags.append({**base,
                'flag_type': 'LOW_PORT_IN_RATE', 'source': 'dlar_store',
                'severity': 'MEDIUM',
                'store_address': r.get('address', ''),
                'amount': rate,
                'description': (f"Store {r.get('address','')} ported in {rate:.1f}% of its "
                                f"activations (threshold: {_kpi.LOW_PORT_IN_PCT:.0f}%)"),
                'coaching_note': ('A low port-in share means few customers are bringing their number '
                                  'across. Ports are the activations the carrier pays most for and '
                                  'the ones that attach the most accessories, so this is a selling '
                                  'conversation, not a churn one.'),
            })

    # ── 7. MISSING STORE PAYMENT (sales but no payment) ──────────
    stores_with_sales: set[str] = set(street_num(r.get('store', '')) for r in valid_sales if r.get('store'))
    stores_with_payment: set[str] = set(street_num(r.get('business_address', '')) for r in pay_detail if r.get('business_address'))
    for num in stores_with_sales - stores_with_payment:
        if num:
            store_addr = next((r.get('store','') for r in valid_sales if street_num(r.get('store','')) == num), '')
            flags.append({**base,
                'flag_type': 'MISSING_STORE_PAYMENT', 'source': 'payment_detail',
                'severity': 'MEDIUM',
                'store_address': store_addr,
                'description': f"Store {store_addr} has sales data but no Carrier payment received",
                'coaching_note': 'Check if Payment Detail file is complete. May indicate reporting error or payment delay.',
            })

    # ── 8. MISSING STORE SALES (payment but no sales) ─────────────
    for num in stores_with_payment - stores_with_sales:
        if num:
            store_addr = next((r.get('business_address','') for r in pay_detail if street_num(r.get('business_address','')) == num), '')
            flags.append({**base,
                'flag_type': 'MISSING_STORE_SALES', 'source': 'sales',
                'severity': 'LOW',
                'store_address': store_addr,
                'description': f"Store {store_addr} received payment but has no sales data uploaded",
                'coaching_note': 'Sales file may be incomplete. Verify all stores are in the uploaded sales file.',
            })

    return _reg.stamp(flags)
