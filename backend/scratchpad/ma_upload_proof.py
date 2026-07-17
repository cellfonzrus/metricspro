"""Proof for agent/commission/ma-manual-upload — the per-carrier SAP-style MANUAL upload of the MA
reports. Pure unit tests over the REAL ma_upload logic + the REAL report_pull mapper; NO live DB, NO
browser.

Run:  cd backend && python3 scratchpad/ma_upload_proof.py

Covers the required proofs:
 (a) MULTI-MONTH SPLIT — one file spanning several months is bucketed to each row's REAL month from the
     file's own date column (report_pull.apply_column_map derives period per row; group_by_period /
     period_counts / detected_periods agree). No single period label is forced onto the file.
 (b) DEDUP IDEMPOTENCE — the same file uploaded twice inserts ZERO new rows (filter_new vs the keys of
     the first load); in-file duplicates collapse (dedupe_within); genuinely different rows survive.
 (c) NATURAL KEYS per report (ma_commission / ma_daily_tx / ma_marketplace_orders) + the empty-key
     content-signature fallback.
 (d) MAPPING ROUND-TRIP — build_column_map(field_sources) -> effective_column_map -> the mapper reads
     the user's headers; TYPES (num/date/text) are inherited from the default so casting is preserved;
     mapping_status precedence saved > default > none.
 (e) LINKAGE — Activation Order ↔ Order Number matched/unmatched counts.
 (f) BUG-2 DATA SOURCE — detected_periods returns the file's months, chronologically, for the dropdown.
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.modules.commcalc import ma_upload as mu          # noqa: E402
from app.modules.commcalc import report_pull as rp         # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"   # luxelink
CARR = "carrier-total-uuid"


def spec(rk):
    for s in rp.DEFAULT_REPORT_SPECS:
        if s["report_key"] == rk:
            return dict(s)
    raise AssertionError(rk)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# (a) MULTI-MONTH SPLIT — a real MA Commission export spanning May/June/July 2026
# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n(a) multi-month split")
comm_spec = spec("ma_commission")
# raw source rows keyed by the portal's OWN headers (per the ma_commission column_map)
comm_rows = [
    {"Date": "05/31/2026", "Activation Order": "AO-1", "IMEI": "111", "SKU": "S1", "Sub Type": "TWP", "Rebate": "10", "User Name": "Rep A"},
    {"Date": "06/01/2026", "Activation Order": "AO-2", "IMEI": "222", "SKU": "S1", "Sub Type": "TWP", "Rebate": "20", "User Name": "Rep A"},
    {"Date": "06/15/2026", "Activation Order": "AO-3", "IMEI": "333", "SKU": "S2", "Sub Type": "TWP", "Rebate": "5",  "User Name": "Rep B"},
    {"Date": "07/02/2026", "Activation Order": "AO-4", "IMEI": "444", "SKU": "S2", "Sub Type": "TWP", "Rebate": "0",  "User Name": "Rep B"},
    {"Date": "07/02/2026", "Activation Order": "AO-4", "SIM": "SIM9", "SKU": "S9", "Sub Type": "SIM", "Rebate": "0",  "User Name": "Rep B"},  # same order, different line
]
mapped = rp.apply_column_map(comm_rows, comm_spec, ORG, source_id=None, carrier_id=CARR)
check("mapped every row", len(mapped) == 5)
check("carrier stamped on rows", all(m.get("carrier_id") == CARR for m in mapped))
check("period derived per row (not one label)", {m["period"] for m in mapped} == {"May 2026", "June 2026", "July 2026"})
pc = mu.period_counts(mapped)
check("per-month counts split correctly", pc == {"May 2026": 1, "June 2026": 2, "July 2026": 2})
grp = mu.group_by_period(mapped)
check("group_by_period buckets to 3 months", set(grp.keys()) == {"May 2026", "June 2026", "July 2026"})
check("group_by_period July holds both AO-4 lines", len(grp["July 2026"]) == 2)
ws, we = mu.date_span(mapped, mu.date_field_for("ma_commission", comm_spec))
check("date span min/max", (ws, we) == ("2026-05-31", "2026-07-02"))

# ══════════════════════════════════════════════════════════════════════════════════════════════
# (b) DEDUP IDEMPOTENCE — same file twice = zero new rows
# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n(b) dedup idempotence")
# first load: keys of everything inserted
keys_after_first = {mu.natural_key("ma_commission", m) for m in mapped}
# second upload of the SAME file → filter_new against those keys must add nothing
new2, dropped2 = mu.filter_new(keys_after_first, mapped, "ma_commission")
check("re-upload of identical file adds 0 rows", len(new2) == 0)
check("all 5 counted as duplicate", dropped2 == 5)
# in-file duplicate (AO-4 SIM line pasted twice) collapses to one
dup_file = mapped + [dict(mapped[-1])]
deduped, within = mu.dedupe_within(dup_file, "ma_commission")
check("in-file duplicate collapses", len(deduped) == 5 and within == 1)
# a genuinely-new row (new order) is NOT dropped
newrow = rp.apply_column_map(
    [{"Date": "07/03/2026", "Activation Order": "AO-5", "IMEI": "555", "SKU": "S3", "Sub Type": "TWP", "Rebate": "7"}],
    comm_spec, ORG, carrier_id=CARR)
n3, d3 = mu.filter_new(keys_after_first, newrow, "ma_commission")
check("a brand-new activation is inserted", len(n3) == 1 and d3 == 0)

# ══════════════════════════════════════════════════════════════════════════════════════════════
# (c) NATURAL KEYS per report + content-sig fallback
# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n(c) natural keys + fallback")
# ma_commission: same order, different IMEI/SKU/sub_type => DIFFERENT keys
kA = mu.natural_key("ma_commission", {"activation_order": "AO-4", "tx_date": "2026-07-02", "imei": "444", "sku": "S2", "sub_type": "TWP"})
kB = mu.natural_key("ma_commission", {"activation_order": "AO-4", "tx_date": "2026-07-02", "sim": "SIM9", "sku": "S9", "sub_type": "SIM"})
check("same order + different line => different keys", kA != kB)
# ma_daily_tx: order + date + product + amount
dtx_spec = spec("ma_daily_tx")
dtx_rows = [
    {"Date of Transaction": "07/05/2026", "Order Number": "ON-1", "Product Name": "Airtime", "Retail Cost": "30", "Merchant Invoice": "27"},
    {"Date of Transaction": "07/05/2026", "Order Number": "ON-1", "Product Name": "SIM Kit", "Retail Cost": "5",  "Merchant Invoice": "4"},
]
dtx_mapped = rp.apply_column_map(dtx_rows, dtx_spec, ORG, carrier_id=CARR)
kd1 = mu.natural_key("ma_daily_tx", dtx_mapped[0]); kd2 = mu.natural_key("ma_daily_tx", dtx_mapped[1])
check("ma_daily_tx: two product lines of one order => distinct keys", kd1 != kd2)
check("ma_daily_tx period derived", {m["period"] for m in dtx_mapped} == {"July 2026"})
# fallback: a row with all key fields empty uses a content signature (still deterministic, still distinct)
e1 = {"activation_order": "", "tx_date": "", "imei": "", "sim": "", "sku": "", "sub_type": "", "rebate": 3}
e2 = {"activation_order": "", "tx_date": "", "imei": "", "sim": "", "sku": "", "sub_type": "", "rebate": 4}
kf1, kf2 = mu.natural_key("ma_commission", e1), mu.natural_key("ma_commission", e2)
check("empty-key rows fall back to content sig", "SIG" in kf1 and kf1 != kf2)
check("identical empty-key rows share a key", mu.natural_key("ma_commission", dict(e1)) == kf1)
# marketplace/handset ordering key
mk = mu.natural_key("ma_marketplace_orders", {"order_number": "MO-1", "product_name": "iPhone", "date_ordered": "2026-07-01", "number_ordered": 2})
check("ma_marketplace_orders key stable", mk == mu.natural_key("ma_marketplace_orders", {"order_number": "MO-1", "product_name": "iPhone", "date_ordered": "2026-07-01", "number_ordered": "2"}))

# ══════════════════════════════════════════════════════════════════════════════════════════════
# (d) MAPPING ROUND-TRIP — user's headers, types inherited, status precedence
# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n(d) mapping round-trip")
default_map = comm_spec["column_map"]
# a tenant whose export renamed "Date" -> "Txn Date" and "Rebate" -> "Reb Amt"
field_sources = {"tx_date": "Txn Date", "activation_order": "Order#", "rebate": "Reb Amt"}
cm = mu.build_column_map(field_sources, default_map)
check("build_column_map maps user headers to dest cols", cm.get("Txn Date", {}).get("col") == "tx_date")
check("build_column_map inherits the numeric type for rebate", cm.get("Reb Amt", {}).get("type") == "num")
check("build_column_map inherits the date type for tx_date", cm.get("Txn Date", {}).get("type") == "date")
# effective map: a saved override wins over the default
eff = mu.effective_column_map(cm, default_map)
check("saved override wins over default", eff is cm)
check("no override => default is used", mu.effective_column_map({}, default_map) is default_map)
# the mapper then reads the TENANT's headers via the saved override
user_rows = [{"Txn Date": "07/09/2026", "Order#": "AO-9", "Reb Amt": "12.50"}]
user_mapped = rp.apply_column_map(user_rows, {**comm_spec, "column_map": eff}, ORG, carrier_id=CARR)
check("override reads renamed headers", user_mapped and user_mapped[0].get("activation_order") == "AO-9")
check("override casts rebate to number", user_mapped[0].get("rebate") == 12.5)
check("override derives period from renamed date", user_mapped[0].get("period") == "July 2026")
# status precedence
check("status: saved override", mu.mapping_status({"column_map": cm, "updated_at": "2026-07-17T00:00:00Z", "saved_by": "sanjot"}, default_map)["source"] == "saved")
check("status: default when no override", mu.mapping_status(None, default_map)["source"] == "default")
check("status: none when neither", mu.mapping_status(None, {})["mapped"] is False)
# target-field catalog + suggestions
cat = mu.target_field_catalog(default_map)
check("catalog lists dest fields", any(f["col"] == "activation_order" for f in cat))
sug = mu.suggest_sources(["Date", "Activation Order", "Rebate", "IMEI"], default_map)
check("suggest matches exact header", sug.get("activation_order") == "Activation Order")

# ══════════════════════════════════════════════════════════════════════════════════════════════
# (e) LINKAGE — Activation Order ↔ Order Number
# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n(e) linkage")
comm_for_link = [{"activation_order": "AO-1"}, {"activation_order": "AO-2"}, {"activation_order": "AO-3"}]
daily_order_numbers = {"AO-2", "AO-3", "AO-77"}
lk = mu.linkage_counts("ma_commission", comm_for_link, daily_order_numbers)
check("linkage matched count", lk["matched"] == 2)
check("linkage unmatched count", lk["unmatched"] == 1)
check("linkage distinct count", lk["distinct"] == 3)
check("linkage None for a non-join report", mu.linkage_counts("ma_marketplace_orders", comm_for_link, daily_order_numbers) is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════
# (f) BUG-2 DATA SOURCE — detected_periods for the period dropdown, chronological
# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n(f) detected_periods (period dropdown source)")
dp = mu.detected_periods(mapped, "tx_date")
check("detected_periods returns the file's months", dp == ["May 2026", "June 2026", "July 2026"])
check("detected_periods empty on no dates", mu.detected_periods([{"period": ""}], "tx_date") == [])

print(f"\n=== ma_upload_proof: {PASS} passed, {FAIL} failed ===")
sys.exit(1 if FAIL else 0)
