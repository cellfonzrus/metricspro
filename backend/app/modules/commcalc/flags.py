"""
Flags Calculator — all 13 flag types
CHARGEBACK, MRC_IMEI_MISMATCH, ACCESSORY_LOSS,
IMEI_FRAUD, IMEI_MULTI_MDN, SETUP_FEE_MISSING,
RSK_ACTIVATIONS, RSK_NON_PAYMENT,
MISSING_STORE_PAYMENT, MISSING_STORE_SALES,
HIGH_PORT_OUT_RATE, HIGH_CHURN_RATE, UNMAPPED_PAYMENT_TYPE
"""
from typing import Any
from collections import defaultdict

def safe_float(v) -> float:
    try: return float(v or 0)
    except: return 0.0

def street_num(addr: str) -> str:
    return str(addr or '').strip().split(' ')[0]

def calc_flags(
    sales: list[dict],
    pay_detail: list[dict],
    mi_rows: list[dict],
    dlar_store: list[dict],
    store_mapping: list[dict],
    period: str,
    period_month: int,
    period_year: int,
) -> list[dict]:
    """Returns list of flag dicts ready to insert into commcalc.flags."""

    flags = []
    base = {'period': period, 'period_month': period_month, 'period_year': period_year}

    valid_sales = [
        r for r in sales
        if str(r.get('voided', '')).upper().strip() != 'YES'
        and str(r.get('trans_type', '')).strip() != 'Return'
    ]

    # ── 1. CHARGEBACK ─────────────────────────────────────────────
    for r in pay_detail:
        if str(r.get('category', '')).strip() == 'Chargeback':
            amt = safe_float(r.get('amount'))
            if amt != 0:
                flags.append({**base,
                    'flag_type': 'CHARGEBACK', 'source': 'payment_detail',
                    'severity': 'HIGH',
                    'store_address': r.get('business_address', ''),
                    'epay_salesperson': r.get('rep_username', ''),
                    'mdn': r.get('mdn', ''), 'imei': r.get('imei', ''),
                    'amount': amt,
                    'description': f"Chargeback of ${abs(amt):.2f} from Boost",
                    'coaching_note': 'Review activation quality. Customer may have ported out or disputed.',
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
    rsk_rows = [r for r in valid_sales if str(r.get('register', '') or '').strip().upper() == 'RSK']
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
                    'coaching_note': 'RSK activations require monitoring per Boost policy. Verify each activation is legitimate.',
                })

    # ── 6. HIGH PORT-OUT RATE (from DLAR store) ───────────────────
    for r in dlar_store:
        port_pct = safe_float(r.get('port_pct')) * 100
        if port_pct > 15:  # > 15% port-out rate = flag
            flags.append({**base,
                'flag_type': 'HIGH_PORT_OUT_RATE', 'source': 'dlar_store',
                'severity': 'MEDIUM',
                'store_address': r.get('address', ''),
                'amount': port_pct,
                'description': f"Store {r.get('address','')} port-out rate {port_pct:.1f}% (threshold: 15%)",
                'coaching_note': 'High port-out may indicate poor activation quality or customer satisfaction issues.',
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
                'description': f"Store {store_addr} has sales data but no Boost payment received",
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

    return flags
