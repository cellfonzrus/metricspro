"""DB-FREE PROOF — the tenant-onboarding COMMISSION-STATEMENT INTAKE (design stage 3, steps 3.1–3.9).

    python3 harness_onboarding_intake.py        (from backend/; no network, no DB)

THE SCENARIO THIS HARNESS IS WRITTEN AS (owner 2026-09-20: "I will not do anything manually … the
system should ask me while onboarding under 3.4 what is considered commission positive or negative …
Whatever is being uploaded should be able to save is most important"). A tenant onboards a carrier
the platform has never seen. They drop a positive-earned statement that carries a title block, 521
lines, deactivation chargebacks and the file's own total row:

    3.1 upload  →  3.2 the header row and the total row are found, not assumed
    3.3 every column is proposed WITH its provenance and three sample values
    3.4 "In this file, is money you EARNED positive or negative?" — answered, never defaulted
    3.5 the six labels with count / Σ raw / Σ canonical / sign mix, largest first
    3.6 every label pre-placed where a rule, a house preset or a keyword hint puts it, flagged as such
    3.8 gross / chargebacks / net per bucket, Σ canonical beside the file's total, difference 0.00
    3.9 commit  →  the mapping, the sign, the rules and the rows are SAVED through the existing
                   writers, and the rows are RE-READ from the table: 521 landed, tie-out 86,970.34

NEGATIVE CONTROLS ARE PART OF THE RUN (§F): an unanswered sign, an unassigned label and a non-zero
difference are each REFUSED; a landing that silently drops rows is caught by the re-read and never
reported as success; a database that cannot store the sign answer refuses instead of writing another
convention. A harness that cannot fail proves nothing.

THE FIXTURE IS REAL AND MEASURED, not invented: the 522 lines of the reported 2026-09-20 import, as
(label, sub-label, signed amount, line count) — 181,336.95 earned over 399 lines, −7,396.27 charged
back over 121, net 86,970.34 over 521 lines plus one total row of exactly that. It is DATA in this
file; no carrier is named in any module this harness proves (§G checks that).

Runs the REAL router functions (analyze / commit) over an in-memory fake of the supabase client, so
the endpoint's own save path — upsert_column_mapping, upsert_commission_category_map,
_ledger_land_rows, _intake_reread — is exercised, not a re-statement of it.
"""
import asyncio
import inspect
import io
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.commcalc import onboarding_intake as OI
from app.modules.commcalc import commission_ledger as CL
from app.modules.commcalc import column_mapping as CM
from app.modules.commcalc import router as R

MIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "database", "migrations")
FE_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "src", "app",
                       "(platform)", "onboarding", "intake", "page.tsx")
ORG = "11111111-2222-3333-4444-555555555555"
HOUSE = R.ORG_ID
CARRIER_ID = "aaaaaaaa-0000-0000-0000-00000000c001"

_pass = _fail = 0
_failures = []


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        _failures.append(name)
        print(f"  FAIL  {name}{(' — ' + str(extra)) if extra else ''}")
    return bool(cond)


def section(t):
    print(f"\n{t}\n" + "─" * len(t))


def money(x):
    return round(float(x or 0.0), 2)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE FIXTURE — the 522 stored lines of the reported import, as measured
# (label, sub-label, signed amount, how many lines carried exactly that amount). DATA, not code.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
STATEMENT = [
    ('', '', 86970.34, 1),
    ('Activations', 'DPP Receivable - Activation', 10.0, 4),
    ('Activations', 'DPP Receivable - Activation', 155.0, 1),
    ('Activations', 'DPP Receivable - Activation', 365.0, 1),
    ('Activations', 'DPP Receivable - Activation', 610.0, 2),
    ('Activations', 'DPP Receivable - Activation', 710.0, 1),
    ('Activations', 'DPP Receivable - Activation', 840.0, 4),
    ('Activations', 'DPP Receivable - Activation', 914.0, 1),
    ('Activations', 'DPP Receivable - Activation', 1010.0, 1),
    ('Activations', 'DPP Receivable - Activation', 1110.0, 2),
    ('Activations', 'DPP Receivable - Activation', 1410.0, 2),
    ('Activations', 'DPP Receivable - Deactivation', -10.0, 2),
    ('Activations', 'DPP Service Fee - Activation', -42.3, 2),
    ('Activations', 'DPP Service Fee - Activation', -33.3, 2),
    ('Activations', 'DPP Service Fee - Activation', -30.3, 1),
    ('Activations', 'DPP Service Fee - Activation', -27.42, 1),
    ('Activations', 'DPP Service Fee - Activation', -25.2, 4),
    ('Activations', 'DPP Service Fee - Activation', -21.3, 1),
    ('Activations', 'DPP Service Fee - Activation', -18.3, 2),
    ('Activations', 'DPP Service Fee - Activation', -10.95, 1),
    ('Activations', 'DPP Service Fee - Activation', -4.65, 1),
    ('Activations', 'DPP Service Fee - Activation', -0.3, 4),
    ('Activations', 'DPP Service Fee - Deactivation', 0.3, 2),
    ('Activations', 'Optional Service Activations', 5.0, 3),
    ('Activations', 'Optional Service Activations', 10.0, 5),
    ('Activations', 'Optional Service Activations', 15.0, 1),
    ('Activations', 'Optional Service Activations', 20.0, 27),
    ('Activations', 'Optional Service Activations', 25.0, 12),
    ('Activations', 'Optional Service Activations', 65.0, 23),
    ('Activations', 'Optional Service Activations', 75.0, 27),
    ('Activations', 'Optional Service Activations', 88.0, 1),
    ('Activations', 'Optional Service Activations', 840.0, 3),
    ('Activations', 'Optional Service Activations', 1110.0, 2),
    ('Activations', 'Optional Service Activations', 1410.0, 1),
    ('Activations', 'Optional Service Chargebacks', -75.0, 13),
    ('Activations', 'Optional Service Chargebacks', -65.0, 2),
    ('Activations', 'Optional Service Chargebacks', -5.0, 2),
    ('Activations', 'Price Plan Activations', 50.0, 1),
    ('Activations', 'Price Plan Activations', 65.0, 1),
    ('Activations', 'Price Plan Activations', 70.0, 1),
    ('Activations', 'Price Plan Activations', 80.0, 1),
    ('Activations', 'Price Plan Activations', 85.0, 1),
    ('Activations', 'Price Plan Activations', 90.0, 1),
    ('Activations', 'Price Plan Activations', 100.0, 3),
    ('Activations', 'Price Plan Activations', 130.0, 1),
    ('Activations', 'Price Plan Activations', 140.0, 4),
    ('Activations', 'Price Plan Activations', 150.0, 33),
    ('Activations', 'Price Plan Deactivations', -275.0, 1),
    ('Activations', 'Price Plan Deactivations', -150.0, 6),
    ('Activations', 'Price Plan Deactivations', -125.0, 1),
    ('Activations', 'Price Plan Deactivations', -65.0, 2),
    ('Activations', 'Price Plan Deactivations', -60.0, 1),
    ('Activations', 'Price Plan Deactivations', -10.0, 1),
    ('Adjustments', 'Charitable Contribution-Activation', -8.25, 1),
    ('Adjustments', 'Charitable Contribution-Upgrade', -12.75, 1),
    ('Adjustments', 'CoOp', 0.0, 2),
    ('FiOS', 'FiOS Commissions', 200.0, 5),
    ('FiOS', 'FiOS Commissions', 500.0, 1),
    ('Incentives', 'New Account', 74.0, 1),
    ('Incentives', 'New Account', 79.0, 1),
    ('Incentives', 'New Account', 170.0, 1),
    ('Incentives', 'New Account', 219.0, 18),
    ('Incentives', 'New Account Deactivations', -219.0, 2),
    ('Incentives', 'New Account Deactivations', -207.0, 1),
    ('Incentives', 'New Account Deactivations', -170.0, 1),
    ('Incentives', 'New Add-A-Line', 29.0, 5),
    ('Incentives', 'New Add-A-Line', 115.0, 1),
    ('Incentives', 'New Add-A-Line', 139.0, 7),
    ('Incentives', 'New Add-A-Line Deactivations', -139.0, 1),
    ('Incentives', 'New Add-A-Line Deactivations', -115.0, 1),
    ('Incentives', 'New Unlimited Plan', 25.0, 13),
    ('Incentives', 'New Unlimited Plan', 60.0, 5),
    ('Incentives', 'New Unlimited Plan Deactivations', -60.0, 2),
    ('Incentives', 'New Unlimited Plan Deactivations', -25.0, 3),
    ('Incentives', 'Upgrade Unlimited Plan', 10.0, 4),
    ('Incentives', 'Upgrade Unlimited Plan', 25.0, 25),
    ('Incentives', 'Upgrade Unlimited Plan', 50.0, 1),
    ('Incentives', 'Upgrade Unlimited Plan Deactivations', -25.0, 2),
    ('Trade', 'Trade Commission Amount', 1.5, 1),
    ('Trade', 'Trade Commission Amount', 3.53, 1),
    ('Trade', 'Trade Commission Amount', 4.5, 1),
    ('Trade', 'Trade Commission Amount', 7.05, 1),
    ('Trade', 'Trade Commission Amount', 8.63, 1),
    ('Trade', 'Trade Commission Amount', 9.0, 1),
    ('Trade', 'Trade Commission Amount', 9.75, 3),
    ('Trade', 'Trade Commission Amount', 13.13, 1),
    ('Trade', 'Trade Commission Amount', 13.5, 1),
    ('Trade', 'Trade Commission Amount', 15.0, 19),
    ('Upgrades', 'DPP Receivable - Upgrade', 10.0, 5),
    ('Upgrades', 'DPP Receivable - Upgrade', 138.0, 3),
    ('Upgrades', 'DPP Receivable - Upgrade', 287.0, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 477.0, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 610.0, 5),
    ('Upgrades', 'DPP Receivable - Upgrade', 830.0, 4),
    ('Upgrades', 'DPP Receivable - Upgrade', 840.0, 7),
    ('Upgrades', 'DPP Receivable - Upgrade', 914.0, 3),
    ('Upgrades', 'DPP Receivable - Upgrade', 999.99, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1000.0, 2),
    ('Upgrades', 'DPP Receivable - Upgrade', 1099.99, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1110.0, 2),
    ('Upgrades', 'DPP Receivable - Upgrade', 1202.0, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1210.0, 5),
    ('Upgrades', 'DPP Receivable - Upgrade', 1299.99, 2),
    ('Upgrades', 'DPP Receivable - Upgrade', 1310.0, 5),
    ('Upgrades', 'DPP Receivable - Upgrade', 1410.0, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1499.99, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1610.0, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 1899.99, 1),
    ('Upgrades', 'DPP Receivable - Upgrade', 2599.98, 1),
    ('Upgrades', 'DPP Receivable - Upgrade Deact', -1299.99, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -78.0, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -50.0, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -48.3, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -45.0, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -42.3, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -39.3, 5),
    ('Upgrades', 'DPP Service Fee - Upgrade', -39.0, 2),
    ('Upgrades', 'DPP Service Fee - Upgrade', -36.3, 5),
    ('Upgrades', 'DPP Service Fee - Upgrade', -36.06, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -33.3, 2),
    ('Upgrades', 'DPP Service Fee - Upgrade', -33.0, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -30.0, 3),
    ('Upgrades', 'DPP Service Fee - Upgrade', -27.42, 3),
    ('Upgrades', 'DPP Service Fee - Upgrade', -25.2, 7),
    ('Upgrades', 'DPP Service Fee - Upgrade', -24.9, 4),
    ('Upgrades', 'DPP Service Fee - Upgrade', -18.3, 5),
    ('Upgrades', 'DPP Service Fee - Upgrade', -14.31, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -8.61, 1),
    ('Upgrades', 'DPP Service Fee - Upgrade', -4.14, 3),
    ('Upgrades', 'DPP Service Fee - Upgrade', -0.3, 5),
    ('Upgrades', 'DPP Service Fee - Upgrade Deact', 39.0, 1),
    ('Upgrades', 'Phone Upgrade', 85.0, 5),
    ('Upgrades', 'Phone Upgrade', 125.0, 8),
    ('Upgrades', 'Phone Upgrade', 155.0, 39),
    ('Upgrades', 'Phone Upgrade', 310.0, 1),
    ('Upgrades', 'Phone Upgrade Deactivations', -155.0, 2),
]
# The file's own headers, as the importer saw them (one extra money column, 'Device Margin', recorded).
FILE_HEADERS = ["Report Section", "Report SubSection", "AgentSSOID", "Master Service Date", "Gross", "Device Margin"]
FILE_TOTAL = 86970.34
# The buckets the tenant chose in 3.6 — their decision, not the platform's.
CHOSEN = {"Activations": "commission", "Upgrades": "commission", "Incentives": "spiff",
          "Trade": "equipment_rebate", "FiOS": "residual_monthly", "Adjustments": "commission"}


def statement_csv(title_block=True, footer=True):
    """The fixture as the CSV a person would drop: a two-line title block, the header, 521 lines,
    and the file's own total row (blank identity, the amount only). Bytes, utf-8 with a BOM."""
    lines = []
    if title_block:
        lines.append("Dealer Compensation Statement")
        lines.append("Period,August 2026")
    lines.append(",".join(FILE_HEADERS))
    n, total_rows = 0, []
    for pn, ot, amt, cnt in STATEMENT:
        for _ in range(cnt):
            if not pn and not ot:
                total_rows.append(f",,,,{amt:.2f},")
                continue
            n += 1
            margin = -15.7 if n % 3 == 0 else 0.0
            lines.append(f"{pn},{ot},R{1 + n % 4},2026-08-{1 + n % 28:02d},{amt:.2f},{margin:.2f}")
    if footer:
        lines.extend(total_rows)          # the file's own total row comes LAST, as a statement writes it
    return ("﻿" + "\n".join(lines) + "\n").encode("utf-8")


def rows_from_statement():
    out = []
    for pn, ot, amt, cnt in STATEMENT:
        for _ in range(cnt):
            out.append({"product_name": pn, "order_type": ot, "raw_amount": amt,
                        "rep_user": "" if not pn else "R1", "trans_date": None if not pn else "2026-08-12"})
    return out


# ══════════════════════════════════════════════════════════════════════════════════════════════════
# AN IN-MEMORY SUPABASE CLIENT — just enough of the query builder the router's intake path uses.
# Tables and their columns are DECLARED so a probe of an absent column raises, as Postgres would.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
class _Q:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.rows, self.filters, self.conflict = "select", None, [], None
        self._order, self._limit, self._range, self.cols = None, None, None, "*"

    def select(self, cols="*"):
        self.op, self.cols = "select", cols
        for c in [c.strip() for c in str(cols).split(",") if c.strip() and c.strip() != "*"]:
            if c not in self.db.columns(self.table):
                raise RuntimeError(f'42703 column "{c}" of {self.table} does not exist')
        return self

    def insert(self, rows):
        self.op, self.rows = "insert", (rows if isinstance(rows, list) else [rows])
        return self

    def upsert(self, rows, on_conflict=None):
        self.op, self.rows, self.conflict = "upsert", (rows if isinstance(rows, list) else [rows]), on_conflict
        return self

    def update(self, row):
        self.op, self.rows = "update", [row]
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, k, v):    self.filters.append(("eq", k, v)); return self
    def neq(self, k, v):   self.filters.append(("neq", k, v)); return self
    def in_(self, k, v):   self.filters.append(("in", k, list(v))); return self
    def is_(self, k, v):   self.filters.append(("is", k, v)); return self
    def order(self, k, desc=False): self._order = (k, desc); return self
    def limit(self, n):    self._limit = n; return self
    def range(self, lo, hi): self._range = (lo, hi); return self

    def _match(self, r):
        for op, k, v in self.filters:
            x = r.get(k)
            if op == "eq" and x != v: return False
            if op == "neq" and x == v: return False
            if op == "in" and x not in v: return False
            if op == "is" and not ((v == "null" and x is None) or (v is not None and v != "null" and x == v)): return False
        return True

    def execute(self):
        t = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            out = [dict(r) for r in t if self._match(r)]
            if self._order:
                k, desc = self._order
                out.sort(key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
            if self._range:
                lo, hi = self._range
                out = out[lo:hi + 1]
            if self._limit is not None:
                out = out[:self._limit]
            return _Res(out)
        if self.op == "insert":
            self.db.check_rows(self.table, self.rows)
            written = []
            for r in self.rows:
                row = dict(r)
                row.setdefault("id", str(uuid.uuid4()))
                if not self.db.drop_inserts.get(self.table):
                    t.append(row)
                    written.append(dict(row))
            return _Res(written)
        if self.op == "upsert":
            self.db.check_rows(self.table, self.rows)
            keys = [k.strip() for k in (self.conflict or "").split(",") if k.strip()]
            written = []
            for r in self.rows:
                row = dict(r)
                hit = next((x for x in t if keys and all(x.get(k) == row.get(k) for k in keys)), None)
                if hit:
                    hit.update(row)
                    written.append(dict(hit))
                else:
                    row.setdefault("id", str(uuid.uuid4()))
                    t.append(row)
                    written.append(dict(row))
            return _Res(written)
        if self.op == "update":
            self.db.check_rows(self.table, self.rows)
            out = []
            for r in t:
                if self._match(r):
                    r.update(self.rows[0])
                    out.append(dict(r))
            return _Res(out)
        if self.op == "delete":
            keep = [r for r in t if not self._match(r)]
            gone = len(t) - len(keep)
            t[:] = keep
            return _Res([{}] * gone)
        raise RuntimeError(self.op)


class _Res:
    def __init__(self, data): self.data = data


class FakeStorage:
    """The supabase storage surface the intake uses to KEEP the dropped file: buckets + upload/download."""
    def __init__(self):
        self.buckets, self.files = set(), {}

    def get_bucket(self, name):
        if name not in self.buckets:
            raise RuntimeError("bucket not found")
        return {"name": name}

    def create_bucket(self, name):
        self.buckets.add(name)

    def from_(self, bucket):
        st = self

        class _B:
            def upload(self_, path, data, opts=None):
                st.files[(bucket, path)] = bytes(data)
                return {"Key": path}

            def download(self_, path):
                if (bucket, path) not in st.files:
                    raise RuntimeError("object not found")
                return st.files[(bucket, path)]
        return _B()


class FakeDB:
    """Declared columns per table (a select of an undeclared column raises, like 42703), an optional
    CHECK on column_mapping.sign_convention (mig 1006 vs 1008), and a 'drop inserts' switch for the
    save-guarantee negative control. `with_buckets=False` = a database without mig 1009 (any access to
    commcalc.commission_bucket raises)."""
    def __init__(self, sign_values=("payout_negative", "payout_positive", "payout_negative_netted"), with_buckets=True):
        self.tables = {}
        self.drop_inserts = {}
        self.sign_values = tuple(sign_values)
        self.storage = FakeStorage()
        self.declared = {
            "commission_bucket": ["id", "org_id", "key", "label", "kind", "sort_order", "is_active", "hint_words",
                                  "pl_line_key", "is_builtin", "created_at", "updated_at"],
            "carrier": ["id", "org_id", "name", "code", "is_default"],
            "column_mapping": ["id", "org_id", "report_key", "carrier_id", "target_field", "source_header",
                               "transform", "is_active", "priority", "updated_at", "sign_convention"],
            "commission_category_map": ["id", "org_id", "source_report", "match_field", "match_op", "pattern",
                                        "category", "sign_rule", "priority", "is_seeded", "updated_at", "leg_bucket"],
            "commission_ledger": ["id", "org_id", "period", "source_report", "account_id", "account_name", "store",
                                  "rep_user", "order_number", "order_type", "product_name", "trans_date", "due_date",
                                  "payment_month", "category", "commission", "spiff", "equipment_rebate",
                                  "residual_monthly", "autopay_residual", "payout_total", "raw_amount", "is_payout",
                                  "origin", "source_table", "source_row_id", "synced_at"],
            "onboarding_run": ["id", "org_id", "run_kind", "period", "status", "current_step", "started_by",
                               "started_at", "signed_off_by", "signed_off_on_behalf", "signed_off_at", "updated_at"],
            "onboarding_stage_state": ["id", "run_id", "org_id", "stage", "step", "instance_key", "status", "payload",
                                       "verified_numbers", "verified_by", "verified_at", "blocking_reason", "updated_at"],
        }

    def columns(self, table):
        if table in self.declared:
            return self.declared[table]
        return ["*"] + [k for r in self.tables.get(table, []) for k in r]  # undeclared: permissive

    def check_rows(self, table, rows):
        if table == "commission_ledger":
            for r in rows:                      # mig 251: origin TEXT NOT NULL DEFAULT 'file'
                r.setdefault("origin", "file")
        if table == "column_mapping":
            for r in rows:
                sc = r.get("sign_convention")
                if sc not in (None, "") and sc not in self.sign_values:
                    raise RuntimeError('23514 new row violates check constraint "column_mapping_sign_convention_ck" (sign_convention)')

    def schema(self, _name): return self
    def table(self, name): return _Q(self, name)

    def seed(self, table, rows):
        for r in rows:
            row = dict(r); row.setdefault("id", str(uuid.uuid4()))
            self.tables.setdefault(table, []).append(row)


class FakeUpload:
    def __init__(self, data, filename="statement.csv"):
        self.data, self.filename = data, filename

    async def read(self): return self.data


def fresh_db(with_state=True, sign_values=None, with_buckets=True):
    db = FakeDB(**({"sign_values": sign_values} if sign_values else {}))
    db.seed("carrier", [{"id": CARRIER_ID, "org_id": ORG, "name": "Northwind Cellular", "code": "northwind"},
                        {"id": "bbbbbbbb-0000-0000-0000-00000000c002", "org_id": ORG, "name": "Other Carrier", "code": "other"}])
    if not with_state:
        db.declared.pop("onboarding_stage_state"); db.declared.pop("onboarding_run")
        db.tables["onboarding_stage_state"] = None   # any access raises
    if with_buckets:
        # the mig-1009 house seed, as rows of the HOUSE org (what the migration inserts)
        db.seed("commission_bucket", [{k: v for k, v in dict(b, org_id=HOUSE).items() if k in db.declared["commission_bucket"]}
                                      for b in CL.builtin_buckets()])
    else:
        db.declared.pop("commission_bucket")
        db.tables["commission_bucket"] = None        # any access raises, as a missing table does
    return db


def use(db):
    R.sb = lambda: db
    R._TABLE_COL_PRESENT.clear()
    return db


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def analyze(db, data=None, file=True, **form):
    use(db)
    kw = dict(source_kind="commission", carrier_id=CARRIER_ID, statement_type="", column_map="",
              sign_answer="", assignments="", typed_total="", org_id=ORG)
    kw.update(form)
    up = FakeUpload(data if data is not None else statement_csv()) if file else None
    return run(R.onboarding_intake_analyze(file=up, **kw))


def commit(db, data=None, file=True, **form):
    use(db)
    kw = dict(source_kind="commission", carrier_id=CARRIER_ID, statement_type="", period="August 2026",
              column_map="", sign_answer="", assignments="", attestation="", typed_total="", verified_by="tester", org_id=ORG)
    kw.update(form)
    up = FakeUpload(data if data is not None else statement_csv()) if file else None
    return run(R.onboarding_intake_commit(file=up, **kw))


def http_error(fn, *a, **kw):
    """(status, detail) of the HTTPException a call raises, or (None, result)."""
    try:
        return None, fn(*a, **kw)
    except R.HTTPException as e:
        return e.status_code, e.detail


import json as _json
ASSIGN = _json.dumps([{"label": k, "bucket": v, "is_reversal": False} for k, v in CHOSEN.items()])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE FIXTURE IS THE REPORTED IMPORT")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
ROWS = rows_from_statement()
DATA = [r for r in ROWS if r["product_name"]]
POS = money(sum(r["raw_amount"] for r in DATA if r["raw_amount"] > 0))
NEG = money(sum(r["raw_amount"] for r in DATA if r["raw_amount"] < 0))
check("522 lines: 521 data lines + 1 total row", len(ROWS) == 522 and len(DATA) == 521, (len(ROWS), len(DATA)))
check("94,366.61 earned over 398 lines; −7,396.27 charged back over 121 (the sign proof's 181,336.95 / 399 INCLUDES the total row)",
      POS == 94366.61 and NEG == -7396.27 and sum(1 for r in DATA if r["raw_amount"] > 0) == 398
      and sum(1 for r in DATA if r["raw_amount"] < 0) == 121, (POS, NEG))
check("net 86,970.34 — and the total row says exactly that", money(POS + NEG) == FILE_TOTAL)
check("six labels, incl. deactivation / chargeback sub-labels",
      sorted({r["product_name"] for r in DATA}) == ["Activations", "Adjustments", "FiOS", "Incentives", "Trade", "Upgrades"]
      and any("Chargebacks" in r["order_type"] for r in DATA) and any("Deact" in r["order_type"] for r in DATA))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. 3.2 DETECT — the header row and the total row are FOUND, not assumed (pure)")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
grids = R._read_upload_grids(statement_csv(), "statement.csv")
shape = OI.stitch_sheets(grids)
check("the CSV reads as one raw grid, title block included (ragged rows survive)",
      len(grids) == 1 and len(grids[0][1]) == 522 + 3 and len(grids[0][1][0]) == 1, len(grids[0][1]))
check("header row = row 2 (below the two-line title block), by the ≥60%-text + next-row-populated rule",
      shape["header_row"] == 2 and shape["headers"] == FILE_HEADERS, (shape["header_row"], shape["headers"]))
check("522 data rows follow it (the total row is a DATA row at this point — the footer rule decides, not position)",
      len(shape["records"]) == 522, len(shape["records"]))
check("every sheet is listed with its role and row count (design §5.6)",
      shape["sheets"] == [{"name": "csv", "rows": 525, "header_row": 2, "data_rows": 522, "used": True, "role": "primary"}], shape["sheets"])
two = [("Sheet1", grids[0][1]), ("Sheet2", grids[0][1][2:8]), ("Summary", [["Total", "86970.34"], ["x", "y"]])]
st2 = OI.stitch_sheets(two)
check("a continuation sheet with the IDENTICAL header is appended; a summary tab with another layout is listed, not used",
      st2["sheet"] == "Sheet1" and len(st2["records"]) == 522 + 5
      and [s["role"] for s in st2["sheets"]] == ["primary", "continuation", "other"], st2["sheets"])
check("an empty sheet finds no header and says so",
      OI.stitch_sheets([("Empty", [])])["header_row"] is None and OI.detect_header_row([["only", "one"]]) is None)
check("a title-only grid (one populated cell per row) never passes as a header",
      OI.detect_header_row([["Statement"], ["Period"], ["August"]]) is None)

FIELDS = CM.target_fields("commission_ledger")
SUGG = CM.suggest(shape["headers"], "commission_ledger", [])
PROP = OI.propose_columns(FIELDS, SUGG, [], [], shape["headers"], shape["records"], carrier_label="Northwind Cellular")
pm = {p["target_field"]: p for p in PROP}
check("3.3 the amount column is proposed 'from your file' (Gross is a registered alias) with 3 samples",
      pm["raw_amount"]["column"] == "Gross" and pm["raw_amount"]["provenance"] == OI.PROV_FILE
      and len(pm["raw_amount"]["samples"]) == 3, pm["raw_amount"])
check("a header with no match is left EMPTY with no provenance — never invented",
      pm["product_name"]["column"] == "" and pm["product_name"]["provenance"] is None, pm["product_name"])
saved_rule = [{"target_field": "product_name", "source_header": "Report Section", "carrier_id": CARRIER_ID}]
house_rule = [{"target_field": "order_type", "source_header": "Report SubSection", "carrier_id": "h1"}]
P2 = {p["target_field"]: p for p in OI.propose_columns(FIELDS, SUGG, saved_rule, house_rule, shape["headers"], shape["records"], "Northwind Cellular")}
check("a saved row for THIS carrier reads 'your earlier choice'; a house row for this code reads 'house default for <carrier>'",
      P2["product_name"]["provenance"] == OI.PROV_EARLIER and P2["order_type"]["provenance"] == OI.PROV_HOUSE
      and P2["order_type"]["provenance_label"] == "house default for Northwind Cellular", (P2["product_name"], P2["order_type"]))
P3 = {p["target_field"]: p for p in OI.propose_columns(FIELDS, SUGG, saved_rule, [], shape["headers"], shape["records"], "", overrides={"product_name": "Report SubSection"})}
check("what the person picked on screen wins, and reads 'from your file' unless it equals the saved row",
      P3["product_name"]["column"] == "Report SubSection" and P3["product_name"]["provenance"] == OI.PROV_FILE)
check("a saved header that is NOT in this file does not prefill (it would be a different layout)",
      OI.propose_columns(FIELDS, SUGG, [{"target_field": "store", "source_header": "Dealer Name", "carrier_id": CARRIER_ID}], [],
                         shape["headers"], shape["records"])[2]["column"] == "")
check("there are exactly three provenances plus 'guess' — none of them is another carrier",
      OI.PROVENANCES == (OI.PROV_FILE, OI.PROV_HOUSE, OI.PROV_EARLIER, OI.PROV_GUESS))

MAP = {"product_name": "Report Section", "order_type": "Report SubSection", "rep_user": "AgentSSOID",
       "trans_date": "Master Service Date", "raw_amount": "Gross"}
PROPC = OI.propose_columns(FIELDS, SUGG, [], [], shape["headers"], shape["records"], overrides=MAP)
rules = OI.mapping_rules(PROPC)
mapped = R._ledger_map_records(shape["records"], rules)
ident = CM.identity_fields("commission_ledger")
kept, footers = OI.split_footer(mapped, ident)
foot = OI.footer_summary(kept, footers)
check("the footer is the ONE row with every identity field blank — found by the mig-1004 shape rule, value 86,970.34",
      len(footers) == 1 and footers[0]["raw_amount"] == FILE_TOTAL and len(kept) == 521, (footers, len(kept)))
check("…and it equals the other 521 lines' own sum, so the file agrees with itself",
      foot["detected"] and foot["file_total_raw"] == FILE_TOTAL and foot["equals_lines_sum"] is True, foot)
check("the identity list is the report's NON-NUMERIC fields (the same list _ledger_footer_drop uses)",
      "raw_amount" not in ident and "product_name" in ident and "order_type" in ident)
nf = OI.footer_summary(kept, [])
check("no total row → detected=False, file_total None (the person may type one; recorded as 'typed')",
      nf["detected"] is False and nf["file_total_raw"] is None)
mc = OI.money_columns(shape["headers"], shape["records"], "Gross", ["Report Section", "Report SubSection", "AgentSSOID", "Master Service Date"])
check("3.3 every money column is recorded with its Σ — the one not picked ('Device Margin') is NOT silently ignored",
      [m["header"] for m in mc] == ["Gross", "Device Margin"] and mc[0]["is_amount"] and not mc[1]["is_amount"]
      and mc[1]["sum"] < 0, mc)
check("an integer-only id column (AgentSSOID is text here; a numeric id is tested directly) is not money",
      OI.money_columns(["Id", "Amt"], [{"Id": "1001", "Amt": "10.50"}, {"Id": "1002", "Amt": "-3.25"}], "Amt")
      == [{"header": "Amt", "sum": 7.25, "cells": 2, "is_amount": True}])

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. 3.4 THE SIGN QUESTION — asked plainly, three real rows each way, no default")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
sp = OI.sign_panels(kept)
check("the question is verbatim", sp["question"] == "In this file, is money you EARNED positive or negative?")
check("two answers, no third and no default",
      [o["value"] for o in sp["options"]] == ["positive", "negative"] and OI.convention_for_answer("") is None
      and OI.convention_for_answer("sideways") is None)
check("the three largest positives: 2,599.98 / 1,899.99 / 1,610.00 with their label and sub-label",
      [r["amount"] for r in sp["positive"]] == [2599.98, 1899.99, 1610.0]
      and sp["positive"][0]["label"] == "Upgrades" and sp["positive"][0]["sub_label"] == "DPP Receivable - Upgrade", sp["positive"])
check("the three largest negatives: −1,299.99 / −275.00 / −219.00",
      [r["amount"] for r in sp["negative"]] == [-1299.99, -275.0, -219.0] and sp["negative"][0]["sub_label"] == "DPP Receivable - Upgrade Deact", sp["negative"])
check("'Earned is positive' → payout_positive; 'Earned is negative' → payout_negative_netted — BOTH net reversals",
      OI.convention_for_answer("positive") == "payout_positive" and OI.convention_for_answer("negative") == "payout_negative_netted"
      and CL.CONVENTIONS["payout_positive"]["reversal_handling"] == "signed"
      and CL.CONVENTIONS["payout_negative_netted"] == {"payout_sign": -1, "reversal_handling": "signed"})
check("a stored declaration displays as its answer (incl. the older wizard's payout_negative as 'negative')",
      OI.answer_for_convention("payout_positive") == "positive" and OI.answer_for_convention("payout_negative") == "negative"
      and OI.answer_for_convention("") is None)
conv = CL.convention_named("payout_positive")
labels = OI.label_summary(kept, conv)
check("3.5 six labels, sorted by |Σ raw| descending, with count / Σ raw / Σ canonical / sign mix",
      [l["label"] for l in labels][:2] == ["Upgrades", "Activations"] and len(labels) == 6
      and all(l["sum_canonical"] == l["sum_raw"] for l in labels) and labels[0]["sign_mix"] == "mixed", [(l["label"], l["sum_raw"]) for l in labels])
lab = {l["label"]: l for l in labels}
check("Σ over the six labels = 86,970.34 and Σ counts = 521", money(sum(l["sum_raw"] for l in labels)) == FILE_TOTAL and sum(l["count"] for l in labels) == 521)
check("each label lists its sub-labels with Σ (the deactivations are visible inside 'Activations')",
      any(s["sub_label"] == "Price Plan Deactivations" and s["sum_raw"] < 0 for s in lab["Activations"]["sub_labels"]))
check("Σ canonical is None while 3.4 is unanswered — nothing is defaulted",
      all(l["sum_canonical"] is None for l in OI.label_summary(kept, None)))
check("reversal pre-flag: text says so ('… Chargeback'), or all-negative after normalisation; 'Adjustments' (all negative) is flagged, 'Upgrades' (mixed) is not",
      lab["Adjustments"]["reversal_flag"] is True and lab["Upgrades"]["reversal_flag"] is False
      and OI.reversal_preflag({"label": "Optional Service Chargebacks", "sign_mix": "mixed"}) is True
      and OI.reversal_preflag({"label": "Bonus", "sign_mix": "all_positive"}, -1) is True)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. 3.6 BUCKETS — pre-placed with provenance; rules written on the tenant's own key")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
SR = OI.source_report_key("northwind", "commission statement")
check("the rule-set key is derived from the carrier CODE and the statement type (a second statement type = a second key, not a branch)",
      SR == "northwind__commission_statement" and OI.source_report_key("northwind", "Residual file") == "northwind__residual_file")
check("the stage instance is keyed per carrier × statement type",
      OI.instance_key("commission", CARRIER_ID, "commission statement") == f"commission:{CARRIER_ID}:commission_statement")
REG = CL.builtin_buckets()
sug = {l["label"]: l for l in OI.suggest_buckets(labels, [], [], "Northwind Cellular", buckets=REG)}
# PIN CHANGED 2026-09-20 (bucket registry, mig 1009): the guess vocabulary is now each bucket's `hint_words`
# (house defaults, tenant-editable) instead of the MA-only DEFAULT_RULES words — so every one of the six
# section labels gets a guess: 'Activations' / 'Upgrades' (activation / upgrade → commission), 'Incentives'
# (incentive → spiff), 'Trade' (trade → equipment rebate), 'FiOS' (via its 'FiOS Commissions' sub-label),
# 'Adjustments' (adjust → misc charges, a DEDUCTION — and all-negative, so the sign-mix tiebreak agrees).
check("with no rules anywhere, the registry's hint words place EVERY section label — each flagged 'guess', none applied",
      all(v["provenance"] == OI.PROV_GUESS for v in sug.values())
      and {k: v["bucket"] for k, v in sug.items()} == {"Activations": "commission", "Upgrades": "commission", "Incentives": "spiff",
                                                       "Trade": "equipment_rebate", "FiOS": "commission", "Adjustments": "misc_charges"},
      {k: (v["bucket"], v["provenance"]) for k, v in sug.items()})
check("a section whose sub-labels hint at DIFFERENT buckets gets no guess",
      OI.keyword_guess("Mixed", ["Spiff Month 1", "Commission M2"], REG) is None and OI.keyword_guess("Bonus", ["SPF x", "Spiff y"], REG) == "spiff")
check("the keyword hints ARE the registry buckets' own hint words (one place — the mig-1009 rows), offered, never applied",
      OI.KEYWORD_HINTS == [(w, b["key"]) for b in CL.builtin_buckets() for w in b["hint_words"]]
      and OI.keyword_guess("Autopay Residual x", (), REG) == "autopay_residual"
      and OI.keyword_guess("Autopay Residual x", (), [b for b in REG if b["key"] != "autopay_residual"]) == "residual_monthly")
check("a tenant's OWN hint words drive the guess (the registry is config): 'co-op' → their own bucket",
      OI.keyword_guess("Co-Op Marketing", (), REG + [CL.normalise_bucket({"key": "coop_marketing", "label": "Co-op", "hint_words": ["co-op"], "kind": "earned"})]) == "coop_marketing")
check("longest hint wins; on a tie a deduction-kind bucket wins; a reversal-flagged label prefers the deduction hit; "
      "one with no deduction hit is offered the FIRST active deduction bucket",
      OI._hint("DPP Service Fee - Activation", REG) == "vendor_fee"                # 'service fee' (11) beats 'activation' (10)
      and OI._hint("Charitable Contribution-Activation", REG) == "misc_charges"     # tie (10 vs 10) → deduction wins
      and OI._hint("DPP Receivable - Upgrade Deact", REG) == "commission"            # 'upgrade' (7) beats 'deact' (5) … on text alone
      and OI._hint("DPP Receivable - Upgrade Deact", REG, prefer_deduction=True) == "chargebacks"   # … but a reversal flag elects the deduction hit
      and {l["label"]: l["bucket"] for l in OI.suggest_buckets([{"label": "Bonus Pool", "sub_labels": [], "reversal_flag": True}], [], [], "", REG)} == {"Bonus Pool": "chargebacks"})
check("a hint is a STEM on a word boundary: 'upgrade' hits 'Upgrades', 'deact' hits 'Deactivations', 'fee' never hits 'coffee'",
      OI._hint("Upgrades", REG) == "commission" and OI._hint("Deactivations", REG) == "chargebacks" and OI._hint("Coffee fund", REG) is None)
tenant_rules = [{"source_report": SR, "match_field": "product_name", "match_op": "equals", "pattern": "Activations", "category": "commission", "priority": 10}]
house_rules = [{"source_report": SR, "match_field": "order_type", "match_op": "contains", "pattern": "New Account", "category": "spiff", "priority": 5}]
sug2 = {l["label"]: l for l in OI.suggest_buckets(labels, tenant_rules, house_rules, "Northwind Cellular")}
check("the tenant's own earlier rule reads 'your earlier choice'; a house rule for this code reads 'house default for <carrier>'; earlier beats house",
      sug2["Activations"]["provenance"] == OI.PROV_EARLIER and sug2["Activations"]["bucket"] == "commission"
      and sug2["Incentives"]["provenance"] == OI.PROV_HOUSE and sug2["Incentives"]["bucket"] == "spiff"
      and sug2["Incentives"]["provenance_label"] == "house default for Northwind Cellular")
assign = [{"label": k, "bucket": v, "is_reversal": False} for k, v in CHOSEN.items()]
rl = OI.rules_for_assignments(SR, assign)
check("one `equals` rule per label on its own field, under the tenant's key, default sign_rule (a netting convention offers reversals by DIRECTION — 'any' would be the abs() inflation)",
      len(rl) == 6 and all(r["match_op"] == "equals" and r["match_field"] == "product_name" and r["sign_rule"] == "negative_only" and r["source_report"] == SR for r in rl)
      and rl[0]["pattern"] == "Activations" and rl[0]["category"] == "commission", rl[0])
check("a label in no bucket stays unassigned and the gate names it",
      OI.unassigned_labels(labels, assign[:-1]) == ["Adjustments"] and OI.unassigned_labels(labels, assign) == [])
check("a blank label / an unknown bucket writes no rule",
      OI.rules_for_assignments(SR, [{"label": "(blank)", "bucket": "commission"}, {"label": "X", "bucket": "other"}]) == [])
blank_rows = [{"product_name": "", "order_type": "Bonus Pool", "raw_amount": 5.0}]
bl = OI.label_summary(blank_rows, conv)
check("a line with a blank label column is labelled by its sub-label and its rule is written on THAT field",
      bl[0]["label"] == "Bonus Pool" and bl[0]["match_field"] == "order_type"
      and OI.rules_for_assignments(SR, [{"label": "Bonus Pool", "match_field": "order_type", "bucket": "spiff"}])[0]["match_field"] == "order_type")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. 3.8 TOTALS — gross / chargebacks / net per bucket, Σ canonical beside the file's total")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
built = [CL.build_row(src, {"org_id": ORG, "source_report": SR}, rl, conv) for src in kept]
tot = OI.bucket_totals(built)
tie = OI.tie_out(tot, FILE_TOTAL, conv["payout_sign"])
b = tot["buckets"]
check("commission: gross Σ positive, chargebacks Σ negative, net = gross + chargebacks (never abs())",
      b["commission"]["gross"] > 0 and b["commission"]["chargebacks"] < 0
      and money(b["commission"]["gross"] + b["commission"]["chargebacks"]) == b["commission"]["net"], b["commission"])
check("THE TIE-OUT — the five buckets sum to the file's own total, 86,970.34, difference 0.00",
      tot["net_total"] == FILE_TOTAL and tie["file_total"] == FILE_TOTAL and tie["difference"] == 0.0 and tie["match"] is True, tie)
check("Σ gross across buckets = 94,366.61 and Σ chargebacks = −7,396.27",
      money(sum(x["gross"] for x in b.values())) == 94366.61 and money(sum(x["chargebacks"] for x in b.values())) == -7396.27)
check("nothing is 'other' when every label is bucketed; the only 'charge' lines are the two $0.00 lines (a zero is never a payout)",
      tot["other"]["count"] == 0 and tot["charges"] == {"total": 0.0, "count": 2} and tot["rows"] == 521, tot["charges"])
check("the zero lines ('CoOp' $0.00) book nothing but are still rows", sum(1 for r in built if r["payout_total"] == 0) == 2)
neg_tot = OI.bucket_totals([CL.build_row({**src, "raw_amount": -src["raw_amount"]}, {"org_id": ORG}, rl, CL.convention_named("payout_negative_netted")) for src in kept])
check("the SAME statement written the other way up, answered 'earned is negative', gives the SAME buckets to the cent",
      neg_tot["buckets"] == tot["buckets"] and OI.tie_out(neg_tot, -FILE_TOTAL, -1)["match"] is True, neg_tot["net_total"])
partial = [CL.build_row(src, {"org_id": ORG, "source_report": SR}, rl[:-1], conv) for src in kept]
ptie = OI.tie_out(OI.bucket_totals(partial), FILE_TOTAL, 1)
check("one label left out → its money is 'other', the difference is exactly that label's Σ and match is False",
      ptie["match"] is False and money(ptie["difference"]) == -lab["Adjustments"]["sum_raw"] and ptie["other_count"] == lab["Adjustments"]["count"] - lab["Adjustments"]["zeros"], ptie)
check("a typed total (no footer) is recorded as 'typed'; nothing to compare against → match None",
      OI.tie_out(tot, None, 1, "86970.34")["file_total_source"] == "typed" and OI.tie_out(tot, None, 1)["match"] is None)
check("sanity banner: the commission bucket netting negative names 3.4",
      any("re-check 3.4" in s for s in OI.sanity_banners([], {"buckets": {"commission": {"count": 3, "net": -1.0}}}, 1)))
check("sanity banner: a reversal-flagged label netting POSITIVE names 3.4; a clean file raises none",
      any("flagged as a reversal" in s for s in OI.sanity_banners([{"label": "Chargebacks", "is_reversal": True, "sum_raw": 50.0}], tot, 1))
      and OI.sanity_banners(assign, tot, 1) == [])
pp = OI.period_proposal(kept)
check("the period is proposed from the statement's OWN dates (one month here, so it is not asked)",
      pp["proposed"] == "August 2026" and pp["spans_two_months"] is False and pp["dated_rows"] == 521, pp)
check("3.7 identity strings are surfaced with count and Σ (the reps here); a file with no store column has none to resolve",
      set(OI.identity_strings(kept).keys()) == {"rep_user"} and sum(r["count"] for r in OI.identity_strings(kept)["rep_user"]) == 521)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("F. THE COMMIT GATE (pure) — refused for the right reasons")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
check("unanswered sign → refused, naming 3.4",
      any("3.4" in r for r in OI.commit_refusals("", labels, assign, tie)))
check("an unassigned label → refused, naming the label",
      any("Adjustments" in r for r in OI.commit_refusals("positive", labels, assign[:-1], tie)))
check("a non-zero difference → refused without an attestation; allowed WITH a reason",
      OI.commit_refusals("positive", labels, assign, ptie) and not OI.commit_refusals("positive", labels, assign, ptie, {"reason": "carrier's total omits FiOS"}))
check("an empty-string reason is not an attestation",
      OI.commit_refusals("positive", labels, assign, ptie, {"reason": "   "}))
check("no total to compare against → refused unless attested",
      OI.commit_refusals("positive", labels, assign, OI.tie_out(tot, None, 1)) and not OI.commit_refusals("positive", labels, assign, OI.tie_out(tot, None, 1), {"reason": "statement has no total line"}))
check("$0.00 = $0.00 is NOT verified without a reason (design §5.3)",
      OI.commit_refusals("positive", [], [], OI.tie_out(OI.bucket_totals([]), 0.0, 1)))
check("the clean scenario passes the gate", OI.commit_refusals("positive", labels, assign, tie) == [])
rail = OI.rail([{"stage": "3", "instance_key": "commission:c1:commission_statement", "step": "3.4", "status": "in_progress", "payload": {"x": 1}}])
check("the rail is a projection of the persisted rows: stage 3 in progress, resume at 3.4",
      rail["stages"][2]["status"] == "in_progress" and rail["resume"] == {"instance_key": "commission:c1:commission_statement", "step": "3.4", "stage": "3"}
      and [s["key"] for s in rail["steps"]] == OI.STEP_KEYS)
check("every instance verified → stage verified; nothing → resume None",
      OI.rail([{"stage": "3", "instance_key": "a", "step": "3.9", "status": "verified"}])["stages"][2]["status"] == "verified"
      and OI.rail([])["resume"] is None)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("G. THE SCENARIO THROUGH THE REAL ENDPOINTS — analyze → answer → assign → commit → RE-READ")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db()
a1 = analyze(db)
check("analyze: one payload carries detect / columns / sign / labels / verify / state",
      all(k in a1 for k in ("detect", "columns", "money_columns", "sign", "labels", "unassigned", "identity", "period", "verify", "state")))
check("analyze: header row 2, 522 rows; with no identity column confirmed yet the footer is 'not detectable yet' and NOTHING is dropped",
      a1["detect"]["header_row"] == 2 and a1["detect"]["data_rows"] == 522 and a1["detect"]["footer"]["detected"] is False
      and "not detectable" in a1["detect"]["footer"]["basis"] and a1["detect"]["footer_rows_dropped"] == 0, a1["detect"]["footer"])
check("analyze: the carrier is this org's ROW (name + normalised code), the rule-set key derived from it",
      a1["carrier"] == {"id": CARRIER_ID, "name": "Northwind Cellular", "code": "northwind"} and a1["source_report"] == "northwind__commission_statement")
check("analyze: 3.4 unanswered and nothing stored → answered=False, no convention, Σ canonical None",
      a1["sign"]["answered"] is False and a1["sign"]["convention"] is None and a1["sign"]["stored_answer"] is None
      and all(l["sum_canonical"] is None for l in a1["labels"]) and a1["verify"]["totals"] is None)
check("analyze: the amount column is proposed from the file; the label column is not (so the person picks it)",
      next(c for c in a1["columns"] if c["target_field"] == "raw_amount")["column"] == "Gross"
      and next(c for c in a1["columns"] if c["target_field"] == "product_name")["column"] == "")
# PIN CHANGED 2026-09-20: this harness's fake now has a file store (Stage B keeps the dropped file), so
# analyze writes exactly ONE thing — the kept-file reference on the stage row — and nothing else.
check("analyze: nothing was written but the KEPT-FILE reference (no mapping row, no rule, no ledger row; the stage row carries only file/filename/kind)",
      not db.tables.get("column_mapping") and not db.tables.get("commission_category_map") and not db.tables.get("commission_ledger")
      and a1["file"]["stored"] is True and len(db.tables.get("onboarding_stage_state") or []) == 1
      and set(db.tables["onboarding_stage_state"][0]["payload"]) <= {"file", "filename", "kind"}, db.tables.get("onboarding_stage_state"))
check("analyze: state is READY (mig 1007 present in the fake); the rail resumes at THIS instance (the kept file)",
      a1["state"]["state_ready"] is True and a1["state"]["rail"]["resume"]["instance_key"] == a1["instance_key"])
# the person confirms the columns and answers 3.4
a2 = analyze(db, column_map=_json.dumps(MAP), sign_answer="positive")
check("with the columns confirmed: the footer is found (86,970.34), 1 row dropped, 521 to land",
      a2["detect"]["footer"]["file_total_raw"] == FILE_TOTAL and a2["detect"]["footer_rows_dropped"] == 1 and a2["verify"]["rows_to_land"] == 521, a2["detect"]["footer"])
# PIN CHANGED 2026-09-20 (mig 1009): the registry's hint words pre-place all six (was 2 of 6 with the MA-only words)
check("with the columns confirmed and 3.4 answered: six labels with Σ canonical, ALL six pre-placed by guess, none unassigned; "
      "the payload carries the registry (eight buckets, source 'house')",
      len(a2["labels"]) == 6 and all(l["sum_canonical"] is not None for l in a2["labels"])
      and a2["unassigned"] == [] and all(l["provenance"] == "guess" for l in a2["labels"])
      and a2["buckets"] == [b["key"] for b in CL.builtin_buckets()] and a2["bucket_meta"]["source"] == "house"
      and a2["bucket_labels"]["misc_charges"] == "Misc charges", (a2["unassigned"], [(l["label"], l["bucket"], l["provenance"]) for l in a2["labels"]]))
check("the 3.8 preview with the guesses placed TIES OUT (every line is in a bucket): 'Adjustments' guessed into the "
      "misc-charges DEDUCTION bucket books −21.00 signed; earned + deductions = the file total",
      a2["verify"]["tie"]["match"] is True and a2["verify"]["totals"]["buckets"]["misc_charges"]["net"] == -21.0
      and a2["verify"]["totals"]["deductions_total"] == -21.0 and a2["verify"]["totals"]["earned_total"] == 86991.34
      and money(a2["verify"]["totals"]["earned_total"] + a2["verify"]["totals"]["deductions_total"]) == FILE_TOTAL, a2["verify"]["totals"])
a2x = analyze(db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=_json.dumps([{"label": k, "bucket": v} for k, v in CHOSEN.items() if k != "Trade"]))
check("a label left unassigned by the person → the preview does NOT tie out, and says by how much",
      a2x["verify"]["tie"]["match"] is False and a2x["verify"]["tie"]["other_count"] > 0 and a2x["unassigned"] == ["Trade"])
check("the ignored money column is on the verify card with its Σ",
      a2["verify"]["ignored_money_columns"] and a2["verify"]["ignored_money_columns"][0]["header"] == "Device Margin")
# the person assigns every label
a3 = analyze(db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN)
check("every label assigned → the preview ties out to the cent, nothing unassigned, no banners",
      a3["unassigned"] == [] and a3["verify"]["tie"]["match"] is True and a3["verify"]["tie"]["our_total"] == FILE_TOTAL and a3["verify"]["banners"] == [])
# state written by the page as it goes
st = R.onboarding_intake_put_state(R.OnboardingIntakeStateIn(instance_key=a3["instance_key"], step="3.6", payload={"sign_answer": "positive", "column_map": MAP}), org_id=ORG)
check("PUT /state persists the step and payload; the rail resumes at 3.6 for this instance",
      st["save"]["saved"] is True and st["rail"]["resume"] == {"instance_key": a3["instance_key"], "step": "3.6", "stage": "3"})
st2 = R.onboarding_intake_put_state(R.OnboardingIntakeStateIn(instance_key=a3["instance_key"], step="3.8", payload={"assignments": CHOSEN}), org_id=ORG)
check("a later PUT MERGES the payload (the 3.4 answer survives the 3.6 write) and one row exists per instance",
      st2["rail"]["instances"][0]["payload"].get("sign_answer") == "positive" and st2["rail"]["instances"][0]["payload"].get("assignments") == CHOSEN
      and len(db.tables["onboarding_stage_state"]) == 1 and len(db.tables["onboarding_run"]) == 1)
# THE COMMIT
c = commit(db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN)
check("commit: ok=True — nothing to report", c["ok"] is True and c["problems"] == [], c.get("problems"))
cm = {r["target_field"]: r for r in db.tables["column_mapping"]}
check("(a) the column map is SAVED through the one writer, carrier-scoped, five rows, headers as confirmed",
      len(cm) == 5 and all(r["carrier_id"] == CARRIER_ID and r["report_key"] == "commission_ledger" and r["org_id"] == ORG for r in cm.values())
      and cm["raw_amount"]["source_header"] == "Gross" and cm["product_name"]["source_header"] == "Report Section", cm.keys())
check("(b) the 3.4 answer is on the amount row as payout_positive — and on NO other row",
      cm["raw_amount"]["sign_convention"] == "payout_positive" and all(r.get("sign_convention") in (None, "") for k, r in cm.items() if k != "raw_amount"))
rules_saved = db.tables["commission_category_map"]
check("(c) six bucket rules under the tenant's own key, org-scoped, equals on the label",
      len(rules_saved) == 6 and all(r["org_id"] == ORG and r["source_report"] == SR and r["match_op"] == "equals" for r in rules_saved)
      and {r["pattern"]: r["category"] for r in rules_saved} == CHOSEN)
led = db.tables["commission_ledger"]
check("(d) 521 rows landed, 0 footer, under (org, source_report, period), origin 'file'",
      len(led) == 521 and all(r["org_id"] == ORG and r["source_report"] == SR and r["period"] == "August 2026" for r in led)
      and c["saved"] == 521 and c["verified_numbers"]["footer_rows_dropped"] == 1)
vn = c["verified_numbers"]
check("(e) RE-READ from the table: rows_landed 521 = rows_built = inserted; tie-out 86,970.34 vs 86,970.34, difference 0.00",
      vn["rows_landed"] == 521 and vn["rows_built"] == 521 and vn["rows_inserted"] == 521
      and vn["tie"]["our_total"] == FILE_TOTAL and vn["tie"]["file_total"] == FILE_TOTAL and vn["tie"]["difference"] == 0.0 and vn["basis"].startswith("re-read"), vn["tie"])
check("the re-read buckets are gross / chargebacks / net and equal the preview shown before commit",
      vn["totals"]["buckets"]["commission"]["chargebacks"] < 0 and vn["shown_before_commit"]["our_total"] == vn["tie"]["our_total"])
check("the stage row is VERIFIED with the numbers, who and when; the rail says so",
      c["state"]["saved"] is True and db.tables["onboarding_stage_state"][0]["status"] == "verified"
      and db.tables["onboarding_stage_state"][0]["verified_by"] == "tester"
      and db.tables["onboarding_stage_state"][0]["verified_numbers"]["tie"]["match"] is True
      and R.onboarding_intake_state(org_id=ORG)["rail"]["stages"][2]["status"] == "verified")
check("the ledger's own read path agrees: summarize() over the landed rows gives payout_total 86,970.34",
      CL.summarize(led, conv=conv)["payout_total"] == FILE_TOTAL)
# next month: the stored answer and the mapping are reused and DISPLAYED
a4 = analyze(db)
check("a later analyze with nothing chosen: the columns read 'your earlier choice', 3.4 shows the stored answer, the labels read 'your earlier choice'",
      all(cc["provenance"] == OI.PROV_EARLIER for cc in a4["columns"] if cc["column"])
      and a4["sign"]["stored_answer"] == "positive" and a4["sign"]["answered"] is True
      and all(l["provenance"] == OI.PROV_EARLIER for l in a4["labels"]) and a4["unassigned"] == [] and a4["verify"]["tie"]["match"] is True)
# re-commit replaces the slice, never doubles it
c2 = commit(db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN)
check("re-committing the same period REPLACES the slice: still 521 rows, still ties out",
      c2["ok"] is True and len(db.tables["commission_ledger"]) == 521 and len(db.tables["column_mapping"]) == 5 and len(db.tables["commission_category_map"]) == 6)
# a second carrier gets nothing from the first
db2 = fresh_db()
db2.tables["column_mapping"] = list(db.tables["column_mapping"])
db2.tables["commission_category_map"] = list(db.tables["commission_category_map"])
a5 = analyze(db2, carrier_id="bbbbbbbb-0000-0000-0000-00000000c002")
check("a SECOND carrier's statement is prefilled from NOTHING of the first: no 'earlier choice' columns, no stored sign, no label placements",
      all(cc["provenance"] != OI.PROV_EARLIER for cc in a5["columns"]) and a5["sign"]["stored_answer"] is None
      and all(l["provenance"] is None or l["provenance"] == OI.PROV_GUESS for l in a5["labels"]) and a5["source_report"] == "other__commission_statement")

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("H. NEGATIVE CONTROLS — refused, and never 'success' without the re-read")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db()
s_, d_ = http_error(commit, db, column_map=_json.dumps(MAP), sign_answer="", assignments=ASSIGN)
check("unanswered 3.4 → 400 naming 3.4; nothing saved",
      s_ == 400 and "3.4" in str(d_) and not db.tables.get("column_mapping") and not db.tables.get("commission_ledger"), (s_, d_))
s_, d_ = http_error(commit, db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=_json.dumps([{"label": k, "bucket": v} for k, v in CHOSEN.items() if k != "Adjustments"]))
check("an unassigned label → 400 naming it; nothing saved",
      s_ == 400 and "Adjustments" in str(d_) and not db.tables.get("column_mapping"), (s_, d_))
wrong = dict(CHOSEN); wrong["Adjustments"] = "spiff"
half = _json.dumps([{"label": k, "bucket": v} for k, v in CHOSEN.items()])
s_, d_ = http_error(commit, db, data=statement_csv(footer=True).replace(b"86970.34", b"90000.00"), column_map=_json.dumps(MAP), sign_answer="positive", assignments=half)
check("a file whose total row disagrees with its lines → 400 with the difference; nothing saved",
      s_ == 400 and "differs" in str(d_) and "3,029.66" in str(d_) and not db.tables.get("commission_ledger"), (s_, d_))
c3 = commit(db, data=statement_csv(footer=True).replace(b"86970.34", b"90000.00"), column_map=_json.dumps(MAP), sign_answer="positive", assignments=half,
            attestation=_json.dumps({"reason": "the carrier's total line includes a manual adjustment they confirmed by email"}))
check("…the SAME commit with an attestation + reason lands and records the attestation with who/when; difference kept on the record",
      c3["ok"] is True and c3["verified_numbers"]["attestation"]["reason"].startswith("the carrier") and c3["verified_numbers"]["attestation"]["by"] == "tester"
      and c3["verified_numbers"]["tie"]["difference"] == -3029.66, c3.get("verified_numbers", {}).get("tie"))
s_, d_ = http_error(commit, db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN, period="")
check("no period → 400 (the period is the slice the statement owns)", s_ == 400 and "period" in str(d_))
s_, d_ = http_error(commit, db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN, source_kind="sales", layout="bogus_layout")
check("source_kind 'sales' is a REAL kind now (Stage B): refused for a real reason (an unknown layout), never as 'not built'",
      s_ == 400 and ("layout" in str(d_) or "pos_source" in str(d_)) and "Stage B" not in str(d_))
s_, d_ = http_error(commit, db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN, source_kind="bogus")
check("an unknown source_kind → 400", s_ == 400)
s_, d_ = http_error(commit, db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN, carrier_id="cccccccc-0000-0000-0000-000000000000")
check("a carrier_id that is not this org's → 400 (org-scoped lookup)", s_ == 400 and "carrier" in str(d_))
# THE SAVE GUARANTEE: the landing silently drops rows → the re-read catches it
db = fresh_db()
db.drop_inserts["commission_ledger"] = True
c4 = commit(db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN)
check("landing that silently loses rows: ok=False, problems name 'rows landed 0 ≠ rows built 521', the stage is NEEDS_INPUT — never 'success'",
      c4["ok"] is False and any("rows landed 0" in p for p in c4["problems"]) and c4["verified_numbers"]["rows_landed"] == 0
      and db.tables["onboarding_stage_state"][0]["status"] == "needs_input" and "rows landed" in db.tables["onboarding_stage_state"][0]["blocking_reason"], c4["problems"])
# THE SIGN MUST BE STORED: a database without mig 1008 refuses 'earned is negative' instead of writing another convention
db = fresh_db(sign_values=("payout_negative", "payout_positive"))
neg_csv = statement_csv().replace(b",-", b",+").replace(b",0.30,", b",0.30,")
# flip every Gross sign: build the negative-earned file explicitly
lines = statement_csv().decode("utf-8-sig").split("\n")
flipped = []
for i, ln in enumerate(lines):
    if i < 3 or not ln:
        flipped.append(ln); continue
    parts = ln.split(",")
    v = float(parts[4]) if parts[4] else 0.0
    parts[4] = f"{-v:.2f}"
    flipped.append(",".join(parts))
NEG_CSV = ("﻿" + "\n".join(flipped)).encode("utf-8")
s_, d_ = http_error(commit, db, data=NEG_CSV, column_map=_json.dumps(MAP), sign_answer="negative", assignments=ASSIGN)
check("pre-1008 database + 'earned is negative' → 400 naming 1008_sign_convention_netted.sql; NO ledger rows written",
      s_ == 400 and "1008" in str(d_) and not db.tables.get("commission_ledger"), (s_, str(d_)[:120]))
db = fresh_db()
c5 = commit(db, data=NEG_CSV, column_map=_json.dumps(MAP), sign_answer="negative", assignments=ASSIGN)
check("with 1008: the negative-earned file commits as payout_negative_netted, 521 rows, buckets tie to the file's −86,970.34 → +86,970.34",
      c5["ok"] is True and c5["sign_convention"] == "payout_negative_netted"
      and {r["target_field"]: r.get("sign_convention") for r in db.tables["column_mapping"]}["raw_amount"] == "payout_negative_netted"
      and c5["verified_numbers"]["tie"]["our_total"] == FILE_TOTAL and c5["verified_numbers"]["tie"]["file_total"] == FILE_TOTAL, c5.get("verified_numbers", {}).get("tie"))
# state absent: the endpoints degrade honestly
db = fresh_db(with_state=False)
a6 = analyze(db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN)
check("without mig 1007: analyze still works, state_ready=False names the migration, rail is empty — no 500",
      a6["state"]["state_ready"] is False and "1007" in a6["state"]["migration"] and a6["verify"]["tie"]["match"] is True)
c6 = commit(db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN)
check("without mig 1007: the commit still LANDS and ties out; state.saved=False with the reason",
      c6["ok"] is True and c6["state"]["saved"] is False and "1007" in c6["state"]["reason"] and len(db.tables["commission_ledger"]) == 521)
sx = R.onboarding_intake_state(org_id=ORG)
check("GET /state without the migration: state_ready False, carriers listed, every kind of the enum is built (Stage B landed)",
      sx["state_ready"] is False and len(sx["carriers"]) == 2 and [k["value"] for k in sx["source_kinds"]] == list(OI.SOURCE_KINDS)
      and all(k["built"] for k in sx["source_kinds"]))
s_, d_ = http_error(R.onboarding_intake_put_state, R.OnboardingIntakeStateIn(instance_key="x", step="9.9"), org_id=ORG)
check("PUT /state validates the step", s_ == 400)
# no footer at all: the tenant must type or attest
db = fresh_db()
s_, d_ = http_error(commit, db, data=statement_csv(footer=False), column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN)
check("a file with NO total row → refused until a total is typed or attested",
      s_ == 400 and "no total" in str(d_))
c7 = commit(db, data=statement_csv(footer=False), column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN, typed_total="86970.34")
check("…a TYPED total ties out and is recorded as 'typed'",
      c7["ok"] is True and c7["verified_numbers"]["tie"]["file_total_source"] == "typed" and c7["verified_numbers"]["footer_rows_dropped"] == 0)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("J. THE OWNER'S CASE (2026-09-20) — label = the sub-section, the total row carries a store and a date")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The owner mapped product_name ← Report SubSection, order_type ← Report Section, store ← Store, order_number
# ← Mobile Number, trans_date ← Master Service Date, raw_amount ← Gross — 28 labels, of which one was the
# file's own total row showing as "(blank)" (its Store / date cells are filled, so rule 1 could not fire),
# and 17 labels had no suggestion (the guess vocabulary was MA-only). Both reproduced here, then fixed.
OWNER_HEADERS = ["Store", "Report Section", "Report SubSection", "Mobile Number", "Master Service Date", "Gross"]
OWNER_MAP = {"store": "Store", "order_type": "Report Section", "product_name": "Report SubSection",
             "order_number": "Mobile Number", "trans_date": "Master Service Date", "raw_amount": "Gross"}


def owner_csv(blank_lines=0, total_label="", subtotals=False, footer=True):
    """The fixture the way the OWNER's file is laid out: the total row carries the store cell ('ALL STORES')
    and the run date, blank section / sub-section / number. `blank_lines` adds real lines with NO label
    (a defect of the file, not a total); `total_label` writes that text into the total row's SubSection
    cell; `subtotals` adds a labelled 'Subtotal' row per section."""
    lines = ["Dealer Compensation Statement", "Period,August 2026", ",".join(OWNER_HEADERS)]
    n, per_section = 0, {}
    for pn, ot, amt, cnt in STATEMENT:
        for _ in range(cnt):
            if not pn and not ot:
                continue
            n += 1
            lines.append(f"Store {1 + n % 2},{pn},{ot},555{n:04d},2026-08-{1 + n % 28:02d},{amt:.2f}")
            per_section[pn] = per_section.get(pn, 0.0) + amt
    for i in range(blank_lines):
        lines.append(f"Store 1,,,555{9000 + i},2026-08-15,5.00")
    if subtotals:
        for sec, tot in per_section.items():
            lines.append(f"ALL STORES,{sec},Subtotal,,2026-08-31,{tot:.2f}")
    if footer:
        # a real statement's total INCLUDES its unlabelled lines — the total is the sum of every other row
        lines.append(f"ALL STORES,,{total_label},,2026-08-31,{FILE_TOTAL + 5.0 * blank_lines:.2f}")
    return ("﻿" + "\n".join(lines) + "\n").encode("utf-8")


COMPANY_LEVEL = _json.dumps({"store": {"Store 1": {"action": "company_level"}, "Store 2": {"action": "company_level"}}})
db = fresh_db()
o1 = analyze(db, data=owner_csv(), column_map=_json.dumps(OWNER_MAP), sign_answer="positive")
check("3.2: the total row is FOUND although its store and date cells are filled — rule 3 (no label, amount = Σ of every other row): "
      "86,970.34, one row dropped, 521 to land",
      o1["detect"]["footer"]["detected"] is True and o1["detect"]["footer"]["file_total_raw"] == FILE_TOTAL
      and o1["detect"]["footer"]["rows"][0]["rule"] == OI.FOOTER_SUM_MATCH and o1["detect"]["footer_rows_dropped"] == 1
      and o1["verify"]["rows_to_land"] == 521 and "sum of every other row" in o1["detect"]["footer"]["basis"], o1["detect"]["footer"])
check("3.5/3.6: 27 labels — the sub-sections — and NO '(blank)' card; it is not a label",
      len(o1["labels"]) == 27 and all(l["label"] != "(blank)" for l in o1["labels"]) and o1["blank_label"] is None, len(o1["labels"]))
check("3.6: with the house-default registry ZERO labels are left without a suggestion (residue: none) — every one flagged 'guess'",
      o1["unassigned"] == [] and all(l["provenance"] == "guess" for l in o1["labels"]),
      [l["label"] for l in o1["labels"] if l["bucket"] == "unassigned"])
placed = {l["label"]: l["bucket"] for l in o1["labels"]}
check("...the deactivation / chargeback labels go to `chargebacks`, the service fees to `vendor_fee`, the charitable contributions "
      "to `misc_charges`, the activations / upgrades / plans to `commission`, the incentives' unlimited plan to `spiff`",
      all(placed[k] == "chargebacks" for k in placed if ("Deact" in k or "Chargeback" in k) and "Fee" not in k)
      and all(CL.bucket_kind(placed[k]) == "deduction" for k in placed if "Deact" in k or "Chargeback" in k or "Fee" in k)
      and placed["DPP Service Fee - Activation"] == "vendor_fee" and placed["DPP Service Fee - Upgrade"] == "vendor_fee"
      and placed["Charitable Contribution-Activation"] == "misc_charges" and placed["CoOp"] == "misc_charges"
      and placed["Price Plan Activations"] == "commission" and placed["Phone Upgrade"] == "commission"
      and placed["New Unlimited Plan"] == "spiff" and placed["FiOS Commissions"] == "commission", placed)
check("...the sign-mix tiebreak: an all-negative label whose words match an EARNED bucket ('DPP Receivable - Upgrade Deact': upgrade) "
      "is pre-flagged as a reversal and suggested `chargebacks`",
      next(l for l in o1["labels"] if l["label"] == "DPP Receivable - Upgrade Deact")["reversal_flag"] is True
      and placed["DPP Receivable - Upgrade Deact"] == "chargebacks")
t = o1["verify"]["totals"]
check("3.8: eight buckets in registry order with their kind; earned 94,327.01 + deductions −7,356.67 = net 86,970.34 = the file's total, "
      "difference 0.00 — THE INVARIANT under the owner's own guesses",
      t["bucket_order"] == [b["key"] for b in CL.builtin_buckets()] and t["buckets"]["chargebacks"]["kind"] == "deduction"
      and t["earned_total"] == 94327.01 and t["deductions_total"] == -7356.67 and t["net_total"] == FILE_TOTAL
      and o1["verify"]["tie"]["match"] is True and o1["verify"]["tie"]["difference"] == 0.0, (t["earned_total"], t["deductions_total"], t["net_total"]))
check("...the deduction buckets read NEGATIVE, signed — never abs(); their 'gross' is the refunds (+39.60 across the fee / deact refunds)",
      t["buckets"]["chargebacks"]["net"] < 0 and t["buckets"]["vendor_fee"]["net"] < 0 and t["buckets"]["misc_charges"]["net"] == -21.0
      and money(sum(t["buckets"][k]["gross"] for k in ("chargebacks", "vendor_fee", "misc_charges"))) == 39.6, {k: t["buckets"][k] for k in ("chargebacks", "vendor_fee", "misc_charges")})
check("...no bucket-level sanity banner (no earned bucket nets negative, no deduction bucket nets positive); the ONLY banners name the two "
      "labels whose text says 'deact' but whose money is POSITIVE (fee refunds) — a real question for the person, not a defect",
      sorted(o1["verify"]["banners"]) == sorted([f"'{l}' is flagged as a reversal but nets POSITIVE under your sign choice — re-check 3.4."
                                                 for l in ("DPP Service Fee - Upgrade Deact", "DPP Service Fee - Deactivation")]), o1["verify"]["banners"])
# the commit under the owner's mapping (stores marked company-level), through the real path, re-read
oc = commit(db, data=owner_csv(), column_map=_json.dumps(OWNER_MAP), sign_answer="positive", identity=COMPANY_LEVEL)
check("3.9: the commit lands 521 rows; the RE-READ buckets tie to 86,970.34; 'chargebacks' rows carry their key in `category`, "
      "their signed amount in payout_total and 0 in all five columns (no new column)",
      oc["ok"] is True and oc["saved"] == 521 and oc["verified_numbers"]["tie"]["match"] is True
      and oc["verified_numbers"]["totals"]["deductions_total"] == -7356.67
      and all(all(r[c] == 0 for c in CL.CATEGORIES) for r in db.tables["commission_ledger"] if r["category"] == "chargebacks")
      and money(sum(r["payout_total"] for r in db.tables["commission_ledger"] if r["category"] == "chargebacks")) < -3000
      and sum(1 for r in db.tables["commission_ledger"] if r["category"] == "chargebacks") == 49, oc.get("problems"))
check("...the rules saved target the eight buckets under the tenant's key; the ledger's own summarize() agrees on the net and the roll-up",
      {r["category"] for r in db.tables["commission_category_map"]} <= set(CL.bucket_keys())
      and CL.summarize(db.tables["commission_ledger"], conv=CL.convention_named("payout_positive"))["net_total"] == FILE_TOTAL
      and CL.summarize(db.tables["commission_ledger"], conv=CL.convention_named("payout_positive"))["deductions_total"] == -7356.67)
# BOTH choices tie: the same file with every reversal left in the bucket it reverses (the §G choice)
check("BOTH tenant choices tie: reversals in their own `chargebacks` bucket (net 86,970.34, deductions −7,356.67) and reversals netted "
      "inside the buckets they reverse (net 86,970.34, deductions 0.00) — neither is a code default",
      c["verified_numbers"]["totals"]["deductions_total"] == 0.0 and c["verified_numbers"]["tie"]["our_total"] == FILE_TOTAL
      and oc["verified_numbers"]["tie"]["our_total"] == FILE_TOTAL)
# the footer rules, pure and on the endpoint
ident = CM.identity_fields("commission_ledger")
shape_o = OI.stitch_sheets(R._read_upload_grids(owner_csv(total_label="Total", subtotals=True), "s.csv"))
mapped_o = R._ledger_map_records(shape_o["records"], OI.mapping_rules(OI.propose_columns(CM.target_fields("commission_ledger"), [], [], [], shape_o["headers"], shape_o["records"], overrides=OWNER_MAP)))
kept_o, foot_o = OI.split_footer(mapped_o, ident, list(OWNER_MAP))
fs_o = OI.footer_summary(kept_o, foot_o)
check("a label cell that IS the word 'Total' is a footer (rule 2); six section 'Subtotal' rows are footers too, flagged subtotal, "
      "and NOT summed into the file's total when the grand total exists: 521 kept, file total 86,970.34",
      len(kept_o) == 521 and sum(1 for f in foot_o if f["kind"] == "subtotal") == 6 and any(f["rule"] == OI.FOOTER_TOTAL_WORD for f in foot_o)
      and fs_o["file_total_raw"] == FILE_TOTAL and fs_o["equals_lines_sum"] is True, (len(kept_o), foot_o[:2], fs_o["file_total_raw"]))
k2, f2 = OI.split_footer(mapped_o, ident, list(OWNER_MAP))
check("with ONLY subtotals (no grand total) their sum is the file's total",
      OI.footer_summary(*OI.split_footer([m for m in mapped_o if OI._label_says_total(m) != "grand_total"], ident, list(OWNER_MAP)))["file_total_raw"] == FILE_TOTAL)
check("a label that merely CONTAINS the word ('Total Care Plan', a product) is NOT a footer — whole-cell only (RULE TWO: no name is assumed)",
      OI._label_says_total({"product_name": "Total Care Plan", "order_type": ""}) is None
      and OI._label_says_total({"product_name": "  Grand Total: ", "order_type": ""}) == "grand_total"
      and OI._label_says_total({"product_name": "", "order_type": "Sub-total"}) == "subtotal")
check("rule 3 needs a real total: a blank-label line whose amount is NOT the sum of the rest is kept (a defect of the file, surfaced below)",
      len(OI.split_footer(mapped_o + [{"product_name": "", "order_type": "", "raw_amount": 5.0, "store": "Store 1"}], ident, list(OWNER_MAP))[0]) == 522)
# lines with NO label that are NOT the total: a red banner, never a card, and the commit is refused naming 3.3
db = fresh_db()
ob = analyze(db, data=owner_csv(blank_lines=3), column_map=_json.dumps(OWNER_MAP), sign_answer="positive")
check("3 real lines with no label → NOT a card ('(blank)' absent from labels, absent from unassigned), a red banner naming 3.3, "
      "reported with count and Σ; the footer is still found",
      ob["blank_label"] == {"count": 3, "sum_raw": 15.0, "message": ob["blank_label"]["message"]} and "check the label column at 3.3" in ob["blank_label"]["message"]
      and all(l["label"] != "(blank)" for l in ob["labels"]) and ob["unassigned"] == [] and ob["verify"]["banners"][0].startswith("3 line(s) carry no label")
      and ob["detect"]["footer"]["file_total_raw"] == FILE_TOTAL + 15.0 and ob["detect"]["footer"]["rows"][0]["rule"] == OI.FOOTER_SUM_MATCH
      and ob["verify"]["tie"]["match"] is False and ob["verify"]["tie"]["difference"] == -15.0, ob["blank_label"])
s_, d_ = http_error(commit, db, data=owner_csv(blank_lines=3), column_map=_json.dumps(OWNER_MAP), sign_answer="positive", identity=COMPANY_LEVEL)
check("...and the commit is REFUSED (400) naming the label column, nothing written",
      s_ == 400 and "carry no label" in str(d_) and "3.3" in str(d_) and not db.tables.get("commission_ledger"), (s_, str(d_)[:160]))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("K. THE OWNER RESUMES: the KEPT file re-analyzes (use_stored=1) with the new suggestions and the footer excluded")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db()
k1 = analyze(db, data=owner_csv(), column_map=_json.dumps(OWNER_MAP), sign_answer="positive")
check("the dropped file is KEPT for this instance (bucket path under this org)",
      k1["file"]["stored"] is True and k1["file"]["path"].startswith(f"{ORG}/") and (R._INTAKE_BUCKET, k1["file"]["path"]) in db.storage.files)
k2 = analyze(db, file=False, use_stored="1", instance_key=k1["instance_key"], column_map=_json.dumps(OWNER_MAP), sign_answer="positive")
check("re-analyze with NO file and use_stored=1 reads the kept file back: same 27 labels, same suggestions, footer excluded, ties",
      k2["filename"] == "statement.csv" and len(k2["labels"]) == 27 and k2["unassigned"] == []
      and {l["label"]: l["bucket"] for l in k2["labels"]} == {l["label"]: l["bucket"] for l in k1["labels"]}
      and k2["detect"]["footer_rows_dropped"] == 1 and k2["verify"]["tie"]["match"] is True)
kc = commit(db, file=False, use_stored="1", instance_key=k1["instance_key"], column_map=_json.dumps(OWNER_MAP), sign_answer="positive", identity=COMPANY_LEVEL)
check("...and the commit with use_stored=1 lands and verifies from the kept file", kc["ok"] is True and kc["saved"] == 521)
s_, d_ = http_error(analyze, fresh_db(), file=False, use_stored="1", instance_key="commission:nothing:kept")
check("use_stored with nothing kept → 400 asking for the file again (never someone else's)", s_ == 400 and "drop the file again" in str(d_).lower())
# PUT /state merges PER KEY: two interleaved writes never clobber each other
db = use(fresh_db())
ik = OI.instance_key("commission", CARRIER_ID, "commission statement")
R.onboarding_intake_put_state(R.OnboardingIntakeStateIn(instance_key=ik, step="3.6", payload={"assignments": [{"label": "A", "bucket": "commission"}]}), org_id=ORG)
R.onboarding_intake_put_state(R.OnboardingIntakeStateIn(instance_key=ik, step="3.6", payload={"sign_answer": "positive"}), org_id=ORG)
R.onboarding_intake_put_state(R.OnboardingIntakeStateIn(instance_key=ik, step="3.6", payload={"assignments": [{"label": "A", "bucket": "spiff"}, {"label": "B", "bucket": "chargebacks"}]}), org_id=ORG)
pl = db.tables["onboarding_stage_state"][0]["payload"]
check("PUT /state merges PER KEY: a debounced write of `assignments` never clobbers `sign_answer` written a moment earlier, and the "
      "latest `assignments` wins; the step stays 3.6 (auto-save never advances the step)",
      pl["sign_answer"] == "positive" and pl["assignments"] == [{"label": "A", "bucket": "spiff"}, {"label": "B", "bucket": "chargebacks"}]
      and db.tables["onboarding_stage_state"][0]["step"] == "3.6" and len(db.tables["onboarding_stage_state"]) == 1, pl)

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("L. PRE-MIGRATION 1009 — display works, a booking to a column-less bucket is REFUSED naming the file")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
db = fresh_db(with_buckets=False)
p1 = analyze(db, data=owner_csv(), column_map=_json.dumps(OWNER_MAP), sign_answer="positive")
check("without the registry table: analyze still works on the built-in mirror — suggestions present, bucket_meta.ready False, 1009 named",
      p1["bucket_meta"]["ready"] is False and "1009" in p1["bucket_meta"]["migration"] and p1["unassigned"] == [] and p1["verify"]["tie"]["match"] is True)
s_, d_ = http_error(commit, db, data=owner_csv(), column_map=_json.dumps(OWNER_MAP), sign_answer="positive", identity=COMPANY_LEVEL)
check("...but a commit that would book to chargebacks / vendor_fee / misc_charges is REFUSED (400) naming 1009_commission_bucket_registry.sql — "
      "nothing written (no mapping, no rule, no row), nothing re-bucketed",
      s_ == 400 and "1009" in str(d_) and "chargebacks" in str(d_) and not db.tables.get("column_mapping") and not db.tables.get("commission_ledger"), (s_, str(d_)[:200]))
p2 = commit(db, column_map=_json.dumps(MAP), sign_answer="positive", assignments=ASSIGN)
check("...while a commit that only uses the five column-backed buckets still lands and ties (byte-identical for existing tenants)",
      p2["ok"] is True and p2["saved"] == 521 and p2["verified_numbers"]["tie"]["match"] is True)
db = fresh_db()
db.tables["commission_bucket"] = []                       # the table exists, the seed did not land
p3 = analyze(db, data=owner_csv(), column_map=_json.dumps(OWNER_MAP), sign_answer="positive")
check("table present but empty → source 'builtin', ready True (a booking is allowed: the registry exists)",
      p3["bucket_meta"]["source"] == "builtin" and p3["bucket_meta"]["ready"] is True)
# a TENANT bucket: the registry drives the tray, the rules and the totals
db = fresh_db()
db.seed("commission_bucket", [{"org_id": ORG, "key": "coop_marketing", "label": "Co-op marketing", "kind": "earned", "sort_order": 55,
                               "is_active": True, "hint_words": ["coop", "co-op"], "pl_line_key": "mdf_income", "is_builtin": False},
                              {"org_id": ORG, "key": "misc_charges", "label": "Misc charges", "kind": "deduction", "sort_order": 80,
                               "is_active": False, "hint_words": [], "pl_line_key": "store_opex", "is_builtin": False}])
p4 = analyze(db, data=owner_csv(), column_map=_json.dumps(OWNER_MAP), sign_answer="positive")
check("a tenant-defined bucket appears in the tray (registry order) and its hint words place 'CoOp' there; a deactivated house bucket "
      "is gone from the tray, and the all-negative labels it would have taken are offered the FIRST active deduction bucket instead "
      "(a 'guess' the person confirms — nothing is booked until they do)",
      "coop_marketing" in p4["buckets"] and "misc_charges" not in p4["buckets"] and p4["bucket_meta"]["source"] == "tenant"
      and {l["label"]: l["bucket"] for l in p4["labels"]}["CoOp"] == "coop_marketing"
      and {l["label"]: (l["bucket"], l["provenance"]) for l in p4["labels"]}["Charitable Contribution-Activation"] == ("chargebacks", "guess")
      and p4["unassigned"] == [], (p4["buckets"], p4["unassigned"]))
# the registry endpoints
use(db)
g = R.get_commission_buckets(org_id=ORG)
check("GET /commission-buckets: merged registry (9 rows: 8 house + 1 tenant, one overridden), the P&L chart to pick from, the kinds, usage counts",
      len(g["buckets"]) == 9 and g["ready"] is True and g["source"] == "tenant" and any(ln["key"] == "carrier_comm" for ln in g["pl_lines"])
      and [k["value"] for k in g["kinds"]] == ["earned", "deduction"] and g["column_backed"] == list(CL.CATEGORIES))
s_, d_ = http_error(R.upsert_commission_bucket, R.CommissionBucketIn(key="vendor_fee", pl_line_key="no_such_line"), org_id=ORG)
check("POST /commission-buckets refuses a P&L link that is not a line of the chart", s_ == 400 and "not a line" in str(d_))
s_, d_ = http_error(R.upsert_commission_bucket, R.CommissionBucketIn(key="Bad Key", label="x"), org_id=ORG)
check("...and a key that is not a key", s_ == 400)
r_ = R.upsert_commission_bucket(R.CommissionBucketIn(key="vendor_fee", label="Distributor fees", pl_line_key="vip_fees", hint_words="fee, surcharge"), org_id=ORG)
check("...an override of a house bucket is written as THIS org's row (label, hint words) and read back merged",
      r_["ok"] and r_["bucket"]["label"] == "Distributor fees" and r_["bucket"]["hint_words"] == ["fee", "surcharge"]
      and any(x["org_id"] == ORG and x["key"] == "vendor_fee" for x in db.tables["commission_bucket"])
      and {b["key"]: b["label"] for b in r_["buckets"]}["vendor_fee"] == "Distributor fees")
s_, d_ = http_error(R.delete_commission_bucket, "commission", org_id=ORG)
check("DELETE refuses a column-backed key", s_ == 400 and "column-backed" in str(d_))
s_, d_ = http_error(R.upsert_commission_bucket, R.CommissionBucketIn(key="x", label="x"), org_id=ORG) if False else (None, None)
use(fresh_db(with_buckets=False))
s_, d_ = http_error(R.upsert_commission_bucket, R.CommissionBucketIn(key="vendor_fee", label="x"), org_id=ORG)
check("POST /commission-buckets pre-1009 → 400 naming the migration, nothing written", s_ == 400 and "1009" in str(d_))

# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("I. WIRED, REGISTERED, AND NO CARRIER NAMED")
# ══════════════════════════════════════════════════════════════════════════════════════════════════
paths = [rt.path for rt in R.router.routes]
check("the four endpoints are mounted",
      all(p in paths for p in ("/commcalc/onboarding/intake/state", "/commcalc/onboarding/intake/analyze", "/commcalc/onboarding/intake/commit"))
      and sum(1 for p in paths if p == "/commcalc/onboarding/intake/state") == 2)
commit_src = inspect.getsource(R.onboarding_intake_commit) + inspect.getsource(R._intake_commit_commission)
imp_src = inspect.getsource(R.commission_ledger_import)
check("the commit saves through the ONE mapping writer and the Category Map's own writer, lands through the import's own path, and re-reads",
      all(t in commit_src for t in ("upsert_column_mapping(", "upsert_commission_category_map(", "_ledger_land_rows(", "_intake_reread(")))
check("/commission-ledger/import lands through the SAME helper (factored, not duplicated)",
      "_ledger_land_rows(" in imp_src and "_ledger_map_records(" in imp_src and ".insert(rows[i:i + 500])" not in imp_src)
check("the analyze endpoint writes nothing",
      not any(t in inspect.getsource(R.onboarding_intake_analyze) for t in (".insert(", ".upsert(", ".update(", "upsert_column_mapping")))
carrier_words = re.compile(r"\b(boost|verizon|cricket|metro|vidapay|total\s+wireless|luxelink|novawave)\b", re.I)
mod_src = inspect.getsource(OI)
intake_router_src = "".join(inspect.getsource(f) for f in (
    R.onboarding_intake_analyze, R.onboarding_intake_commit, R.onboarding_intake_state, R.onboarding_intake_put_state,
    R._intake_prepare, R._intake_prepare_commission, R._intake_commit_commission, R._intake_payload, R._intake_carrier,
    R._intake_house_defaults, R._intake_save_state, R._intake_reread))
check("no carrier name in onboarding_intake.py or in the router's intake functions (RULE TWO)",
      not carrier_words.search(mod_src) and not carrier_words.search(intake_router_src))
check("no pandas / fastapi / supabase import in the pure module",
      not re.search(r"^\s*(import|from)\s+(pandas|fastapi|supabase)", mod_src, re.M))
for mig, must in (("1007_onboarding_intake_state.sql", ("onboarding_run", "onboarding_stage_state", "-- REVERT:", "IF NOT EXISTS")),
                  ("1008_sign_convention_netted.sql", ("payout_negative_netted", "-- REVERT:", "column_mapping_sign_convention_ck"))):
    src = open(os.path.join(MIG_DIR, mig), encoding="utf-8").read()
    check(f"migration {mig} exists, is idempotent/additive and carries a REVERT note", all(m in src for m in must))
idx = open(os.path.join(MIG_DIR, "..", "..", "docs", "SYSTEM_DATA_FLOW_INDEX.md"), encoding="utf-8").read()
check("registered in the index: the endpoints, the module, the tables and the page",
      all(t in idx for t in ("/commcalc/onboarding/intake/analyze", "/commcalc/onboarding/intake/commit", "onboarding_intake.py",
                             "commcalc.onboarding_stage_state", "onboarding/intake/page.tsx", "harness_onboarding_intake.py")))
page = open(FE_PAGE, encoding="utf-8").read() if os.path.exists(FE_PAGE) else ""
shared_fe = os.path.join(os.path.dirname(FE_PAGE), "intake-shared.tsx")
shared = open(shared_fe, encoding="utf-8").read() if os.path.exists(shared_fe) else ""
check("the page asks 3.4 verbatim, with the two buttons, and calls the three endpoints (BASE lives in the shared intake module since Stage B)",
      OI.SIGN_QUESTION in page and "Earned is positive" in page and "Earned is negative" in page
      and "/commcalc/onboarding/intake'" in (page + shared)
      and all(t in page for t in ("${BASE}/state", "${BASE}/analyze", "${BASE}/commit")))
check("the page has no default sign answer (the buttons start unselected) and no drag library",
      "signAnswer" in page and "useState<SignAnswer>(null)" in page and "react-dnd" not in page and "dnd-kit" not in page)
# ── THE BUCKETS ARE THE REGISTRY on every screen (owner: "this is not to be hardcoded") ──
FE = os.path.join(os.path.dirname(FE_PAGE), "..", "..", "commcalc")
fe_ledger = open(os.path.join(FE, "commission-ledger", "page.tsx"), encoding="utf-8").read()
fe_setup = open(os.path.join(FE, "commission-ledger", "setup", "page.tsx"), encoding="utf-8").read()
fe_map = open(os.path.join(FE, "commission-category-map", "page.tsx"), encoding="utf-8").read()
stage2 = open(os.path.join(os.path.dirname(FE_PAGE), "stage2.tsx"), encoding="utf-8").read()
check("no bucket key is hardcoded in the intake page, the ledger report, the setup wizard, the category map or the shared module — "
      "every tray / tile / picker renders the backend's registry payload",
      all("'autopay_residual'" not in src and "'equipment_rebate'" not in src for src in (page, shared, fe_ledger, fe_setup, fe_map, stage2))
      and "bucket_rows" in page and "bucketKeys(" in fe_ledger and "a.categories" in fe_setup and "BucketsPanel" in fe_map)
check("the Category → Bucket Map page hosts the Buckets settings panel: add / rename / reorder / deactivate, kind, hint words, the P&L line "
      "picked from the chart the backend sends, through GET/POST/DELETE /commission-buckets",
      all(t in fe_map for t in ("/api/v1/commcalc/commission-buckets", "method: 'POST'", "method: 'DELETE'", "pl_lines", "hint_words", "kinds", "sort_order", "is_active")))
check("the 3.6 tray groups earned and deduction buckets and shows the blank-label banner; the 3.8 card shows Earned (+) / Reversed (−) / Net "
      "with the earned and deduction roll-ups and Σ all buckets",
      all(t in page for t in ("earnedKeys", "dedKeys", "a.blank_label", "Earned (+)", "Reversed (−)", "Earned buckets", "Deduction buckets", "Σ all buckets (net")))
# ── AUTO-SAVE (owner 2026-09-20: "as you keep assigning the buckets it should auto save and give an option to save manually also") ──
check("the shared module carries ONE debounced auto-save hook (600 ms, trailing) that merges pending patches, flushes on demand, and on "
      "pagehide / beforeunload / unmount with a keepalive PUT; and the indicator renders saving / saved hh:mm / not saved — why / run migration 1007",
      all(t in shared for t in ("export function useAutoSave", "AUTOSAVE_MS = 600", "pagehide", "beforeunload", "keepalive", "Saving…", "Saved ✓", "Not saved —", "Not saved: run migration", "export function SaveButton")))
check("the intake page: the bucket dropdown's onChange reaches the save — assignments are in the `draft`, the draft effect calls schedule(draft), "
      "and persistNow PUTs /state with the CURRENT step (auto-save never advances the step)",
      "assignments: assignments.filter(x => x.bucket !== UNASSIGNED)" in page and "schedule(draft)" in page
      and "body: JSON.stringify({ instance_key: instanceKey, step, payload: patch, by: who }), keepalive" in page
      and "useAutoSave(persistNow)" in page)
check("...the draft carries every field the owner named: assignments, reversal flags, sign answer, identity decisions, typed total, period, "
      "attestation, column map, sheet / header row / footer overrides",
      all(t in page for t in ("reversal_flags: reversal", "sign_answer: signAnswer", "identity: decisions", "typed_total: typedTotal", "period,",
                              "attestation: attest", "column_map: columnMap", "sheet, header_row: headerRow, footer_mode: footerMode")))
check("...a Save button (with the indicator) sits on EVERY step 3.1–3.8 (7 action rows: 3.5 and 3.6 share one) and on stage 2's steps 2.1–2.5 (5 rows)",
      page.count("{saveUi}") == 7 and stage2.count("{saveUi}") == 5 and "useAutoSave(persistNow)" in stage2, (page.count("{saveUi}"), stage2.count("{saveUi}")))
check("...RESTORE reads every one of those fields back on reopen (stage 3 and stage 2)",
      all(t in page for t in ("p.reversal_flags", "p.attestation", "p.sheet", "p.header_row", "p.footer_mode", "p.typed_total", "p.period", "p.identity", "p.sign_answer", "p.assignments"))
      and all(t in stage2 for t in ("p.attestation", "p.typed_total", "p.sheet", "p.header_row", "p.footer_mode", "p.identity", "p.column_map")))

print(f"\n══ onboarding intake: {_pass} passed, {_fail} failed ══")
if _failures:
    print("FAILED:\n  - " + "\n  - ".join(_failures))
sys.exit(1 if _fail else 0)
