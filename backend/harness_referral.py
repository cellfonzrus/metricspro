"""Offline proof (no live DB, no network) for the Referral package.

OWNER DIRECTIVE 2026-08-13 (sanjot@): staff create a referral → a QR goes to the referring party → the
referred customer comes back, the QR is scanned, the sale is done → once the LINE IS ACTIVATED, an
approval (USER-DEFINED amount + payout date) goes to the referrer. "Must be FOOLPROOF so nobody can
scam the system."

SECTION A — normalization: phone (SQL-parity with core.referral.*_phone_norm), name, email, and the six
            product 'bubbles' (canonicalize + validate; a forged eighth product is rejected).
SECTION B — config resolution: defaults, partial override, garbage clamping, bad enum degrade.
SECTION C — the TOKEN: unforgeable (tamper → None), unguessable, wrong-secret → None, fail-closed when
            no secret, round-trips id+version; version bump invalidates an old token.
SECTION D — the STATE MACHINE: the money-safety invariants — pay only from approved, approve only from
            commission_pending (which is only reachable via activated); illegal jumps refused.
SECTION E — expiry: redeem_deadline uses the STRICTER of the two windows; is_redeem_expired.
SECTION F — anti-fraud: self-referral block (+ tenant off-switch), duplicate customer / open-referral
            block (+ void frees the number), velocity cap (+ window + cap=0 disables).
SECTION G — segregation of duties (a rep can't approve their OWN referral) + commission math + payout date.
SECTION H — dashboard math: funnel order, $ pending-approval vs approved-unpaid vs paid, fraud tally.
SECTION I — router wiring against an in-memory fake Supabase client whose .eq() ACTUALLY FILTERS and
            whose .update() ACTUALLY MUTATES (a no-op stub proves nothing — [[fake-client-eq-noop-trap]]):
            org scoping on read AND insert, create-time fraud flag, transition gating, approve
            segregation-of-duties, the PUBLIC redeem (uniform 404 on bad/used/expired token; single-use;
            fraud-trip stays uniform), and missing-table degrade.

Run: `python3 harness_referral.py` from backend/.
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} :: {detail}")


NOW = datetime(2026, 8, 13, 15, 0, tzinfo=timezone.utc)
SECRET = b"unit-test-referral-secret"


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION A — normalization + products
# ══════════════════════════════════════════════════════════════════════════════════════════════
from app.modules.referral import referral_core as core  # noqa: E402

check("A1 formatted number -> last 10", core.normalize_phone("(516) 555-0134") == "5165550134")
check("A2 +1 country code dropped", core.normalize_phone("+1 516 555 0134") == "5165550134")
check("A3 an extension does NOT shift the key", core.normalize_phone("5165550134 x22") == "5165550134",
      core.normalize_phone("5165550134 x22"))
check("A4 12+ digits keep the FIRST ten", core.normalize_phone("516555013422") == "5165550134")
check("A5 too short refuses rather than half-matching", core.normalize_phone("5550") == "")
check("A6 empty / None safe", core.normalize_phone(None) == "" and core.normalize_phone("") == "")
# ⚠️ SQL PARITY: identical to the generated columns core.referral.*_phone_norm (mig 850), which are a
# byte-copy of core.crm_lead.phone_norm (mig 800). If these disagree, every self-referral / duplicate
# check silently misses.
SQL_PARITY = {"(516) 555-0134": "5165550134", "+1 516 555 0134": "5165550134",
              "1-516-555-0134": "5165550134", "5165550134 x22": "5165550134",
              "5550": "", "516555013422": "5165550134"}
check("A7 Python matches the SQL generated column on every verified case",
      all(core.normalize_phone(k) == v for k, v in SQL_PARITY.items()),
      {k: core.normalize_phone(k) for k, v in SQL_PARITY.items() if core.normalize_phone(k) != v})
check("A8 mask shows only the last 4", core.mask_phone("(516) 555-0134") == "••••0134")
check("A9 name collapses whitespace", core.normalize_name("  john   smith ") == "john smith")
check("A10 email normalized", core.normalize_email("  Bob@Example.COM ") == "bob@example.com")

# Products — the exact six bubbles, canonicalized, deduped, in display order.
check("A11 the six bubbles are exactly the directive's set",
      core.ALLOWED_PRODUCTS == ["Phone", "Activations", "Tablet", "BYOD", "Home Internet", "Accessories"])
check("A12 aliases + case canonicalize",
      core.normalize_products(["phone", "HOME INTERNET", "byod"]) == ["Phone", "BYOD", "Home Internet"],
      core.normalize_products(["phone", "HOME INTERNET", "byod"]))
check("A13 duplicates collapse and order is the canonical bubble order",
      core.normalize_products(["Accessories", "Phone", "phone"]) == ["Phone", "Accessories"])
ok, norm, rej = core.validate_products(["Phone", "Spaceship"])
check("A14 an unknown product is REJECTED loudly, not silently dropped",
      not ok and rej == ["Spaceship"] and norm == ["Phone"], (ok, norm, rej))
ok, norm, rej = core.validate_products(["Phone", "Tablet"])
check("A15 a clean multi-select validates", ok and norm == ["Phone", "Tablet"])
ok, norm, rej = core.validate_products([])
check("A16 an empty selection is valid (products captured later)", ok and norm == [])
ok, norm, rej = core.validate_products("Phone")
check("A17 a scalar is not a multi-select and is refused", not ok)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION B — config
# ══════════════════════════════════════════════════════════════════════════════════════════════
check("B1 no row -> pure defaults", core.resolve_config(None) == core.DEFAULT_CONFIG)
check("B2 partial override keeps other defaults",
      core.resolve_config({"qr_expiry_hours": 24})["redemption_window_hours"]
      == core.DEFAULT_CONFIG["redemption_window_hours"]
      and core.resolve_config({"qr_expiry_hours": 24})["qr_expiry_hours"] == 24)
check("B3 None does not blank a default",
      core.resolve_config({"duplicate_match": None})["duplicate_match"] == "phone")
check("B4 garbage int falls back", core.resolve_config({"qr_expiry_hours": "abc"})["qr_expiry_hours"] == 168)
check("B5 negative clamps to 0", core.resolve_config({"velocity_window_days": -5})["velocity_window_days"] == 0)
check("B6 bad enum degrades to phone",
      core.resolve_config({"duplicate_match": "carrier"})["duplicate_match"] == "phone")
check("B7 booleans coerce", core.resolve_config({"require_approval": "false"})["require_approval"] is False
      and core.resolve_config({"self_referral_block": 1})["self_referral_block"] is True)
check("B8 money default is a float >= 0",
      core.resolve_config({"default_commission_amount": "-9"})["default_commission_amount"] == 0.0)

CFG = core.resolve_config({})


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION C — the token
# ══════════════════════════════════════════════════════════════════════════════════════════════
tok = core.sign_token("REF-1", 1, SECRET)
check("C1 a signed token round-trips id + version", core.verify_token(tok, SECRET) == ("REF-1", 1))
check("C2 a tampered signature is refused (constant-time compare)",
      core.verify_token(tok[:-2] + ("AA" if not tok.endswith("AA") else "BB"), SECRET) is None)
check("C3 a token minted under another secret is refused",
      core.verify_token(core.sign_token("REF-1", 1, b"other-secret"), SECRET) is None)
check("C4 FAIL CLOSED: no secret -> cannot sign", core.sign_token("REF-1", 1, None) is None)
check("C5 FAIL CLOSED: no secret -> cannot verify", core.verify_token(tok, None) is None)
check("C6 garbage token never raises, returns None", core.verify_token("not.a.token", SECRET) is None
      and core.verify_token("", SECRET) is None and core.verify_token(None, SECRET) is None)
check("C7 a version bump invalidates the old QR (re-issue = revoke)",
      core.verify_token(tok, SECRET) == ("REF-1", 1)
      and core.verify_token(core.sign_token("REF-1", 2, SECRET), SECRET) == ("REF-1", 2)
      and core.sign_token("REF-1", 2, SECRET) != tok)
check("C8 the token is unguessable (id is not recoverable without the signature half)",
      "." in tok and core.verify_token(tok.split(".")[0] + ".", SECRET) is None)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION D — the state machine
# ══════════════════════════════════════════════════════════════════════════════════════════════
check("D1 the happy path is legal end to end",
      all(core.can_transition(a, b) for a, b in [
          ("created", "sent"), ("sent", "redeemed"), ("redeemed", "sale_logged"),
          ("sale_logged", "activated"), ("activated", "commission_pending"),
          ("commission_pending", "approved"), ("approved", "paid")]))
check("D2 MONEY-SAFE: cannot pay before approval",
      not core.can_transition("commission_pending", "paid")
      and not core.can_transition("activated", "paid")
      and not core.can_transition("sale_logged", "paid"))
check("D3 MONEY-SAFE: cannot approve before activation",
      not core.can_transition("sale_logged", "approved")
      and not core.can_transition("redeemed", "approved"))
check("D4 cannot skip straight from created to redeemed", not core.can_transition("created", "redeemed"))
check("D5 terminal states go nowhere",
      not core.can_transition("paid", "approved") and not core.can_transition("void", "sent")
      and not core.can_transition("rejected", "approved"))
check("D6 a fraud flag can be raised from any live state, and only resolves to void/rejected",
      core.can_transition("sent", "flagged_fraud") and core.can_transition("activated", "flagged_fraud")
      and core.can_transition("flagged_fraud", "void")
      and not core.can_transition("flagged_fraud", "approved"))
check("D7 unknown states are refused", not core.can_transition("banana", "paid")
      and not core.can_transition("approved", "banana"))
check("D8 the error message names the missing prerequisite",
      "activated" in core.transition_error("sale_logged", "approved")
      and "approved" in core.transition_error("commission_pending", "paid"))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION E — expiry
# ══════════════════════════════════════════════════════════════════════════════════════════════
created = NOW.isoformat()
dl = core.redeem_deadline(created, {"qr_expiry_hours": 168, "redemption_window_hours": 72})
check("E1 redeem_deadline uses the STRICTER (shorter) of the two windows",
      dl == NOW + timedelta(hours=72), dl)
check("E2 a zero window is ignored, the other bound wins",
      core.redeem_deadline(created, {"qr_expiry_hours": 0, "redemption_window_hours": 48})
      == NOW + timedelta(hours=48))
check("E3 both zero -> no expiry", core.redeem_deadline(created, {"qr_expiry_hours": 0, "redemption_window_hours": 0}) is None)
check("E4 a garbage created_at -> no deadline", core.redeem_deadline("nonsense", CFG) is None)
r_fresh = {"created_at": created, "redeem_expires_at": (NOW + timedelta(hours=10)).isoformat()}
r_stale = {"created_at": created, "redeem_expires_at": (NOW - timedelta(hours=1)).isoformat()}
check("E5 not expired before the deadline", not core.is_redeem_expired(r_fresh, CFG, NOW))
check("E6 expired after the deadline", core.is_redeem_expired(r_stale, CFG, NOW))
check("E7 no parseable deadline -> not expired (fail open; token+state are the real gates)",
      not core.is_redeem_expired({"created_at": "nonsense"}, {"qr_expiry_hours": 0, "redemption_window_hours": 0}, NOW))


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION F — anti-fraud
# ══════════════════════════════════════════════════════════════════════════════════════════════
check("F1 self-referral (same phone) is blocked",
      core.self_referral_conflict("516-555-0134", "(516) 555 0134", CFG) != "")
check("F2 different phones are fine", core.self_referral_conflict("5165550134", "5165559999", CFG) == "")
check("F3 blank phones never collide", core.self_referral_conflict("", "", CFG) == "")
check("F4 the tenant can turn the self-referral block off",
      core.self_referral_conflict("5165550134", "5165550134", {**CFG, "self_referral_block": False}) == "")

REFS = [
    {"id": "A", "referral_no": 1, "referrer_phone": "5160000001", "customer_phone": "5165550134",
     "status": "activated", "created_at": (NOW - timedelta(days=1)).isoformat()},
    {"id": "B", "referral_no": 2, "referrer_phone": "5160000002", "customer_phone": "5165550134",
     "status": "void", "created_at": (NOW - timedelta(days=1)).isoformat()},
]
check("F5 a customer already on an OPEN referral can't be referred again",
      core.duplicate_conflict("5165550134", REFS, CFG) != "")
check("F6 but a VOID/closed referral frees the number",
      core.duplicate_conflict("5165550134", [REFS[1]], CFG) == "")
check("F7 an existing CUSTOMER (from the master) is blocked",
      core.duplicate_conflict("5169998888", [], CFG, is_existing_customer=True) != "")
check("F8 duplicate_match='none' disables the whole gate",
      core.duplicate_conflict("5165550134", REFS, {**CFG, "duplicate_match": "none"}) == ""
      and core.duplicate_conflict("5169998888", [], {**CFG, "duplicate_match": "none"}, True) == "")
check("F9 exclude_id keeps a row from self-blocking on re-check",
      core.duplicate_conflict("5165550134", REFS, CFG, exclude_id="A") == "")

VELO = [{"id": str(i), "referrer_phone": "5167770000", "status": "sent",
         "created_at": (NOW - timedelta(days=2)).isoformat()} for i in range(3)]
check("F10 under the cap is fine",
      core.velocity_exceeded("5167770000", VELO, {**CFG, "max_referrals_per_referrer": 5}, NOW) == "")
check("F11 at/over the cap is blocked",
      core.velocity_exceeded("5167770000", VELO, {**CFG, "max_referrals_per_referrer": 3}, NOW) != "")
check("F12 a cap of 0 disables the velocity limit",
      core.velocity_exceeded("5167770000", VELO, {**CFG, "max_referrals_per_referrer": 0}, NOW) == "")
old = [{"id": "z", "referrer_phone": "5167770000", "status": "sent",
        "created_at": (NOW - timedelta(days=90)).isoformat()}]
check("F13 referrals outside the rolling window don't count",
      core.count_referrals_in_window("5167770000", old, 30, NOW) == 0)
check("F14 void/expired attempts don't consume the allowance",
      core.count_referrals_in_window("5167770000",
                                     [{"referrer_phone": "5167770000", "status": "void",
                                       "created_at": NOW.isoformat()}], 30, NOW) == 0)
check("F15 run_fraud_checks aggregates every trip",
      len(core.run_fraud_checks("5165550134", "5165550134", REFS,
                                {**CFG, "max_referrals_per_referrer": 1},
                                NOW, is_existing_customer=True)) >= 2)
check("F16 a clean referral trips nothing",
      core.run_fraud_checks("5160009999", "5165551212", [], CFG, NOW) == [])


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION G — segregation of duties + money
# ══════════════════════════════════════════════════════════════════════════════════════════════
REF_G = {"id": "G1", "created_by": "EMP-REP", "created_by_app_user_id": "uid-rep",
         "commission_amount": None, "payout_date": None}
check("G1 the creator (by employee id) can't approve their own referral",
      core.approval_conflict("EMP-REP", "uid-other", REF_G) != "")
check("G2 the creator (by app-user id) can't approve their own referral",
      core.approval_conflict("EMP-OTHER", "uid-rep", REF_G) != "")
check("G3 a different manager CAN approve", core.approval_conflict("EMP-MGR", "uid-mgr", REF_G) == "")
check("G4 unset commission uses the tenant default", core.compute_commission(REF_G, {"default_commission_amount": 40}) == 40.0)
check("G5 a per-referral amount overrides the default",
      core.compute_commission({"commission_amount": 55.5}, {"default_commission_amount": 40}) == 55.5)
check("G6 a negative amount clamps to 0", core.compute_commission({"commission_amount": -10}, CFG) == 0.0)
check("G7 payout date = approval + offset when unset",
      core.resolve_payout_date({"payout_date": None}, {"default_payout_offset_days": 30}, NOW)
      == (NOW + timedelta(days=30)).date().isoformat())
check("G8 an explicit payout date wins",
      core.resolve_payout_date({"payout_date": "2026-12-25"}, {"default_payout_offset_days": 30}, NOW)
      == "2026-12-25")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION H — dashboard math
# ══════════════════════════════════════════════════════════════════════════════════════════════
DASH = [
    {"status": "sent"}, {"status": "sent"}, {"status": "redeemed"},
    {"status": "commission_pending", "commission_amount": 25},
    {"status": "commission_pending", "commission_amount": 30},
    {"status": "approved", "commission_amount": 50},
    {"status": "paid", "commission_amount": 20},
    {"status": "flagged_fraud", "fraud_flag": True},
    {"status": "void"},
]
s = core.summarize(DASH, {"default_commission_amount": 25})
check("H1 funnel is in pipeline order",
      [b["status"] for b in s["funnel"]] == core.FUNNEL_ORDER)
check("H2 $ pending approval sums only commission_pending", s["pending_approval_amount"] == 55.0, s["pending_approval_amount"])
check("H3 $ approved-unpaid sums only approved", s["approved_unpaid_amount"] == 50.0)
check("H4 $ paid sums only paid", s["paid_amount"] == 20.0)
check("H5 fraud is tallied", s["fraud_flag_count"] == 1)
check("H6 counts roll up", s["total"] == 9 and s["pending_approval_count"] == 2)
check("H7 an empty program is zeros, not a crash",
      core.summarize([], CFG)["total"] == 0 and core.summarize([], CFG)["pending_approval_amount"] == 0.0)


# ══════════════════════════════════════════════════════════════════════════════════════════════
# SECTION I — router wiring against a fake client whose .eq() FILTERS and .update() MUTATES
# ══════════════════════════════════════════════════════════════════════════════════════════════
# [[fake-client-eq-noop-trap]]: a stub .eq that returns self without filtering passes an org-scoping
# test the real code fails. This one filters for real; update mutates the stored dicts in place so a
# single-use redemption actually flips the row (a no-op update would let a second scan through).
class FakeQuery:
    def __init__(self, store, missing=False):
        self.store = store              # the shared list backing this table
        self.rows = list(store)         # references to the SAME dicts, so update mutates the store
        self.missing = missing
        self._pending = None

    def select(self, *_a, **_k):
        if self.missing:
            raise RuntimeError("relation does not exist")
        return self

    def eq(self, col, val):
        self.rows = [r for r in self.rows if r.get(col) == val]
        return self

    def in_(self, col, vals):
        self.rows = [r for r in self.rows if r.get(col) in set(vals)]
        return self

    def gte(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col) or "") >= str(val)]
        return self

    def lte(self, col, val):
        self.rows = [r for r in self.rows if str(r.get(col) or "") <= str(val)]
        return self

    def ilike(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def insert(self, row):
        if self.missing:
            raise RuntimeError("relation does not exist")
        self._pending = ("insert", dict(row))
        return self

    def update(self, row):
        self._pending = ("update", dict(row))
        return self

    def upsert(self, row, **_k):
        self._pending = ("upsert", dict(row))
        return self

    def delete(self):
        self._pending = ("delete", {})
        return self

    def execute(self):
        if self._pending:
            kind, row = self._pending
            if kind == "insert":
                row.setdefault("id", f"new-{len(self.store)}")
                self.store.append(row)
                return type("R", (), {"data": [row], "count": 1})()
            if kind == "upsert":
                key = row.get("org_id")
                for existing in self.store:
                    if existing.get("org_id") == key:
                        existing.update(row)
                        return type("R", (), {"data": [existing], "count": 1})()
                self.store.append(row)
                return type("R", (), {"data": [row], "count": 1})()
            if kind == "update":
                for r in self.rows:       # rows are references into the store → mutate in place
                    r.update(row)
                return type("R", (), {"data": list(self.rows), "count": len(self.rows)})()
            return type("R", (), {"data": list(self.rows), "count": len(self.rows)})()
        return type("R", (), {"data": list(self.rows), "count": len(self.rows)})()


class FakeSchema:
    def __init__(self, tables, missing=()):
        self.tables, self.missing = tables, set(missing)

    def table(self, name):
        self.tables.setdefault(name, [])
        return FakeQuery(self.tables[name], missing=name in self.missing)

    def rpc(self, *_a, **_k):
        return type("R", (), {"execute": lambda _s=None: type("X", (), {"data": []})()})()


class FakeClient:
    def __init__(self, tables, missing=()):
        self.tables, self.missing = tables, missing

    def schema(self, _name):
        return FakeSchema(self.tables, self.missing)


ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "00000000-0000-0000-0000-0000000000ff"


def fresh_tables():
    return {
        "referral": [
            {"id": "RA", "org_id": ORG_A, "referral_no": 1, "referrer_name": "Alice",
             "referrer_phone": "5160000001", "status": "created", "store_code": "B-01",
             "created_by": "EMP-REP", "created_by_app_user_id": "uid-rep", "token_version": 1,
             "products": [], "created_at": NOW.isoformat()},
            {"id": "RB", "org_id": ORG_B, "referral_no": 1, "referrer_name": "OtherTenant",
             "referrer_phone": "5160000002", "status": "created", "store_code": "B-01",
             "token_version": 1, "products": [], "created_at": NOW.isoformat()},
        ],
        "referral_config": [{"org_id": ORG_A, "duplicate_match": "phone"}],
        "referral_audit": [],
        "customers": [],
    }


import app.modules.referral.router as rr  # noqa: E402


def _body(model, d):
    """Build the request model FastAPI hands the handler, instead of a plain dict.

    These endpoints were migrated from `body: dict` to a declared pydantic model, so the handler
    reads `body.<field>`. A probe passing a dict dies with AttributeError BEFORE reaching the logic
    under test — the harness then reads as "failing" while proving nothing. `model_validate`
    reproduces FastAPI's own call shape, including which fields count as explicitly set
    (`model_fields_set`), which several handlers branch on.
    """
    return model.model_validate(d)
from fastapi import HTTPException  # noqa: E402

MGR = {"org_id": ORG_A, "employee_id": "EMP-MGR", "id": "uid-mgr", "perms": {"scope": "all"},
       "super_admin": False, "role": "admin"}


def patch(tables, caller=MGR, keyset=None, missing=()):
    fake = FakeClient(tables, missing=missing)
    rr.get_supabase = lambda: fake
    rr.sb = lambda: fake.schema("core")
    rr._caller = lambda *_a, **_k: caller
    rr._keyset = lambda *_a, **_k: keyset
    rr._secret = lambda: SECRET
    # PIN THE CLOCK. Every fixture row is stamped at NOW (2026-08-13) and the redeem-deadline checks
    # compare against `rr._now()`, which was the REAL wall clock — so the QR-redeem assertions passed
    # only while the harness was younger than the 48h redeem window and have been failing on the
    # calendar ever since, with nothing wrong in the product. A proof harness whose result depends on
    # the day it is run cannot be trusted in either direction.
    rr._now = lambda: NOW
    return fake


# ── org scoping on read ──
T = fresh_tables(); patch(T)
res = rr.list_referrals(org_id=ORG_A)
check("I1 the referral list is org-scoped (the other tenant's row is NOT returned)",
      [r["id"] for r in res["rows"]] == ["RA"], [r["id"] for r in res["rows"]])
check("I2 the other tenant sees only its own", [r["id"] for r in rr.list_referrals(org_id=ORG_B)["rows"]] == ["RB"])

# ── create stamps org_id from the param, never the body ──
T = fresh_tables(); patch(T)
created = rr.create_referral(_body(rr.CreateReferralIn, {"referrer_phone": "5165551212", "referrer_name": "New",
                              "org_id": ORG_B, "products": ["Phone", "BYOD"]}), org_id=ORG_A)
check("I3 a new referral is created and org-stamped from the PARAM, not the body",
      T["referral"][-1]["org_id"] == ORG_A and not created["flagged"], T["referral"][-1].get("org_id"))
check("I4 products are normalized on create", T["referral"][-1]["products"] == ["Phone", "BYOD"])

# ── create refuses a referral with no referrer contact ──
T = fresh_tables(); patch(T)
try:
    rr.create_referral(_body(rr.CreateReferralIn, {"referrer_name": "No contact"}), org_id=ORG_A)
    check("I5 a referral with no referrer phone/email is refused", False, "accepted")
except HTTPException as e:
    check("I5 a referral with no referrer phone/email is refused", e.status_code == 400)

# ── create with a bad product is refused loudly ──
T = fresh_tables(); patch(T)
try:
    rr.create_referral(_body(rr.CreateReferralIn, {"referrer_phone": "5165551212", "products": ["Spaceship"]}), org_id=ORG_A)
    check("I6 a forged product option is refused, not silently dropped", False, "accepted")
except HTTPException as e:
    check("I6 a forged product option is refused, not silently dropped",
          e.status_code == 400 and "Spaceship" in str(e.detail))

# ── create-time self-referral trips the fraud flag (not a silent fail) ──
T = fresh_tables(); patch(T)
out = rr.create_referral(_body(rr.CreateReferralIn, {"referrer_phone": "5167778888", "customer_phone": "516-777-8888"}), org_id=ORG_A)
check("I7 a self-referral at create is FLAGGED with a reason, not accepted clean",
      out["flagged"] and out["referral"]["status"] == "flagged_fraud" and out["reasons"], out.get("reasons"))
check("I8 the fraud flag wrote an audit row",
      any(a["to_status"] == "flagged_fraud" for a in T["referral_audit"]))

# ── config write whitelist ──
T = fresh_tables(); patch(T)
try:
    rr.put_config({"qr_expiry_hours": 24, "not_a_setting": 1}, org_id=ORG_A)
    check("I9 an unknown config key is REFUSED, not silently dropped", False, "accepted")
except HTTPException as e:
    check("I9 an unknown config key is REFUSED, not silently dropped",
          e.status_code == 400 and "not_a_setting" in str(e.detail))

# ── the transition gate: illegal jumps are refused; the happy path works ──
T = fresh_tables(); patch(T)
rid = "RA"
rr.send_qr(rid, _body(rr.ReferralNoteIn, {}), org_id=ORG_A)
check("I10 created -> sent works and stamps a redeem deadline",
      T["referral"][0]["status"] == "sent" and T["referral"][0].get("redeem_expires_at"))
try:
    rr.approve(rid, _body(rr.ApproveReferralIn, {"commission_amount": 25}), org_id=ORG_A)
    check("I11 you cannot approve a referral that is only 'sent'", False, "approved a sent referral")
except HTTPException as e:
    check("I11 you cannot approve a referral that is only 'sent'", e.status_code == 400)
# walk it up the ladder
rr.redeem_view  # (public path tested below); drive staff steps here
T["referral"][0]["status"] = "redeemed"
rr.log_sale(rid, _body(rr.LogSaleIn, {"sale_ref": "S-9"}), org_id=ORG_A)
rr.activate(rid, _body(rr.ActivateReferralIn, {"activation_ref": "ACT-9"}), org_id=ORG_A)
rr.submit_for_approval(rid, _body(rr.ReferralNoteIn, {}), org_id=ORG_A)
check("I12 the ladder reaches commission_pending only via activated",
      T["referral"][0]["status"] == "commission_pending"
      and T["referral"][0].get("activated_at") and T["referral"][0].get("sale_ref") == "S-9")

# ── segregation of duties on approve ──
# The referral was created by EMP-REP; an approver who IS EMP-REP must be refused.
patch(T, caller={"org_id": ORG_A, "employee_id": "EMP-REP", "id": "uid-rep",
                 "perms": {"scope": "all"}, "role": "admin"})
try:
    rr.approve(rid, _body(rr.ApproveReferralIn, {"commission_amount": 25}), org_id=ORG_A)
    check("I13 a rep cannot approve their OWN referral (segregation of duties)", False, "self-approved")
except HTTPException as e:
    check("I13 a rep cannot approve their OWN referral (segregation of duties)", e.status_code == 403)
# a different manager approves, with a user-defined amount + date
patch(T, caller=MGR)
appr = rr.approve(rid, _body(rr.ApproveReferralIn, {"commission_amount": 42, "payout_date": "2026-12-01"}), org_id=ORG_A)
check("I14 a different manager approves with the user-defined amount + date",
      T["referral"][0]["status"] == "approved" and appr["commission_amount"] == 42.0
      and appr["payout_date"] == "2026-12-01" and T["referral"][0]["approver_employee_id"] == "EMP-MGR")
rr.mark_paid(rid, _body(rr.ReferralNoteIn, {}), org_id=ORG_A)
check("I15 paid is reachable from approved", T["referral"][0]["status"] == "paid")

# ── a plain rep can't approve at all ──
T = fresh_tables(); T["referral"][0]["status"] = "commission_pending"
patch(T, caller={"org_id": ORG_A, "employee_id": "EMP-X", "id": "uid-x", "perms": {"scope": "store"}})
try:
    rr.approve("RA", _body(rr.ApproveReferralIn, {"commission_amount": 25}), org_id=ORG_A)
    check("I16 a store-scoped rep cannot approve a payout at all", False, "approved")
except HTTPException as e:
    check("I16 a store-scoped rep cannot approve a payout at all", e.status_code == 403)

# ── PUBLIC redeem: uniform 404 on bad/used/expired; single-use; fraud stays uniform ──
T = fresh_tables()
deadline = (NOW + timedelta(hours=48)).isoformat()
T["referral"][0].update({"status": "sent", "redeem_expires_at": deadline})
patch(T)
good_tok = core.sign_token("RA", 1, SECRET)
view = rr.redeem_view(good_tok)
check("I17 a valid token shows the intake form (bubbles), never the referrer PII",
      view["ok"] and view["allowed_products"] == core.ALLOWED_PRODUCTS and "referrer_phone" not in view)
for bad in ("garbage", core.sign_token("RA", 2, SECRET), core.sign_token("NOPE", 1, SECRET)):
    try:
        rr.redeem_view(bad)
        check(f"I18 uniform 404 on a bad token ({bad[:6]})", False, "no 404")
    except HTTPException as e:
        check(f"I18 uniform 404 on a bad token ({str(bad)[:6]})", e.status_code == 404)
# submit intake → redeemed (single use)
resp = rr.redeem_submit(good_tok, _body(rr.RedeemSubmitIn, {"customer_name": "Bob", "customer_phone": "5165551212",
                                   "products": ["Phone", "Home Internet"]}))
check("I19 a clean redeem captures intake and moves to redeemed",
      resp == {"ok": True} and T["referral"][0]["status"] == "redeemed"
      and T["referral"][0]["customer_phone"] == "5165551212"
      and T["referral"][0]["products"] == ["Phone", "Home Internet"])
try:
    rr.redeem_view(good_tok)
    check("I20 SINGLE-USE: the same token 404s on a second scan", False, "still valid")
except HTTPException as e:
    check("I20 SINGLE-USE: the same token 404s on a second scan", e.status_code == 404)

# expired token → 404
T = fresh_tables()
T["referral"][0].update({"status": "sent", "redeem_expires_at": (NOW - timedelta(hours=1)).isoformat()})
patch(T)
try:
    rr.redeem_view(core.sign_token("RA", 1, SECRET))
    check("I21 an expired referral 404s at redeem", False, "not expired")
except HTTPException as e:
    check("I21 an expired referral 404s at redeem", e.status_code == 404)

# a self-referral at redeem flags fraud but the public response is UNIFORM (no oracle)
T = fresh_tables()
T["referral"][0].update({"status": "sent", "referrer_phone": "5165551212",
                         "redeem_expires_at": (NOW + timedelta(hours=48)).isoformat()})
patch(T)
resp = rr.redeem_submit(core.sign_token("RA", 1, SECRET),
                        _body(rr.RedeemSubmitIn, {"customer_name": "Bob", "customer_phone": "516-555-1212", "products": []}))
check("I22 a self-referral at redeem FLAGS fraud but the customer sees the SAME thank-you (no oracle)",
      resp == {"ok": True} and T["referral"][0]["status"] == "flagged_fraud"
      and T["referral"][0]["fraud_flag"] is True)

# ── missing table degrades, never 500 ──
T = fresh_tables(); patch(T, missing=("referral",))
res = rr.list_referrals(org_id=ORG_A)
check("I23 an un-run migration degrades to an empty list with a note, never a crash",
      res["rows"] == [] and "note" in res, res)


# ══════════════════════════════════════════════════════════════════════════════════════════════
print("\n".join(f"  ✔ {p}" for p in PASS))
if FAIL:
    print("\nFAILURES:")
    print("\n".join(f"  ✘ {f}" for f in FAIL))
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
