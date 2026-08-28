"""PDF → words, for the receipt-format parsers. Thin I/O wrapper over pdfplumber (already a backend
dependency). Kept out of receipt_formats/ so those stay pure/unit-testable without pdfplumber."""
from __future__ import annotations

_PAGE_STRIDE = 100000  # keep page-2 rows sorting AFTER page-1 rows (top = page*stride + y)


def extract_pages_words(pdf_bytes: bytes) -> list[dict]:
    """Every word across all pages as {"text","x0","x1","top","bottom"}, with `top` made globally
    increasing across pages so a table that continues onto the next page stays in reading order.
    Returns [] on an unreadable/passwordless-failure PDF (caller falls back to manual/vision)."""
    try:
        import pdfplumber
    except Exception:
        return []
    import io
    out: list[dict] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for pi, page in enumerate(pdf.pages):
                base = pi * _PAGE_STRIDE
                for w in page.extract_words(use_text_flow=False, keep_blank_chars=False):
                    out.append({"text": w["text"], "x0": float(w["x0"]), "x1": float(w["x1"]),
                                "top": base + float(w["top"]), "bottom": base + float(w["bottom"])})
    except Exception:
        return []
    return out


def is_pdf(raw: bytes) -> bool:
    return bool(raw) and raw[:5] == b"%PDF-"
