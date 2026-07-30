"""PAGE ↔ API contract proof for /commcalc/device-cost-recon.

`tsc --noEmit` proves the page COMPILES; it cannot prove the page reads keys the endpoint actually
returns — `d?.delta_totals` type-checks fine against `any` and renders "—" forever if the backend spells
it `delta_total`. And `[[curl-verified-not-ui-verified-apiv1]]`: a request that works in curl 404s in the
app when the page forgets the `/api/v1` prefix. So this reads the REAL page source and the REAL endpoint
payload (over the harness's fake Supabase, no DB) and asserts they agree:

  1. the page requests the endpoint at `/api/v1/...` and passes `org_id` (RULE ONE, query param)
  2. EVERY top-level `d.<key>` the page reads exists on the real payload
  3. every key read through the page's local aliases (t=tiles, pol=policy, inv=inventory,
     liab=liability, unlink=unlinkable) exists on that nested object
  4. every ExportColumn `field:` in all five export/table column sets exists on a real row/group/
     overlap/delta/source object — so RULE FOUR's exports cannot ship empty columns
  5. the page sends every filter param the handler declares (no dead control), and sends no param the
     handler does not accept (no silently-ignored filter)
  6. the grant key is spelled identically in the page, the backend gate and the 403 detail

Run: `python3 scratchpad/device_cost_recon_ui_contract.py` from the backend dir.
"""
import io, os, re, sys, types

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _BACKEND)
os.environ.setdefault("COMMCALC_CFG_CACHE_TTL", "0")

PAGE = os.path.abspath(os.path.join(
    _BACKEND, "..", "frontend", "src", "app", "(platform)", "commcalc", "device-cost-recon", "page.tsx"))

_pass = _fail = 0


def check(name, cond, extra=""):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  PASS  {name}")
    else:
        _fail += 1
        print(f"  FAIL  {name}{(' — ' + extra) if extra else ''}")


def _load_harness_helpers():
    """The harness's fake client + fixtures WITHOUT its 191 assertions (it is top-level sequential and
    ends in sys.exit — the house style). Only the section above its first banner is executed."""
    path = os.path.join(_BACKEND, "harness_device_cost_recon.py")
    src = io.open(path, encoding="utf-8").read()
    marker = 'print("\\n── 1. handler contract'
    assert marker in src, "harness layout changed — the helper split marker is gone"
    mod = types.ModuleType("dcr_harness_helpers")
    mod.__file__ = path
    exec(compile(src[:src.index(marker)], path, "exec"), mod.__dict__)
    return mod


H = _load_harness_helpers()
src = io.open(PAGE, encoding="utf-8").read()
H.install(H.full_store(org=H.TEN))
payload = H.call(H.TEN)

print("\n── 1. the request the page actually makes ────────────────────────────────────────────")
m = re.search(r"api\(`([^`]+)`\)", src)
check("the page calls api() with a template URL", bool(m))
url = m.group(1) if m else ""
check("…at /api/v1/commcalc/device-cost-recon (the prefix trap)",
      url.startswith("/api/v1/commcalc/device-cost-recon"), url)
check("…and appends the org param", "orgParam()" in url)
check("orgParam() sends org_id as a QUERY param off the active org",
      re.search(r"orgParam\s*=\s*\(\)\s*=>.*org_id=", src, re.S) is not None)
check("the page never hard-codes the house org id", "00000000-0000-0000-0000-000000000001" not in src)

print("\n── 2. every top-level key the page reads exists on the payload ───────────────────────")
top = sorted(set(re.findall(r"\bd\??\.([a-z_][a-z0-9_]*)", src)))
missing = [k for k in top if k not in payload]
check(f"all {len(top)} top-level `d.<key>` reads exist on the real payload", not missing, str(missing))
check("`ready` is on the payload (the page's render gate)", payload.get("ready") is True)

# The EMPTY payload matters just as much: a key the page reads but the empty branch omits renders
# "undefined" on the very first load of a month with no data — the state a new tenant always starts in.
H.install(H.full_store(org=H.TEN))
empty = H.call(H.TEN, period="not-a-month")
missing_empty = [k for k in top if k not in empty]
check("…and on the EMPTY/unreadable-period payload too (a new tenant's first load)",
      not missing_empty, str(missing_empty))
check("the empty payload is still `ready` so the page renders its note, not a spinner",
      empty.get("ready") is True and empty.get("note"))

print("\n── 3. keys read through the page's local aliases ─────────────────────────────────────")
ALIASES = {"t": "tiles", "pol": "policy", "inv": "inventory", "liab": "liability",
           "unlink": "unlinkable"}
for alias, target in ALIASES.items():
    check(f"the page's `{alias}` alias really points at d.{target}",
          re.search(rf"const {alias} = d\?\.{target}\b", src) is not None)
    keys = sorted(set(re.findall(rf"\b{alias}\??\.([a-z_][a-z0-9_]*)", src)))
    obj = payload.get(target) or {}
    bad = [k for k in keys if k not in obj]
    check(f"all {len(keys)} `{alias}.<key>` reads exist on d.{target}", not bad, str(bad))
    obj_e = empty.get(target) or {}
    bad_e = [k for k in keys if k not in obj_e]
    check(f"…and on the EMPTY payload's d.{target} too", not bad_e, str(bad_e))

print("\n── 4. RULE FOUR: every export column's `field` exists on its objects ─────────────────")
SETS = {
    "cols": ("rows", payload["rows"]),
    "deltaCols": ("delta_rows", payload["delta_rows"]),
    "overlapCols": ("overlaps", payload["overlaps"]),
    "groupCols": ("groups", payload["groups"]),
    "sourceCols": ("tiles.by_source", payload["tiles"]["by_source"]),
}
for name, (where, objs) in SETS.items():
    block = re.search(rf"const {name}: ExportColumn\[\] = \[(.*?)\n  \]", src, re.S)
    check(f"the `{name}` column set is present in the page", bool(block))
    if not block:
        continue
    fields = re.findall(r"field:\s*'([a-z_][a-z0-9_]*)'", block.group(1))
    check(f"…{name} declares a field for every column", len(fields) > 0)
    present = set()
    for o in (objs or []):
        present |= set(o.keys())
    bad = [f for f in fields if f not in present]
    check(f"…every `{name}` field exists on a real {where} object", not bad, str(bad))
check("the page ships FIVE export surfaces (row table + delta + overlaps + rollup + linkability)",
      len(SETS) == 5 and src.count("<ReportExportBar") == 4 and "<ReportShell" in src)

print("\n── 5. no dead control, no silently-ignored param ─────────────────────────────────────")
import inspect
from app.modules.commcalc import router as R
accepted = set(inspect.signature(R.device_cost_recon_endpoint).parameters)
sent = set(re.findall(r"qs\.set\('([a-z_]+)'", src))
# plus the base params built in the URLSearchParams constructor
ctor = re.search(r"new URLSearchParams\(\{(.*?)\}\)", src, re.S)
if ctor:
    sent |= set(re.findall(r"([a-z_]+):", ctor.group(1)))
unknown = sorted(sent - accepted)
check(f"every one of the {len(sent)} params the page sends is accepted by the handler",
      not unknown, str(unknown))
# controls that exist on the handler and matter for RULE FIVE must actually be wired
must_send = {"period", "window_months", "group_by", "stores", "markets", "reps", "sources",
             "arrangements", "timings", "products", "months", "precedence", "ma_recognition_date",
             "price_basis", "overlap_only", "unlinkable_only", "recognized_only", "include_cancelled"}
check("every RULE FIVE / policy control on the handler is wired to a page control",
      must_send <= sent, str(sorted(must_send - sent)))

print("\n── 6. the grant key is spelled identically in all three places ───────────────────────")
from app.modules.commcalc import device_cost_recon as D
check("page ↔ pure module ↔ gate all say 'device_cost_recon'",
      "hasDataGrant(permissions, 'device_cost_recon')" in src
      and D.GRANT_KEY == "device_cost_recon")
check("the page's 403 detector matches the backend's detail text",
      "/device_cost_recon/i" in src)
check("the page renders a LOCK note instead of a raw error, and nothing else when locked",
      "LockNote" in src and re.search(r"if \(locked\) \{\s*\n\s*return <div[^>]*>\{header\}<LockNote", src))
check("the page does not fire a doomed request without the grant",
      "if (!clientGranted) { setLocked(true)" in src)

print("\n══════════════════════════════════════════════════════════════════════════════════════")
print(f"  UI CONTRACT   PASS {_pass}   FAIL {_fail}")
print("══════════════════════════════════════════════════════════════════════════════════════")
sys.exit(1 if _fail else 0)
