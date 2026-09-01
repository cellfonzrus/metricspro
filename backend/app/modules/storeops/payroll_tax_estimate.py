"""Payroll-with-tax ESTIMATE engine — the PYTHON TWIN of `frontend/src/lib/payroll-tax.ts`.

WHY A TWIN EXISTS (W3, scheduled workforce reports 2026-09-01): the Payroll-with-Tax page's spec
deliberately keeps the withholding math in the BROWSER over `GET /storeops/payroll-raw`'s raw
inputs, "so stored figures never go stale when rates change". A scheduled email/WhatsApp send has
no browser, so the notify report registry (the server twin of each page's export — its own module
docstring) needs the same arithmetic server-side. This is the established cross-language-twin
convention (`frontend/src/lib/cell-safety.ts` is the TS twin of `notify/render.py`'s formula guard);
it re-uses the SAME data path (`/payroll-raw`) and only mirrors the page's presentation math.

⚠️ KEEP IN LOCKSTEP with `frontend/src/lib/payroll-tax.ts` — same constants, same expression order,
same rounding (JS `Math.round((n + Number.EPSILON) * 100) / 100`, i.e. half-UP on the cents digit —
Python's built-in banker's `round()` would disagree on exact .5 cents). The TS file's own header
says the rate constants are "grouped here so they can later be moved to a config table (SaaS
directive)"; when that lands, BOTH twins read the config row and this table becomes the seed.

⚠️ ESTIMATE for internal visibility — flat-rate, not a substitute for a payroll provider's exact
withholding (same caveat the page itself displays).

LEAF MODULE: stdlib only, pure — provable offline (backend/harness_workforce_report_registry.py).
"""
import math

# Mirror of payroll-tax.ts TAX_RATES — field for field.
TAX_RATES = {
    "fica_ss": 0.062, "fica_ss_wage_base": 168600,   # Social Security (employee share), 2024 base
    "fica_medicare": 0.0145,                          # Medicare (employee share)
    "federal_supplemental": 0.22,                     # flat 22% (skipped / supplemental mode)
    # simplified flat effective federal rate by filing status (hourly-wage approximation)
    "federal_by_status": {"Single": 0.12, "Married": 0.10, "HOH": 0.11},
    "federal_allowance_credit": 15,                   # $ reduction per allowance per period
    # state income tax (flat approximations; PA is genuinely flat, others progressive → estimate)
    "state_sit": {"NY": 0.0633, "NJ": 0.05525, "PA": 0.0307, "DE": 0.05},
    "ny_disability_rate": 0.005, "ny_disability_max": 0.60,   # NYS Disability, capped $0.60/wk
    "ot_multiplier": 1.5, "ot_threshold": 40,         # overtime above 40 hrs (per period passed in)
}

_EPSILON = 2.220446049250313e-16   # Number.EPSILON — same nudge the TS twin applies before rounding


def _round2(n):
    """JS `Math.round((n + Number.EPSILON) * 100) / 100` — half-up cents. All inputs here are
    non-negative (federal is clamped to >= 0 before rounding), matching the TS twin's domain."""
    return math.floor((float(n) + _EPSILON) * 100 + 0.5) / 100


def compute_pay(total_hours, rate, w4):
    """Line-for-line port of payroll-tax.ts `computePay`. `w4` is the `settings` dict
    `/storeops/payroll-raw` already emits: {filing_status, allowances, state, extra_withholding,
    skipped}. Returns the same keys as the TS `PayrollLine`."""
    t = TAX_RATES
    w4 = w4 or {}
    th, rate = float(total_hours or 0), float(rate or 0)
    regular_hours = min(max(th, 0), t["ot_threshold"])
    ot_hours = max(th - t["ot_threshold"], 0)
    gross = _round2(regular_hours * rate + ot_hours * rate * t["ot_multiplier"])

    fica_ss = _round2(gross * t["fica_ss"])
    fica_medicare = _round2(gross * t["fica_medicare"])

    if w4.get("skipped"):
        federal = gross * t["federal_supplemental"]
    else:
        fbs = t["federal_by_status"]
        federal = gross * fbs.get(str(w4.get("filing_status") or "Single"), fbs["Single"])
    federal = _round2(max(0, federal - float(w4.get("allowances") or 0) * t["federal_allowance_credit"])
                      + float(w4.get("extra_withholding") or 0))

    state_code = str(w4.get("state") or "NY").upper()
    state = _round2(gross * t["state_sit"].get(state_code, 0))
    disability = (_round2(min(gross * t["ny_disability_rate"], t["ny_disability_max"]))
                  if state_code == "NY" else 0)

    deductions = _round2(fica_ss + fica_medicare + federal + state + disability)
    net = _round2(gross - deductions)
    employer_fica = _round2(fica_ss + fica_medicare)   # employer matches SS + Medicare
    return {"regular_hours": _round2(regular_hours), "ot_hours": _round2(ot_hours), "gross": gross,
            "fica_ss": fica_ss, "fica_medicare": fica_medicare, "federal": federal, "state": state,
            "disability": disability, "deductions": deductions, "net": net,
            "employer_fica": employer_fica}
