"""Offline proof for the DM verified-correction overlay (closing/verified_overlay.py, TKT-1030).
MONEY-CRITICAL: proves a DM's verified store-day correction replaces the rep-summed figure on BOTH
column families, zeroes the folded sibling so `closing_cash = epay_cash + store_cash` never
double-counts, applies ONLY when verified, and never fabricates a value the DM didn't enter.

Run: `python3 harness_verified_overlay.py` from backend/.
"""
import sys
sys.path.insert(0, ".")

from app.modules.closing import verified_overlay as V  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


# ── apply_overlay: cash correction maps to both families + zeroes the folded ePay sibling ──────────
agg = {"store_cash": 100.0, "t_cash": 100.0, "epay_cash": 0.0, "store_cc": 50.0, "t_credit": 50.0,
       "epay_cc": 0.0, "t_ext_cc": 0.0, "acc_sale": 10.0, "other_account": 5.0, "t_zelle": 5.0,
       "t_store_acct": 0.0, "t_gift": 0.0, "epay_on_cash": 8.0, "epay_on_cc": 2.0}
V.apply_overlay(agg, {"verified": True, "dm_store_cash": 250.0})
check("dm_store_cash overrides legacy store_cash", agg["store_cash"] == 250.0, agg)
check("dm_store_cash overrides canonical t_cash", agg["t_cash"] == 250.0, agg)
check("dm_store_cash zeroes the folded epay_cash (no double-count)", agg["epay_cash"] == 0.0, agg)
check("closing_cash = epay_cash + store_cash equals the DM figure exactly",
      round(agg["epay_cash"] + agg["store_cash"], 2) == 250.0, agg)
check("a cash correction leaves credit/acc/other untouched",
      agg["store_cc"] == 50.0 and agg["acc_sale"] == 10.0 and agg["other_account"] == 5.0, agg)

# ── credit correction: both families + zero epay_cc + t_ext_cc ─────────────────────────────────────
agg2 = {"store_cc": 50.0, "t_credit": 40.0, "t_ext_cc": 10.0, "epay_cc": 0.0}
V.apply_overlay(agg2, {"dm_store_cc": 300.0})
check("dm_store_cc overrides store_cc AND t_credit", agg2["store_cc"] == 300.0 and agg2["t_credit"] == 300.0, agg2)
check("dm_store_cc zeroes epay_cc and t_ext_cc siblings",
      agg2["epay_cc"] == 0.0 and agg2["t_ext_cc"] == 0.0, agg2)

# ── other/zelle: other_account + t_zelle, zero t_store_acct + t_gift ────────────────────────────────
agg3 = {"other_account": 5.0, "t_zelle": 3.0, "t_store_acct": 1.0, "t_gift": 1.0}
V.apply_overlay(agg3, {"dm_other": 77.0})
check("dm_other overrides other_account AND t_zelle", agg3["other_account"] == 77.0 and agg3["t_zelle"] == 77.0, agg3)
check("dm_other zeroes t_store_acct and t_gift", agg3["t_store_acct"] == 0.0 and agg3["t_gift"] == 0.0, agg3)

# ── accessory + ePay-split fields ──────────────────────────────────────────────────────────────────
agg4 = {"acc_sale": 10.0, "epay_on_cash": 8.0, "epay_on_cc": 2.0, "store_cash": 100.0, "t_cash": 100.0}
V.apply_overlay(agg4, {"dm_acc_sale": 44.0, "dm_epay_cash": 60.0, "dm_epay_cc": 15.0})
check("dm_acc_sale overrides acc_sale", agg4["acc_sale"] == 44.0, agg4)
check("dm_epay_cash overrides epay_on_cash (the ePay split, not the cash total)", agg4["epay_on_cash"] == 60.0, agg4)
check("dm_epay_cc overrides epay_on_cc", agg4["epay_on_cc"] == 15.0, agg4)
check("an ePay-split correction does NOT change the cash total", agg4["store_cash"] == 100.0 and agg4["t_cash"] == 100.0, agg4)

# ── partial correction: only the set dm_* fields move; the rest keep the rep-summed value ──────────
agg5 = {"store_cash": 100.0, "t_cash": 100.0, "epay_cash": 0.0, "store_cc": 50.0, "acc_sale": 10.0}
V.apply_overlay(agg5, {"dm_store_cash": 120.0})   # DM only touched cash
check("partial: unset dm_store_cc leaves store_cc at the rep sum", agg5["store_cc"] == 50.0, agg5)
check("partial: unset dm_acc_sale leaves acc_sale at the rep sum", agg5["acc_sale"] == 10.0, agg5)
check("partial: the one set field still applies", agg5["store_cash"] == 120.0, agg5)

# ── keys absent from the aggregate are never introduced (a cash-only reader stays cash-only) ───────
agg6 = {"t_cash": 100.0, "store_cash": 100.0, "epay_cash": 0.0}   # no credit keys at all
V.apply_overlay(agg6, {"dm_store_cc": 999.0})     # credit correction, but agg has no credit keys
check("a credit correction does not add credit keys to a cash-only aggregate",
      "store_cc" not in agg6 and "t_credit" not in agg6, agg6)

# ── None / empty dm_row is a no-op ─────────────────────────────────────────────────────────────────
agg7 = {"store_cash": 100.0}
V.apply_overlay(agg7, None)
V.apply_overlay(agg7, {})
check("None/empty dm_row leaves the aggregate untouched", agg7["store_cash"] == 100.0, agg7)

# ── has_correction ─────────────────────────────────────────────────────────────────────────────────
check("has_correction True when a dm_* is set", V.has_correction({"dm_store_cash": 1.0}) is True)
check("has_correction False for a verified-but-uncorrected row", V.has_correction({"verified": True}) is False)
check("has_correction False for None", V.has_correction(None) is False)


# ── overlay_cash_reader: deposit/cash-position shape — corrects t_cash + epay subset, NEVER zeroes ──
cr = {"t_cash": 500.0, "epay_cash": 120.0, "rows": 3}   # epay_cash here = ePay-ON-cash SUBSET
V.overlay_cash_reader(cr, {"dm_store_cash": 800.0})
check("cash-reader: dm_store_cash overrides t_cash (total cash)", cr["t_cash"] == 800.0, cr)
check("cash-reader: the ePay-on-cash subset is NEVER zeroed by a cash-total correction", cr["epay_cash"] == 120.0, cr)
cr2 = {"t_cash": 500.0, "epay_cash": 120.0}
V.overlay_cash_reader(cr2, {"dm_epay_cash": 90.0})
check("cash-reader: dm_epay_cash overrides the ePay-on-cash portion", cr2["epay_cash"] == 90.0, cr2)
check("cash-reader: an ePay-portion correction leaves t_cash alone", cr2["t_cash"] == 500.0, cr2)
cr3 = {"t_cash": 500.0}   # no epay_cash key
V.overlay_cash_reader(cr3, None)
check("cash-reader: None dm_row is a no-op", cr3["t_cash"] == 500.0, cr3)


# ── build_overlay_map: only verified rows, keyed by (NORM store, date) ─────────────────────────────
class _R:
    def __init__(self, data): self.data = data


class _Q:
    def __init__(self, rows): self._rows, self._f = rows, []
    def select(self, *_a, **_k): return self
    def eq(self, k, v): self._f.append((k, v)); return self
    def in_(self, k, vals): self._f.append((k, ("__in__", set(str(x) for x in vals)))); return self
    def execute(self):
        out = []
        for r in self._rows:
            ok = True
            for k, v in self._f:
                if isinstance(v, tuple) and v and v[0] == "__in__":
                    if str(r.get(k)) not in v[1]: ok = False; break
                elif r.get(k) != v: ok = False; break
            if ok: out.append(r)
        return _R(out)


class _Schema:
    def __init__(self, rows): self._rows = rows
    def table(self, _t): return _Q(self._rows)


class _Client:
    def __init__(self, rows): self._rows = rows
    def schema(self, _n): return _Schema(self._rows)


rows = [
    {"org_id": "org", "store_code": "s1", "close_date": "2026-08-15", "verified": True, "dm_store_cash": 200.0},
    {"org_id": "org", "store_code": "S2", "close_date": "2026-08-15", "verified": False, "dm_store_cash": 999.0},  # unverified
    {"org_id": "org", "store_code": "S3", "close_date": "2026-08-14", "verified": True, "dm_store_cc": 50.0},      # other date
]
m = V.build_overlay_map(_Client(rows), "org", ["2026-08-15"])
check("build_overlay_map includes the verified store-day, normalized key", ("S1", "2026-08-15") in m, list(m))
check("build_overlay_map EXCLUDES the unverified store-day", ("S2", "2026-08-15") not in m, list(m))
check("build_overlay_map EXCLUDES a store-day outside the requested dates", ("S3", "2026-08-14") not in m, list(m))

# end-to-end: map → apply reproduces the DM figure with no double-count
agg8 = {"store_cash": 100.0, "t_cash": 100.0, "epay_cash": 0.0}
V.apply_overlay(agg8, m.get(("S1", "2026-08-15")))
check("end-to-end overlay lands the verified DM cash figure", agg8["store_cash"] == 200.0 and agg8["epay_cash"] == 0.0, agg8)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
    sys.exit(1)
