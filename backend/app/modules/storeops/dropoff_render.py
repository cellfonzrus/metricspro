"""ONE home for "draw this reference in the symbology its label type declares".

WHY IT EXISTS. The first four labels were 1D barcodes and it was tempting to call Code 128 "the"
answer. The fifth label was a Happy Returns bag (2026-11-18) and it carries QR codes — a different
shape of code entirely, for the same job. Had the renderer been a `render_svg(reference)` call
scattered through the endpoints, adding QR would have meant an `if` in each of them, and the next
carrier would have meant another. So WHICH code to draw is read, once, from the tenant's own
declaration of that label type, and every caller comes through here.

WHAT IS A POLICY AND WHAT IS A TENANT'S WORD. The symbologies themselves are published standards —
a policy, where a house default is right, so an UNdeclared label type draws Code 128, which is what
every carrier label measured here uses and what a store's hand scanner reads by default. Which
symbology a PARTICULAR label type uses is a tenant's observation of a vendor's paperwork and lives
in its declaration (`storeops.dropoff_label_profile.symbology`, mig 1127).

STATED, NOT ASSUMED: this platform has VERIFIED, by decoding the photographs, that an Amazon return
label's Code 128 carries exactly the reference printed beneath it. It has NOT verified what a Happy
Returns QR carries — that needs one scan of a working bag label to confirm, and until it does, a QR
re-printed from the bag id is the platform's best reconstruction rather than a proven duplicate.
`verified_symbology()` says which is which, so a screen can tell a person the difference instead of
presenting both with the same confidence.
"""
from __future__ import annotations

from app.modules.core.code128 import Code128Error, render_svg as _code128_svg

CODE128 = "code128b"
QR = "qr"
SUPPORTED = (CODE128, QR)

# Measured from real labels (2026-11-18): the bars on Amazon's red, green and blue return labels
# were decoded from photographs and carry exactly the printed reference. Nothing else is proven.
VERIFIED = (CODE128,)


def normalize(symbology: str | None) -> str:
    """A declared symbology → one this module draws. Unknown or blank reads as Code 128 (the house
    default for the POLICY), rather than refusing: a tenant mid-setup still gets a usable barcode."""
    s = (symbology or "").strip().lower().replace("-", "").replace("_", "")
    if s in ("qr", "qrcode"):
        return QR
    return CODE128


def verified_symbology(symbology: str | None) -> bool:
    """Has this platform PROVED that a code it draws is what the original label carried?

    True only for Code 128, and only because the real labels were decoded here. A screen uses this
    to say "this is a duplicate of the barcode" versus "this is built from the reference we read",
    which are different promises to somebody about to send a package back.
    """
    return normalize(symbology) in VERIFIED


def render(reference: str, symbology: str | None = None) -> str:
    """`reference` → an SVG, in the declared symbology. Raises Code128Error by name on input the
    symbology cannot carry — never a silently mangled code."""
    kind = normalize(symbology)
    if kind == QR:
        return _qr_svg(reference)
    return _code128_svg(reference)


def _qr_svg(reference: str) -> str:
    """A QR, drawn by `segno` (already a dependency — the referral QR uses it; one library, one
    home). A missing library is reported as a refusal in this module's own words, because the
    alternative is a store person meeting a stack trace."""
    if not (reference or "").strip():
        raise Code128Error("Nothing to encode — the reference is empty.")
    try:
        import io

        import segno
    except Exception as e:  # pragma: no cover - environment, not logic
        raise Code128Error(f"QR codes cannot be drawn here ({type(e).__name__})")
    buf = io.BytesIO()
    # Error correction M: a re-printed code gets taped to a bag and handled, and the extra
    # redundancy costs a few millimetres. `scale` 4 keeps a module comfortably above what a counter
    # scanner resolves; the border is the quiet zone the specification requires.
    segno.make(reference.strip(), error="m").save(buf, kind="svg", scale=4, border=3, xmldecl=False)
    return buf.getvalue().decode("utf-8")
