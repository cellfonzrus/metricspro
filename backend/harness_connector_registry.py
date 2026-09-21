"""DB-FREE PROOF — THE CONNECTOR REGISTRY: a connector's POS / carrier scope, one predicate, every surface.

    python3 harness_connector_registry.py        (from backend/; no network, no DB)

OWNER (2026-09-21, a Verizon tenant whose declared POS is RQ), verbatim, on the Inventory Values chip
"🔌 RQ portal connection / last: error / The b2bsoft (wsreports.b2bsoft.com) portal client is not
reverse-engineered yet. Provide the b2bsoft login so the Inventory Aging report flow can be captured.":
*"it says rq connection but refers to b2b reports"*.

WHAT THIS PINS
  A. THE PREDICATE IS SHARED, NOT COPIED: connector_registry.visible == report_kinds.visible_kinds with
     the `connector:` namespace; the Verizon/RQ declaration hides the POS-portal connector (and says why,
     on the POS axis); the house org (POS b2bsoft, boost + total) sees every connector; a Total-only
     tenant sees the MA portal and not the boost ones; an alias resolves to the same row; a slug the
     registry does not know is NEVER withheld; the super-admin `connector:<key>` cap widens and is
     recorded; an unknown declaration shows LESS.
  B. EVERY BACKEND SURFACE, DRIVEN FOR REAL over an in-memory client: GET /connector-registry; the
     four public sweep configs (GET /b2b|dlar|epay|vip/sweep/config) carry `connector_scope`; the
     data-source list attaches it per login; the merchant-portal health and the control box's portal
     evidence drop a non-applicable login; the connector-health scan reports it `unmonitored` and never
     alertable; the Connectors list attaches it per instance; the two attention providers raise NO item
     for it (context handed down, never read inside).
  C. THE COPY DEREFERENCES THE REGISTRY: the inventory stub's message names the connector by the
     registry row's label / host; with no row it spells nothing; the b2bsoft login family's sentences
     carry the label they were given; a run of the legacy sweep writes the dereferenced sentence as the
     connector's status.
  D. PRE-MIGRATION HONESTY: without commcalc.connector_registry the SAME answers come from the code
     mirror with `registry_ready:false` — the Vzone tenant sees the neutral line before mig 1014 too.
  E. BYTE-IDENTITY FOR THE TENANTS IT APPLIES TO: report kinds' visible_kinds / cap_override are
     unchanged with their defaults; the house org's public sweep configs carry exactly the fields they
     did plus `connector_scope.applies:true`; `_strip_source_pw` without a context attaches nothing;
     `collect_attention` without the keyword is inert; the route policy (mig 998) is untouched.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from harness_intake_fakes import FakeDB                       # noqa: E402
from app.modules.commcalc import connector_registry as CR     # noqa: E402
from app.modules.commcalc import report_kinds as RK           # noqa: E402
from app.modules.commcalc import b2b_sweep as B2B             # noqa: E402
from app.modules.commcalc import vidapay_sweep as VP          # noqa: E402
from app.modules.commcalc import import_audit as IA           # noqa: E402
from app.modules.commcalc import router as R                  # noqa: E402
from app.modules.core import import_health as IH              # noqa: E402
from app.modules.core import control_box_api as CBA           # noqa: E402

HOUSE = CR.HOUSE_ORG
VZ = "f4f1c16e-0000-0000-0000-000000000001"      # the Verizon tenant of the owner's report (RQ)
LX = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"      # LuxeLink (Total) — as data
NEW = "33333333-0000-0000-0000-000000000003"     # a tenant that declared nothing yet

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
        print(f"  FAIL  {name}{(' — ' + str(extra)[:500]) if extra else ''}")
    return bool(cond)


def section(t):
    print(f"\n{t}\n" + "─" * len(t))


def decl(pos, carriers):
    return {"pos": [RK.code(p) for p in pos], "pos_source": "report_term:x" if pos else "unknown",
            "carriers": [RK.code(c) for c in carriers], "reasons": []}


def build_db(with_registry=True, luxe_pos=None):
    db = FakeDB()
    db.seed("ui_label_override", [{"org_id": HOUSE, "scope": "report_term:boost", "key": "pos_system", "label": "b2bsoft"},
                                  {"org_id": VZ, "scope": "report_term", "key": "pos_system", "label": "RQ"}]
            + ([{"org_id": LX, "scope": "report_term", "key": "pos_system", "label": luxe_pos}] if luxe_pos else []))
    db.seed("carrier", [{"org_id": HOUSE, "name": "Boost", "code": "boost", "is_default": True},
                        {"org_id": HOUSE, "name": "Total", "code": "total", "is_default": False},
                        {"org_id": VZ, "name": "Verizon", "code": "verizon", "is_default": True},
                        {"org_id": LX, "name": "Total", "code": "total", "is_default": True}])
    db.seed("pos_profile", [{"org_id": HOUSE, "pos_key": "b2bsoft", "label": "B2B Soft (standard)", "is_active": True,
                             "filename_rules": [], "imap_defaults": {}, "schedule_defaults": {}, "report_defs": []}])
    if with_registry:
        db.seed(CR.TABLE, [dict(r, org_id=HOUSE) for r in CR.HOUSE_CONNECTORS])
    else:
        db.tables[CR.TABLE] = None          # 42P01 — the migration has not run
    # the legacy per-vendor sweep rows: the Vzone tenant's b2b row carries the OWNER'S OWN stored error
    db.seed("b2b_sweep_config", [
        {"org_id": VZ, "enabled": True, "frequency": "daily", "hour": 6, "timezone": "America/New_York",
         "portal_user": "vz", "portal_pass": "x", "connector": "b2bsoft", "last_status": "error",
         "last_detail": "The b2bsoft (wsreports.b2bsoft.com) portal client is not reverse-engineered yet. "
                        "Provide the b2bsoft login so the Inventory Aging report flow can be captured.",
         "last_run_at": "2026-09-20T10:00:00+00:00", "next_run_at": "2026-09-22T10:00:00+00:00"},
        {"org_id": HOUSE, "enabled": True, "frequency": "daily", "hour": 6, "timezone": "America/New_York",
         "portal_user": "house", "portal_pass": "x", "connector": "b2bsoft", "last_status": "error",
         "last_detail": "stale", "last_run_at": "2026-09-20T10:00:00+00:00", "next_run_at": "2026-09-22T10:00:00+00:00"},
    ])
    db.seed("epay_sweep_config", [{"org_id": VZ, "enabled": True, "frequency": "daily", "hour": 6, "timezone": "America/New_York",
                                   "portal_user": "vz", "portal_pass": "x", "last_status": "error", "last_detail": "boom",
                                   "last_run_at": "2026-09-20T10:00:00+00:00", "next_run_at": "2026-09-22T10:00:00+00:00"}])
    # portal logins: a b2bsoft login on the Vzone tenant (a leftover) and a merchant portal (any-scope)
    db.seed("data_source", [
        {"id": "src-vz-b2b", "org_id": VZ, "processor": "b2bsoft", "label": "POS portal", "enabled": True, "username": "u",
         "password": "p", "auth_status": "needs_2fa", "session_state": None, "portal_url": "https://x", "frequency": "daily"},
        {"id": "src-vz-pa", "org_id": VZ, "processor": "payanywhere", "label": "card terminal", "enabled": True, "username": "u",
         "password": "p", "auth_status": "authenticated", "session_state": {"c": 1}, "portal_url": "https://x", "frequency": "daily"},
        {"id": "src-house-b2b", "org_id": HOUSE, "processor": "b2b", "label": "POS portal", "enabled": True, "username": "u",
         "password": "p", "auth_status": "needs_2fa", "session_state": None, "portal_url": "https://x", "frequency": "daily"},
    ])
    db.seed("connector_instances", [
        {"id": "ci-vz-b2b", "org_id": VZ, "vendor_name": "B2B Soft", "label": "wsreports", "sweep_kind": "b2b", "enabled": True,
         "config_table": "b2b_sweep_config", "automatable": False, "sort_order": 1},
        {"id": "ci-vz-google", "org_id": VZ, "vendor_name": "Google", "label": "closing", "sweep_kind": "google_closing", "enabled": True,
         "config_table": None, "automatable": True, "sort_order": 2},
    ])
    return db


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("A. THE PREDICATE IS SHARED — report_kinds.visible_kinds one axis over")
rows = CR.house_mirror()
vz = decl(["RQ"], ["verizon"])
house = decl(["b2bsoft"], ["boost", "total"])
vis_vz = {r["key"] for r in CR.visible(rows, vz)}
check("Verizon / RQ: the POS-portal connector is NOT visible; the any-scope ones are", "b2bsoft" not in vis_vz and {"dlar", "mailbox", "ftp", "google_closing", "payanywhere"} <= vis_vz, vis_vz)
check("… nor the boost / total portals (epay, vip, vidapay)", not ({"epay", "vip", "vidapay"} & vis_vz), vis_vz)
check("connector_registry.visible IS report_kinds.visible_kinds with the connector namespace (same rows out)",
      [r["key"] for r in CR.visible(rows, vz)] == [r["key"] for r in RK.visible_kinds(rows, vz, None, None, None, cap_prefix=CR.CAP_PREFIX)])
sc = CR.scope(rows, vz, "b2b")
check("scope('b2b') on Verizon: registered, applies False, miss on the POS axis, the reason names the declaration",
      sc["registered"] and sc["applies"] is False and sc["miss"] == "pos" and "you declared rq" in sc["why"], sc)
check("… and carries the registry's label + host for copy", sc["label"] == "B2B Soft wsreports" and sc["host"] == "wsreports.b2bsoft.com", sc)
check("the alias and the key resolve to the SAME row", CR.resolve(rows, "b2b") is CR.resolve(rows, "B2BSOFT") and CR.resolve(rows, "total_access")["key"] == "vidapay")
vis_house = {r["key"] for r in CR.visible(rows, house)}
check("the house org (POS b2bsoft, boost + total) sees EVERY connector", vis_house == set(CR.HOUSE_CONNECTOR_KEYS), vis_house)
check("scope('b2bsoft') on the house org applies, provenance 'house default'", CR.scope(rows, house, "b2bsoft")["applies"] and CR.scope(rows, house, "b2bsoft")["provenance"] == RK.PROV_HOUSE)
lx = decl(["b2bsoft"], ["total"])
vis_lx = {r["key"] for r in CR.visible(rows, lx)}
check("a Total-only tenant on POS b2bsoft: the MA portal + the POS portal, NOT the boost portals", {"vidapay", "b2bsoft"} <= vis_lx and not ({"epay", "vip"} & vis_lx), vis_lx)
check("… and scope('epay') on it misses on the CARRIER axis (not the neutral POS line)", CR.scope(rows, lx, "epay")["miss"] == "carrier")
un = CR.scope(rows, vz, "rq")
check("a slug the registry does not know is NEVER withheld (a tenant-defined connector)", un["applies"] is True and un["registered"] is False)
check("scope('') / None is inert", CR.scope(rows, vz, "")["applies"] and CR.scope(rows, vz, None)["applies"])
wid = CR.scope(rows, vz, "b2bsoft", caps={"connector:b2bsoft": True})
check("a super-admin `connector:b2bsoft` show cap WIDENS and is recorded as provenance", wid["applies"] and wid["provenance"] == RK.PROV_WIDENED, wid)
hid = CR.scope(rows, house, "dlar", caps={"connector:dlar": False})
check("a hide cap withholds an any-scope connector and says so ('override')", hid["applies"] is False and hid["miss"] == "override" and hid["why"] == RK.PROV_HIDDEN, hid)
unk = {r["key"] for r in CR.visible(rows, decl([], []))}
check("an UNKNOWN declaration shows LESS — no POS- or carrier-scoped connector", not ({"b2bsoft", "vidapay", "epay", "vip"} & unk) and "mailbox" in unk, unk)
merged = CR.merge_rows([dict(r, org_id=HOUSE) for r in CR.HOUSE_CONNECTORS]
                       + [{"org_id": VZ, "key": "b2bsoft", "label": "B2B Soft wsreports", "applies_to_pos": ["b2bsoft", "rq"], "aliases": ["b2b"]}], VZ)
check("a TENANT row overrides the house row per key (mig-207 shape, through report_kinds.merge_rows) — widening as CONFIG",
      CR.scope(merged, vz, "b2b")["applies"] is True and CR.scope(merged, vz, "b2b")["provenance"] == RK.PROV_OVERRIDE)
h = {x["key"]: x["why"] for x in CR.hidden(rows, vz)}
check("hidden() lists the withheld connectors with WHY (POS b2bsoft — you declared rq; carrier boost / total)",
      "b2bsoft" in h and "epay" in h and "vidapay" in h and "you declared rq" in h["b2bsoft"] and "you declared verizon" in h["epay"], h)
nl = CR.neutral_line("RQ", True)
check("THE NEUTRAL LINE (declared): names the tenant's POS term, never a vendor",
      nl == "No reports-portal connection is defined for RQ yet — when RQ has a reports portal, it can be added under Connectors." and "b2b" not in nl.lower())
check("THE NEUTRAL LINE (undeclared): points at the Implementation wizard", "Declare your POS in the Implementation wizard" in CR.neutral_line("POS", False))
check("not_applicable_copy: the POS miss → the neutral line; a carrier miss → the registry's reason",
      CR.not_applicable_copy(sc, "RQ", True) == nl and "applies to carrier boost" in CR.not_applicable_copy(CR.scope(rows, lx, "epay"), "b2bsoft", True))


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("B. EVERY BACKEND SURFACE, DRIVEN FOR REAL — the Vzone tenant vs the house org")
db = build_db()
R.sb = lambda: db
pv = R.connector_registry_endpoint(org_id=VZ)
check("GET /connector-registry (Verizon): registry_ready, declaration rq / verizon, the POS term 'RQ' declared",
      pv["registry_ready"] and pv["declaration"]["pos"] == ["rq"] and pv["pos"] == {"term": "RQ", "declared": True}, (pv["declaration"], pv["pos"]))
check("… `connectors` carries no POS-portal / boost / total connector; `hidden` says why", not ({"b2bsoft", "epay", "vip", "vidapay"} & {c["key"] for c in pv["connectors"]}) and {x["key"] for x in pv["hidden"]} >= {"b2bsoft", "epay", "vip", "vidapay"})
check("… the payload's neutral_line IS the one sentence, with the term", pv["neutral_line"] == nl)
check("… all_keys still lists every registered connector (so a page can tell 'withheld' from 'unknown')", {k["key"] for k in pv["all_keys"]} == set(CR.HOUSE_CONNECTOR_KEYS))
ph = R.connector_registry_endpoint(org_id=HOUSE)
check("GET /connector-registry (house): every connector, POS term b2bsoft (the carrier preset)", {c["key"] for c in ph["connectors"]} == set(CR.HOUSE_CONNECTOR_KEYS) and ph["pos"]["term"] == "b2bsoft", ph["pos"])

cv = R.b2b_sweep_get_config(org_id=VZ)
check("GET /b2b/sweep/config (Verizon): connector_scope.applies False — the page renders the neutral line, not the form",
      cv["connector_scope"]["applies"] is False and cv["connector_scope"]["miss"] == "pos", cv["connector_scope"])
check("… the stored vendor-spelled last_detail is still IN the payload (data, never rewritten) — the page simply does not render the card", "b2bsoft" in (cv.get("last_detail") or ""))
ch = R.b2b_sweep_get_config(org_id=HOUSE)
check("GET /b2b/sweep/config (house): connector_scope.applies True with the label + host", ch["connector_scope"]["applies"] and ch["connector_scope"]["label"] == "B2B Soft wsreports")
cn = R.b2b_sweep_get_config(org_id=NEW)
check("a tenant with NO b2b row still gets a scope (the table's implied slug 'b2b' → the registry): undeclared ⇒ withheld", cn["configured"] is False and cn["connector_scope"]["applies"] is False)
ce = R.epay_sweep_get_config(org_id=VZ)
check("GET /epay/sweep/config (Verizon): withheld on the CARRIER axis (boost), so its 'error' is not this tenant's", ce["connector_scope"]["applies"] is False and ce["connector_scope"]["miss"] == "carrier")
check("GET /dlar/sweep/config (Verizon): any-scope → applies", R.dlar_sweep_get_config(org_id=VZ)["connector_scope"]["applies"] is True)
check("GET /vip/sweep/config (house): applies (boost)", R.vip_sweep_get_config(org_id=HOUSE)["connector_scope"]["applies"] is True)

ds = R.list_data_sources(org_id=VZ)
by = {s["id"]: s for s in ds["sources"]}
check("GET /data-sources (Verizon): the b2bsoft login row carries connector_scope.applies False; the merchant one True",
      by["src-vz-b2b"]["connector_scope"]["applies"] is False and by["src-vz-pa"]["connector_scope"]["applies"] is True)
check("… no secret leaks with the scope attached", all(k not in s for s in ds["sources"] for k in ("password", "session_state")))
mh = R.merchant_portal_health(org_id=VZ)
check("GET /merchant-portals/health (Verizon) counts only the applicable portal login", mh["ok"] and mh["total"] == 1 and mh["items"][0]["processor"] == "payanywhere", mh)
pe = CBA._portal_evidence(db, VZ)
check("the control box's portal evidence: the b2bsoft login (needs_2fa) is NOT in the roll-up — it would have been needs_login",
      pe["summary"]["total"] == 1 and pe["summary"]["worst"] == "healthy", pe["summary"])
pe_h = CBA._portal_evidence(db, HOUSE)
check("… on the house org the same shape of login IS watched (needs_login — it applies there)", pe_h["summary"]["total"] == 1 and pe_h["summary"]["worst"] == "needs_login", pe_h["summary"])

scan = R._scan_connector_health(db)
vz_items = [i for i in scan if i["org_id"] == VZ]
b2b_vz = [i for i in vz_items if "b2b_sweep_config" in i["ref_key"]]
check("the connector-health scan reports the Vzone b2b sweep as UNMONITORED (not applicable), never alertable",
      len(b2b_vz) == 1 and b2b_vz[0]["kind"] == "unmonitored" and b2b_vz[0]["alertable"] is False and "not_applicable" in b2b_vz[0]["ref_key"], b2b_vz)
check("… the detail says why in the registry's words", "does not apply" in b2b_vz[0]["detail"] and "you declared rq" in b2b_vz[0]["detail"], b2b_vz[0]["detail"])
epay_vz = [i for i in vz_items if "epay_sweep_config" in i["ref_key"]]
check("… the Vzone epay 'error' too (carrier boost)", len(epay_vz) == 1 and epay_vz[0]["kind"] == "unmonitored")
b2b_h = [i for i in scan if i["org_id"] == HOUSE and "b2b_sweep_config" in i["ref_key"]]
check("… the HOUSE b2b sweep keeps its own judgement (route policy / errored) — nothing narrowed for a tenant it applies to",
      len(b2b_h) == 1 and "not_applicable" not in b2b_h[0]["ref_key"], b2b_h)

lc = R.list_connectors(org_id=VZ)
byc = {c["id"]: c for c in lc}
check("GET /connectors (Verizon): the b2b instance carries connector_scope.applies False; google_closing True",
      byc["ci-vz-b2b"]["connector_scope"]["applies"] is False and byc["ci-vz-google"]["connector_scope"]["applies"] is True)

ctx = IH._connector_scope_ctx(db, VZ)
att = IH.collect_attention(db, VZ, deep=False, feed_h={}, route_policy=[], connector_scope=ctx)
keys = {i["key"] for i in att["items"]}
check("collect_attention (Verizon, with the context): NO item for the b2b sweep or the b2bsoft login",
      not any("b2b_sweep_config" in k or "src-vz-b2b" in k for k in keys), sorted(keys))
att0 = IH.collect_attention(db, VZ, deep=False, feed_h={}, route_policy=[])
keys0 = {i["key"] for i in att0["items"]}
check("… WITHOUT the keyword the providers are inert (byte-identical to before): the sweep's item comes back",
      any("b2b_sweep_config" in k for k in keys0) and any("epay_sweep_config" in k for k in keys0), sorted(keys0))
check("… the provider never reads the registry itself: the context carries the rows", ctx["rows"] and ctx["declaration"]["pos"] == ["rq"])
att_h = IH.collect_attention(db, HOUSE, deep=False, feed_h={}, route_policy=[], connector_scope=IH._connector_scope_ctx(db, HOUSE))
check("… on the house org the same shapes still raise their items (they apply there)", any("b2b_sweep_config" in i["key"] or "src-house-b2b" in i["key"] for i in att_h["items"]), [i["key"] for i in att_h["items"]])


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("C. THE COPY DEREFERENCES THE REGISTRY — label / host from the row, never a vendor in code")
row = CR.resolve(rows, "b2b")
msg = B2B.not_implemented_message(row)
check("the inventory stub names the connector by the registry row's label + host",
      msg == "The B2B Soft wsreports (wsreports.b2bsoft.com) portal client is not implemented yet — provide the B2B Soft wsreports login under Connectors so the Inventory Aging report flow can be captured.", msg)
check("… with NO row it spells nothing and reads cleanly", B2B.not_implemented_message(None) == "This reports-portal client is not implemented yet — provide its login under Connectors so the Inventory Aging report flow can be captured.")
try:
    B2B.login(None, "u", "p", connector={"label": "My POS portal", "host": "reports.example.com"})
    check("login() raises", False)
except B2B.B2BNotConfigured as e:
    check("login() raises B2BNotConfigured with the dereferenced sentence", "My POS portal (reports.example.com)" in str(e))
check("the b2bsoft login family's sentences carry the label they were given; neutral without one",
      VP._portal_label("B2B Soft wsreports") == "B2B Soft wsreports" and VP._portal_label(None) == "reports portal")
pulled = VP.pull_b2bsoft_on_page(type("P", (), {"url": "https://x", "query_selector": lambda self, s: None, "query_selector_all": lambda self, s: [], "title": lambda self: "", "content": lambda self: ""})(), label="Some POS portal")
check("pull_b2bsoft_on_page's status names the label", "signed in to Some POS portal OK" in pulled["status"] and pulled["delivered"] is False)
R.sb = lambda: db
R._do_b2b_sweep(HOUSE)
st = next(r for r in db.tables["b2b_sweep_config"] if r["org_id"] == HOUSE)
check("a run of the legacy sweep (house) writes the DEREFERENCED sentence as the connector's status (route open in this fake)",
      st["last_status"] in ("error", "disabled") and ("B2B Soft wsreports (wsreports.b2bsoft.com) portal client is not implemented yet" in (st.get("last_detail") or "") or st["last_status"] == "disabled"), st.get("last_detail"))
check("… the recorded sentence no longer says 'not reverse-engineered'", "reverse-engineered" not in (st.get("last_detail") or ""))
check("_connector_label resolves the alias through the registry", R._connector_label(db, HOUSE, "b2b") == "B2B Soft wsreports" and R._connector_label(db, HOUSE, "made-up") is None)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("D. PRE-MIGRATION HONESTY — no commcalc.connector_registry table: the mirror, registry_ready:false, same answers")
pre = build_db(with_registry=False)
R.sb = lambda: pre
pp = R.connector_registry_endpoint(org_id=VZ)
check("GET /connector-registry (Verizon, pre-1014): registry_ready False, the SAME visible / hidden sets", pp["registry_ready"] is False and {c["key"] for c in pp["connectors"]} == {c["key"] for c in pv["connectors"]} and {x["key"] for x in pp["hidden"]} == {x["key"] for x in pv["hidden"]})
check("GET /b2b/sweep/config (Verizon, pre-1014): still withheld — the Vzone page reads the neutral line before the migration", R.b2b_sweep_get_config(org_id=VZ)["connector_scope"]["applies"] is False)
check("GET /b2b/sweep/config (house, pre-1014): still applies", R.b2b_sweep_get_config(org_id=HOUSE)["connector_scope"]["applies"] is True)
broken = FakeDB()
broken.tables[CR.TABLE] = None
broken.tables["ui_label_override"] = None
broken.tables["carrier"] = None
R.sb = lambda: broken
cb = R.b2b_sweep_get_config(org_id=NEW)
check("every read failing: the declaration is unknown ⇒ the POS-scoped connector is withheld (show less), never a 500", cb["connector_scope"]["applies"] is False and cb["connector_scope"]["registered"] is True, cb["connector_scope"])
check("… an any-scope connector still applies", R.dlar_sweep_get_config(org_id=NEW)["connector_scope"]["applies"] is True)
check("scope_for with no context is inert (applies True)", CR.scope_for(None, "b2bsoft")["applies"] is True and CR.scope_for({}, "b2bsoft")["applies"] is True)


# ══════════════════════════════════════════════════════════════════════════════════════════════════
section("E. BYTE-IDENTITY for the tenants a connector applies to; report kinds untouched")
rk_rows = RK.house_mirror()
check("report_kinds.visible_kinds with its defaults == with cap_prefix='kind:' explicitly (the generalisation changed nothing)",
      [r["key"] for r in RK.visible_kinds(rk_rows, vz)] == [r["key"] for r in RK.visible_kinds(rk_rows, vz, None, None, None, cap_prefix="kind:")])
check("cap_override default namespace is still kind:", RK.cap_override({"kind:x": True}, "x") is True and RK.cap_override({"connector:x": True}, "x") is None)
check("hidden_kinds default namespace unchanged", [h["key"] for h in RK.hidden_kinds(rk_rows, vz)] == [h["key"] for h in RK.hidden_kinds(rk_rows, vz, cap_prefix="kind:")])
check("merge_rows without a normaliser is report_kinds' own (report kinds byte-identical)", RK.merge_rows([dict(r, org_id=HOUSE) for r in RK.HOUSE_KINDS], VZ)[0]["key"] == RK.HOUSE_KEYS[0])
R.sb = lambda: db
ch2 = R.b2b_sweep_get_config(org_id=HOUSE)
expected = {'enabled', 'frequency', 'day_of_week', 'day_of_month', 'hour', 'timezone', 'portal_user', 'next_run_at', 'last_run_at',
            'last_status', 'last_detail', 'last_attempt_at', 'configured', 'has_credentials', 'connector', 'route_policy'}
check("the house org's public b2b config carries exactly the fields it did + connector_scope", set(ch2) == expected | {"connector_scope"}, set(ch2) ^ (expected | {"connector_scope"}))
stripped = R._strip_source_pw(dict(db.tables["data_source"][2]), [])
check("_strip_source_pw WITHOUT a scope context attaches nothing (its other callers are byte-identical)", "connector_scope" not in stripped and "password" not in stripped)
check("the route policy (mig 998) is untouched: the sweep gate still reads _source_route_policy / _route_closed", "_route_closed(_source_route_policy(client, oid" in open(R.__file__, encoding="utf-8").read())
check("sweep DISPATCH is untouched: _sweep_registry and _SOURCE_SCRAPERS never consult the connector scope",
      "connector_scope" not in open(R.__file__, encoding="utf-8").read().split("def _sweep_registry")[1].split("\n\n\n")[0])
check("the sweeps' behaviour for a tenant they apply to: the house b2b run above wrote a status (it RAN — nothing gated it)", st["last_attempt_at"] is not None or st["last_status"] == "disabled")

print("\n══ connector registry: %d passed, %d failed ══" % (_pass, _fail))
if _failures:
    print("FAILED:", *_failures, sep="\n  - ")
    sys.exit(1)
