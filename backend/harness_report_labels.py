"""HARNESS — Carrier-aware report column labels + banner terminology (owner directive 2026-09-02).

Proves, DB-free (stdlib + the pure module only), the whole display-label resolution that mig 945
seeds and GET/PUT /commcalc/report-labels serve:

  · precedence: TENANT OVERRIDE > HOUSE CARRIER PRESET > BUILT-IN DEFAULT — per carrier;
  · banner gating: default ON (today's behavior); the Boost preset turns the b2bsoft-MTD
    unrecognized-contract-type warning OFF; a tenant override wins in either direction;
  · byte-identity: an org with no carrier row / no preset / no override resolves to EMPTY maps in
    the payload — every page's built-in header renders unchanged (incl. LuxeLink's 'Edge' and the
    Activations page's different 'New Activation' built-in);
  · carrier identity: normalize_carrier_code mirrors the frontend rbac.carrierCode (the LIVE rows:
    house 'Boost Mobile'/code 'boost' → boost; LuxeLink 'Total Wireless'/code NULL → total);
  · junk safety: unknown banner keys/values and blank rows never survive parsing;
  · the mig-945 seed rows themselves resolve to the owner's asks (Boost: edge→ACIMA + banner off;
    Total: edge→Edge + banner on).

  python3 backend/harness_report_labels.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.modules.commcalc import report_labels as rl                     # noqa: E402

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


HOUSE = rl.HOUSE_ORG
LUXE = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"      # Total-side live org (evidence 2026-09-03)
T_NEW = "33333333-3333-3333-3333-333333333333"     # a hypothetical new tenant

# The EXACT rows migration 945 seeds (kept in lockstep — if the migration changes, change these).
MIG_945_ROWS = [
    {"org_id": HOUSE, "scope": "report_col:boost", "key": "edge", "label": "ACIMA"},
    {"org_id": HOUSE, "scope": "report_col:total", "key": "edge", "label": "Edge"},
    {"org_id": HOUSE, "scope": "report_banner:boost", "key": "unrecognized_ct_recon", "label": "off"},
    {"org_id": HOUSE, "scope": "report_banner:total", "key": "unrecognized_ct_recon", "label": "on"},
]

print("== carrier identity (mirrors frontend rbac.carrierCode) ==")
check("code 'boost' -> boost", rl.normalize_carrier_code("boost") == "boost")
check("live house row name 'Boost Mobile' -> boost", rl.normalize_carrier_code("Boost Mobile") == "boost")
check("live LuxeLink name 'Total Wireless' (code NULL) -> total",
      rl.normalize_carrier_code("Total Wireless") == "total")
check("'Total' -> total", rl.normalize_carrier_code("Total") == "total")
check("'VidaPay' -> total", rl.normalize_carrier_code("VidaPay") == "total")
check("'Cricket Wireless' -> cricket", rl.normalize_carrier_code("Cricket Wireless") == "cricket")
check("unknown 'Verizon' -> slug verizon", rl.normalize_carrier_code("Verizon") == "verizon")
check("blank/None -> ''", rl.normalize_carrier_code(None) == "" and rl.normalize_carrier_code("  ") == "")

print("== default carrier pick ==")
check("is_default wins", rl.default_carrier(
    [{"code": "boost"}, {"code": "total", "is_default": True}]) == "total")
check("sole row wins without is_default (live house shape: boost, is_default false)",
      rl.default_carrier([{"name": "Boost Mobile", "code": "boost", "is_default": False}]) == "boost")
check("no carriers -> '' (never an assumed carrier)", rl.default_carrier([]) == "")

print("== parse + precedence: tenant override > carrier preset > built-in default ==")
# LuxeLink (total) against the mig-945 seeds, no override: edge stays 'Edge', banner ON.
parsed = rl.parse_label_rows(MIG_945_ROWS, LUXE, ["total"])
payload = rl.build_payload(parsed, ["total"], "total")
check("Total preset: edge -> 'Edge' (byte-identical for LuxeLink)",
      payload["columns"]["total"].get("edge") == "Edge")
check("Total preset: ct-gap banner ON", rl.banner_on(payload["banners"]["total"], "unrecognized_ct_recon"))

# The Boost side (the live house org is the boost-carrier org) against the same seeds.
parsed_b = rl.parse_label_rows(MIG_945_ROWS, HOUSE, ["boost"])
payload_b = rl.build_payload(parsed_b, ["boost"], "boost")
check("Boost preset: edge -> 'ACIMA' (owner ask #2)", payload_b["columns"]["boost"].get("edge") == "ACIMA")
check("Boost preset: ct-gap banner OFF (owner ask #1)",
      not rl.banner_on(payload_b["banners"]["boost"], "unrecognized_ct_recon"))
check("Boost preset touches no other column key", set(payload_b["columns"]["boost"]) == {"edge"})

# Tenant override beats the carrier preset — both directions.
rows_ovr = MIG_945_ROWS + [
    {"org_id": HOUSE, "scope": "report_col", "key": "edge", "label": "Lease-to-own"},
    {"org_id": HOUSE, "scope": "report_banner", "key": "unrecognized_ct_recon", "label": "on"},
]
parsed_o = rl.parse_label_rows(rows_ovr, HOUSE, ["boost"])
payload_o = rl.build_payload(parsed_o, ["boost"], "boost")
check("tenant column override beats Boost preset ('Lease-to-own' > 'ACIMA')",
      payload_o["columns"]["boost"].get("edge") == "Lease-to-own")
check("tenant banner override 'on' beats Boost preset 'off'",
      rl.banner_on(payload_o["banners"]["boost"], "unrecognized_ct_recon"))
check("override also lands in the '_' no-carrier fallback map",
      payload_o["columns"]["_"].get("edge") == "Lease-to-own")

# A tenant override 'off' beats a preset 'on' (the other direction).
rows_off = MIG_945_ROWS + [
    {"org_id": LUXE, "scope": "report_banner", "key": "unrecognized_ct_recon", "label": "off"}]
p_off = rl.build_payload(rl.parse_label_rows(rows_off, LUXE, ["total"]), ["total"], "total")
check("tenant banner override 'off' beats Total preset 'on'",
      not rl.banner_on(p_off["banners"]["total"], "unrecognized_ct_recon"))

print("== byte-identity: no rows / no carrier -> built-ins render ==")
p_empty = rl.build_payload(rl.parse_label_rows([], T_NEW, []), [], "")
check("no carriers: no per-carrier column maps, '_' map empty",
      set(p_empty["columns"]) == {"_"} and p_empty["columns"]["_"] == {})
check("no rows: banner defaults ON", rl.banner_on(p_empty["banners"]["_"], "unrecognized_ct_recon"))
p_nopreset = rl.build_payload(rl.parse_label_rows([], T_NEW, ["verizon"]), ["verizon"], "verizon")
check("carrier with no preset rows: empty column map (Verizon org byte-identical)",
      p_nopreset["columns"]["verizon"] == {})
check("payload never bakes defaults into resolved maps (each page keeps its OWN built-in header)",
      "activation" not in p_nopreset["columns"]["verizon"])

print("== lazy auto-assign for a NEW tenant (no setup hook) ==")
# The new tenant only INSERTS its carrier row (the onboarding Carrier Selection step); the same
# house preset rows immediately resolve for it.
p_new = rl.build_payload(rl.parse_label_rows(MIG_945_ROWS, T_NEW, ["boost"]), ["boost"], "boost")
check("new boost tenant auto-inherits edge -> 'ACIMA'", p_new["columns"]["boost"].get("edge") == "ACIMA")
check("new boost tenant auto-inherits banner OFF",
      not rl.banner_on(p_new["banners"]["boost"], "unrecognized_ct_recon"))
check("…and presets stay visible as a distinct layer for the settings UI",
      p_new["presets"]["boost"]["columns"].get("edge") == "ACIMA"
      and p_new["overrides"]["columns"] == {})

print("== junk safety ==")
junk = [
    {"org_id": HOUSE, "scope": "report_banner:boost", "key": "unrecognized_ct_recon", "label": "banana"},
    {"org_id": HOUSE, "scope": "report_banner:boost", "key": "not_a_banner", "label": "off"},
    {"org_id": HOUSE, "scope": "report_col:boost", "key": "", "label": "X"},
    {"org_id": HOUSE, "scope": "report_col:boost", "key": "edge", "label": "   "},
    {"org_id": "someone-else", "scope": "report_col", "key": "edge", "label": "LEAKED"},
    None,
]
p_junk = rl.build_payload(rl.parse_label_rows(junk, HOUSE, ["boost"]), ["boost"], "boost")
check("junk banner value dropped -> default ON",
      rl.banner_on(p_junk["banners"]["boost"], "unrecognized_ct_recon"))
check("unknown banner key dropped", "not_a_banner" not in p_junk["banners"]["boost"])
check("blank key / blank label / foreign-org rows dropped",
      p_junk["columns"]["boost"] == {} and p_junk["overrides"]["columns"] == {})
check("banner_on on unknown key defaults True", rl.banner_on({}, "some_future_banner"))
check("resolve_banners filters junk on hand-built dicts too",
      rl.resolve_banners({"unrecognized_ct_recon": "banana"}, {}) == {"unrecognized_ct_recon": "on"})

print("== registry sanity (settings UI contract) ==")
check("edge is a labelable column with default 'Edge'",
      dict(rl.LABELABLE_COLUMNS).get("edge") == "Edge")
check("registry keys unique", len(dict(rl.LABELABLE_COLUMNS)) == len(rl.LABELABLE_COLUMNS))
check("every banner has an on/off default",
      all(v["default"] in ("on", "off") for v in rl.BANNERS.values()))
check("payload lists editable columns + banner keys for the settings panel",
      p_empty["editable_columns"][0]["key"] == "total_activation"
      and p_empty["banner_keys"][0]["key"] == "unrecognized_ct_recon")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
