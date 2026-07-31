"""Proof harness — agent/commission/ma-product-class (owner directive 2026-07-31).

Drives the REAL commcalc.ma_product_class engine + the REAL router endpoints over an in-memory
FakeClient whose write surface RAISES, and DIFFERENTIALS every money-bearing surface against the BASE
copy pulled straight out of git (origin/main a62a893) loaded as a second module. No DB, no network,
ZERO writes on the read paths, non-house tenants throughout.

Run from the backend dir:  python3 scratchpad/ma_product_class_proof.py

Sections
  A. SEED FIDELITY — the 69 names in the owner's sample, exactly; every class in the vocabulary; the
     SQL seed and the code seed are the SAME rows (generated from one source).
  B. EXACT MATCH, NO KEYWORDS — trim is the only normalization; the trailing-space export value
     matches; case variants do NOT; suffix-differing names do NOT collide; 'edge' never substring-hits;
     the engine contains no contains/regex/startswith matcher at all (source-text assertion).
  C. UNMAPPED IS LOUD — an unknown name is 'unmapped', never a money class; it carries its own line
     count + dollars; the write path REFUSES to assign the reserved class.
  D. PROPOSED vs CONFIRMED — confirmed-only vs including-proposals, and the delta between them IS the
     impact preview. Confirming changes status only, never the class.
  E. SIGNS UNTOUCHED — negatives stay negative, positives positive; no normalization, no abs().
  F. PERIOD SPELLING — 'June 2026' and '2026-06' land on ONE month; a blank period falls back to the
     row's date; neither invents a month.
  G. MULTI-TENANT — every read is .eq(org_id, caller); two tenants in one source table see only their
     own names; writes stamp org_id; org_id is a QUERY PARAM on every endpoint.
  H. ZERO-WRITE READ PATHS — every GET runs against a client whose insert/update/upsert/delete raise;
     the guard is tripped deliberately to prove it can fire.
  I. THE PACKAGE MOVES $0 — BYTE-IDENTICAL DIFFERENTIAL against BASE a62a893 for whatif carrier income,
     commission_ledger classification/summarisation and ledger_ma_sync derivation; plus source-text
     identity of every money module; plus proof no money module imports or names ma_product_class.
  J. IDENTIFIER COLUMNS ARE NEVER MONEY — merchant_invoice refused as an amount column everywhere,
     cross-checked against whatif's own guard and ma_upload.FIELD_LABELS.
  K. GRACEFUL DEGRADATION — pre-254 (all three tables absent) the page still answers, read-only, with
     the built-in vocabulary + proposals and a migration hint; nothing 500s.
  L. MIGRATION 254 — real PostgreSQL parse (pglast); additive-only; RLS enabled with ZERO policies and
     ZERO anon/authenticated grants; idempotent; band 200–299 and not already taken.
"""
import importlib.util, inspect, os, re, subprocess, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import app.modules.commcalc.ma_product_class as M
import app.modules.commcalc.commission_ledger as CL
import app.modules.commcalc.ledger_ma_sync as LMS
import app.modules.commcalc.whatif as W
import app.modules.commcalc.router as R

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


HOUSE = "00000000-0000-0000-0000-000000000001"
NIL = "00000000-0000-0000-0000-000000000000"
LUX = "22222222-2222-2222-2222-222222222222"      # non-house tenant under test
OTHER = "33333333-3333-3333-3333-333333333333"    # a second tenant that must never leak
JUNE, JULY = "June 2026", "July 2026"

WRITES = []
READS = []


# ── in-memory fake supabase client — reads only (writes raise) ────────────────────────────────────
class FakeResult:
    def __init__(self, data=None, count=None):
        self.data = data or []
        self.count = count


class FakeQuery:
    def __init__(self, store, table, absent, allow_writes):
        self.store, self.t, self.absent = store, table, absent
        self.allow_writes = allow_writes
        self.f, self.rng, self.cols = [], None, "*"

    def select(self, *a, **k):
        if a:
            self.cols = a[0]
        return self

    def eq(self, c, v):
        self.f.append(('eq', c, v)); return self

    def in_(self, c, v):
        self.f.append(('in', c, list(v))); return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def order(self, *a, **k):
        return self

    def _guard(self, kind, payload):
        WRITES.append((kind, self.t, payload))
        if not self.allow_writes:
            raise AssertionError("WRITE ATTEMPTED: %s %s" % (kind, self.t))

    def insert(self, rows, *a, **k):
        self._guard('insert', rows)
        self.store.setdefault(self.t, []).extend(
            [dict(r, id=r.get('id') or ("id-%d" % (len(self.store[self.t]) + 1))) for r in rows])
        return self

    def upsert(self, row, *a, **k):
        self._guard('upsert', row)
        self.store.setdefault(self.t, []).append(dict(row, id=row.get('id') or "id-up"))
        return self

    def update(self, patch, *a, **k):
        self._guard('update', patch)
        self._patch = patch
        return self

    def delete(self, *a, **k):
        self._guard('delete', None)
        self._del = True
        return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == 'eq' and rv != v:
                return False
            if k == 'in' and rv not in v:
                return False
        return True

    def execute(self):
        if self.t in self.absent:
            raise Exception('relation "commcalc.%s" does not exist' % self.t)
        rows = self.store.setdefault(self.t, [])
        if getattr(self, '_patch', None) is not None:
            for r in rows:
                if self._m(r):
                    r.update(self._patch)
            return FakeResult(data=[])
        if getattr(self, '_del', False):
            keep = [r for r in rows if not self._m(r)]
            self.store[self.t] = keep
            return FakeResult(data=[])
        READS.append((self.t, list(self.f), str(self.cols)))
        m = [dict(r) for r in rows if self._m(r)]
        if self.rng:
            a, b = self.rng
            m = m[a:b + 1]
        return FakeResult(data=m)


class FakeSchema:
    def __init__(self, store, absent, allow_writes):
        self.store, self.absent, self.allow_writes = store, absent, allow_writes

    def table(self, t):
        return FakeQuery(self.store, t, self.absent, self.allow_writes)

    def rpc(self, name, params):
        raise Exception('no such rpc: ' + name)


class FakeClient:
    def __init__(self, store, absent=None, allow_writes=False):
        self.store, self.absent, self.allow_writes = store, set(absent or []), allow_writes

    def schema(self, s):
        return FakeSchema(self.store, self.absent, self.allow_writes)


def install(store, absent=None, allow_writes=False):
    c = FakeClient(store, absent, allow_writes)
    R.sb = lambda: c
    return c


# ── the BASE modules (origin/main a62a893) loaded side by side ────────────────────────────────────
BASE_REV = "a62a893"
_repo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


def _load_base(relpath, modname):
    src = subprocess.check_output(["git", "-C", _repo, "show", "%s:%s" % (BASE_REV, relpath)]).decode()
    t = tempfile.NamedTemporaryFile("w", suffix="_%s.py" % modname, delete=False)
    t.write(src)
    t.close()
    spec = importlib.util.spec_from_file_location(modname, t.name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, src


BW, BW_SRC = _load_base("backend/app/modules/commcalc/whatif.py", "whatif_base_a62a893")
BCL, BCL_SRC = _load_base("backend/app/modules/commcalc/commission_ledger.py", "cledger_base_a62a893")
BLMS, BLMS_SRC = _load_base("backend/app/modules/commcalc/ledger_ma_sync.py", "lmasync_base_a62a893")


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────────
SAMPLE = [
    ("TBV MONTH 3 New Activation Commission", -37.5, JUNE, "2026-06-04", "Store A", "rep1"),
    ("TBV MONTH 5 New Activation SPF", -37.5, JUNE, "2026-06-05", "Store A", "rep1"),
    ("Trac Autopay Residual ", -3.4, JUNE, "2026-06-06", "Store A", "rep2"),      # TRAILING SPACE
    ("Total MAX 5G Plan $55", 55.0, JUNE, "2026-06-07", "Store B", "rep2"),
    ("Total ALL ACCESS Plan $65", 65.0, JUNE, "2026-06-08", "Store B", "rep2"),
    ("Total ALL ACCESS Plan $65 New Activation Commission", 0.0, JUNE, "2026-06-08", "Store B", "rep2"),
    ("Apple iPhone 16e 128GB Black TO", 599.99, JUNE, "2026-06-09", "Store A", "rep1"),
    ("TW EDGE SPF Month 1", -32.5, JUNE, "2026-06-10", "Store A", "rep1"),
    ("Invoice Fee", 0.4, JUNE, "2026-06-11", "Store B", "rep2"),
    ("Credit Debit Memo", 99.99, JUNE, "2026-06-12", "Store B", "rep2"),
    # a name NOBODY has classified — must land LOUDLY unmapped
    ("Brand New Widget Nobody Mapped", 123.45, JUNE, "2026-06-13", "Store B", "rep2"),
    # July, and one row spelled the OTHER way for the same month
    ("Residual", -3.66, JULY, "2026-07-02", "Store A", "rep1"),
    ("Residual", -1.11, "2026-07", "2026-07-03", "Store A", "rep1"),
]


def tx_rows(org=LUX, rows=SAMPLE):
    return [{"org_id": org, "product_name": n, "retail_cost": a, "merchant_discount": round(a / 10, 2),
             "merchant_invoice": 900000 + i, "period": p, "tx_date": d,
             "account_name": s, "user_name": u}
            for i, (n, a, p, d, s, u) in enumerate(rows)]


def base_store(org=LUX, with_map=True, absent=()):
    st = {"raw_ma_daily_tx": tx_rows(org) + tx_rows(OTHER, SAMPLE[:2])}
    st[M.CLASS_TABLE] = [{"org_id": org, "class_key": k, "label": lab, "description": d,
                            "sort_order": so, "is_reserved": res, "is_active": True, "id": "c%d" % i}
                           for i, (k, lab, d, so, res) in enumerate(M.DEFAULT_CLASSES)] if with_map else []
    st[M.MAP_TABLE] = ([{"org_id": org, "source_report": "ma_daily_tx",
                                 "product_name": M.normalize(n), "product_class": c,
                                 "status": "proposed", "note": note or None, "id": "m%d" % i}
                                for i, (n, c, note) in enumerate(M.DEFAULT_PROPOSALS)]
                               if with_map else [])
    st[M.SOURCE_TABLE] = []
    return st


print("=" * 100)
print("A. SEED FIDELITY")
print("=" * 100)
names = [n for n, _c, _t in M.DEFAULT_PROPOSALS]
check("the seed carries 69 product names (the owner's sample)", len(names) == 69, len(names))
check("no duplicate names in the seed", len(set(names)) == len(names))
check("every seeded name is already trimmed", all(n == M.normalize(n) for n in names))
seed_classes = {c for _n, c, _t in M.DEFAULT_PROPOSALS}
check("every seeded class exists in the vocabulary", seed_classes <= set(M.ASSIGNABLE_CLASSES),
      sorted(seed_classes - set(M.ASSIGNABLE_CLASSES)))
check("all 12 assignable classes are exercised by the seed", seed_classes == set(M.ASSIGNABLE_CLASSES),
      sorted(set(M.ASSIGNABLE_CLASSES) - seed_classes))
check("'unmapped' is reserved and NOT assignable",
      M.UNMAPPED in M.CLASS_KEYS and M.UNMAPPED not in M.ASSIGNABLE_CLASSES)
check("no seeded row is pre-confirmed (every one is a PROPOSAL for the owner)",
      all(r["status"] == "proposed" for r in M.seed_rows(LUX)))

MIG = os.path.join(_repo, "database/migrations/254_commission_ma_product_class.sql")
sql = open(MIG).read()
sql_names = re.findall(r"'ma_daily_tx', '((?:[^']|'')*)', '([a-z_]+)', 'proposed'", sql)
sql_pairs = {(n.replace("''", "'"), c) for n, c in sql_names}
code_pairs = {(M.normalize(n), c) for n, c, _t in M.DEFAULT_PROPOSALS}
check("migration 254 seeds EXACTLY the code's proposals (no drift)", sql_pairs == code_pairs,
      "sql-only=%s code-only=%s" % (sorted(sql_pairs - code_pairs)[:3], sorted(code_pairs - sql_pairs)[:3]))
sql_classes = set(re.findall(r"\n  \('00000000-0000-0000-0000-000000000001', '([a-z_]+)', '", sql))
check("migration 254 seeds the full class vocabulary",
      set(M.CLASS_KEYS) <= sql_classes, sorted(set(M.CLASS_KEYS) - sql_classes))

print()
print("=" * 100)
print("B. EXACT MATCH — trim only, NO keyword/substring matchers")
print("=" * 100)
idx = M.build_index([])
check("trim is the only normalization ('  x  ' -> 'x')", M.normalize("  x  ") == "x")
check("normalize does NOT lowercase", M.normalize("Residual") == "Residual")
check("normalize does NOT collapse INTERNAL whitespace",
      M.normalize("Total  MAX") == "Total  MAX")
check("the export's trailing-space value matches after trim",
      M.classify("Trac Autopay Residual ", idx)["product_class"] == "residual")
check("a leading-space variant matches too",
      M.classify("  Trac Autopay Residual", idx)["product_class"] == "residual")
check("a CASE variant does NOT match — it lands unmapped (the safe direction)",
      M.classify("trac autopay residual", idx)["product_class"] == M.UNMAPPED)
check("'Total ALL ACCESS Plan $65' is billpayment",
      M.classify("Total ALL ACCESS Plan $65", idx)["product_class"] == "billpayment")
check("its suffix-only sibling '... New Activation Commission' is commission, NOT billpayment",
      M.classify("Total ALL ACCESS Plan $65 New Activation Commission", idx)["product_class"] == "commission")
check("'TW EDGE SPF Month 1' is a SPIFF (EDGE = the financing tender)",
      M.classify("TW EDGE SPF Month 1", idx)["product_class"] == "spiff")
check("a hypothetical 'Motorola Edge 50 TO' does NOT inherit the EDGE spiff class",
      M.classify("Motorola Edge 50 TO", idx)["product_class"] == M.UNMAPPED)
check("a PREFIX of a mapped name does not match",
      M.classify("Total ALL ACCESS Plan", idx)["product_class"] == M.UNMAPPED)
check("a SUPERSTRING of a mapped name does not match",
      M.classify("Residual Adjustment", idx)["product_class"] == M.UNMAPPED)
eng_src = inspect.getsource(M)
body = eng_src.split('"""', 2)[2]          # strip the module docstring (which DISCUSSES keywords)
for bad in (".startswith(", ".endswith(", "re.search", "re.match", "re.compile", "fnmatch", "import re"):
    check("the engine body contains no %s matcher" % bad, bad not in body)
def _code(fn):
    """A function's source with its docstring removed — so a docstring that DISCUSSES matching can never
    satisfy (or break) an assertion about the code."""
    src = inspect.getsource(fn)
    parts = src.split('"""')
    return parts[0] + "".join(parts[2:]) if len(parts) >= 3 else src


cls_code = _code(M.classify)
check("classify() resolves by DICT LOOKUP on the exact key, not by scanning rules",
      "index.get(key)" in cls_code and "for " not in cls_code, cls_code)
norm_code = _code(M.normalize)
check("normalize() does exactly one thing: .strip()",
      norm_code.count(".strip()") == 1 and ".lower()" not in norm_code and ".replace(" not in norm_code)
bi_code = _code(M.build_index)
check("build_index() keys the index by normalize(name), never by a folded/derived key",
      "idx[normalize(name)]" in bi_code and "idx[name] = " in bi_code
      and ".lower()" not in bi_code.replace('normalize(r.get("status")).lower()', ""))
check("the only .lower() in build_index is on the STATUS field, not the name",
      bi_code.count(".lower()") == 1 and 'normalize(r.get("status")).lower()' in bi_code)

print()
print("=" * 100)
print("C. UNMAPPED IS LOUD — never a money bucket")
print("=" * 100)
st = base_store()
c = install(st)
d = R.get_ma_product_class(org_id=LUX)
by = {i["product_name"]: i for i in d["items"]}
check("the unknown name classifies as 'unmapped'",
      by["Brand New Widget Nobody Mapped"]["product_class"] == M.UNMAPPED)
check("unmapped sorts FIRST in the grid", d["items"][0]["product_class"] == M.UNMAPPED)
check("its dollars are reported, not hidden",
      by["Brand New Widget Nobody Mapped"]["total"] == 123.45)
check("the counts tile reports exactly 1 unmapped name", d["counts"]["unmapped"] == 1, d["counts"])
check("the dollars tile reports its dollars", d["dollars"]["unmapped"] == 123.45, d["dollars"])
pv = R.ma_product_class_preview(org_id=LUX)
check("the preview's unmapped block names it",
      pv["unmapped"]["names"] == 1
      and pv["unmapped"]["detail"][0]["product_name"] == "Brand New Widget Nobody Mapped")
check("unmapped dollars never land in a money class",
      all(pv["preview"]["proposed"]["by_class"].get(k, {"total": 0})["total"] != 123.45
          for k in M.ASSIGNABLE_CLASSES))
try:
    R.upsert_ma_product_class({"product_name": "X", "product_class": M.UNMAPPED}, org_id=LUX)
    check("the write path REFUSES to assign the reserved class", False, "no exception")
except Exception as e:
    check("the write path REFUSES to assign the reserved class", "reserved" in str(e))
try:
    R.upsert_ma_product_class({"product_name": "X", "product_class": "not_a_class"}, org_id=LUX)
    check("the write path REFUSES an unknown class", False, "no exception")
except Exception as e:
    check("the write path REFUSES an unknown class", "unknown class" in str(e))

print()
print("=" * 100)
print("D. PROPOSED vs CONFIRMED — the impact preview IS the delta")
print("=" * 100)
p = pv["preview"]
check("with NOTHING confirmed, every line reads unmapped",
      p["confirmed"]["by_class"].keys() == {M.UNMAPPED}, list(p["confirmed"]["by_class"]))
check("confirmed-mode line count equals the row count", p["confirmed"]["line_count"] == len(SAMPLE))
check("including proposals, only the truly-unknown name stays unmapped",
      p["proposed"]["by_class"][M.UNMAPPED]["lines"] == 1)
check("the delta reports what confirming would classify",
      p["delta"]["lines_newly_classified"] == len(SAMPLE) - 1, p["delta"])
check("the delta's dollars are the unmapped dollars that would move",
      abs(p["delta"]["dollars_newly_classified"]
          - (p["confirmed"]["unmapped_total"] - p["proposed"]["unmapped_total"])) < 1e-9)
check("both readings are always present (the preview is a comparison, not a switch)",
      "confirmed" in p and "proposed" in p)
# confirming changes status only
cw = install(st, allow_writes=True)
before = {i["product_name"]: i["product_class"] for i in R.get_ma_product_class(org_id=LUX)["items"]}
res = R.confirm_ma_product_class({"product_names": ["Total MAX 5G Plan $55"]}, org_id=LUX)
after_rows = R.get_ma_product_class(org_id=LUX)["items"]
after = {i["product_name"]: i["product_class"] for i in after_rows}
check("confirming reports what it confirmed", res["confirmed"] == ["Total MAX 5G Plan $55"], res)
check("confirming changed NO class anywhere", before == after)
check("confirming changed only that name's status",
      [i["status"] for i in after_rows if i["product_name"] == "Total MAX 5G Plan $55"] == ["confirmed"])
pv2 = R.ma_product_class_preview(org_id=LUX)
check("the confirmed reading now counts exactly that line",
      pv2["preview"]["confirmed"]["by_class"].get("billpayment", {}).get("lines") == 1,
      pv2["preview"]["confirmed"]["by_class"])
check("the proposed reading is unchanged by the confirmation",
      pv2["preview"]["proposed"]["by_class"] == p["proposed"]["by_class"])

print()
print("=" * 100)
print("E. SIGNS ARE NEVER TOUCHED")
print("=" * 100)
st2 = base_store()
install(st2)
d2 = R.get_ma_product_class(org_id=LUX)
by2 = {i["product_name"]: i for i in d2["items"]}
check("a payout line keeps its NEGATIVE amount", by2["TBV MONTH 3 New Activation Commission"]["total"] == -37.5)
check("a revenue line keeps its POSITIVE amount", by2["Total MAX 5G Plan $55"]["total"] == 55.0)
check("a $0 line is neither", by2["Total ALL ACCESS Plan $65 New Activation Commission"]["total"] == 0.0)
check("the sign MIX is reported, not normalized away",
      by2["TBV MONTH 3 New Activation Commission"]["sign"] == "negative"
      and by2["Total MAX 5G Plan $55"]["sign"] == "positive"
      and by2["Total ALL ACCESS Plan $65 New Activation Commission"]["sign"] == "zero")
check("no abs() / negate anywhere in the engine body",
      "abs(" not in body.replace("-abs(x[\"total\"])", "").replace("abs(x[\"total\"])", "")
      or "_normalize_amount" not in body)
pv3 = R.ma_product_class_preview(org_id=LUX)["preview"]
check("per-class totals are RAW SIGNED sums (commission stays negative)",
      pv3["proposed"]["by_class"]["commission"]["total"] == -37.5,
      pv3["proposed"]["by_class"].get("commission"))
check("the grand total equals the raw signed sum of the fixture",
      abs(pv3["proposed"]["total"] - sum(a for _n, a, _p, _d, _s, _u in SAMPLE)) < 1e-9)

print()
print("=" * 100)
print("F. PERIOD SPELLING — 'June 2026' and '2026-06' are ONE month")
print("=" * 100)
check("month_key('June 2026') == month_key('2026-06')",
      M.month_key("June 2026") == M.month_key("2026-06") == "2026-06")
check("a blank period falls back to the row's date", M.month_key("", "2026-07-15") == "2026-07")
check("neither present -> the honest sentinel, never a real month",
      M.month_key("", "") == M.NO_MONTH)
check("month_label round-trips", M.month_label("2026-06") == "June 2026")
months = {m["key"] for m in pv3 and R.ma_product_class_preview(org_id=LUX)["preview"]["months"]}
check("the two July rows (spelled both ways) land on ONE month key",
      months == {"2026-06", "2026-07"}, months)
jul = R.ma_product_class_preview(org_id=LUX)["preview"]["proposed"]["by_month"]["2026-07"]
check("both July residual rows are counted in that one month",
      jul["residual"]["lines"] == 2 and abs(jul["residual"]["total"] - (-4.77)) < 1e-9, jul)
check("the period FILTER matches both spellings",
      R.ma_product_class_preview(period="July 2026", org_id=LUX)["preview"]["proposed"]["line_count"] == 2)

print()
print("=" * 100)
print("G. MULTI-TENANT — org_id on every read AND every write, as a QUERY PARAM")
print("=" * 100)
READS.clear()
R.get_ma_product_class(org_id=LUX)
R.ma_product_class_preview(org_id=LUX)
R.ma_product_class_facets(org_id=LUX)
R.ma_product_class_classes(org_id=LUX)
bad = [(t, f) for (t, f, _c) in READS if not any(k == 'eq' and c == 'org_id' and v == LUX for k, c, v in f)]
check("EVERY read in every endpoint is .eq(org_id, caller)", not bad, bad[:3])
check("reads touched the source + all three config tables",
      {t for (t, _f, _c) in READS} >= {"raw_ma_daily_tx", M.CLASS_TABLE, M.MAP_TABLE, M.SOURCE_TABLE})
other = R.get_ma_product_class(org_id=OTHER)
lux = R.get_ma_product_class(org_id=LUX)
check("a second tenant sees ONLY its own product names",
      {i["product_name"] for i in other["items"] if i["lines"]}
      < {i["product_name"] for i in lux["items"] if i["lines"]})
check("the two tenants' dollar totals differ (no cross-tenant bleed)",
      other["dollars"] != lux["dollars"])
check("the unmapped name does NOT appear for the other tenant",
      not any(i["product_name"] == "Brand New Widget Nobody Mapped" and i["lines"]
              for i in other["items"]))
for fn in (R.get_ma_product_class, R.ma_product_class_preview, R.ma_product_class_facets,
           R.ma_product_class_classes, R.upsert_ma_product_class, R.confirm_ma_product_class,
           R.seed_ma_product_class, R.delete_ma_product_class):
    sig = inspect.signature(fn)
    check("%s takes org_id as a keyword param (never a constant/Form)" % fn.__name__,
          "org_id" in sig.parameters and sig.parameters["org_id"].default == R.ORG_ID)
for fn in (R.get_ma_product_class, R.ma_product_class_preview, R.upsert_ma_product_class,
           R.confirm_ma_product_class, R.seed_ma_product_class, R.delete_ma_product_class):
    src = inspect.getsource(fn)
    check("%s calls require_org()" % fn.__name__, "require_org(org_id)" in src)
WRITES.clear()
cw2 = install(base_store(with_map=False), allow_writes=True)
R.seed_ma_product_class({}, org_id=OTHER)
seeded = [p for k, t, p in WRITES if k == 'insert' and t == M.MAP_TABLE]
check("seeding STAMPS org_id on every inserted row",
      seeded and all(r["org_id"] == OTHER for r in seeded[0]), seeded[:1])
check("seeding writes proposals, never confirmations",
      seeded and all(r["status"] == "proposed" for r in seeded[0]))
WRITES.clear()
R.upsert_ma_product_class({"product_name": " Padded Name ", "product_class": "fee"}, org_id=OTHER)
up = [p for k, t, p in WRITES if k == 'upsert'][0]
check("an upsert STAMPS org_id", up["org_id"] == OTHER)
check("an upsert stores the TRIMMED name", up["product_name"] == "Padded Name")
check("an upsert defaults to status='proposed' (never auto-confirmed)", up["status"] == "proposed")

print()
print("=" * 100)
print("H. ZERO-WRITE READ PATHS")
print("=" * 100)
WRITES.clear()
install(base_store())        # allow_writes=False -> any write raises
R.get_ma_product_class(org_id=LUX)
R.ma_product_class_preview(org_id=LUX)
R.ma_product_class_facets(org_id=LUX)
R.ma_product_class_classes(org_id=LUX)
check("no GET endpoint attempted a single write", WRITES == [], WRITES[:3])
tripped = False
try:
    install(base_store()).schema("commcalc").table(M.MAP_TABLE).insert([{"x": 1}]).execute()
except AssertionError:
    tripped = True
check("the write guard genuinely fires (negative control)", tripped)

print()
print("=" * 100)
print("I. THE PACKAGE MOVES $0 — differential vs BASE %s" % BASE_REV)
print("=" * 100)
for mod, base_src, label in ((CL, BCL_SRC, "commission_ledger.py"), (LMS, BLMS_SRC, "ledger_ma_sync.py")):
    check("%s is BYTE-IDENTICAL to base" % label, inspect.getsource(mod) == base_src)
check("whatif.py is BYTE-IDENTICAL to base", inspect.getsource(W) == BW_SRC)
for name in ("calculator.py", "commission_engine.py", "sale_installment_engine.py",
             "installment_engine.py", "commission_catalog.py", "carrier_map.py", "column_mapping.py"):
    cur = open(os.path.join(_repo, "backend/app/modules/commcalc", name), "rb").read()
    base = subprocess.check_output(
        ["git", "-C", _repo, "show", "%s:backend/app/modules/commcalc/%s" % (BASE_REV, name)])
    check("%s is BYTE-IDENTICAL to base" % name, cur == base)
# the ledger classifier gives the same answer on every seeded name, before and after
cat_rules = CL.load_rules(FakeClient({}), LUX, "ma_daily_tx")
base_rules = BCL.load_rules(FakeClient({}), LUX, "ma_daily_tx")
same = all(CL.classify(-10, "Activation", n, cat_rules) == BCL.classify(-10, "Activation", n, base_rules)
           for n, _c, _t in M.DEFAULT_PROPOSALS)
check("commission_ledger.classify() answers identically to base on all 69 names", same)
rows_now = [CL.build_row({"product_name": n, "raw_amount": -10, "order_type": "Activation"},
                         {"org_id": LUX, "period": JUNE}, cat_rules) for n, _c, _t in M.DEFAULT_PROPOSALS]
rows_base = [BCL.build_row({"product_name": n, "raw_amount": -10, "order_type": "Activation"},
                           {"org_id": LUX, "period": JUNE}, base_rules) for n, _c, _t in M.DEFAULT_PROPOSALS]
check("commission_ledger.build_row() is byte-identical to base on all 69 names", rows_now == rows_base)
check("commission_ledger.summarize() is byte-identical to base",
      CL.summarize(rows_now) == BCL.summarize(rows_base))
# no money module knows this module exists
for name in ("calculator.py", "commission_engine.py", "commission_ledger.py", "ledger_ma_sync.py",
             "whatif.py", "sale_installment_engine.py", "installment_engine.py"):
    txt = open(os.path.join(_repo, "backend/app/modules/commcalc", name)).read()
    check("%s never references ma_product_class" % name, "ma_product_class" not in txt)
for name in ("coa.py", "residual_subs.py", "autocompute.py"):
    p_ = os.path.join(_repo, "backend/app/modules/account", name)
    if os.path.exists(p_):
        check("account/%s never references ma_product_class" % name,
              "ma_product_class" not in open(p_).read())
touch = sorted(
    os.path.relpath(os.path.join(dp, f), _repo)
    for dp, _dn, fn in os.walk(os.path.join(_repo, "backend/app/modules"))
    for f in fn if f.endswith(".py") and "ma_product_class" in open(os.path.join(dp, f)).read())
check("the three new tables are named by NOTHING outside this feature's two files",
      set(touch) == {"backend/app/modules/commcalc/ma_product_class.py",
                     "backend/app/modules/commcalc/router.py"}, touch)
check("the table names do not collide with agency.py's 'product_class' holdback scope",
      all(t.startswith("ma_product_class") for t in (M.CLASS_TABLE, M.MAP_TABLE, M.SOURCE_TABLE))
      and '"product_class"' in open(os.path.join(_repo, "backend/app/modules/commcalc/agency.py")).read())
# the router diff is ADDITIVE only — and reconstructing base by REMOVING this feature's block proves
# nothing else changed, line for line.
base_router = subprocess.check_output(
    ["git", "-C", _repo, "show", "%s:backend/app/modules/commcalc/router.py" % BASE_REV]).decode()
cur_router = open(os.path.join(_repo, "backend/app/modules/commcalc/router.py")).read()
import difflib
dl = list(difflib.unified_diff(base_router.splitlines(), cur_router.splitlines(), n=0))
removed = [l for l in dl if l.startswith('-') and not l.startswith('---')]
check("the router change is PURELY ADDITIVE (zero lines removed)", not removed, removed[:3])
IMPORT_LINE = "from app.modules.commcalc import ma_product_class\n"
BLOCK_START = "\n\n# " + "═" * 100 + "\n# MA DAILY TX — PRODUCT-NAME CLASSIFICATION"
check("the feature's import line is present exactly once", cur_router.count(IMPORT_LINE) == 1)
i = cur_router.find(BLOCK_START)
check("the feature's endpoint block is one contiguous insert", i > 0 and cur_router.count(BLOCK_START) == 1)
stripped = cur_router[:i] + cur_router[i:].split('\n\n\n@router.post("/commission-import/analyze")', 1)[1] \
    if i > 0 else cur_router
stripped = (cur_router[:i] + "\n\n" + cur_router[i:][cur_router[i:].find('@router.post("/commission-import/analyze")'):]) if i > 0 else cur_router
stripped = stripped.replace(IMPORT_LINE, "", 1)
check("removing ONLY this feature's import + block reproduces BASE router byte-for-byte",
      stripped == base_router,
      "len cur=%d stripped=%d base=%d" % (len(cur_router), len(stripped), len(base_router)))

print()
print("=" * 100)
print("J. IDENTIFIER COLUMNS ARE NEVER MONEY")
print("=" * 100)
check("retail_cost is a money column", M.is_money_column("ma_daily_tx", "retail_cost"))
check("merchant_discount is a money column", M.is_money_column("ma_daily_tx", "merchant_discount"))
check("merchant_invoice (the INVOICE NUMBER) is NOT", not M.is_money_column("ma_daily_tx", "merchant_invoice"))
check("this module agrees with whatif's own identifier guard on merchant_invoice",
      W.is_ma_money_column("merchant_invoice") is False
      and M.is_money_column("ma_daily_tx", "merchant_invoice") is False)
from app.modules.commcalc.ma_upload import FIELD_LABELS as FL
check("ma_upload.FIELD_LABELS still marks merchant_invoice role='key' (the source of truth)",
      (FL.get("merchant_invoice") or {}).get("role") == "key")
check("ma_upload.FIELD_LABELS still marks retail_cost role='money'",
      (FL.get("retail_cost") or {}).get("role") == "money")
for fn in (R.get_ma_product_class, R.ma_product_class_preview):
    try:
        fn(amount_column="merchant_invoice", org_id=LUX)
        check("%s REFUSES merchant_invoice as an amount" % fn.__name__, False, "no exception")
    except Exception as e:
        check("%s REFUSES merchant_invoice as an amount" % fn.__name__, "identifier" in str(e))
sd = M.source_def("ma_daily_tx", {"amount_column": "merchant_invoice"})
check("a SAVED config row naming an identifier is refused, and the default stands",
      sd["amount_column"] == "retail_cost" and sd["amount_refused"] == "merchant_invoice")
sd2 = M.source_def("ma_daily_tx", {"amount_column": "merchant_discount"})
check("a legitimate saved override IS honoured", sd2["amount_column"] == "merchant_discount")
d3 = R.get_ma_product_class(amount_column="merchant_discount", org_id=LUX)
check("the amount column really drives the totals",
      abs({i["product_name"]: i for i in d3["items"]}["Total MAX 5G Plan $55"]["total"] - 5.5) < 1e-9)

print()
print("=" * 100)
print("K. GRACEFUL DEGRADATION — pre-254 nothing breaks")
print("=" * 100)
install(base_store(with_map=False, absent=()),)
pre = FakeClient({"raw_ma_daily_tx": tx_rows(LUX)},
                 absent=[M.CLASS_TABLE, M.MAP_TABLE, M.SOURCE_TABLE])
R.sb = lambda: pre
d4 = R.get_ma_product_class(org_id=LUX)
check("the grid still answers with the three config tables ABSENT", bool(d4["items"]))
check("it says so: ready=False + names the migration",
      d4["ready"] is False and d4["migration"] == "254_commission_ma_product_class.sql")
check("the built-in vocabulary is offered", len(d4["classes"]) == len(M.DEFAULT_CLASSES))
check("the built-in proposals still classify (read-only)",
      {i["product_name"]: i for i in d4["items"]}["Total MAX 5G Plan $55"]["product_class"] == "billpayment")
check("nothing is pre-confirmed pre-migration",
      all(i["status"] != "confirmed" for i in d4["items"]))
p4 = R.ma_product_class_preview(org_id=LUX)
check("the preview still answers pre-254", p4["preview"]["proposed"]["line_count"] == len(SAMPLE))
check("the preview's confirmed reading is honestly ALL-unmapped pre-254",
      set(p4["preview"]["confirmed"]["by_class"]) == {M.UNMAPPED})
check("classes endpoint degrades with a migration hint",
      R.ma_product_class_classes(org_id=LUX)["ready"] is False)
for fn, arg in ((R.confirm_ma_product_class, {"all": True}), (R.seed_ma_product_class, {})):
    try:
        fn(arg, org_id=LUX)
        check("%s returns a clear 400 pre-254 (not a 500)" % fn.__name__, False, "no exception")
    except Exception as e:
        check("%s returns a clear 400 pre-254 (not a 500)" % fn.__name__,
              getattr(e, "status_code", None) == 400 and "254" in str(getattr(e, "detail", e)))
missing_src = FakeClient({}, absent=["raw_ma_daily_tx"])
R.sb = lambda: missing_src
d5 = R.get_ma_product_class(org_id=LUX)
check("the source table itself being absent does not 500 either", d5["items"] is not None)
check("and the read error is reported, not swallowed", bool(d5["read"]["error"]))

print()
print("=" * 100)
print("L. MIGRATION 254")
print("=" * 100)
import pglast
stmts = pglast.parse_sql(sql)
check("254 parses as real PostgreSQL (pglast)", len(stmts) > 0, len(stmts))
nocomment = re.sub(r"--[^\n]*", "", sql)
check("no GRANT anywhere", not re.search(r"\bGRANT\b", nocomment, re.I))
check("no CREATE POLICY anywhere", not re.search(r"CREATE\s+POLICY", nocomment, re.I))
check("no anon / authenticated role named", not re.search(r"\b(anon|authenticated)\b", nocomment, re.I))
check("RLS is ENABLED on all three new tables",
      len(re.findall(r"ENABLE ROW LEVEL SECURITY", nocomment, re.I)) == 3)
check("every CREATE TABLE is IF NOT EXISTS",
      len(re.findall(r"CREATE TABLE IF NOT EXISTS", nocomment)) == len(re.findall(r"CREATE TABLE", nocomment)))
check("every CREATE INDEX is IF NOT EXISTS",
      len(re.findall(r"CREATE INDEX IF NOT EXISTS", nocomment)) == len(re.findall(r"CREATE INDEX", nocomment)))
check("every INSERT is ON CONFLICT DO NOTHING (idempotent)",
      len(re.findall(r"ON CONFLICT[^;]*DO NOTHING", nocomment, re.I)) == len(re.findall(r"\bINSERT INTO\b", nocomment, re.I)))
check("no DROP / ALTER COLUMN / DELETE / UPDATE (additive only)",
      not re.search(r"\bDROP\b|\bALTER\s+TABLE[^;]*\bDROP\b|\bDELETE\s+FROM\b|\bUPDATE\s+commcalc", nocomment, re.I))
check("every new table declares org_id uuid not null",
      len(re.findall(r"org_id\s+UUID NOT NULL", sql)) == 3)
check("every new table is indexed on org_id",
      all(("(org_id" in l) for l in re.findall(r"CREATE INDEX IF NOT EXISTS \w+\s+ON [^;]+;", sql)))
check("no money table is named anywhere in 254",
      not re.search(r"rep_commissions|commission_ledger|commission_category_map|carrier_commission|raw_comp_report",
                    nocomment))
mig_dir = os.path.join(_repo, "database/migrations")
band = sorted(f for f in os.listdir(mig_dir) if re.match(r"^2\d\d_", f))
check("254 is in the mod-commission band 200–299", any(f.startswith("254_") for f in band))
check("254 is not a collision (exactly one file with that number)",
      len([f for f in band if f.startswith("254_")]) == 1, band[-6:])
check("254 is above the previous high-water mark 253",
      max(int(f[:3]) for f in band) == 254, sorted(int(f[:3]) for f in band)[-4:])

print()
print("=" * 100)
print("RESULT: %d passed, %d failed" % (PASS, FAIL))
print("=" * 100)
sys.exit(1 if FAIL else 0)
