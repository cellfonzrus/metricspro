"""LOCK — a surface may OFFER the multi-month option only by asking THE predicate (owner 2026-09-25: "if multi
month is not confgured then it should not be shown as an available option"). stdlib only; static scan.

THE PREDICATE: backend `commcalc/multimonth_config` (`schedule_counts` / `money_by_period` / `decide` / `load`,
served by `GET /commcalc/multimonth/status`); frontend `_lib/multimonthOffer.ts` (`multimonthOffered`,
`multimonthRows`) read through `_lib/multimonth.ts` (`useMultimonthStatus`).

FAILS THE BUILD WHEN:
  (a) the backend home stops reading BOTH schedule tables org-scoped and active-only, or the R1 pay-source
      guard stops counting schedules through `schedule_counts` (two answers to "is there a schedule");
  (b) a frontend file that OFFERS multi-month — renders the multi-month rows / section / copy, or reads the
      multi-month pay columns — does not ask the predicate (`multimonthOffered(` / `multimonthRows(` /
      `multimonthRowsFor`), unless it is EXCUSED below with its reason (and the excuse is still true);
  (c) the predicate's frontend half stops keeping money visible (`multimonthRows` must show a non-zero amount
      whatever the status) or the hook stops defaulting to OFFERED on a failed read;
  (d) negative controls — each planted violation turns this lock RED.

    python3 backend/harness_multimonth_offer_lock.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")
FE = os.path.join(os.path.dirname(ROOT), "frontend", "src")
HOME = "modules/commcalc/multimonth_config.py"
FE_HOME = "app/(platform)/commcalc/_lib/multimonthOffer.ts"
FE_HOOK = "app/(platform)/commcalc/_lib/multimonth.ts"
OFFERS = re.compile(r"multi[\-‑ ]?month|multimonth|installment_comm_sale|residual_installment_comm", re.I)
ASKS = ("multimonthOffered(", "multimonthRows(", "multimonthRowsFor")
# files that mention multi-month but do not OFFER it as an option of a rep's pay — each with WHY
EXCUSED = {
    "app/(platform)/commcalc/plan-installments/page.tsx":
        "the multi-month CONFIGURATION page — where a schedule is created; hiding it would make 'off' permanent",
    "app/(platform)/commcalc/payout-schedules/page.tsx":
        "the residual schedule CONFIGURATION page (mig 057) — same reason",
    "app/(platform)/configurations/page.tsx": "the settings directory's link to the configuration page",
    "lib/rbac.ts": "the NAV entry of the configuration page (tileOnly)",
    "app/(platform)/commcalc/page.tsx":
        "the commission hub's diagnostic copy + link to configure schedules (a missing trigger is the diagnosis)",
    "app/(platform)/commcalc/pay-simulator/_components/PaySimulator.tsx":
        "renders its multi-month block only when the backend returns multimonth levers, which exist only for the "
        "org's ACTIVE plan_installment_schedule rows (`if (levers.length === 0) return null`)",
    "app/(platform)/commcalc/ma-upload/page.tsx": "'historical (multi-month)' is an UPLOAD mode, not a pay option",
}
P = F = 0


def check(label, ok, detail=""):
    global P, F
    if ok:
        P += 1
        print("  PASS  %s" % label)
    else:
        F += 1
        print("  FAIL  %s   %s" % (label, str(detail)[:400]))


def read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def walk(root, exts):
    out = {}
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules", ".next")]
        for f in fs:
            if f.endswith(exts):
                p = os.path.join(dp, f)
                out[os.path.relpath(p, root).replace(os.sep, "/")] = read(p)
    return out


def ts_code(src):
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    src = re.sub(r"\{/\*[\s\S]*?\*/\}", "", src)
    return "\n".join(ln.split("//", 1)[0] if "://" not in ln else ln for ln in src.split("\n"))


def py_code(src):
    src = re.sub(r'"""[\s\S]*?"""', '""', src)
    return "\n".join(ln.split("#", 1)[0] for ln in src.split("\n"))


def fn_body(src, name):
    m = re.search(r"^(?:async\s+)?def\s+%s\s*\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    rest = src[m.start():]
    nxt = re.search(r"^(?:@router\.|def |class |async def )", rest[m.end() - m.start():], re.M)
    return rest[: (m.end() - m.start()) + nxt.start()] if nxt else rest


def scan(be, fe):
    v = []
    home = py_code(be.get(HOME, ""))
    for t in ('"payout_schedule"', '"plan_installment_schedule"'):
        if t not in home:
            v.append(("a", HOME, "no longer reads " + t))
    if '.eq("org_id", org_id)' not in home or '.eq("is_active", True)' not in home:
        v.append(("a", HOME, "schedule_counts is not org-scoped + active-only"))
    guard = py_code(fn_body(be.get("modules/commcalc/router.py", ""), "_has_any_pay_source"))
    if "_mmc.schedule_counts(" not in guard:
        v.append(("a", "router.py", "_has_any_pay_source counts schedules on its own (not through schedule_counts)"))
    if "'payout_schedule'" in guard or "'plan_installment_schedule'" in guard:
        v.append(("a", "router.py", "_has_any_pay_source still spells a schedule table"))
    stale = []
    for rel, src in sorted(fe.items()):
        if rel in (FE_HOME, FE_HOOK):
            continue
        code = ts_code(src)
        offers = bool(OFFERS.search(code))
        asks = any(a in code for a in ASKS)
        if offers and not asks and rel not in EXCUSED:
            v.append(("b", rel, "offers multi-month without asking the predicate"))
    for rel in EXCUSED:
        if rel not in fe or not OFFERS.search(ts_code(fe[rel])):
            stale.append(rel)
    feh = ts_code(fe.get(FE_HOME, ""))
    if "(instSale || 0) !== 0 ||" not in feh or "offered !== false" not in feh:
        v.append(("c", FE_HOME, "multimonthRows no longer keeps money visible / multimonthOffered no longer defaults open"))
    hook = ts_code(fe.get(FE_HOOK, ""))
    if "useState<MultimonthStatus>(MULTIMONTH_UNKNOWN)" not in hook or ".catch(() => { if (alive) setS(MULTIMONTH_UNKNOWN) })" not in hook:
        v.append(("c", FE_HOOK, "the hook no longer defaults to OFFERED on load / on a failed read"))
    return v, stale


be = walk(APP, (".py",))
fe = walk(FE, (".ts", ".tsx"))
viol, stale = scan(be, fe)
for part, label in (("a", "(a) ONE backend home reads both schedule tables org-scoped + active; the R1 guard counts through it"),
                    ("b", "(b) every frontend surface that offers multi-month asks the predicate (or is excused by name)"),
                    ("c", "(c) money is never hidden; a failed / pending read keeps the option offered")):
    check(label, not [x for x in viol if x[0] == part], [x[1:] for x in viol if x[0] == part])
check("(b) every excuse is still true (the excused file still mentions multi-month)", not stale, stale)

print("\n  negative controls")


def planted(be_mut=None, fe_mut=None):
    b2, f2 = dict(be), dict(fe)
    b2.update(be_mut or {})
    f2.update(fe_mut or {})
    return scan(b2, f2)


v, _ = planted(fe_mut={"app/(platform)/commcalc/new-report/page.tsx":
                       "export default function X(){ return <div>Multi-month installments {fmt(r.installment_comm_sale)}</div> }"})
check("(d) a new page offering multi-month without asking → RED", any(x[0] == "b" and "new-report" in x[1] for x in v))
v, _ = planted(fe_mut={"app/(platform)/commcalc/reports/page.tsx":
                       fe["app/(platform)/commcalc/reports/page.tsx"].replace("multimonthRows(", "localRows(").replace("multimonthOffered(", "localOffered(").replace("multimonthRowsFor", "rowsFor")})
check("(d) the reports page dropping the predicate → RED", any(x[0] == "b" and "reports" in x[1] for x in v))
v, _ = planted(be_mut={"modules/commcalc/router.py": be["modules/commcalc/router.py"].replace(
    "_mmc.schedule_counts(client, org_id)", "{'payout_schedule': 0}")})
check("(d) the R1 guard counting on its own → RED", any(x[0] == "a" for x in v))
v, _ = planted(fe_mut={FE_HOME: fe[FE_HOME].replace("(instSale || 0) !== 0 ||", "")})
check("(d) a predicate that hides money → RED", any(x[0] == "c" for x in v))
v, s = planted(fe_mut={"app/(platform)/commcalc/ma-upload/page.tsx": "export default function X(){ return null }"})
check("(d) a stale excuse → RED", "app/(platform)/commcalc/ma-upload/page.tsx" in s)
v, s = planted()
check("(d) the unmodified tree is GREEN", not v and not s)

print("\n%d passed, %d failed" % (P, F))
if F:
    sys.exit(1)
print("OK — multi-month is offered only through the one predicate; money is never hidden.")
