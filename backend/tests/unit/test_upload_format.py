"""Unit: THE one home for "what formats may an uploaded document be" (index §POS-16.3).

WHAT WAS WRONG. The rule lived inside `hr/router.py` — `_MAGIC`, `_sniff_format`,
`_format_allowed`, `_DEFAULT_UPLOAD_FORMATS` — reachable only by the three onboarding upload
routes. The W-9 a wholesale customer sends back (§POS-16) is the same question asked by a different
module, and copying the sniffer there is how one upload path would come to accept a renamed
executable while the other refused it.

So the fact moves to `app/core/upload_format.py` and HR dereferences it. These tests pin the
behaviour that must not change in the move — above all that the EXTENSION never grants anything.
"""
import pytest

from app.core import upload_format as uf

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\nrest of a pdf"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
SCRIPT = b"#!/bin/sh\nrm -rf /\n"
ZIP = b"PK\x03\x04" + b"\x00" * 20


def test_a_file_is_what_its_bytes_say():
    assert uf.sniff(PDF) == "pdf"
    assert uf.sniff(JPEG) == "jpeg"
    assert uf.sniff(PNG) == "png"


def test_an_unrecognised_header_is_nothing_however_it_is_named():
    assert uf.sniff(SCRIPT) is None
    assert uf.sniff(ZIP) is None
    assert uf.sniff(b"") is None


def test_the_extension_can_never_grant_access():
    ok, detected = uf.allowed(SCRIPT, "totally-a.pdf", ("pdf",))
    assert ok is False
    assert detected == "pdf"      # the NAME's claim, reported only to word the error


def test_a_format_outside_the_allow_list_is_refused_even_when_real():
    ok, detected = uf.allowed(PNG, "scan.png", ("pdf",))
    assert ok is False and detected == "png"


def test_jpg_and_jpeg_are_one_format():
    assert uf.allowed(JPEG, "scan.jpg", ("jpg",))[0] is True
    assert uf.allowed(JPEG, "scan.jpg", ("jpeg",))[0] is True


def test_an_empty_allow_list_falls_back_to_the_declared_default_not_to_everything():
    assert uf.DEFAULT_FORMATS == ("pdf", "jpeg")
    assert uf.allowed(PDF, "w9.pdf", ())[0] is True
    assert uf.allowed(PNG, "w9.png", ())[0] is False


def test_the_refusal_is_a_sentence_naming_what_is_accepted():
    s = uf.refusal_sentence(("pdf", "jpeg"), "png")
    assert "PDF" in s and "JPEG" in s and "PNG" in s
    assert s.rstrip().endswith(".")


def test_the_refusal_says_so_when_the_file_is_not_recognised_at_all():
    s = uf.refusal_sentence(("pdf",), None)
    assert "could not be recognized" in s or "could not be recognised" in s


@pytest.mark.parametrize("label,data", [("pdf", PDF), ("jpeg", JPEG), ("png", PNG)])
def test_every_declared_format_is_sniffable(label, data):
    assert uf.sniff(data) == label
    assert label in uf.FORMATS
