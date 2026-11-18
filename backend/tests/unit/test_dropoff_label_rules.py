"""Unit: the rules behind rescuing a drop-off package (index §STOREOPS-12).

Pure functions only — no app, no database, no network. The exhaustive proof lives in
`backend/harness_dropoff_label.py`; what is pinned HERE is the handful of properties a future
change is most likely to break quietly, written as the project's test standard asks (2026-10-28).

THE ONE THAT MATTERS MOST is the tenant rule (owner 2026-10-30): an organisation that has declared
no label types must be TOLD so, and one tenant's declaration must never recognise another tenant's
reference. Zero looks like an answer, which is exactly why that defect class hides.
"""
import pytest

from app.modules.storeops import dropoff_label as L

AMAZON = [{"id": "a1", "label_name": "Amazon return (RED)",
           "reference_pattern": r"D[A-Za-z0-9]{7}RRMA"}]
PARTNER = [{"id": "b1", "label_name": "Returns partner bag",
            "reference_pattern": r"HR-\d{10}"}]

AMAZON_LABEL = "RMA: DTHdkRH1RRMA\nTrack No: 1ZXJ29269035544760\nRED  Fri 11/13"
PARTNER_LABEL = "Returns bag\nHR-0192837465"


def test_an_org_that_declared_nothing_is_told_so():
    out = L.read_reference(AMAZON_LABEL, [])
    assert out["identified"] is False
    assert out["reason"] == "no_label_types_declared"
    assert out["candidates"] == []


def test_declarations_cannot_be_omitted_by_a_caller():
    with pytest.raises(TypeError):
        L.read_reference(AMAZON_LABEL, None)


@pytest.mark.parametrize("text,profiles,expected", [
    (AMAZON_LABEL, AMAZON, "DTHdkRH1RRMA"),
    (PARTNER_LABEL, PARTNER, "HR-0192837465"),      # a hyphen inside a reference must survive
])
def test_each_tenant_reads_its_own_label(text, profiles, expected):
    assert L.read_reference(text, profiles)["candidates"][0]["reference"] == expected


@pytest.mark.parametrize("text,profiles", [
    (AMAZON_LABEL, PARTNER),
    (PARTNER_LABEL, AMAZON),
])
def test_one_tenants_declaration_never_recognises_anothers_reference(text, profiles):
    assert L.read_reference(text, profiles)["identified"] is False


def test_the_tracking_number_is_not_mistaken_for_the_reference():
    found = [c["reference"] for c in L.read_reference(AMAZON_LABEL, AMAZON)["candidates"]]
    assert found == ["DTHdkRH1RRMA"]


def test_a_pattern_matches_a_whole_reference_or_nothing():
    assert L.matches_any("DTHdkRH1RRMAX", AMAZON) is None
    assert L.read_reference("ZZDTHdkRH1RRMAZZ", AMAZON)["identified"] is False


def test_a_declaration_that_cannot_compile_is_named():
    with pytest.raises(L.ProfileError):
        L._compiled({"label_name": "broken", "reference_pattern": "D[("})


def test_a_decoded_barcode_beats_a_text_read_and_needs_no_declaration():
    r = L.resolve_reading(barcode={"best": "DTHdkRH1RRMA"}, text=AMAZON_LABEL, profiles=[])
    assert (r["source"], r["reference"], r["confirm_required"]) == (
        "barcode", "DTHdkRH1RRMA", False)
    assert r["matched_declaration"] is False


def test_a_text_read_is_always_confirmed_and_names_its_lookalikes():
    r = L.resolve_reading(barcode={"best": None}, text=AMAZON_LABEL, profiles=AMAZON)
    assert r["source"] == "text"
    assert r["confirm_required"] is True
    assert [a["char"] for a in r["ambiguous"]] == ["1"]
    assert r["reference"] == "DTHdkRH1RRMA"      # reported, never auto-corrected


def test_a_correction_by_a_person_is_recorded_as_one():
    reading = L.resolve_reading(barcode={"best": None}, text=AMAZON_LABEL, profiles=AMAZON)
    row = L.scan_row(org_id="org-a", store_code="7710", reading=reading,
                     confirmed_reference="DTHdkRHlRRMA", source_photo_name=None, read_by="rep@x")
    assert row["corrected"] is True
    assert (row["read_reference"], row["confirmed_reference"]) == ("DTHdkRH1RRMA", "DTHdkRHlRRMA")
