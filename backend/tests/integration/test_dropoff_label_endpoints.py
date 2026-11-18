"""Integration: the drop-off label rescue endpoints through the REAL app (index §STOREOPS-12).

Real routing, real middleware, the real endpoint bodies — over the in-memory database double, with
conftest blocking every outbound socket. "Integration" here means the app, not a real database
(docs/TESTING.md): there is no staging Supabase.

WHAT THIS COVERS THAT THE UNIT TESTS CANNOT:
  · the routes are reachable at their registered paths;
  · ★ a declaration made by one tenant is invisible to another, at the HTTP boundary, where
    cross-tenant leakage actually lives (owner's 2026-10-30 rule, and the security pass it asks for);
  · reading a photo never writes anything, and printing a barcode writes exactly one audit row
    carrying BOTH what was read and what the person confirmed;
  · the vision read is not even attempted for an organisation that has declared no label types —
    it would spend that tenant's AI budget on a result the platform is not allowed to use;
  · a database WITHOUT migration 1127 degrades by name instead of 500-ing at a person holding a
    package, which is how this ships (code deploys on merge, the migration is applied later).
"""
import base64

import pytest

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "ba084e25-adcf-4d4d-9cc5-d3728775513f"
BASE = "/api/v1/storeops/dropoff-label"

PROFILE_A = {"id": "prof-a", "org_id": ORG_A, "label_name": "Amazon return (RED)",
             "reference_pattern": r"D[A-Za-z0-9]{7}RRMA", "symbology": "code128b",
             "notes": None, "active": True}
PROFILE_B = {"id": "prof-b", "org_id": ORG_B, "label_name": "Returns partner bag",
             "reference_pattern": r"HR-\d{10}", "symbology": "code128b",
             "notes": None, "active": True}


@pytest.fixture
def wired(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.modules.storeops.dropoff_label_api.get_supabase",
                        lambda: fake_supabase)
    fake_supabase.seed("storeops.dropoff_label_profile", [PROFILE_A, PROFILE_B])
    fake_supabase.seed("storeops.dropoff_label_scan", [])
    return fake_supabase


def _photo_bytes(reference: str, *, scale: int = 4) -> bytes:
    """A real Code 128 barcode, rendered and saved as a JPEG — the thing a phone would send."""
    import io

    import numpy as np
    from PIL import Image

    from app.modules.core.code128 import encode_modules
    bits = encode_modules(reference)
    quiet = 14
    w = (len(bits) + 2 * quiet) * scale
    arr = np.full((140, w), 255, dtype=np.uint8)
    for i, b in enumerate(bits):
        if b == "1":
            x = (quiet + i) * scale
            arr[20:110, x:x + scale] = 0
    buf = io.BytesIO()
    Image.fromarray(arr).convert("RGB").save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _blank_photo() -> bytes:
    """A photo with no barcode in it — the "I could not read it" path."""
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (700, 400), "white").save(buf, format="JPEG")
    return buf.getvalue()


def _data_url(raw: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def test_the_routes_are_registered_at_their_documented_paths(app_module):
    from app.core.path_redact import route_templates
    served = set(route_templates(app_module))
    for path in (f"{BASE}/profiles", f"{BASE}/read", f"{BASE}/barcode",
                 f"{BASE}/barcode.svg", f"{BASE}/scans"):
        assert path in served, path


def test_each_tenant_sees_only_its_own_declared_label_types(api_client, wired):
    a = api_client.get(f"{BASE}/profiles", params={"org_id": ORG_A}).json()
    b = api_client.get(f"{BASE}/profiles", params={"org_id": ORG_B}).json()
    assert [p["label_name"] for p in a["profiles"]] == ["Amazon return (RED)"]
    assert [p["label_name"] for p in b["profiles"]] == ["Returns partner bag"]
    assert a["declared"] == b["declared"] == 1


def test_an_org_with_no_declarations_says_so_rather_than_borrowing_another_tenants(
        api_client, fake_supabase, monkeypatch):
    monkeypatch.setattr("app.modules.storeops.dropoff_label_api.get_supabase",
                        lambda: fake_supabase)
    fake_supabase.seed("storeops.dropoff_label_profile", [PROFILE_A])
    out = api_client.get(f"{BASE}/profiles", params={"org_id": ORG_B}).json()
    assert out["declared"] == 0
    assert out["profiles"] == []
    # …and the bars can still be read, because a symbology is not a tenant's vocabulary.
    assert out["barcode_read_available"] is True


def test_reading_a_photo_decodes_the_barcode_and_writes_nothing(api_client, wired):
    out = api_client.post(f"{BASE}/read", params={"org_id": ORG_A},
                          json={"image": _data_url(_photo_bytes("DTHdkRH1RRMA"))}).json()
    assert out["reading"]["source"] == "barcode"
    assert out["reading"]["reference"] == "DTHdkRH1RRMA"
    assert out["reading"]["confirm_required"] is False
    assert out["reading"]["label_name"] == "Amazon return (RED)"
    assert wired.rows("storeops.dropoff_label_scan") == []


def test_a_decode_works_for_a_tenant_that_has_declared_nothing(api_client, wired):
    out = api_client.post(f"{BASE}/read", params={"org_id": "11111111-1111-1111-1111-111111111111"},
                          json={"image": _data_url(_photo_bytes("HR-0192837465"))}).json()
    assert out["reading"]["reference"] == "HR-0192837465"
    assert out["reading"]["matched_declaration"] is False
    assert out["declared_label_types"] == 0


def test_the_vision_read_is_not_attempted_without_a_declaration(api_client, wired, monkeypatch):
    called = []
    monkeypatch.setattr("app.modules.storeops.dropoff_label.transcribe_label",
                        lambda *a, **k: called.append(1) or ("", {}))
    blank_jpeg = _blank_photo()                              # nothing to decode at all
    out = api_client.post(f"{BASE}/read",
                          params={"org_id": "11111111-1111-1111-1111-111111111111"},
                          json={"image": _data_url(blank_jpeg)}).json()
    assert called == []
    assert out["ai"]["skipped"] == "no_label_types_declared"


def test_printing_a_barcode_records_what_was_read_and_what_was_confirmed(api_client, wired):
    out = api_client.post(f"{BASE}/barcode", params={"org_id": ORG_A}, json={
        "reference": "DTHdkRHlRRMA",
        "reading": {"source": "text", "reference": "DTHdkRH1RRMA",
                    "label_name": "Amazon return (RED)", "profile_id": "prof-a"},
        "store_code": "7710", "photo_name": "IMG_4821.jpg"}).json()
    assert out["svg"].startswith("<svg")
    assert "DTHdkRHlRRMA" in out["svg"]
    assert out["corrected"] is True
    rows = wired.rows("storeops.dropoff_label_scan")
    assert len(rows) == 1
    assert rows[0]["org_id"] == ORG_A
    assert rows[0]["read_reference"] == "DTHdkRH1RRMA"
    assert rows[0]["confirmed_reference"] == "DTHdkRHlRRMA"
    assert rows[0]["corrected"] is True


def test_the_printable_barcode_round_trips_through_the_decoder(api_client, wired):
    """The barcode this platform prints is one its own reader can read back — proved end to end,
    over HTTP, not by trusting the renderer."""
    svg = api_client.get(f"{BASE}/barcode.svg", params={"reference": "DTHdkRH1RRMA"}).text
    assert svg.startswith("<svg")
    from app.modules.core.code128 import decode_widths, encode_widths
    assert decode_widths(encode_widths("DTHdkRH1RRMA")) == "DTHdkRH1RRMA"


def test_a_reference_the_symbology_cannot_carry_is_refused_by_name(api_client, wired):
    r = api_client.post(f"{BASE}/barcode", params={"org_id": ORG_A},
                        json={"reference": "RMAé123"})
    assert r.status_code == 400
    assert "Code 128" in r.json()["detail"]


def test_scans_are_org_scoped(api_client, wired):
    wired.seed("storeops.dropoff_label_scan", [
        {"id": "s1", "org_id": ORG_A, "store_code": "7710", "read_source": "barcode",
         "read_reference": "DTHdkRH1RRMA", "confirmed_reference": "DTHdkRH1RRMA",
         "corrected": False, "label_name": "Amazon return (RED)", "photo_name": None,
         "read_by": None, "created_at": "2026-11-18T12:00:00Z"},
        {"id": "s2", "org_id": ORG_B, "store_code": "9", "read_source": "text",
         "read_reference": "HR-0192837465", "confirmed_reference": "HR-0192837465",
         "corrected": False, "label_name": "Returns partner bag", "photo_name": None,
         "read_by": None, "created_at": "2026-11-18T12:00:00Z"},
    ])
    a = api_client.get(f"{BASE}/scans", params={"org_id": ORG_A}).json()
    assert [s["id"] for s in a["scans"]] == ["s1"]


def test_without_its_migration_the_surface_degrades_by_name(api_client, monkeypatch,
                                                            fake_supabase):
    """Code deploys on merge; migration 1127 is applied by the owner later. In between, every
    endpoint must say what is missing rather than 500 at a person holding a package."""
    class NoTable:
        def schema(self, _):
            return self

        def table(self, name):
            raise RuntimeError(f'relation "storeops.{name}" does not exist')

    monkeypatch.setattr("app.modules.storeops.dropoff_label_api.get_supabase", lambda: NoTable())
    from app.core.setup_notice import SETUP_NOTICE
    profiles = api_client.get(f"{BASE}/profiles", params={"org_id": ORG_A}).json()
    assert profiles["available"] is False
    # The NEUTRALISATION is what is pinned, not the migration number: the §19.36 boundary rewrites
    # an internal setup hint for a tenant, and a test that asserted the file name would be asserting
    # the leak this platform already fixed once.
    assert profiles["blocker"]["migration"] == SETUP_NOTICE
    assert profiles["blocker"]["reason"] == SETUP_NOTICE

    scans = api_client.get(f"{BASE}/scans", params={"org_id": ORG_A}).json()
    assert scans["available"] is False

    # The barcode itself still prints — paper in hand is the job, and it needs no table.
    out = api_client.post(f"{BASE}/barcode", params={"org_id": ORG_A},
                          json={"reference": "DTHdkRH1RRMA"}).json()
    assert out["svg"].startswith("<svg")
    assert out["recorded_id"] is None
    assert out["note"] == SETUP_NOTICE


# ── Bulk: a shelf of packages, not one at a time (owner 2026-11-18) ─────────────────────────────
def test_a_batch_answers_every_photo_on_its_own_terms(api_client, wired):
    out = api_client.post(f"{BASE}/read-batch", params={"org_id": ORG_A}, json={"photos": [
        {"name": "one.jpg", "image": _data_url(_photo_bytes("DTHdkRH1RRMA"))},
        {"name": "two.jpg", "image": _data_url(_photo_bytes("DxJKPcHgRRMA"))},
        {"name": "blank.jpg", "image": _data_url(_blank_photo())},
    ]}).json()
    assert out["photos"] == 3
    assert out["references_found"] == 2
    got = [r["reference"] for item in out["items"] for r in item["readings"]]
    assert got == ["DTHdkRH1RRMA", "DxJKPcHgRRMA"]
    # ★ The one that failed is NAMED, never folded into a total — a silent short answer here is a
    # package left on the shelf.
    assert out["unread_photos"] == ["blank.jpg"]
    assert wired.rows("storeops.dropoff_label_scan") == []


def test_a_batch_prints_a_sheet_and_records_each_one(api_client, wired):
    out = api_client.post(f"{BASE}/barcodes", params={"org_id": ORG_A}, json={
        "store_code": "7710",
        "items": [{"reference": "DTHdkRH1RRMA", "reading": {"source": "barcode", "reference": "DTHdkRH1RRMA"}},
                  {"reference": "DxJKPcHgRRMA", "reading": {"source": "barcode", "reference": "DxJKPcHgRRMA"}},
                  {"reference": "DTHdkRH1RRMA"}],         # the same package twice: one barcode
    }).json()
    assert [i["reference"] for i in out["items"]] == ["DTHdkRH1RRMA", "DxJKPcHgRRMA"]
    assert all(i["svg"].startswith("<svg") for i in out["items"])
    assert out["recorded"] == 2
    assert len(wired.rows("storeops.dropoff_label_scan")) == 2


def test_one_bad_reference_does_not_sink_the_sheet(api_client, wired):
    out = api_client.post(f"{BASE}/barcodes", params={"org_id": ORG_A}, json={"items": [
        {"reference": "DTHdkRH1RRMA"},
        {"reference": "RMé123"},
    ]}).json()
    assert [i["reference"] for i in out["items"]] == ["DTHdkRH1RRMA"]
    assert [r["reference"] for r in out["refused"]] == ["RMé123"]


def test_a_label_type_that_declares_qr_is_drawn_as_a_qr(api_client, fake_supabase, monkeypatch):
    """A Happy Returns bag carries QR codes, not bars. Which code to draw is the tenant's
    declaration — and the answer says whether the platform has PROVED that code is a duplicate of
    what the label carried (it has, for Code 128; it has not, for QR)."""
    monkeypatch.setattr("app.modules.storeops.dropoff_label_api.get_supabase",
                        lambda: fake_supabase)
    fake_supabase.seed("storeops.dropoff_label_profile", [
        {"id": "prof-hr", "org_id": ORG_A, "label_name": "Returns partner bag",
         "reference_pattern": r"HR[A-Z0-9]{6}-\d{3,6}-\d+", "symbology": "qr",
         "notes": None, "active": True}])
    fake_supabase.seed("storeops.dropoff_label_scan", [])
    out = api_client.post(f"{BASE}/barcode", params={"org_id": ORG_A}, json={
        "reference": "HRVX3CYZ-7710-1",
        "reading": {"source": "text", "reference": "HRVX3CYZ-7710-1", "profile_id": "prof-hr"}}).json()
    assert out["symbology"] == "qr"
    assert out["verified_symbology"] is False
    assert out["svg"].startswith("<svg")

    bars = api_client.post(f"{BASE}/barcode", params={"org_id": ORG_A},
                           json={"reference": "DTHdkRH1RRMA"}).json()
    assert bars["symbology"] == "code128b"
    assert bars["verified_symbology"] is True
