"""THE PROOF — the service fee on a bill payment is not a transaction a contract type could have described.

Drives the REAL `router._classification_gaps` / `_txn_activation_candidate` / `_accessory_config_uncached`
over in-memory rows. DB-free: the config row is served by a tiny fake client, so nothing here touches
Supabase and the harness runs in CI.

THE DEFECT, as the owner saw it (2026-10-03, Sales Report):
    "⚠️ 693 transaction(s) have no contract type and no activation rule matched — map them … so they
     count as activations."

MEASURED AGAINST PRODUCTION (house org, read-only, before the fix):
    October 2026   693 unrecovered — 690 of them bill-payment receipts
    September 2026 4,206 unrecovered — 4,142 of them bill-payment receipts
    August 2026    4,315 unrecovered — 4,270 of them bill-payment receipts
AFTER the fix, same rows, same reader: 3 / 64 / 45. The other two tenants are unchanged (4 / 13 / 13 / 9).

THE CLASS, NOT THE INSTANCE. `_txn_activation_candidate` suppresses a blank-contract-type transaction when
every line is EXCLUDED, a BILL-PAYMENT product, or an ACCESSORY. The house org rings the customer's
service fee as its OWN sales line beside the payment line, so the payment was suppressed and the FEE was
not — one surviving line made every walk-in bill payment read as an unclassified activation. Acting on the
banner would have swept bill payments into the activation count: the exact failure the predicate was
written to prevent for the other tenant.

ONE FACT, ONE HOME, DEREFERENCED. The fee vocabulary is not re-decided here. It is
`epay_fee_recon.resolve_fee_descs` over the per-org mig-1045 column `accessory_config.billpay_fee_product_desc`
— the same home the P&L booking (`account/coa.py`), the fee reconciliation and the pickup-netting basis
already read. `_accessory_config_uncached` resolves it onto `acfg['billpay_fee_descs']` from the whole-row
read it already does, and the predicate reads it from there.

  python3 backend/harness_billpay_fee_not_activation.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("SUPABASE_KEY", "test")

from app.modules.commcalc import router as R            # noqa: E402
from app.modules.commcalc import epay_fee_recon as FR    # noqa: E402

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name}{(' — ' + detail) if detail else ''}")


# ── THE FAKE CLIENT (DB-free): serves ONE accessory_config row, nothing else ───────────────────────
class _Q:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def maybe_single(self):
        return self

    def single(self):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _Schema:
    def __init__(self, tables):
        self._t = tables

    def table(self, name):
        return _Q(self._t.get(name, []))


class FakeClient:
    def __init__(self, cfg_row):
        self._t = {"accessory_config": [cfg_row] if cfg_row else []}

    def schema(self, _s):
        return _Schema(self._t)


ORG = "00000000-0000-0000-0000-000000000001"

# The house org's REAL config shape, as read from production 2026-10-03 (read-only): the accessory
# department list, no billpay_products, no contract-type map, no activation rules, no fee config (so the
# fee vocabulary must resolve to the HOUSE default).
HOUSE_CFG = {
    "org_id": ORG,
    "departments": ["c2 wireless", "ondigo", "sim cards", "voicecomm"],
    "categories": ["accessory"],
    "product_keywords": None,
    "billpay_products": [],
    "billpay_fee_product_desc": [],
    "contract_type_map": {},
    "activation_rules": [],
    "box_departments": ["Android - XP", "IPHONE - XP", "TABLET - XP"],
    "activation_details_rules": None,
}


def line(dept, cat, prod, tid, ct="", ext="0", voided=""):
    return {"trans_id": tid, "store": "117 E Burnside Ave", "trans_date": "2026-10-01",
            "salesperson": "rep", "department": dept, "category": cat, "product_desc": prod,
            "contract_type": ct, "ext_price": ext, "gp": "0", "voided": voided, "trans_type": "Sale"}


# The house RTR payout exclusion, exactly as seeded (word-anchored 'RTR' on product_desc).
def is_excluded(r):
    words = str(r.get("product_desc") or "").replace("$", " ").replace("-", " ").upper().split()
    return "RTR" in words


# A walk-in bill payment, as the POS actually rings it: the RTR payment line + the service fee line.
def billpay(tid):
    return [line("Bill Payments", "Boost RTR", "Boost RTR $1-$650", tid, ext="50"),
            line("Bill Payments", "Other Charge", "ePay Service Charge", tid, ext="4")]


# A real activation the POS left with a blank contract type (production tid 214887's shape).
def blank_activation(tid):
    return [line("", "", "Boost Protect Tier 1", tid),
            line("", "", "Unlimited+ $50 Unlimited Data, Talk & Text + 40GB", tid),
            line("Android - XP", "Motorola", "moto g play - 2026", tid, ext="249.99"),
            line("Dev. Charges or Fees", "Device Setup Charge",
                 "Device Setup Charge (non-refundable)", tid, ext="35"),
            line("Bill Payments", "Boost RTR", "Boost RTR $1-$650", tid, ext="60")]


def gaps(rows, cfg=HOUSE_CFG, exc=is_excluded):
    acfg = R._accessory_config_uncached(FakeClient(cfg), ORG)
    return R._classification_gaps(rows, acfg, is_excluded=exc, want_samples=True), acfg


# ── A. THE REGISTRY IS DEREFERENCED, NOT COPIED ───────────────────────────────────────────────────
_, acfg = gaps([])
ok("A1 the resolved config carries the fee vocabulary", "billpay_fee_descs" in acfg)
ok("A2 an unset tenant resolves to the ONE registry's house default",
   tuple(acfg["billpay_fee_descs"]) == FR.HOUSE_FEE_DESCS, repr(acfg.get("billpay_fee_descs")))
_, acfg_cfg = gaps([], cfg=dict(HOUSE_CFG, billpay_fee_product_desc=["Wallet Fee"]))
ok("A3 a tenant's own vocabulary is what resolves (config, never code)",
   tuple(acfg_cfg["billpay_fee_descs"]) == ("wallet fee",), repr(acfg_cfg.get("billpay_fee_descs")))
ok("A4 the predicate's answer follows the CONFIG, not a constant",
   R._txn_activation_candidate(billpay("t"), acfg, is_excluded) is False
   and R._txn_activation_candidate(billpay("t"), acfg_cfg, is_excluded) is True)

# ── B. THE REGRESSION: the 690 (one of them, in miniature) ────────────────────────────────────────
rows = []
for i in range(690):
    rows += billpay(f"bp{i}")
g, _ = gaps(rows)
ok("B1 690 bill payments are 690 blank-contract-type transactions",
   g["blank_ct_transactions"] == 690, str(g["blank_ct_transactions"]))
ok("B2 NONE of them is an activation candidate — the banner stays silent",
   g["blank_ct_unrecovered"] == 0, str(g["blank_ct_unrecovered"]))
ok("B3 all 690 are reported as non-activations, never hidden",
   g["blank_ct_non_activation"] == 690, str(g["blank_ct_non_activation"]))
ok("B4 no note is raised", g.get("note") is None, repr(g.get("note")))

# ── C. THE REAL GAP IS STILL REPORTED (the fix cannot hide a true unclassified activation) ─────────
g, _ = gaps(rows + blank_activation("214887"))
ok("C1 the one genuine blank-contract-type activation IS still alarmed on",
   g["blank_ct_unrecovered"] == 1, str(g["blank_ct_unrecovered"]))
ok("C2 the banner names it", bool(g.get("note")) and "1 transaction(s)" in (g.get("note") or ""),
   repr(g.get("note")))
tids = [t["trans_id"] for t in (g.get("blank_ct_unrecovered_txns") or [])]
ok("C3 it is listed by trans_id for the owner to read off", tids == ["214887"], str(tids))
by_line = g.get("blank_ct_unrecovered_by_line") or []
ok("C4 the fee line never appears in the 'map these' list",
   not any(FR.is_fee_desc(r.get("product_desc")) for r in by_line),
   str([r.get("product_desc") for r in by_line]))

# ── D. THE FEE ALONE IS NOT AN ACTIVATION; A SALE ALONGSIDE IT STILL IS ───────────────────────────
fee_only = [line("Bill Payments", "Other Charge", "ePay Service Charge", "f1", ext="4")]
g, _ = gaps(fee_only)
ok("D1 a fee-only receipt is not an activation candidate", g["blank_ct_unrecovered"] == 0)
g, _ = gaps(billpay("mix") + [line("Android - XP", "Motorola", "moto g play - 2026", "mix", ext="249.99")])
ok("D2 a bill payment that ALSO sold a phone is still a candidate", g["blank_ct_unrecovered"] == 1)
g, _ = gaps(billpay("acc") + [line("Ondigo", "Cables", "MyBat USB-C Cable", "acc", ext="19.99")])
ok("D3 fee + accessory only is still not a candidate", g["blank_ct_unrecovered"] == 0)

# ── E. A CLASSIFIED LINE AND A VOID ARE UNAFFECTED ────────────────────────────────────────────────
mapped = dict(HOUSE_CFG, contract_type_map={"activation": "premium"})
g, _ = gaps(billpay("c1") + [line("Android - XP", "Motorola", "moto g play", "c1",
                                  ct="Activation", ext="249.99")], cfg=mapped)
ok("E1 a transaction whose line DOES classify is neither blank nor unrecovered",
   g["blank_ct_transactions"] == 0 and g["blank_ct_unrecovered"] == 0,
   f"{g['blank_ct_transactions']}/{g['blank_ct_unrecovered']}")
voided = [line("Bill Payments", "Boost RTR", "Boost RTR $1-$650", "v1", ext="50", voided="true"),
          line("Bill Payments", "Other Charge", "ePay Service Charge", "v1", ext="4", voided="true")]
g, _ = gaps(voided)
ok("E2 a voided bill payment is counted nowhere",
   g["blank_ct_transactions"] == 0 and g["blank_ct_unrecovered"] == 0)

# ── F. BYTE-IDENTITY FOR A TENANT THAT RINGS NO FEE LINE (the other tenant's shape) ───────────────
other = dict(HOUSE_CFG, org_id="854f6d7b", departments=[], categories=[],
             billpay_products=["Total Wireless RTR Wallet"], billpay_fee_product_desc=[])
tw = [line("Rtr", "Rtr", "Total Wireless RTR Wallet", "tw1", ext="40")]
g, _ = gaps(tw, cfg=other)
ok("F1 a tenant with no fee line is unchanged — the payment alone suppresses",
   g["blank_ct_transactions"] == 1 and g["blank_ct_unrecovered"] == 0)
g, _ = gaps(tw + [line("SimMarketplace", "Sim", "Device Upgrade", "tw1", ext="0")], cfg=other)
ok("F2 and its real gaps are still reported", g["blank_ct_unrecovered"] == 1)

# ── G. NEGATIVE CONTROLS — the fix is load-bearing ────────────────────────────────────────────────
no_vocab = dict(acfg)
no_vocab["billpay_fee_descs"] = ()
ok("G1 NEG with no vocabulary resolved, the bill payment reads as a candidate again (the old bug)",
   R._txn_activation_candidate(billpay("t"), no_vocab, is_excluded) is True)
ok("G2 NEG without the RTR exclusion the payment line itself still alarms (unrelated path intact)",
   R._txn_activation_candidate(billpay("t"), acfg, None) is False)   # billpay default tokens catch it
ok("G3 NEG the fee test never swallows a device line",
   R._txn_activation_candidate(blank_activation("x"), acfg, is_excluded) is True)

print(f"PASS {len(PASS)}")
for p in PASS:
    print(f"  [PASS] {p}")
if FAIL:
    print(f"\n❌ {len(FAIL)} failure(s):")
    for f in FAIL:
        print(f"  [FAIL] {f}")
    sys.exit(1)
print("\n✅ harness_billpay_fee_not_activation: ALL PASS")
