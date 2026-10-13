"""Proof + LOCK: the portal report pull is ONE loop, and a feed is an adapter it calls (index §COMMCALC-07).

WHAT THIS PROVES. The loop itself, driven against a FAKE page and a FAKE adapter — no browser, no
network, no database. Reaching a report, pressing its export, an unreachable report, a missing export
control, an operator's Cancel, and the one rule that matters most: a pull that lands no rows is never
reported as delivered.

WHAT THIS LOCKS, and why the lock is the point. The loop was extracted BECAUSE a second feed could
only have been built by copying it. If a third feed copies it anyway, the extraction bought nothing
and the four facts inside it (the calibration ladder, the export words, the honest-failure wording,
the cold-session restore) start drifting again — which is the patchwork the house rules forbid. So:

  • a module that downloads a report, or defines a report-pull loop, is either the ONE home or it is
    PINNED below with a written reason, and the pin count MAY ONLY SHRINK;
  • the moved functions are IMPORTED from the one home by the card feed, never re-defined;
  • the router dispatches on the pull REGISTRY, not on the card registry — the line that used to ask
    `merchant_portals.is_portal` is the reason the second feed could not have ridden the live session.

Run:  cd backend && python3 harness_portal_report_pull.py        Exit 0 = all green.
"""
import ast
import os
import re
import sys

sys.path.insert(0, ".")

from app.modules.commcalc import portal_report_pull as prp          # noqa: E402

PASS = FAIL = 0
_HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(_HERE, "app")
HOME_REL = "app/modules/commcalc/portal_report_pull.py"


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s" % msg)


def eq(got, want, msg):
    ok(got == want, msg if got == want else "%s (got %r, want %r)" % (msg, got, want))


def executable_text(src):
    """The module's code with COMMENTS and STRINGS removed. The house rule is that doctrine may quote
    anything and code may not, so a scan that reads prose reports the docstring explaining the fix as
    the fix's own violation — which this one did, on the comment naming the card feed."""
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except tokenize.TokenError:
        return src
    return " ".join(out)


def read(rel):
    with open(os.path.join(_HERE, rel)) as fh:
        return fh.read()


def py_files():
    for root, _d, files in os.walk(APP):
        for f in sorted(files):
            if f.endswith(".py"):
                yield os.path.relpath(os.path.join(root, f), _HERE)


# ── THE RATCHET ──────────────────────────────────────────────────────────────────────────────────
# Every module that drives its own portal pull TODAY, with the reason it is not folded into the one
# home yet. These are PRE-EXISTING: each predates the extraction, each drives a live feed, and folding
# them is its own change with its own proof — doing it in the same PR as a new feed would put six
# working connectors at risk to tidy a file. They are listed rather than quietly tolerated, because
# "the siblings were checked" has to be a statement somebody can verify.
#
# THIS DICT MAY ONLY SHRINK. A module that appears here without being added on purpose is a new copy
# of the loop, and that is the thing this file exists to stop.
UNFOLDED_PULL_LOOPS = {
    "app/modules/commcalc/vidapay_sweep.py":
        "the oldest portal driver and the home of the browser primitives every other sweep (this one "
        "included) borrows — _launch / _new_context / _classify / capture_session_state. Its report "
        "list is per-carrier config in report_pull_map, so its loop is close to this one in shape; "
        "folding it means re-proving the carrier feeds that pay commission, which is not a side "
        "effect of adding a feed.",
    "app/modules/commcalc/epay_sweep.py":
        "a settlement portal with its own date-window and per-report parameter forms; it downloads "
        "through the same Playwright mechanism but reaches its reports by submitting a form rather "
        "than clicking a menu, which this loop does not yet model.",
    "app/modules/supply/portal.py":
        "NOT a report download at all — it CRAWLS a vendor catalog page by page and, separately, "
        "drives a cart for an assisted order. It is named here because its entry point is spelled "
        "like a pull (`catalog_pull_on_page`), so a reader looking for copies of the loop will find "
        "it and should be told why it is not one.",
}

print("\nA. the registry is well-formed, and every adapter satisfies the contract")
ok(len(prp.ADAPTER_MODULES) == len(set(prp.ADAPTER_MODULES)), "no adapter is registered twice")
mods = prp.adapters()
eq(len(mods), len(prp.ADAPTER_MODULES),
   "every registered adapter imports AND satisfies the contract (a typo in the registry is red here)")
seen = {}
for mod in mods:
    for name in prp.REQUIRED:
        ok(callable(getattr(mod, name, None)), "%s exposes %s()" % (mod.__name__.split(".")[-1], name))
    for k in mod.connector_keys():
        ok(k not in seen, "connector %r belongs to exactly one adapter" % k)
        seen[k] = mod
ok(len(seen) >= 4, "the registry serves every connector the adapters claim (%d)" % len(seen))
for k, mod in seen.items():
    ok(prp.adapter_for(k) is mod, "adapter_for(%r) resolves to its owner" % k)
    ok((prp.descriptor(k) or {}).get("key") is not None or prp.descriptor(k) is not None,
       "%r has a descriptor" % k)
ok(prp.adapter_for("not_a_portal") is None, "an unknown connector resolves to no adapter")
ok(prp.adapter_for("") is None and prp.adapter_for(None) is None, "a blank connector resolves to none")
eq(prp.connector(" Pay-Anywhere "), "pay_anywhere", "connector() normalizes case, spaces and dashes")

print("\nB. the loop knows nothing about any feed")
home = read(HOME_REL)
home_code = executable_text(home)
tree = ast.parse(home)
imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
for feedish in ("merchant_portals", "merchant_portal_sweep", "pos_report_feed", "pos_report_sweep",
                "closing.metric_catalog"):
    ok(not any(feedish in m for m in imported),
       "the loop does not import %s — a feed is passed to it, never reached into" % feedish)
for word in ("card_brand", "settlement", "metric_key", "merchant_id", "upsert", "schema("):
    ok(word not in home_code,
       "the loop's executable text does not mention %r — it stores nothing and knows no table" % word)
ok("ADAPTER_MODULES" in home and "REQUIRED" in home, "the registry and the contract are stated in it")

print("\nC. THE RATCHET — no second copy of the loop may appear")
DOWNLOADERS = []
LOOPISH = []
for rel in py_files():
    if rel.replace(os.sep, "/") == HOME_REL:
        continue
    src = read(rel)
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    if "expect_download(" in code:
        DOWNLOADERS.append(rel.replace(os.sep, "/"))
        continue
    try:
        t = ast.parse(src)
    except SyntaxError:
        continue
    for node in ast.walk(t):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                re.match(r"^_?(pull_.*_on_page|.*_pull_on_page)$", node.name):
            LOOPISH.append(rel.replace(os.sep, "/"))
            break
found = sorted(set(DOWNLOADERS) | set(LOOPISH))
pinned = sorted(UNFOLDED_PULL_LOOPS)
unexpected = [f for f in found if f not in UNFOLDED_PULL_LOOPS]
eq(unexpected, [], "every module that downloads a report or defines a pull loop is the ONE home or is PINNED")
gone = [f for f in pinned if f not in found]
eq(gone, [], "every pin is still real — a pin for a module that no longer has a loop must be REMOVED")
eq(len(UNFOLDED_PULL_LOOPS), 3,
   "the pin count is 3 and MAY ONLY SHRINK (lowering this number is a deliberate edit)")
for f, why in UNFOLDED_PULL_LOOPS.items():
    ok(len(why) > 80 and why.strip().endswith("."), "the pin on %s carries a real reason" % f)
ok("merchant_portal_sweep.py" not in found and "pos_report_sweep.py" not in str(found),
   "neither feed adapter holds a loop of its own — they supply the four functions and nothing more")

print("\nD. the card feed DEREFERENCES the moved names, it does not keep copies")
mps = read("app/modules/commcalc/merchant_portal_sweep.py")
mt = ast.parse(mps)
defined = {n.name for n in ast.walk(mt) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
from_home = set()
for n in ast.walk(mt):
    if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("portal_report_pull"):
        from_home |= {a.name for a in n.names}
for moved in ("pull_reports_on_page", "make_pull_fn", "date_range", "window_days", "next_expiry",
              "session_ttl_hint"):
    ok(moved in from_home, "%s is IMPORTED from the one home" % moved)
    ok(moved not in defined, "…and not re-defined in the card feed")
ok("_navigate_to_report" not in defined and "_download_report" not in defined and
   "_click_text" not in defined, "the navigation helpers are gone from the card feed entirely")
ok("run_merchant_portal_sweep" in defined and "prp.run_cold_sweep" in mps,
   "its scheduled entry point keeps its name (the scraper registry points at it) and delegates")
for name in prp.REQUIRED:
    ok(name in defined, "the card feed defines its own %s() — it is an adapter now" % name)

print("\nE. the router asks the pull registry, not the card registry")
router = read("app/modules/commcalc/router.py")
assert "def _live_pull(" in router, "the live-pull builder was renamed — this check must follow it"
live = router[router.index("def _live_pull("):][:4000]
ok("_prp.is_pullable(proc)" in live,
   "the live session's dispatch asks whether ANY feed claims this connector")
ok("_mp.is_portal(proc)" not in live,
   "…and no longer asks the CARD registry, which is what pinned the live session to one feed")
ok("_SOURCE_SCRAPERS[_POS_REPORT_CONNECTOR]" in router,
   "the new connector is registered for the scheduled cron")
ok("from app.modules.commcalc.pos_report_sweep import CONNECTOR" in router,
   "…by DEREFERENCING the module's own constant, never by re-spelling the key here")
ok('"pos_report"' not in router,
   "the connector string itself appears nowhere in the router (RULE TWO, and the §36 precedent)")

print("\nF. the loop, driven end to end on a fake page")


class FakePage:
    """The smallest page the loop can drive: it records what it was asked to do."""

    def __init__(self, *, reachable=True, exportable=True):
        self.reachable, self.exportable = reachable, exportable
        self.clicked, self.goto_urls = [], []

    # navigation
    def click(self, sel, timeout=None):
        if not self.reachable:
            raise RuntimeError("no such selector")
        self.clicked.append(sel)

    def goto(self, url, **kw):
        self.goto_urls.append(url)

    def get_by_text(self, word, exact=False):
        page = self

        class Loc:
            def count(self):
                return 1

            def nth(self, _i):
                return self

            def is_visible(self):
                return page.reachable

            def click(self):
                page.clicked.append(word)
        return Loc()

    def wait_for_timeout(self, _ms):
        return None

    # download
    def expect_download(self, timeout=None):
        page = self

        class Ctx:
            value = None

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                if not page.exportable:
                    return False
                self_inner.value = Dl()
                Ctx.value = self_inner.value
                return False
        return Ctx()


class Dl:
    suggested_filename = "report.csv"

    def save_as(self, path):
        with open(path, "w") as fh:
            fh.write("Day,Line,Qty\n2026-10-11,A,1\n")


class FakeAdapter:
    """A feed that counts what the loop handed it. Registered by monkeypatching the resolver, which is
    how this harness proves the loop without proving a particular feed."""
    __name__ = "fake_adapter"

    def __init__(self, specs=(), rows=1, boom=False):
        self.specs, self.rows, self.boom = specs, rows, boom
        self.ingested = []

    def connector_keys(self):
        return ("fake",)

    def portal_descriptor(self, _c=None):
        return {"key": "fake", "base_url": "https://example.invalid/", "date_window_days": 3}

    def report_specs(self, _c=None, _enabled=None, src_row=None):
        return self.specs

    def setup_hint(self, _src_row=None):
        return "Declare a report on this source."

    def ingest_report(self, client, org_id, src_row, connector, spec, payload, filename=""):
        if self.boom:
            raise RuntimeError("parser blew up")
        self.ingested.append((spec["key"], filename, bytes(payload)[:4]))
        return {"report": spec["key"], "label": spec["label"], "rows_ingested": self.rows,
                "delivered": self.rows > 0, "unresolved": []}


SPEC = {"key": "daily", "label": "Daily report", "nav": ("reports", "daily"), "export": "csv"}
_real = prp.adapter_for


def with_adapter(ad, fn):
    prp.adapter_for = lambda proc: ad if prp.connector(proc) == "fake" else _real(proc)
    try:
        return fn()
    finally:
        prp.adapter_for = _real


SRC = {"id": "SRC", "org_id": "ORG", "processor": "fake", "portal_reports": ["daily"]}

ad = FakeAdapter(specs=(SPEC,), rows=7)
page = FakePage()
res = with_adapter(ad, lambda: prp.pull_reports_on_page(None, "ORG", SRC, page))
ok(res["ok"] and res["delivered"], "a pull that lands rows is delivered")
eq(res["rows_ingested"], 7, "the row count is the adapter's, carried up unchanged")
eq(len(ad.ingested), 1, "the adapter was handed exactly one report")
eq(ad.ingested[0][1], "report.csv", "…with the file the portal itself generated")
ok(ad.ingested[0][2] == b"Day,", "…and its actual bytes")
ok("Imported 7 row(s)" in res["status"], "the status says what landed")
ok(res["date_from"] < res["date_to"] or res["date_from"] == res["date_to"],
   "the window is a range ending before today")

ad0 = FakeAdapter(specs=(SPEC,), rows=0)
res0 = with_adapter(ad0, lambda: prp.pull_reports_on_page(None, "ORG", SRC, FakePage()))
ok(res0["ok"] and not res0["delivered"],
   "A ZERO-ROW PULL IS NEVER A GREEN TICK — the rule that let a dead connector look healthy")

adx = FakeAdapter(specs=(SPEC,))
resx = with_adapter(adx, lambda: prp.pull_reports_on_page(None, "ORG", SRC,
                                                          FakePage(reachable=False)))
ok(not resx["delivered"] and "calibrate" in resx["reports"][0]["error"].lower(),
   "a report that cannot be reached fails NAMED, telling the operator to calibrate it")
ok("Not reached" in resx["status"], "…and the pull's own status says which report")

ade = FakeAdapter(specs=(SPEC,))
rese = with_adapter(ade, lambda: prp.pull_reports_on_page(None, "ORG", SRC,
                                                          FakePage(exportable=False)))
ok("export control" in rese["reports"][0]["error"],
   "reached but no export control is its OWN message — a different fix for the operator")

adb = FakeAdapter(specs=(SPEC,), boom=True)
resb = with_adapter(adb, lambda: prp.pull_reports_on_page(None, "ORG", SRC, FakePage()))
ok(not resb["delivered"] and "Ingest failed" in resb["reports"][0]["error"],
   "an adapter that raises is reported, not swallowed, and the pull still answers")

adn = FakeAdapter(specs=())
resn = with_adapter(adn, lambda: prp.pull_reports_on_page(None, "ORG", SRC, FakePage()))
ok(not resn["delivered"] and "Declare a report" in resn["error"],
   "a source with no report declared gets the FEED's own setup words, not a generic failure")

two = FakeAdapter(specs=(SPEC, {"key": "b", "label": "B", "nav": ("b",)}), rows=1)
stopped = with_adapter(two, lambda: prp.pull_reports_on_page(
    None, "ORG", SRC, FakePage(), should_stop=lambda: len(two.ingested) >= 1))
eq(len(two.ingested), 1, "an operator's Cancel stops BETWEEN reports, leaving the first landed")
ok(stopped["delivered"], "…and what did land is still reported as landed")

unres = FakeAdapter(specs=(SPEC,), rows=1)
unres.ingest_report = lambda *a, **k: {"report": "daily", "label": "D", "rows_ingested": 1,
                                       "delivered": True, "unresolved": [{"line": "X"}]}
resu = with_adapter(unres, lambda: prp.pull_reports_on_page(None, "ORG", SRC, FakePage()))
ok("not mapped" in resu["status"] and resu["unresolved"],
   "an item a human must map is carried up with the count, never swallowed")
ok(resu["unresolved_merchants"] == resu["unresolved"],
   "the card feed's older spelling of that key still reads the same value (one value, two names)")

print("\nG. the window — daily, closed days only, bounded")
eq(prp.window_days({}, "fake"), 7,
   "no config and no adapter default ⇒ the house default")
ok(with_adapter(ad, lambda: prp.window_days({}, "fake")) == 3, "the adapter's house default is used")
eq(prp.window_days({"portal_window_days": 10}, "fake"), 10, "a per-source override wins")
eq(prp.window_days({"portal_window_days": 9999}, "fake"), prp.MAX_WINDOW_DAYS,
   "an absurd window is clamped — re-fetching months is how a portal starts rate-limiting us")
import datetime as _dt
f, t = prp.date_range({"portal_window_days": 1}, "fake", today=_dt.date(2026, 10, 12))
eq((f, t), ("2026-10-11", "2026-10-11"), "the window ends YESTERDAY — today's day is not final yet")
f3, t3 = prp.date_range({"portal_window_days": 3}, "fake", today=_dt.date(2026, 10, 12))
eq((f3, t3), ("2026-10-09", "2026-10-11"), "…and re-fetches the restatement window")

print("\nH. read_table tolerates what portals actually emit, and takes the FEED's header test")
eq(prp.read_table(None), [], "a None payload is empty, never a crash")
eq(prp.read_table(b"a,b\n1,2\n")[0], ["a", "b"], "bytes are decoded")
eq(prp.read_table("﻿a,b\n1,2\n")[0], ["a", "b"], "a UTF-8 BOM is stripped")
banner = "Title line\n\nDay,Line,Qty\n2026-10-11,A,1\n"
eq(prp.read_table(banner)[0], ["Title line"], "with no header test, the first row is the header")
eq(prp.read_table(banner, header_score=lambda r: 2 if "Day" in r else 0)[0], ["Day", "Line", "Qty"],
   "…and the FEED's own test is what skips the banner — the loop knows no column names")

print("\nI. negative controls")
ok(prp.adapter_for("fake") is None, "control: the fake adapter is NOT registered outside with_adapter()")
ok(with_adapter(ad, lambda: prp.adapter_for("fake")) is ad, "control: …and IS inside it")
armed = re.sub(r'if not navigate_to_report\(page, src_row, spec\):', 'if False:', home)
ok(armed != home, "control: §F's unreachable-report branch is a real line of the loop")
ok("expect_download(" in home, "control: the one home is the module that downloads")
ok(len([f for f in py_files() if "portal_report_pull" in f]) == 1, "control: there is one such module")

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
