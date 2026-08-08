"""
Port-out, transfer-out, and suspension flag detection from MI report.
Rep/store attribution always comes from Sales (rep who wrote the receipt),
matched by phone number — never the MI report's rep_username. That is UNCHANGED:
`epay_salesperson` and `store_address` still come only from the sales match.

The one addition (mig 285) is `store_code`, a visibility-only column used to route
the flag to a district manager. It is filled from the MI row's own dealer door
(salesforce_id -> commcalc.store_mapping) ONLY when the sales match found no store,
because a flag with no store reaches nobody at all. See `_sf_to_code` below.
"""
from datetime import datetime
from collections import defaultdict

def safe_float(v):
    try: return float(v or 0)
    except: return 0.0

def _parse(d):
    if not d: return None
    try: return datetime.strptime(str(d)[:10], '%Y-%m-%d')
    except: return None

def days_between(a, b):
    da, db = _parse(a), _parse(b)
    if da and db: return (db - da).days
    return None

def _sf_to_code(store_mapping):
    """{UPPER salesforce_id -> store_code} from the org's OWN commcalc.store_mapping rows.

    WHY IT IS HERE (mig 285, owner 2026-08-07 "all flags need to be fed thru the dm"): store/rep
    attribution on these flags comes from a SALES match on the customer's MDN, so a line SOLD IN AN
    EARLIER MONTH — i.e. most of the subscriber base — produces a flag with a BLANK store and reaches
    no district manager. 27,428 of the house org's 31,033 flag rows were in exactly that state, 17,662
    of them with no MDN and no IMEI either because the MI row itself carried neither.

    The MI row always carries `salesforce_id` — the dealer door that owns the line — and store_mapping
    already maps it to a store_code. Used ONLY to fill `store_code` when the sales match found nothing,
    so the sales answer (the rep who wrote the receipt) stays authoritative wherever it exists.

    SAP-configurable: read straight out of the tenant's own config table, no carrier/tenant branch. A
    tenant with no salesforce_id column populated gets an empty map and this is a strict no-op.
    Ambiguity is refused — a door mapped to two store_codes resolves to nothing rather than a guess."""
    multi = {}
    for m in (store_mapping or []):
        sf = str(m.get('salesforce_id') or '').strip().upper()
        code = str(m.get('store_code') or '').strip()
        if sf and code:
            multi.setdefault(sf, set()).add(code)
    return {sf: next(iter(v)) for sf, v in multi.items() if len(v) == 1}


def calc_portout_flags(mi_rows, sales, store_mapping, period, period_month, period_year):
    base = {'period': period, 'period_month': period_month, 'period_year': period_year}
    flags = []
    sf_to_code = _sf_to_code(store_mapping)

    # Build phone -> sale (true selling rep/store) from sales
    sale_by_phone = {}
    model_by_imei = {}
    for s in sales:
        mdn = str(s.get('mdn','') or '').replace('.0','').strip()
        if mdn and mdn not in sale_by_phone:
            sale_by_phone[mdn] = s
        sn = str(s.get('serial_1','') or '').replace('.0','').strip()
        pd_desc = str(s.get('product_desc','') or '').strip()
        if sn and pd_desc and sn not in model_by_imei:
            model_by_imei[sn] = pd_desc

    for m in mi_rows:
        status = str(m.get('subscriber_status','') or '').strip().upper()
        phone = str(m.get('phone_number','') or '').replace('.0','').strip()
        act = m.get('mi_activation_date')
        tout = m.get('residual_transfer_out_date')
        deact = m.get('mi_deactivation_date')
        mrc = safe_float(m.get('base_mrc'))
        plan = m.get('customer_plan','')
        imei = str(m.get('device_serial','') or '').replace('.0','').strip()

        sale = sale_by_phone.get(phone, {})
        rep = str(sale.get('salesperson','') or '').strip()
        store = str(sale.get('store','') or '').strip()
        # DM ROUTING (mig 285): when there is no sales match there is no store string, and the flag
        # reaches nobody. Fall back to the door that owns the line. `store_address` is deliberately
        # left as it was — only the new, visibility-only `store_code` is filled, and only when the
        # sales answer is absent, so nothing that already attributed correctly changes.
        door_code = None if store else sf_to_code.get(str(m.get('salesforce_id') or '').strip().upper())

        # STABLE IDENTITY (mig 287, owner 2026-08-08 "DM review should not be erased"): the flag must
        # be re-findable on the next recalculation or the DM's review is lost. 17,662 of the house
        # org's MI flags carry NO MDN and NO IMEI because the MI row itself carries neither — but
        # 100% of those rows DO carry `subscriber_id`, so that is the identity we persist. Identity
        # only: it is not displayed, not summed and pays nobody.
        sub_id = str(m.get('subscriber_id') or '').strip()

        # ── PORT-OUT (customer left Boost) ───────────────────────
        if status == 'PORTED-OUT':
            end_date = tout or deact
            d = days_between(act, end_date)
            if d is not None and d <= 30:
                ft, sev = 'PORT_OUT_30DAY', 'CRITICAL'
            elif d is not None and d <= 60:
                ft, sev = 'PORT_OUT_60DAY', 'HIGH'
            elif d is not None:
                ft, sev = 'PORT_OUT_90PLUS', 'LOW'
            else:
                ft, sev = 'PORT_OUT_NODATE', 'LOW'
            flags.append({**base,
                'flag_type': ft, 'source': 'mi_report', 'severity': sev,
                'store_address': store, 'store_code': door_code, 'epay_salesperson': rep,
                'mdn': phone, 'imei': imei, 'subscriber_id': sub_id, 'amount': mrc,
                'days_active': d, 'phone_model': model_by_imei.get(imei,''), 'customer_plan': plan,
                'description': f"Ported out{f' after {d} days' if d is not None else ''} — plan {plan}, MRC ${mrc:.2f}",
                'coaching_note': 'Port-out within 60 days is a loss. Review at user discretion for chargeback.' if (d is not None and d <= 60) else 'Port-out 3rd month onward — reporting only.',
            })

        # ── RESIDUAL TRANSFER-OUT while ACTIVE (went to another store) ──
        elif status == 'ACTIVE' and tout:
            d = days_between(act, tout)
            flags.append({**base,
                'flag_type': 'RESIDUAL_TRANSFER_OUT', 'source': 'mi_report', 'severity': 'MEDIUM',
                'store_address': store, 'store_code': door_code, 'epay_salesperson': rep,
                'mdn': phone, 'imei': imei, 'subscriber_id': sub_id, 'amount': mrc,
                'days_active': d, 'phone_model': model_by_imei.get(imei,''), 'customer_plan': plan,
                'description': f"Active customer's residual transferred out{f' after {d} days' if d is not None else ''} — upgraded at another store",
                'coaching_note': 'Customer stayed with the Carrier but upgraded elsewhere. Retention/CS follow-up.',
            })

        # ── INVOLUNTARY-SUSPENDED (non-payment, 3MR tracking) ────
        elif status == 'INVOLUNTARY-SUSPENDED':
            ds = days_between(act, deact)
            flags.append({**base,
                'flag_type': 'INVOLUNTARY_SUSPENDED', 'source': 'mi_report', 'severity': 'MEDIUM',
                'store_address': store, 'store_code': door_code, 'epay_salesperson': rep,
                'mdn': phone, 'imei': imei, 'subscriber_id': sub_id, 'amount': mrc,
                'days_active': ds, 'phone_model': model_by_imei.get(imei,''), 'customer_plan': plan,
                'description': f"Involuntary suspended (non-payment) — plan {plan}, MRC ${mrc:.2f}",
                'coaching_note': 'Non-payment affects 3MR. Reporting only — rep may follow up with customer.',
            })

    return flags
