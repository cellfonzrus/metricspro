"""
Port-out, transfer-out, and suspension flag detection from MI report.
Rep/store attribution always comes from Sales (rep who wrote the receipt),
matched by phone number — never the MI report's rep_username.
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

def calc_portout_flags(mi_rows, sales, store_mapping, period, period_month, period_year):
    base = {'period': period, 'period_month': period_month, 'period_year': period_year}
    flags = []

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
                'store_address': store, 'epay_salesperson': rep,
                'mdn': phone, 'imei': imei, 'amount': mrc,
                'days_active': d, 'phone_model': model_by_imei.get(imei,''), 'customer_plan': plan,
                'description': f"Ported out{f' after {d} days' if d is not None else ''} — plan {plan}, MRC ${mrc:.2f}",
                'coaching_note': 'Port-out within 60 days is a loss. Review at user discretion for chargeback.' if (d is not None and d <= 60) else 'Port-out 3rd month onward — reporting only.',
            })

        # ── RESIDUAL TRANSFER-OUT while ACTIVE (went to another store) ──
        elif status == 'ACTIVE' and tout:
            d = days_between(act, tout)
            flags.append({**base,
                'flag_type': 'RESIDUAL_TRANSFER_OUT', 'source': 'mi_report', 'severity': 'MEDIUM',
                'store_address': store, 'epay_salesperson': rep,
                'mdn': phone, 'imei': imei, 'amount': mrc,
                'days_active': d, 'phone_model': model_by_imei.get(imei,''), 'customer_plan': plan,
                'description': f"Active customer's residual transferred out{f' after {d} days' if d is not None else ''} — upgraded at another store",
                'coaching_note': 'Customer stayed with the Carrier but upgraded elsewhere. Retention/CS follow-up.',
            })

        # ── INVOLUNTARY-SUSPENDED (non-payment, 3MR tracking) ────
        elif status == 'INVOLUNTARY-SUSPENDED':
            ds = days_between(act, deact)
            flags.append({**base,
                'flag_type': 'INVOLUNTARY_SUSPENDED', 'source': 'mi_report', 'severity': 'MEDIUM',
                'store_address': store, 'epay_salesperson': rep,
                'mdn': phone, 'imei': imei, 'amount': mrc,
                'days_active': ds, 'phone_model': model_by_imei.get(imei,''), 'customer_plan': plan,
                'description': f"Involuntary suspended (non-payment) — plan {plan}, MRC ${mrc:.2f}",
                'coaching_note': 'Non-payment affects 3MR. Reporting only — rep may follow up with customer.',
            })

    return flags
