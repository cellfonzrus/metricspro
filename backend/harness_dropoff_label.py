"""Harness — the drop-off label rescue: the tenant rule, the reading decision, and the photo reader.
DB-free and network-free (the photo part renders its own barcodes and photographs them in memory).

WHAT IT PROVES, and the defect each part is aimed at:

  A. THE TENANT RULE (CLAUDE.md, owner 2026-10-30). An organisation that has declared no label types
     is TOLD so; it never gets another tenant's patterns. And one tenant's declaration never
     recognises another tenant's reference — the negative half, which is what pins the mechanism
     rather than the symptom. `profiles` is a REQUIRED argument; calling without it is an error, so
     a future caller cannot quietly answer on the house org's behalf.
  B. A BROKEN DECLARATION IS NAMED, not skipped. A tenant whose pattern will not compile must hear
     that, because the alternative reads on screen as "this label has no reference on it".
  C. PATTERNS ARE ANCHORED. An unanchored pattern matches INSIDE a longer string, which would hand
     back a truncated reference that scans perfectly into the wrong record.
  D. THE READING ORDER. A decoded barcode (check-verified) beats a text read, needs no declaration,
     and is not marked for confirmation; a text read ALWAYS is, and names its ambiguous characters.
  E. LOOK-ALIKES ARE REPORTED, NEVER CORRECTED. The platform does not choose between `1` and `l`.
  F. THE PHOTO READER finds a barcode in a photograph — tilted, blurred, inverted, under a shadow —
     and finds NOTHING in a picture that has no barcode, which is the property that keeps it from
     inventing a reference out of printed text.
  G. THE AUDIT ROW keeps both what was read and what the person confirmed, and flags the difference.

Run: cd backend && python3 harness_dropoff_label.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app.modules.core.code128 import encode_modules  # noqa: E402
from app.modules.storeops import dropoff_label as L  # noqa: E402
from app.modules.storeops import dropoff_photo as P  # noqa: E402

PASS, FAIL = [], []


def check(label, got, want=True):
    """EQUALITY, with `want` defaulting to True. Deliberately not check(label, cond, detail): that
    shape passes whenever the third argument is truthy and reports green while proving nothing."""
    if got == want:
        PASS.append(label)
        print(f"  ✓ {label}")
    else:
        FAIL.append(label)
        print(f"  ✗ {label}\n      got:  {got!r}\n      want: {want!r}")


def section(t):
    print(f"\n── {t}")


# Two tenants, each with its OWN description of its OWN paperwork. Neither is a house default.
TENANT_A = [{"id": "a1", "label_name": "Amazon return (RED/GREEN/BLUE)",
             "reference_pattern": r"D[A-Za-z0-9]{7}RRMA"}]
TENANT_B = [{"id": "b1", "label_name": "Returns partner bag",
             "reference_pattern": r"HR-\d{10}"}]

A_LABEL = ("RMA: DTHdkRH1RRMA\nTrack No: 1ZXJ29269035544760\nRED   Fri 11/13\n"
           "Description:\nDr. Brown's Anti-Colic Options+ Wide-Neck Glass Baby Bottle, 5 oz, 3-Pack\n"
           "To/Return Center:\nBKY1 D6 Noram International Partners LLC\n1085 Aviation Blvd\n"
           "Hebron  KY 41048")
B_LABEL = "Return bag\nHR-0192837465\nDrop at counter"


# ── A. the tenant rule ───────────────────────────────────────────────────────────────────────────
section("A. a tenant's labels are a tenant's vocabulary")
check("an org that declared nothing is told so, and gets no candidates",
      L.read_reference(A_LABEL, [])["reason"], "no_label_types_declared")
check("…and identifies nothing", L.read_reference(A_LABEL, [])["identified"], False)
check("tenant A's label reads under tenant A's declaration",
      [c["reference"] for c in L.read_reference(A_LABEL, TENANT_A)["candidates"]], ["DTHdkRH1RRMA"])
check("tenant B's label reads under tenant B's declaration",
      [c["reference"] for c in L.read_reference(B_LABEL, TENANT_B)["candidates"]], ["HR-0192837465"])
check("★ tenant A's label is NOT recognised by tenant B's declaration",
      L.read_reference(A_LABEL, TENANT_B)["identified"], False)
check("★ tenant B's label is NOT recognised by tenant A's declaration",
      L.read_reference(B_LABEL, TENANT_A)["identified"], False)
check("the reason names the missing recognition, not a missing label",
      L.read_reference(B_LABEL, TENANT_A)["reason"], "no_reference_matched_a_declared_label_type")
try:
    L.read_reference(A_LABEL, None)      # type: ignore[arg-type]
    check("declarations cannot be omitted", False)
except TypeError:
    check("declarations cannot be omitted", True)
check("the matched candidate carries the tenant's OWN name for the label",
      L.read_reference(A_LABEL, TENANT_A)["candidates"][0]["label_name"],
      "Amazon return (RED/GREEN/BLUE)")
# A tenant declaring BOTH shapes gets both, each attributed — one org, several vendors.
BOTH = TENANT_A + TENANT_B
names = sorted(c["label_name"] for c in L.read_reference(A_LABEL + "\n" + B_LABEL, BOTH)["candidates"])
check("one org may declare several label types and each match is attributed",
      names, ["Amazon return (RED/GREEN/BLUE)", "Returns partner bag"])


# ── B. a broken declaration is named ─────────────────────────────────────────────────────────────
section("B. a declaration that cannot be used is named, not skipped")
for bad, why in [({"label_name": "x", "reference_pattern": "D[("}, "unclosed group"),
                 ({"label_name": "x", "reference_pattern": "   "}, "blank pattern"),
                 ({"label_name": "x"}, "no pattern at all")]:
    try:
        L._compiled(bad)
        check(f"refuses a declaration with {why}", False)
    except L.ProfileError:
        check(f"refuses a declaration with {why}", True)


# ── C. patterns are anchored ─────────────────────────────────────────────────────────────────────
section("C. a pattern matches a whole reference or nothing")
check("a reference with a trailing character does not match",
      L.matches_any("DTHdkRH1RRMAX", TENANT_A), None)
check("a reference with a leading character does not match",
      L.matches_any("XDTHdkRH1RRMA", TENANT_A), None)
check("the exact reference matches", (L.matches_any("DTHdkRH1RRMA", TENANT_A) or {}).get("id"), "a1")
check("a reference embedded in a longer run of text is not pulled out mid-string",
      L.read_reference("ZZDTHdkRH1RRMAZZ", TENANT_A)["identified"], False)


# ── D. what is trusted, and what must be confirmed ───────────────────────────────────────────────
section("D. a decoded barcode is a fact; a text read is a suggestion")
r = L.resolve_reading(barcode={"best": "DTHdkRH1RRMA"}, text="", profiles=TENANT_A)
check("a decoded barcode is the source", r["source"], "barcode")
check("a decoded barcode needs no confirmation", r["confirm_required"], False)
check("a decoded barcode is attributed to the declaration it matches", r["label_name"],
      "Amazon return (RED/GREEN/BLUE)")

r0 = L.resolve_reading(barcode={"best": "DTHdkRH1RRMA"}, text="", profiles=[])
check("★ a decode works for a tenant that has declared nothing", r0["reference"], "DTHdkRH1RRMA")
check("…and says it matched no declaration", r0["matched_declaration"], False)
check("…and still does not ask for confirmation", r0["confirm_required"], False)

rt = L.resolve_reading(barcode={"best": None}, text=A_LABEL, profiles=TENANT_A)
check("a text read is the source when the bars could not be decoded", rt["source"], "text")
check("a text read is ALWAYS confirmed by a person", rt["confirm_required"], True)
check("a text read returns the reference it found", rt["reference"], "DTHdkRH1RRMA")
check("a text read names its look-alike characters",
      [a["char"] for a in rt["ambiguous"]], ["1"])

rn = L.resolve_reading(barcode={"best": None}, text="nothing useful here", profiles=TENANT_A)
check("nothing readable returns no reference", rn["reference"], None)
check("…with no source claimed", rn["source"], None)
check("…and the reason said out loud", rn["reason"], "no_reference_matched_a_declared_label_type")
check("nothing readable and nothing declared says THAT instead",
      L.resolve_reading(barcode=None, text="x", profiles=[])["reason"], "no_label_types_declared")
# The tracking number on the same label must never be mistaken for the reference.
check("the tracking number on the label is not returned as the reference",
      "1ZXJ29269035544760" not in str(L.read_reference(A_LABEL, TENANT_A)), True)


# ── E. look-alikes are reported, never corrected ─────────────────────────────────────────────────
section("E. the platform never chooses between 1 and l")
amb = L.ambiguous_positions("D1lO5RRMA")
check("every confusable character is flagged with its position",
      [(a["index"], a["char"]) for a in amb], [(1, "1"), (2, "l"), (3, "O"), (4, "5")])
check("'1' offers l and I", amb[0]["could_be"], ["l", "I"])
check("a reference with no confusable characters flags nothing",
      L.ambiguous_positions("DxJKPcHgRRMA"), [])
check("the reference itself is returned untouched",
      L.resolve_reading(barcode={"best": None}, text="RMA: DTHdkRH1RRMA",
                        profiles=TENANT_A)["reference"], "DTHdkRH1RRMA")


# ── F. the photo reader ──────────────────────────────────────────────────────────────────────────
section("F. finding a barcode in a photograph")
try:
    import numpy as np
    from PIL import Image, ImageFilter
    _IMAGING = True
except Exception:                                        # pragma: no cover - environment
    _IMAGING = False
    print("  ! numpy/Pillow not installed — photo checks skipped (the API degrades by name)")

if _IMAGING:
    def photograph(text, *, rotate=0.0, blur=0.0, scale=3, shade=False, invert=False):
        """Render a real Code 128 barcode and then abuse it the way a store photo abuses one."""
        bits = encode_modules(text)
        quiet = 14
        w = (len(bits) + 2 * quiet) * scale
        h = 150
        arr = np.full((h, w), 255, dtype=np.uint8)
        for i, b in enumerate(bits):
            if b == "1":
                x = (quiet + i) * scale
                arr[20:h - 40, x:x + scale] = 0
        img = Image.fromarray(arr).convert("L")
        if rotate:
            img = img.rotate(rotate, expand=True, fillcolor=255)
        if blur:
            img = img.filter(ImageFilter.GaussianBlur(blur))
        if shade:
            a = np.asarray(img, dtype=float)
            ramp = np.linspace(1.0, 0.45, a.shape[1])[None, :]   # one half of the label in shadow
            img = Image.fromarray((a * ramp).astype(np.uint8))
        if invert:
            img = img.rotate(180, expand=True, fillcolor=255)
        import io
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=88)
        return buf.getvalue()

    REF = "DTHdkRH1RRMA"
    for name, kw in [("a clean photo", {}),
                     ("a photo taken at an angle", {"rotate": 6.0}),
                     ("a blurred photo", {"blur": 0.8}),
                     ("an upside-down package", {"invert": True}),
                     ("half the label in shadow", {"shade": True}),
                     ("angled, blurred and shaded at once",
                      {"rotate": -5.0, "blur": 0.9, "shade": True})]:
        got = P.decode_barcode_image(photograph(REF, **kw)).get("best")
        check(f"reads the reference off {name}", got, REF)

    # Blur is the hard limit, and it is a RATIO, not an absolute: the bars survive to roughly a
    # third of a module and then they are physically gone. Past that the honest answer is "I could
    # not read it", which hands the person the text path — not a guess at what the bars said.
    check("a badly blurred photo yields no barcode rather than a guess",
          P.decode_barcode_image(photograph(REF, blur=2.6)).get("best"), None)
    check("…and the same blur over fatter bars still reads",
          P.decode_barcode_image(photograph(REF, blur=2.0, scale=8)).get("best"), REF)

    other = P.decode_barcode_image(photograph("HR-0192837465")).get("best")
    check("reads a different tenant's reference shape just as well", other, "HR-0192837465")

    # ★ The property that stops it inventing a reference: a picture with no barcode reads as none.
    import io as _io
    blank = Image.new("RGB", (900, 500), "white")
    buf = _io.BytesIO(); blank.save(buf, format="JPEG")
    check("★ a blank photo yields no barcode", P.decode_barcode_image(buf.getvalue())["best"], None)

    noise = Image.fromarray((np.random.default_rng(7).integers(0, 255, (400, 900))
                             ).astype(np.uint8)).convert("RGB")
    buf = _io.BytesIO(); noise.save(buf, format="JPEG")
    check("★ a photo of noise yields no barcode", P.decode_barcode_image(buf.getvalue())["best"], None)

    # Striped things that are NOT barcodes: a shelf edge, a radiator, printed text at a distance.
    stripes = np.full((300, 900), 255, dtype=np.uint8)
    stripes[:, ::7] = 0
    buf = _io.BytesIO(); Image.fromarray(stripes).convert("RGB").save(buf, format="JPEG")
    check("★ regular stripes are not read as a barcode",
          P.decode_barcode_image(buf.getvalue())["best"], None)

    tiny = P.decode_barcode_image(photograph(REF, scale=1))
    check("a barcode too small to measure is reported as unread, not guessed",
          tiny["best"] in (None, REF), True)


# ── G. the audit row ─────────────────────────────────────────────────────────────────────────────
section("G. what was read, what was confirmed, and the difference")
reading = L.resolve_reading(barcode={"best": None}, text="RMA: DTHdkRH1RRMA", profiles=TENANT_A)
same = L.scan_row(org_id="org-a", store_code=" 7710 ", reading=reading,
                  confirmed_reference="DTHdkRH1RRMA", source_photo_name="IMG_1.jpg",
                  read_by="rep@example.com")
check("the row keeps what the platform read", same["read_reference"], "DTHdkRH1RRMA")
check("the row keeps what the person confirmed", same["confirmed_reference"], "DTHdkRH1RRMA")
check("an unchanged reference is not flagged as corrected", same["corrected"], False)
check("the store code is trimmed", same["store_code"], "7710")
fixed = L.scan_row(org_id="org-a", store_code=None, reading=reading,
                   confirmed_reference="DTHdkRHlRRMA", source_photo_name=None, read_by=None)
check("★ a person's correction is recorded as a correction", fixed["corrected"], True)
check("…with both readings kept",
      (fixed["read_reference"], fixed["confirmed_reference"]),
      ("DTHdkRH1RRMA", "DTHdkRHlRRMA"))
check("no store is None, never an empty string", fixed["store_code"], None)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
