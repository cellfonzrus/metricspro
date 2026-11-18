"""Reading a drop-off return label from a PHOTOGRAPH — the bars first, the printed text second.

THE PROBLEM, stated as a class rather than as one store's instance. A package is consolidated by
scanning the reference barcode on its return label. When that scan fails the package is stranded:
the number is still THERE, printed under the bars and often still in the bars, but the store's
hand scanner could not read it off crumpled, curled or faintly-printed paper. So a phone photo is
taken, and this module recovers the reference from it.

TWO READS, AND THEY ARE NOT EQUALLY TRUSTWORTHY — which is the whole design:

  1. `decode_barcode_image` DECODES THE BARS in the photo. A camera sensor is not a laser scanner:
     it sees the whole symbol at once, can be re-thresholded, and every scanline is another
     attempt. In testing against the real labels this recovered the reference from photographs of
     exactly the packages the store's scanner had refused — including one photographed upside
     down. A decode is SELF-VERIFYING (Code 128 carries a modulo-103 check symbol), so a result
     here is a fact, not a guess, and it needs no declaration from the tenant: a published
     symbology is a policy, not anybody's vocabulary.

  2. Reading the PRINTED TEXT is the fallback, and it is a guess. `1` / `l` / `I` and `0` / `O` are
     one blurred pixel apart, and these references are mixed-case, so a text read is always handed
     to a person to confirm, with the ambiguous characters named. That path lives in
     dropoff_label.py because WHICH text on the label is the reference is a thing each tenant
     declares, not something this file may assume.

WHY THE DECODER ITSELF IS NOT HERE. The Code 128 symbol table is one fact with one home
(core/code128.py). This file measures bar and space widths in pixels and hands them over; it owns
finding a barcode in a picture, and nothing about what the bars mean.

DEGRADES RATHER THAN BREAKS: numpy/Pillow are imported lazily, so an environment without an
imaging stack loses the barcode read and keeps the text read, saying so by name.
"""
from __future__ import annotations

from collections import Counter

from app.modules.core.code128 import PATTERNS, START_B, decode_widths

# A photo bigger than this is scaled down before scanning: beyond roughly 1600px across, extra
# pixels buy no accuracy (the bars are already several pixels wide) and cost real time per request.
MAX_WIDTH = 1600
# Scanlines are taken every Nth row. A barcode printed for a hand scanner is tens of pixels tall in
# any usable photo, so every third row still gives dozens of independent attempts.
ROW_STEP = 3
# A reading is returned only when this many scanlines agree. One scanline that decodes WITH a valid
# check symbol is already strong, but a second agreeing line rules out a freak quantisation.
MIN_VOTES = 2
# Once this many scanlines agree, scanning stops: more agreement cannot change the answer, and a
# store person is standing at a counter with a package in hand.
ENOUGH_VOTES = 6
# A hard ceiling on the work one photo may cost. A photo WITHOUT a barcode is the expensive case —
# printed text produces thousands of runs to test — and that is exactly the case where the answer
# ("I could not read it, type it in") is already known to be acceptable. Without this, a screenshot
# uploaded by mistake would hold a worker for ten seconds.
MAX_SECONDS = 4.0


class PhotoReadUnavailable(RuntimeError):
    """The imaging stack is not installed here — stated by name, never silently treated as 'no
    barcode found', which would send a store person to manual entry for the wrong reason."""


def _runs(bits) -> list[int]:
    out: list[int] = []
    cur = bits[0]
    n = 0
    for v in bits:
        if v == cur:
            n += 1
        else:
            out.append(n)
            cur = v
            n = 1
    out.append(n)
    return out


def _start_here(widths: list[int], i: int) -> bool:
    """Could a Start B symbol begin at run `i`? Quantised against its own 11 modules, so this costs
    nothing and rejects almost every position — which is what makes scanning a whole photo without
    first 'finding the barcode' affordable. No band detection, no assumption that the label fills
    the frame, no assumption about where in the picture the barcode sits."""
    seg = widths[i:i + 6]
    if len(seg) < 6:
        return False
    m = sum(seg) / 11.0
    if m <= 0:
        return False
    return "".join(str(max(1, min(4, int(round(w / m))))) for w in seg) == PATTERNS[START_B]


def _decode_line(widths: list[int]) -> list[str]:
    """Every Code 128 symbol that starts anywhere along one scanline, read in both directions.

    Both directions because a package lands on the counter whichever way up it lands, and asking a
    store person to orient the label is how a tool stops being used.
    """
    found: list[str] = []
    for seq in (widths, widths[::-1]):
        for i in range(0, max(0, len(seq) - 20)):
            if not _start_here(seq, i):
                continue
            text = decode_widths(seq[i:])
            if text:
                found.append(text)
    return found


def decode_barcode_image(raw: bytes) -> dict:
    """Photo bytes → {"found": [...], "best": str|None, "votes": int, "scanlines": int}.

    `best` is None whenever no reading reached MIN_VOTES — never the most popular near-miss. A
    barcode this module cannot vouch for must send the caller to the text read, where a person
    confirms, rather than hand over a number that will scan cleanly into the wrong record.
    """
    try:
        import numpy as np
        from PIL import Image
    except Exception as e:  # pragma: no cover - environment, not logic
        raise PhotoReadUnavailable(f"image reading is not available here ({type(e).__name__})")
    import io

    img = Image.open(io.BytesIO(raw)).convert("L")
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, max(1, int(img.height * MAX_WIDTH / img.width))))
    g = np.asarray(img, dtype=float)
    h, w = g.shape
    if w < 60 or h < 10:
        return {"found": [], "best": None, "votes": 0, "scanlines": 0}

    # A local mean, not one threshold for the picture: half a label in shadow is the normal case,
    # and a global threshold turns the shaded half into one solid bar. Two window widths and four
    # darkness offsets are tried because the hard photos are hard in different ways — one of the
    # four real samples decodes ONLY at the wider window, and another only at the faintest offset.
    # A pass that fails costs one convolution; a pass that is missing costs the store a package.
    import time as _time
    deadline = _time.monotonic() + MAX_SECONDS
    votes: Counter = Counter()
    lines = 0
    for k in (max(9, (w // 40) | 1), max(9, (w // 22) | 1)):
        ker = np.ones(k) / k
        for y in range(0, h, ROW_STEP):
            if _time.monotonic() > deadline or (votes and votes.most_common(1)[0][1] >= ENOUGH_VOTES):
                break
            row = g[y]
            local = np.convolve(np.pad(row, (k // 2, k // 2), mode="edge"), ker, mode="valid")
            for offset in (1, 4, 8, 14, 22):  # a blurred print → a dark one
                bits = (row < local - offset).astype(int)
                idx = bits.nonzero()[0]
                if idx.size < 30:
                    continue
                widths = _runs(bits[idx[0]:idx[-1] + 1].tolist())
                if len(widths) < 20:
                    continue
                lines += 1
                for text in _decode_line(widths):
                    votes[text] += 1
    best = None
    top = votes.most_common(1)
    if _time.monotonic() > deadline and not top:
        # Say that the search was cut short rather than reporting a clean "no barcode here": the
        # two mean different things to the person holding the package.
        return {"found": [], "best": None, "votes": 0, "scanlines": lines, "timed_out": True}
    if top and top[0][1] >= MIN_VOTES:
        best = top[0][0]
    return {
        "found": [t for t, _ in votes.most_common(5)],
        "best": best,
        "votes": (top[0][1] if top else 0),
        "scanlines": lines,
        "timed_out": False,
    }
