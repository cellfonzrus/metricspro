"""§CORE-xx — A PAGE LOAD NEVER QUERIES ONCE PER ITEM, AND THE BACKLOG ONLY SHRINKS.

The 1,000-user load test (2026-10-28) established that this platform queues rather than crashes,
and that what it queues on is ROUND TRIPS: every read is an HTTPS call to Supabase costing ~15-25 ms
while holding one of the `workers x 40` worker threads. So

    max requests/sec  ~=  (workers x 40) / (round trips per request x round trip time)

and the number of round trips a page makes is a capacity decision, not a style preference.

THE SHAPE THIS REFUSES
----------------------
An **N+1**: a query inside a loop, filtered by the loop variable.

    for key in keys:                                               # 20 keys
        client.table('t').select('*').eq('key', key).execute()     # 20 round trips

It looks fine on three rows of test data and grows with production data, which is always bigger.

WHAT IS *NOT* AN N+1, AND WHY EACH EXEMPTION IS SAFE
----------------------------------------------------
Four loop shapes contain a query and cost a FIXED number of trips. §A plants a control for each, so
an exemption cannot quietly widen into a licence:

  1. a literal tuple/list/set            `for tbl, col in (('a','x'), ('b','y')):`
  2. `range(...)` / `while` pagination   one trip per PAGE, which is the point of paging
  3. a module-level constant collection  `for tbl, col in _UNMATCHED_SRCS:` — a list of TABLES
  4. a chunked batch read                `for chunk in chunks(ids): ....in_('id', chunk)`
     — the loop variable reaches `.in_`, not `.eq`. This is the FIX, not the defect: one trip per
     100-200 items instead of one per item. Flagging it would push people away from the right answer.

THE TWO GATES
-------------
  §B  **GET handlers: exactly zero.** A page load is what a thousand users wait on. There is no
      allowance and no pinned list here — a new one fails the build the day it is written.
  §C  **Everywhere else: a pinned per-file inventory that may only SHRINK.** Writes are one user and
      one click, so they are real but not urgent; they are REPORTED, never hidden, and the count
      cannot grow. A file that improves must update its pin, which is the only way the list moves.

Pins are DERIVED — regenerate with `--bless` after fixing something, never edit by hand.
"""
import ast, collections, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app")

# ── derived pins: file -> number of per-item queries outside a GET handler. MAY ONLY SHRINK ──────
PINNED = {
    "app/core/column_tolerant.py": 1,
    "app/core/encryption_backfill.py": 6,
    "app/modules/account/autocompute.py": 1,
    "app/modules/approvals/engine.py": 4,
    "app/modules/asset/router.py": 2,
    "app/modules/billing/ai_meter.py": 1,
    "app/modules/billing/router.py": 1,
    "app/modules/chat/router.py": 3,
    "app/modules/closing/router.py": 2,
    "app/modules/commcalc/agency.py": 3,
    "app/modules/commcalc/b2b_sweep.py": 1,
    "app/modules/commcalc/import_audit.py": 1,
    "app/modules/commcalc/ma_overview.py": 1,
    "app/modules/commcalc/ma_recon.py": 1,
    "app/modules/commcalc/report_kinds.py": 1,
    "app/modules/commcalc/router.py": 27,
    "app/modules/commcalc/vip_sweep.py": 1,
    "app/modules/commcalc/whatif.py": 1,
    "app/modules/core/ai_gate.py": 1,
    "app/modules/core/control_box_api.py": 1,
    "app/modules/core/import_health.py": 1,
    "app/modules/core/onboarding.py": 1,
    "app/modules/core/router.py": 3,
    "app/modules/crm/router.py": 4,
    "app/modules/helpdesk/router.py": 1,
    "app/modules/hr/letters.py": 1,
    "app/modules/hr/router.py": 1,
    "app/modules/notify/router.py": 1,
    "app/modules/pos/catalog_suggest.py": 2,
    "app/modules/pos/commcalc_feed.py": 2,
    "app/modules/pos/inventory_integrity_router.py": 2,
    "app/modules/pos/receipt_import.py": 1,
    "app/modules/pos/router.py": 1,
    "app/modules/remediation/playbooks.py": 2,
    "app/modules/remediation/router.py": 1,
    "app/modules/storeops/payroll_approval.py": 3,
    "app/modules/storeops/router.py": 7,
    "app/modules/supply/store.py": 3,
    "app/modules/vision/retention.py": 1,
    "app/modules/vision/router.py": 2,
    "app/scripts/backfill_receipt_import_encryption.py": 1,
}

PASS, FAIL = [], []


def ok(label, cond, detail=""):
    (PASS if cond else FAIL).append((label, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{('  [' + detail + ']') if detail else ''}")


# ── the scanner ──────────────────────────────────────────────────────────────────────────────────
def _module_consts(tree):
    out = set()
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
            if isinstance(n.value, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
                out |= {t.id for t in tgts if isinstance(t, ast.Name)}
    return out


def _tnames(t):
    if isinstance(t, ast.Name):
        return {t.id}
    if isinstance(t, (ast.Tuple, ast.List)):
        s = set()
        for e in t.elts:
            s |= _tnames(e)
        return s
    return set()


def _reaches_in(call, names):
    """True when the loop variable is handed to `.in_` somewhere in the query chain — a chunked
    batch read, which is the shape we WANT."""
    n = call
    while isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
        if n.func.attr == "in_" and len(n.args) >= 2:
            if {x.id for x in ast.walk(n.args[1]) if isinstance(x, ast.Name)} & names:
                return True
        n = n.func.value
    return False


class _Scan(ast.NodeVisitor):
    def __init__(self, consts):
        self.consts, self.stack, self.fn, self.hits, self.routes = consts, [], [], [], {}

    def visit_FunctionDef(self, n):
        for d in n.decorator_list:
            if (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                    and d.func.attr in ("get", "post", "put", "delete", "patch")):
                self.routes[n.name] = d.func.attr.upper()
        self.fn.append(n.name)
        self.generic_visit(n)
        self.fn.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def _fixed(self, it):
        if isinstance(it, (ast.Tuple, ast.List, ast.Set)):
            return True
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range":
            return True
        if isinstance(it, ast.Name) and it.id in self.consts:
            return True
        if isinstance(it, ast.Attribute) and it.attr.isupper():
            return True
        return False

    def _loop(self, node, names, fixed):
        self.stack.append((names, fixed))
        self.generic_visit(node)
        self.stack.pop()

    def visit_For(self, n):
        self._loop(n, _tnames(n.target), self._fixed(n.iter))

    visit_AsyncFor = visit_For

    def visit_While(self, n):
        self._loop(n, set(), True)

    def visit_ListComp(self, n):
        g = n.generators[0]
        self._loop(n, _tnames(g.target), self._fixed(g.iter))

    visit_SetComp = visit_GeneratorExp = visit_DictComp = visit_ListComp

    def visit_Call(self, node):
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "execute" and self.stack:
            used = {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}
            for names, fixed in self.stack:
                if fixed or not names or not (used & names):
                    continue
                if not _reaches_in(node, names):
                    self.hits.append((node.lineno, self.fn[-1] if self.fn else "?"))
                break
        self.generic_visit(node)


def scan_source(src):
    tree = ast.parse(src)
    s = _Scan(_module_consts(tree))
    s.visit(tree)
    return s.hits, s.routes


def scan_tree(root=APP):
    per_file, gets = {}, []
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, HERE).replace(os.sep, "/")
            try:
                hits, routes = scan_source(open(p, encoding="utf-8").read())
            except Exception:
                continue
            non_get = []
            for ln, func in hits:
                if routes.get(func) == "GET":
                    gets.append((rel, ln, func))
                else:
                    non_get.append((ln, func))
            if non_get:
                per_file[rel] = len(non_get)
    return per_file, gets


if "--bless" in sys.argv:
    per_file, gets = scan_tree()
    if gets:
        print("REFUSING to bless: a GET handler has a per-item query. §B has no pinned list — "
              "fix it instead.")
        for rel, ln, func in gets:
            print(f"  {rel}:{ln}  {func}")
        sys.exit(1)
    body = "\n".join(f'    "{k}": {v},' for k, v in sorted(per_file.items()))
    src = open(__file__, encoding="utf-8").read()
    a = src.index("PINNED = {\n") + len("PINNED = {\n")
    b = src.index("}\n", a)
    open(__file__, "w", encoding="utf-8").write(src[:a] + body + "\n" + src[b:])
    print(f"blessed: {len(per_file)} files, {sum(per_file.values())} per-item queries")
    sys.exit(0)

print(__doc__.split("\n")[0])
print()

# ── A. every exemption has its own planted control ───────────────────────────────────────────────
print("A. each exemption is proved, so it cannot widen into a licence")
CASES = [
    ("a literal tuple of tables is FIXED cost",
     "def f(c, o):\n for t, col in (('a','x'), ('b','y')):\n  c.table(t).select(col).eq('org_id', o).execute()\n", 0),
    ("`range(...)` paging is FIXED cost",
     "def f(c, o):\n for i in range(0, 10):\n  c.table('t').select('*').range(i, i+9).eq('org_id', o).execute()\n", 0),
    ("a module-level constant collection is FIXED cost",
     "SRCS = (('a','x'), ('b','y'))\ndef f(c, o):\n for t, col in SRCS:\n  c.table(t).select(col).eq('org_id', o).execute()\n", 0),
    ("a CHUNKED batch read is the fix, never a finding",
     "def f(c, o, ids):\n for chunk in [ids[i:i+100] for i in range(0, len(ids), 100)]:\n  c.table('t').select('*').eq('org_id', o).in_('id', chunk).execute()\n", 0),
    ("a query filtered by the loop variable IS a finding",
     "def f(c, o, ids):\n for i in ids:\n  c.table('t').select('*').eq('org_id', o).eq('id', i).execute()\n", 1),
    ("...in a comprehension too",
     "def f(c, o, ids):\n return [c.table('t').select('*').eq('id', i).execute() for i in ids]\n", 1),
    ("...and nested two loops deep",
     "def f(c, ids):\n for a in ids:\n  for b in a:\n   c.table('t').select('*').eq('id', b).execute()\n", 1),
    ("a query that ignores the loop variable is not a finding",
     "def f(c, o, ids):\n for i in ids:\n  c.table('t').select('*').eq('org_id', o).execute()\n", 0),
]
for label, src, want in CASES:
    hits, _ = scan_source(src)
    ok(f"A control: {label}", len(hits) == want, f"{len(hits)} found, wanted {want}")

ok("A the GET classifier reads the decorator, not the name",
   scan_source("import r\n@r.router.get('/x')\ndef h(c, ids):\n"
               " for i in ids:\n  c.table('t').eq('id', i).execute()\n")[1].get("h") == "GET")

per_file, gets = scan_tree()

# ── B. a page load never queries once per item ───────────────────────────────────────────────────
print("\nB. GET handlers — exactly zero, with no allowance")
ok("B1 no GET handler queries once per item",
   not gets,
   "; ".join(f"{r}:{l} {f}" for r, l, f in gets) if gets else
   "every page load is a fixed number of reads")
ok("B2 the scan actually reached the app (it is not vacuously empty)",
   sum(per_file.values()) > 0, f"{sum(per_file.values())} findings outside GET handlers")

# ── C. everywhere else: pinned, shrink-only ──────────────────────────────────────────────────────
print("\nC. writes and sweeps — reported, pinned, and the list may only shrink")
grew = {f: (n, PINNED.get(f, 0)) for f, n in per_file.items() if n > PINNED.get(f, 0)}
ok("C1 no file gained a per-item query",
   not grew,
   "; ".join(f"{f} {was}->{now}" for f, (now, was) in grew.items()) if grew else
   f"{len(per_file)} files, {sum(per_file.values())} queries, pin {sum(PINNED.values())}")
stale = {f: (PINNED[f], per_file.get(f, 0)) for f in PINNED if per_file.get(f, 0) < PINNED[f]}
ok("C2 no pin is stale — a file that improved has updated its pin (`--bless`)",
   not stale,
   "; ".join(f"{f} pinned {was} but now {now}" for f, (was, now) in stale.items()) if stale
   else "every pin matches")
ok("C3 the pin total may only go down",
   sum(per_file.values()) <= sum(PINNED.values()),
   f"{sum(per_file.values())} now, {sum(PINNED.values())} pinned")

print()
print(f"{'PASS' if not FAIL else 'RED '}: {len(PASS)} passed, {len(FAIL)} failed")
for label, detail in FAIL:
    print(f"  FAIL  {label}\n        {detail}")
sys.exit(1 if FAIL else 0)
