"""THE IN-MEMORY SUPABASE CLIENT the onboarding-intake proof harnesses drive the REAL router with —
extracted verbatim from harness_onboarding_intake_c.py (2026-09-21) so harness_onboarding_intake_d.py
(the 2.5a activation-type step) does not carry a fourth copy. Declared columns make a select of an
undeclared column raise like Postgres 42703; storage is a dict. No network, no DB."""
import uuid


class _Q:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.rows, self.filters, self.conflict = "select", None, [], None
        self._order, self._limit, self._range, self.cols = None, None, None, "*"
        self._count = None

    def select(self, cols="*", **kw):
        self.op, self.cols = "select", cols
        for c in [c.strip() for c in str(cols).split(",") if c.strip() and c.strip() != "*"]:
            if c not in self.db.columns(self.table):
                raise RuntimeError(f'42703 column "{c}" of {self.table} does not exist')
        if kw.get("count"):
            self._count = True
        return self

    def insert(self, rows):
        self.op, self.rows = "insert", (rows if isinstance(rows, list) else [rows])
        return self

    def upsert(self, rows, on_conflict=None):
        self.op, self.rows, self.conflict = "upsert", (rows if isinstance(rows, list) else [rows]), on_conflict
        return self

    def update(self, row, count=None):
        self.op, self.rows, self._count = "update", [row], count
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, k, v):    self.filters.append(("eq", k, v)); return self
    def neq(self, k, v):   self.filters.append(("neq", k, v)); return self
    def in_(self, k, v):   self.filters.append(("in", k, list(v))); return self
    def is_(self, k, v):   self.filters.append(("is", k, v)); return self
    def gte(self, k, v):   self.filters.append(("gte", k, v)); return self
    def lte(self, k, v):   self.filters.append(("lte", k, v)); return self
    def lt(self, k, v):    self.filters.append(("lt", k, v)); return self
    def ilike(self, k, v): self.filters.append(("ilike", k, v)); return self
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
            if op == "gte" and not (x is not None and str(x) >= str(v)): return False
            if op == "lte" and not (x is not None and str(x) <= str(v)): return False
            if op == "lt" and not (x is not None and str(x) < str(v)): return False
            if op == "ilike" and not (x is not None and str(v).strip("%").lower() in str(x).lower()): return False
        return True

    def execute(self):
        t = self.db.tables.setdefault(self.table, [])
        if t is None:
            raise RuntimeError(f'42P01 relation "{self.table}" does not exist')
        if self.op == "select":
            out = [dict(r) for r in t if self._match(r)]
            if self._order:
                k, desc = self._order
                out.sort(key=lambda r: (r.get(k) is None, str(r.get(k))), reverse=desc)
            n_all = len(out)
            if self._range:
                lo, hi = self._range
                out = out[lo:hi + 1]
            if self._limit is not None:
                out = out[:self._limit]
            return _Res(out, count=n_all if self._count else None)
        if self.op == "insert":
            self.db.check_rows(self.table, self.rows)
            written = []
            for r in self.rows:
                row = dict(r)
                row.setdefault("id", str(uuid.uuid4()))
                if self.db.drop_inserts.get(self.table):
                    continue
                if self.db.drop_every_other.get(self.table) and len(written) % 2:
                    written.append(dict(row))
                    continue
                t.append(row)
                written.append(dict(row))
            return _Res(written)
        if self.op == "upsert":
            self.db.check_rows(self.table, self.rows, upsert=True)
            keys = [k.strip() for k in (self.conflict or "").split(",") if k.strip()]
            written = []
            for r in self.rows:
                row = dict(r)
                hit = next((x for x in t if keys and all(x.get(k) == row.get(k) for k in keys)
                            and all(row.get(k) is not None for k in keys)), None)
                if hit:
                    hit.update(row)
                    written.append(dict(hit))
                else:
                    row.setdefault("id", str(uuid.uuid4()))
                    if not self.db.drop_inserts.get(self.table):
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
            return _Res(out, count=len(out) if self._count else None)
        if self.op == "delete":
            keep = [r for r in t if not self._match(r)]
            gone = len(t) - len(keep)
            t[:] = keep
            return _Res([{}] * gone)
        raise RuntimeError(self.op)


class _Res:
    def __init__(self, data, count=None): self.data, self.count = data, count


class FakeStorage:
    def __init__(self, broken=False):
        self.buckets, self.files, self.broken = set(), {}, broken

    def get_bucket(self, name):
        if name not in self.buckets:
            raise RuntimeError("bucket not found")
        return {"name": name}

    def create_bucket(self, name):
        if self.broken:
            raise RuntimeError("storage unavailable")
        self.buckets.add(name)

    def from_(self, bucket):
        st = self

        class _B:
            def upload(self_, path, data, opts=None):
                if st.broken:
                    raise RuntimeError("storage unavailable")
                st.files[(bucket, path)] = bytes(data)
                return {"Key": path}

            def download(self_, path):
                if (bucket, path) not in st.files:
                    raise RuntimeError("object not found")
                return st.files[(bucket, path)]
        return _B()


class FakeDB:
    def __init__(self, storage_broken=False):
        self.tables = {}
        self.drop_inserts = {}
        self.drop_every_other = {}
        self.storage = FakeStorage(broken=storage_broken)
        self.declared = {
            "carrier": ["id", "org_id", "name", "code", "is_default"],
            "column_mapping": ["id", "org_id", "report_key", "carrier_id", "target_field", "source_header",
                               "transform", "is_active", "priority", "updated_at", "sign_convention"],
            # `source` = mig 727 (writer provenance) — since 2026-09-20 also the report KIND that wrote the row
            # (landing_identity.KIND_STAMP); `raw_sales_product` = mig 1011, the by-product aggregate's OWN table
            "raw_sales": ["id", "org_id", "period", "period_month", "period_year", "store", "salesperson", "user_login",
                          "department", "category", "product_desc", "product_id", "gp", "ext_price", "trans_id",
                          "trans_date", "contract_type", "mdn", "serial_1", "register", "tender_type", "voided",
                          "trans_type", "sku", "customer", "email", "customer_no", "quantity", "total_cost",
                          "pricing_discounts", "contract_no", "source", "created_at"],
            "raw_sales_product": ["id", "org_id", "period", "period_month", "period_year", "store", "salesperson", "user_login",
                                  "department", "category", "product_desc", "product_id", "gp", "ext_price", "trans_id",
                                  "trans_date", "contract_type", "mdn", "serial_1", "register", "tender_type", "voided",
                                  "trans_type", "sku", "customer", "email", "customer_no", "quantity", "total_cost",
                                  "pricing_discounts", "contract_no", "source", "created_at"],
            "daily_sales_feed": ["id", "org_id", "period", "store", "salesperson", "department", "category", "product_desc",
                                 "gp", "ext_price", "trans_id", "trans_date", "contract_type", "mdn", "serial_1", "tender_type",
                                 "voided", "trans_type", "quantity"],
            "inventory_aging_device": ["id", "org_id", "imei", "serial", "sku", "item", "store", "unit_cost",
                                       "received_date", "days_in_stock", "as_of_date", "source", "raw_row",
                                       "on_hand", "off_hand_as_of", "status", "quantity", "total_cost", "category",
                                       "updated_at", "created_at"],
            "store_mapping": ["id", "org_id", "store_code", "store_address", "market", "is_active"],
            "stores": ["id", "org_id", "store_code", "address", "market", "is_active", "entity_id", "timezone"],
            "store_aliases": ["id", "org_id", "alias", "store_code", "note", "source", "confidence"],
            "rep_aliases": ["id", "org_id", "alias", "canonical"],
            "employees": ["id", "org_id", "employee_id", "name", "home_store", "epay_salesperson", "is_active"],
            "onboarding_run": ["id", "org_id", "run_kind", "period", "status", "current_step", "started_by",
                               "started_at", "signed_off_by", "signed_off_on_behalf", "signed_off_at", "updated_at"],
            "onboarding_stage_state": ["id", "run_id", "org_id", "stage", "step", "instance_key", "status", "payload",
                                       "verified_numbers", "verified_by", "verified_at", "blocking_reason", "updated_at"],
            # ── Stage C destinations (each an EXISTING table; columns per their migrations) ──
            "pos_tender_summary": ["id", "org_id", "close_date", "store", "tender_type", "tender_class", "amount",
                                   "source", "updated_at"],                                             # mig 062
            "merchant_settlement_day": ["id", "org_id", "source_id", "portal_key", "report_key", "settlement_role",
                                        "business_date", "merchant_id", "terminal_id", "store_label", "store_code",
                                        "card_brand", "gross_amount", "refund_amount", "net_amount", "fee_amount",
                                        "txn_count", "currency", "batch_ref", "source_line", "raw", "pulled_at",
                                        "created_at"],                                                   # mig 955
            "raw_ma_daily_tx": ["id", "org_id", "period", "period_month", "period_year", "source_id", "tx_date",
                                "due_date", "account_id", "account_name", "direct_ma_id", "direct_ma_name",
                                "top_ma_id", "top_ma_name", "order_number", "user_name", "order_type",
                                "product_name", "retail_cost", "merchant_discount", "merchant_invoice"],   # mig 083
            "raw_epay_daily_tx": ["id", "org_id", "transaction_id", "transaction_source_id", "invoice_id",
                                  "settlement_date", "terminal_id", "user_name", "product", "product_title",
                                  "tx_type", "host_timestamp", "control_number", "retail", "discount", "cost",
                                  "commission", "is_fee", "source_batch", "store_code"],                # mig 903
            "store_merchant_id": ["id", "org_id", "store_code", "processor", "merchant_id", "not_required", "note",
                                  "created_at", "updated_at"],                                          # mig 902
            "data_source": ["id", "org_id", "processor", "label", "settlement_role", "portal_calibration",
                            "account_id", "enabled"],                                                   # mig 083/955
            "report_pull_map": ["id", "org_id", "report_key", "display_name", "target_table", "column_map",
                                "param_spec", "export_pref", "enabled", "sort_order", "processor"],       # mig 207
            "raw_custom_import": ["id", "org_id", "report_key", "period", "period_month", "period_year",
                                  "source_filename", "row_index", "data", "carrier_id"],                # mig 099
            # ── the two config homes the 2.5a step writes (harness_onboarding_intake_d) ──
            "accessory_config": ["id", "org_id", "departments", "categories", "product_keywords", "acima_tenders",
                                 "box_departments", "setup_fee_keywords", "billpay_products", "contract_type_map",
                                 "activation_rules", "activation_details_rules", "box_count_buckets",
                                 "catalog_classify_enabled", "catalog_accessory_categories", "apply_to_gp",
                                 "definition_drives_pay", "gp_acc_basis", "updated_at"],                # mig 208 … 313 … 930
            "exec_metric_config": ["id", "org_id", "bucket", "rules", "basis", "carrier", "applicable", "updated_at"],  # mig 204/962/963
        }

    def columns(self, table):
        if table in self.declared:
            return self.declared[table]
        return ["*"] + [k for r in (self.tables.get(table) or []) for k in r]

    def check_rows(self, table, rows, upsert=False):
        if table in self.declared:
            for r in rows:
                bad = [k for k in r if k not in self.declared[table]]
                if bad:
                    raise RuntimeError(f'42703 column "{bad[0]}" of {table} does not exist')
        if table in ("raw_sales", "raw_sales_product", "raw_ma_daily_tx"):
            for r in rows:
                if not r.get("period"):
                    raise RuntimeError(f'23502 null value in column "period" of {table} violates not-null constraint')

    def schema(self, _name): return self
    def table(self, name): return _Q(self, name)

    def seed(self, table, rows):
        for r in rows:
            row = dict(r); row.setdefault("id", str(uuid.uuid4()))
            self.tables.setdefault(table, []).append(row)


class FakeUpload:
    def __init__(self, data, filename="export.xlsx"):
        self.data, self.filename = data, filename

    async def read(self): return self.data




# ══════════════════════════════════════════════════════════════════════════════════════════════════
# THE MEASURED VOCABULARY FIXTURE (owner 2026-09-21) — shared by harness_line_class.py and
# harness_onboarding_intake_d.py so the two proofs count the very same lines.
# ══════════════════════════════════════════════════════════════════════════════════════════════════
# The measured August-2026 export: contract_type blank on every row; department = the category top;
# the activation TYPE in the category path leaf; BYOD also in the product name. Counts per leaf as
# measured, laid over 88 invoices so the DISTINCT-invoice rule is visible: 40 new-activation lines on
# 39 invoices (one family plan carries two), 25 upgrades on 25, 24 BYOD on 23 (one carries two), the 4
# hardware-only units on one invoice.
DEPT = "Activations (Price Sheet)"
P = ">> Activations (Price Sheet) >> "
CAT = {
    "na": P + "Verizon Wireless >> Equip Rebates (VZ Commission) >> New Activation (VZ Commission)",
    "upg": P + "Verizon Wireless >> Equip Rebates (VZ Commission) >> Upgrades (VZ Commission)",
    "byod": P + "Cellular Equipment >> Customer Provided Device",
    "hw": P + "Prepaid/Hardware Only (Equip)",
    "apple": P + "Cellular Equipment >> SmartPhones >> Apple >> Apple Smartphones",
    "samsung": P + "Cellular Equipment >> SmartPhones >> Android >> Samsung Smartphones",
    "tcl": P + "Cellular Equipment >> Basic Phones >> TCL Basic",
    "spf": P + "Features >> Features - SPF",
    "nospf": P + "Features >> Features - No SPF",
    "plan": P + "Rate Plans >> Unlimited Plans",
    "rebate": P + "Verizon Wireless >> Equip Rebates (VZ Commission) >> Device Rebate",
    "acc": ">> Accessories >> Chargers",
}
PROD = {"na": "New Activation (VZ Commission)", "upg": "Upgrade (VZ Commission)", "byod": "Customer Owned Device (START PAW)",
        "hw": "Prepaid Hardware Only", "apple": "Apple iPhone 16", "samsung": "Samsung Galaxy S25", "tcl": "TCL Flip 2",
        "spf": "Unlimited Plus SPF", "nospf": "Feature No SPF", "plan": "Unlimited Welcome", "rebate": "Device Rebate", "acc": "Portable Charger"}


def measured_fixture_rows():
    rows, inv = [], 1000
    stores = ["10 Main St", "22 Oak Ave"]
    reps = ["alice", "bob", "carol"]

    def add(kind, tid, n=1, price=10.0):
        for _ in range(n):
            rows.append({"trans_id": str(tid), "trans_date": f"2026-08-{(tid % 28) + 1:02d}", "store": stores[tid % 2],
                         "salesperson": reps[tid % 3], "user_login": reps[tid % 3], "contract_type": "",
                         "department": DEPT if CAT[kind].startswith(P) else "Accessories", "category": CAT[kind],
                         "product_desc": PROD[kind], "gp": round(price * 0.3, 2), "ext_price": price, "voided": "No",
                         "trans_type": "", "period": "August 2026", "quantity": 1, "mdn": "", "serial_1": f"S{tid}{len(rows)}",
                         "tender_type": "Cash"})
    phones = {"apple": 49, "samsung": 12, "tcl": 6}
    phone_kinds = [k for k, n in phones.items() for _ in range(n)]     # 67 handset lines
    pi = 0
    # 39 new-activation invoices (one carries two new-activation lines and two phones)
    for i in range(39):
        inv += 1
        add("na", inv, 2 if i == 0 else 1)
        for _ in range(2 if i == 0 else 1):
            add(phone_kinds[pi], inv, price=400.0); pi += 1
        add("spf", inv, 3); add("plan", inv)
    # 25 upgrade invoices
    for i in range(25):
        inv += 1
        add("upg", inv)
        add(phone_kinds[pi], inv, price=350.0); pi += 1
        add("nospf", inv, 2); add("rebate", inv)
    # 23 BYOD invoices (one carries two BYOD lines); the remaining handsets ride here as add-ons
    for i in range(23):
        inv += 1
        add("byod", inv, 2 if i == 0 else 1)
        add("spf", inv, 1); add("plan", inv)
        if pi < len(phone_kinds):
            add(phone_kinds[pi], inv, price=300.0); pi += 1
    while pi < len(phone_kinds):        # any handset left over rides the last BYOD invoice
        add(phone_kinds[pi], inv, price=300.0); pi += 1
    # 1 hardware-only invoice with 4 prepaid units and an accessory
    inv += 1
    add("hw", inv, 4, price=59.0); add("acc", inv, price=25.0)
    return rows


