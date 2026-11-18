"""A stranded drop-off package → a reference the store can scan again.

THE JOB. A customer brings a return; the store's printer produces a carrier label; the package is
consolidated later by scanning that label's reference barcode. When the barcode will not scan the
package stays in the back room and is never sent back. The reference itself is not lost — it is
printed on the label — so the platform reads it and prints a fresh barcode.

THE DECISION THIS FILE OWNS: what did we read, how sure are we, and what must a person check before
anything is printed. Everything else is somebody else's home:

  • core/code128.py              — what a barcode IS (the symbol table, encode and decode).
  • storeops/dropoff_photo.py    — finding bars in a photograph and measuring them.
  • this file                    — which string on a label is THE REFERENCE, and how much to trust it.

A TENANT'S LABELS ARE A TENANT'S VOCABULARY (owner directive 2026-10-30, CLAUDE.md "Every feature is
for EVERY tenant"). "Amazon prints RED, GREEN and BLUE labels and its reference looks like
D·······RRMA" is one tenant's observation of one vendor's paperwork — not a house fact. So the
profiles are a REQUIRED argument here, never a default with one tenant's words behind them, and an
organisation that has declared nothing is told it has declared nothing:

    read_reference(text, profiles=[])  ->  identified False, reason "no_label_types_declared"

never a silent fall back to the first tenant's patterns. Zero looks like an answer, which is exactly
why that class of defect hides (index §COMMCALC-49.10).

THE ONE THING THAT IS NOT A DECLARATION: decoding the bars. Code 128 is a published symbology with
its own check symbol, so a decoded barcode is self-verifying and tenant-neutral — it is a POLICY,
and a house default is right for a policy. That is why `resolve_reading` prefers the decoded bars
over any text match: a decode is a fact, a text read is a guess.

WHY A TEXT READ IS ALWAYS CONFIRMED BY A HUMAN. These references are mixed case and the characters
that matter most are the ones a camera blurs together: 1/l/I and 0/O and 5/S. Getting one wrong
produces a barcode that scans perfectly into the wrong record, which is worse than the package
sitting on the shelf. So a text read is returned with `confirm_required` true and the ambiguous
positions named, and the screen makes the person look at them.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# Characters a camera genuinely confuses, with what each might be instead. Used to ASK a person,
# never to silently substitute: an automatic correction here would be the platform guessing at a
# reference number, which is the one thing this feature must not do.
LOOKALIKES: dict[str, str] = {
    "0": "O", "O": "0", "1": "lI", "l": "1I", "I": "1l", "5": "S", "S": "5",
    "8": "B", "B": "8", "2": "Z", "Z": "2", "6": "G", "G": "6",
}

# How much of a photo's text the reader is allowed to consider. A label carries a tracking number, a
# postcode, a description and an address; every one of them is a string of characters that could
# pass a loose pattern, which is why a profile's pattern is anchored (see `_compiled`).
MAX_TEXT = 8000


class ProfileError(ValueError):
    """A declared label type that cannot be used, named rather than skipped — a tenant whose
    declaration is broken must hear that, not quietly get no reading."""


def _compiled(profile: dict) -> re.Pattern:
    """A declared profile's `reference_pattern` → a compiled, ANCHORED regular expression.

    Anchored at both ends on purpose: an unanchored `D.{7}RRMA` matches inside a longer string, so a
    smudge that turns one character into two would still "match" and hand back a truncated
    reference. A reference either looks exactly like what the tenant declared or it is not one.
    """
    pat = (profile or {}).get("reference_pattern")
    if not isinstance(pat, str) or not pat.strip():
        raise ProfileError(f"label type {profile.get('label_name')!r} declares no reference pattern")
    try:
        return re.compile(rf"\A(?:{pat.strip()})\Z")
    except re.error as e:
        raise ProfileError(
            f"label type {profile.get('label_name')!r} has an unusable reference pattern ({e})")


def ambiguous_positions(reference: str) -> list[dict]:
    """Every character of `reference` a photograph could have got wrong, with the alternatives.

    Returned so the SCREEN can point at them. The platform does not choose between `1` and `l`; the
    person holding the package does, by looking at the label.
    """
    out: list[dict] = []
    for i, ch in enumerate(reference or ""):
        if ch in LOOKALIKES:
            out.append({"index": i, "char": ch, "could_be": list(LOOKALIKES[ch])})
    return out


def matches_any(reference: str, profiles: Iterable[dict]) -> dict | None:
    """The first declared label type whose pattern the reference fits, or None.

    None is a real answer: it means this organisation has not declared a label type that looks like
    this, which is a thing the store should see rather than a reason to accept the string anyway.
    """
    for p in profiles or []:
        if _compiled(p).match(reference or ""):
            return p
    return None


def read_reference(text: str, profiles: list[dict]) -> dict:
    """Text read off a label photo → the candidate references the ORG's own declarations recognise.

    `profiles` is required and is not defaulted. An organisation that has declared no label types
    gets `identified: False` with the reason said out loud — never another tenant's patterns.
    """
    if profiles is None:
        raise TypeError("read_reference needs the organisation's declared label types")
    if not profiles:
        return {"identified": False, "reason": "no_label_types_declared", "candidates": []}
    body = (text or "")[:MAX_TEXT]
    # SEARCHED, not tokenised. An earlier version split the text on every non-alphanumeric character
    # and tested the pieces — which silently refused every reference containing a hyphen or a slash,
    # i.e. one of the two tenant shapes this was written for. The declaration says what a reference
    # looks like; the reader must not also decide which characters a reference may contain. What it
    # DOES impose is a boundary: a match must not continue into a longer alphanumeric run, so a
    # smudge that lengthens a reference cannot have a valid-looking prefix pulled out of it.
    seen: set[str] = set()
    candidates: list[dict] = []
    for profile in profiles:
        pattern = (profile.get("reference_pattern") or "").strip()
        _compiled(profile)                      # a broken declaration is named here, not skipped
        try:
            scanner = re.compile(rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])")
        except re.error as e:                   # unreachable via _compiled, kept honest anyway
            raise ProfileError(f"label type {profile.get('label_name')!r} cannot be searched ({e})")
        for m in scanner.finditer(body):
            tok = m.group(0)
            if tok in seen:
                continue
            seen.add(tok)
            candidates.append({
                "reference": tok,
                "label_name": profile.get("label_name"),
                "profile_id": profile.get("id"),
                "ambiguous": ambiguous_positions(tok),
            })
    if not candidates:
        return {"identified": False, "reason": "no_reference_matched_a_declared_label_type",
                "candidates": []}
    return {"identified": True, "candidates": candidates}


def resolve_reading(*, barcode: dict | None, text: str, profiles: list[dict]) -> dict:
    """Everything known about one photo → ONE answer the screen can act on.

    The order is the point. A DECODED barcode wins, because Code 128's check symbol makes it a fact
    and because it needs no declaration — so a tenant that has declared nothing can still rescue a
    package whose bars are merely too faint for a hand scanner. A text read is only ever a
    suggestion, and says so.

    Returns {source, reference, label_name, confirm_required, ambiguous, candidates, reason}.
    `source` is "barcode", "text" or None; `reference` is None whenever source is None. Nothing here
    ever returns a reference it is not prepared to name the provenance of.
    """
    decoded = ((barcode or {}).get("best") or "").strip()
    if decoded:
        hit = matches_any(decoded, profiles or [])
        return {
            "source": "barcode",
            "reference": decoded,
            # A decode is trustworthy even when it matches nothing declared — the bars checked out.
            # Saying WHICH declared type it matched is useful; requiring one would make a correct
            # reading unusable because the paperwork had not been described yet.
            "label_name": (hit or {}).get("label_name"),
            "profile_id": (hit or {}).get("id"),
            "matched_declaration": hit is not None,
            "confirm_required": False,
            "ambiguous": [],
            "candidates": [],
            "reason": None,
        }
    read = read_reference(text, profiles if profiles is not None else [])
    if not read.get("identified"):
        return {"source": None, "reference": None, "label_name": None, "profile_id": None,
                "matched_declaration": False, "confirm_required": True, "ambiguous": [],
                "candidates": [], "reason": read.get("reason")}
    first = read["candidates"][0]
    return {
        "source": "text",
        "reference": first["reference"],
        "label_name": first.get("label_name"),
        "profile_id": first.get("profile_id"),
        "matched_declaration": True,
        # ALWAYS true for a text read, even when nothing looks ambiguous: the model read a
        # photograph, and the cost of a wrong reference is a package delivered to the wrong vendor.
        "confirm_required": True,
        "ambiguous": first.get("ambiguous") or [],
        "candidates": read["candidates"],
        "reason": None,
    }


# ── What the vision model is asked ───────────────────────────────────────────────────────────────
# Deliberately NOT "find the RMA". The model is asked to TRANSCRIBE, and this module decides what
# counts as a reference using the tenant's declarations. A prompt that names one tenant's field
# ("RMA") would be that tenant's vocabulary smuggled into shared code — RULE TWO — and would read
# nothing on a label that calls it something else.
TRANSCRIBE_PROMPT = (
    "This is a photo of a parcel/return shipping label. Transcribe EVERY line of printed text you "
    "can see, exactly as printed, one line per line, preserving upper and lower case precisely. "
    "Do not interpret, translate, correct or re-order anything. If a character is unclear, "
    "transcribe your best reading of it. Return ONLY the transcribed text, no prose, no code fences."
)


def transcribe_label(raw: bytes, ext: str, *, model: str, timeout_s: float, max_retries: int,
                     api_key: str) -> tuple[str, dict]:
    """Photo bytes → (transcribed text, meta). NEVER raises, and never returns a partial guess as
    text: a failure comes back as ("", {"error"/"skipped": …}) so the caller falls to manual entry
    with the reason visible rather than to an empty-looking label.

    Capped timeout and retries are arguments, not defaults invented here: the platform already
    learned (closing/ai_limits.py) that an uncapped vision call pins a worker thread for half an
    hour and that every store uploads inside the same hour.
    """
    if not api_key or not raw:
        return "", {"skipped": "no_vision_key"}
    try:
        from anthropic import Anthropic
        cli = Anthropic(api_key=api_key, timeout=timeout_s, max_retries=max_retries)
        media = "image/png" if str(ext).lower() == "png" else "image/jpeg"
        import base64 as _b64
        msg = cli.messages.create(
            model=model, max_tokens=1200,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media,
                                             "data": _b64.b64encode(raw).decode("ascii")}},
                {"type": "text", "text": TRANSCRIBE_PROMPT}]}])
        text = "".join(getattr(b, "text", "") for b in (msg.content or []))
        return text.strip(), {"model": model, "response": msg}
    except Exception as e:   # never surface a provider string or a stack trace to a store screen
        return "", {"error": f"{type(e).__name__}"}


def scan_row(*, org_id: str, store_code: str | None, reading: dict, confirmed_reference: str,
             source_photo_name: str | None, read_by: str | None) -> dict[str, Any]:
    """The audit row for one rescued package. PURE — it builds the dict; the caller writes it.

    Why this is recorded at all: the reference that gets re-printed is the one a PERSON confirmed,
    which may differ from what was read. Keeping both, with which of the two the machine proposed,
    is what makes a later "this package went to the wrong vendor" answerable instead of a mystery.
    """
    return {
        "org_id": org_id,
        "store_code": (store_code or "").strip() or None,
        "read_source": reading.get("source"),
        "read_reference": reading.get("reference"),
        "confirmed_reference": confirmed_reference,
        "corrected": bool(reading.get("reference")
                          and reading.get("reference") != confirmed_reference),
        "label_name": reading.get("label_name"),
        "profile_id": reading.get("profile_id"),
        "photo_name": (source_photo_name or "")[:200] or None,
        "read_by": read_by,
    }
