"""Integration: what a business type hides, read from the DATABASE, for EVERY tenant (§CORE-06.5).

Owner report 2026-11-27, in the NY LOGISTICS tenant: *"i cannot see the targtet setting dashboard and the
incentives dashboard in ny logistcis - why is that and fix it"*.

WHY THIS TEST EXISTS, and why it is an integration test rather than one more harness check. The hide-list is
a ROW (`core.tenant_vertical.nav_hidden`, seeded by mig 1020 and re-declared by mig 1134), not a constant,
and `backend/harness_nav_reachability.py` proves the mechanism against the code MIRROR. A mirror that agrees
with itself proves nothing about the read: the payload the browser's nav gate acts on comes back through
`verticals.load_vocab` → `resolve_vertical` → `payload`, which tolerates a database missing mig 1024's
column and falls back to the mirror by key. So the question *"does the row the app actually reads still hide
Target Settings"* has to be asked of a real read. It is asked here, through the production functions, over
the in-memory double (docs/TESTING.md — there is no staging Supabase).

MULTI-TENANT (project rule 2026-10-30). Every assertion runs for BOTH business types, each seeded as its own
tenant in ONE table, so one tenant's hide-list leaking into another's payload surfaces as the wrong tenant's
pages going missing rather than as a number that is hard to argue about. A tenant that declares nothing, and
one that declares a value the vocabulary does not know, are asserted too: both must read as the default and
neither may inherit the other type's list.
"""
import pytest

from app.modules.core import verticals as V

HOUSE = "00000000-0000-0000-0000-000000000001"          # wireless retail (the default)
ORG_UPS = "00000000-0000-0000-0000-0000000000c1"        # a franchise shipping store, the reported shape
ORG_UNSET = "00000000-0000-0000-0000-0000000000c2"      # declares nothing
ORG_JUNK = "00000000-0000-0000-0000-0000000000c3"       # declares a value the vocabulary does not know

#: The four pages the owner could not reach, and the one page the hide-list really means to hide.
REPORTED = ("/commcalc/targets/settings", "/commcalc/targets/my",
            "/commcalc/incentive-rules", "/commcalc/daily-incentive")
DASHBOARDS = ("/hub/incentives", "/hub/incentive-payout-plans", "/hub/targets-coaching")
STILL_HIDDEN = "/commcalc/targets"                      # Daily Targets — the one entry that is intended
#: Pages this business type has never been meant to see; asserted so a widening is not mistaken for a fix.
STAYS_HIDDEN = ("/commcalc/chargebacks", "/commcalc/ma-commission", "/commcalc/dlar", "/hub/distributors")


def _vocab_rows():
    """`core.tenant_vertical` as the migrations leave it — read from the ONE mirror, never retyped here."""
    return [{"key": r["key"], "label": r["label"], "is_default": r["is_default"],
             "uses_carriers": r["uses_carriers"], "nav_hidden": list(r["nav_hidden"]),
             "sort_order": r["sort_order"], "closing_hidden": list(r["closing_hidden"]),
             "is_active": True}
            for r in V.HOUSE_VERTICALS]


UPS_KEY = next(r["key"] for r in V.HOUSE_VERTICALS if not r["is_default"])
WL_KEY = next(r["key"] for r in V.HOUSE_VERTICALS if r["is_default"])


@pytest.fixture
def wired(fake_supabase):
    """Four tenants in ONE table, so a cross-tenant leak cannot pass by being the only row seeded."""
    fake_supabase.seed("core.tenant_vertical", _vocab_rows())
    fake_supabase.seed("storeops.tenants", [
        {"org_id": HOUSE, "name": "House", "vertical": None},
        {"org_id": ORG_UPS, "name": "Franchise shipping store", "vertical": UPS_KEY},
        {"org_id": ORG_UNSET, "name": "Declares nothing", "vertical": None},
        {"org_id": ORG_JUNK, "name": "Declares nonsense", "vertical": "no_such_business_type"},
    ])
    return fake_supabase


def _payload(sb, org):
    out = V.me_payload(sb, org)
    assert out["registry_ready"] is True, "the vocabulary was read from the database, not the mirror fallback"
    return out


def test_the_franchise_store_can_reach_every_page_the_owner_reported(wired):
    nav = _payload(wired, ORG_UPS)["nav_hidden"]
    for href in REPORTED:
        assert not V.href_hidden(href, nav), f"{href} is still hidden from {UPS_KEY}"
    for href in DASHBOARDS:
        assert not V.href_hidden(href, nav), f"the {href} dashboard is still hidden from {UPS_KEY}"


def test_the_one_page_the_hide_list_means_to_hide_is_still_hidden(wired):
    """The fix is an EXACT entry, not a deletion: Daily Targets stays hidden, its six children do not."""
    nav = _payload(wired, ORG_UPS)["nav_hidden"]
    assert V.href_hidden(STILL_HIDDEN, nav)
    assert f"{STILL_HIDDEN}$" in nav and STILL_HIDDEN not in nav
    for href in STAYS_HIDDEN:
        assert V.href_hidden(href, nav), f"{href} was widened, which is not what was asked for"


@pytest.mark.parametrize("org", [HOUSE, ORG_UNSET, ORG_JUNK])
def test_a_tenant_that_declares_nothing_or_nonsense_hides_nothing(wired, org):
    """The default business type hides no page — and must never inherit the franchise list."""
    out = _payload(wired, org)
    assert out["key"] == WL_KEY
    assert out["nav_hidden"] == []
    for href in REPORTED + DASHBOARDS + (STILL_HIDDEN,) + STAYS_HIDDEN:
        assert not V.href_hidden(href, out["nav_hidden"])


def test_an_unknown_value_is_reported_rather_than_silently_another_types_gates(wired):
    assert _payload(wired, ORG_JUNK)["source"] == "unknown_value"
    assert _payload(wired, ORG_UNSET)["source"] == "default"
    assert _payload(wired, ORG_UPS)["source"] == "tenant"


def test_one_tenants_hide_list_never_appears_in_anothers_payload(wired):
    ups = _payload(wired, ORG_UPS)["nav_hidden"]
    assert ups, "the franchise list must not be empty, or the leak test below is vacuous"
    for org in (HOUSE, ORG_UNSET, ORG_JUNK):
        assert not set(_payload(wired, org)["nav_hidden"]) & set(ups)


def test_every_hidden_entry_is_a_real_page_shape(wired):
    """A stale or malformed entry hides nothing and is how a hide-list rots unnoticed."""
    for href in _payload(wired, ORG_UPS)["nav_hidden"]:
        assert href.startswith("/"), href
        assert " " not in href and not href.endswith("/"), href
        assert href.count("$") == 0 or href.endswith("$"), href


def test_the_payload_still_carries_everything_the_nav_gate_reads(wired):
    out = _payload(wired, ORG_UPS)
    for field in ("key", "label", "source", "uses_carriers", "nav_hidden", "hidden_modules",
                  "closing_hidden", "choices", "registry_ready", "closing_sections"):
        assert field in out, field
    assert out["uses_carriers"] is False
    assert len(out["choices"]) == len(V.HOUSE_VERTICALS)
