"""ZR-ENG-CLR-004 Section 2.3/AC-04: rendering for the three non-native
delivery modes.

  B -- Field-map official form: overlay structured values onto the uploaded
       blank official PDF at counsel-approved coordinates
       (AgreementFormTemplate.field_anchor_map), never re-typesetting the
       form itself.
  C -- Prescribed-content reconstruction: rendered the same way as mode A
       (native), but diff-checked against the template's own
       authoritative_content_text so drift from the approved reference is
       detected rather than silently accepted.
  D -- External approved document: the uploaded document IS the agreement;
       a signature-wrapper page is appended (never inserted into or
       overwriting the source pages).

Uses pypdf for reading/writing/merging and reportlab (already a dependency,
same as generate_agreement_pdf) to render the overlay/wrapper content."""

from __future__ import annotations

import difflib
from datetime import datetime, timezone
from io import BytesIO

import pypdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def render_mode_b_overlay(source_pdf_bytes: bytes, field_anchor_map: dict, values: dict) -> bytes:
    """Overlays `values` onto `source_pdf_bytes` at the coordinates named in
    field_anchor_map ({"field_name": {"page": int, "x": float, "y": float}}).
    The source form's own pages, layout and content are never modified --
    only a transparent overlay is merged on top, page by page."""
    reader = pypdf.PdfReader(BytesIO(source_pdf_bytes))
    writer = pypdf.PdfWriter()

    fields_by_page: dict[int, list[tuple[str, dict]]] = {}
    for field_name, anchor in field_anchor_map.items():
        page_no = int(anchor.get("page", 0))
        fields_by_page.setdefault(page_no, []).append((field_name, anchor))

    overlay_pages: dict[int, "pypdf.PageObject"] = {}
    for page_no, fields in fields_by_page.items():
        if page_no >= len(reader.pages):
            continue
        page = reader.pages[page_no]
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=(page_width, page_height))
        c.setFont("Helvetica", 10)
        for field_name, anchor in fields:
            value = str(values.get(field_name, ""))
            c.drawString(float(anchor["x"]), float(anchor["y"]), value)
        c.save()
        buf.seek(0)
        overlay_pages[page_no] = pypdf.PdfReader(buf).pages[0]

    for i, page in enumerate(reader.pages):
        if i in overlay_pages:
            page.merge_page(overlay_pages[i])
        writer.add_page(page)

    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def render_mode_d_document(source_pdf_bytes: bytes, snapshot: dict, agreement_status: str) -> bytes:
    """Appends a signature-wrapper page to the external approved document --
    the source document's own pages are copied through unmodified; the
    wrapper is a new trailing page, never an edit to the pages before it."""
    reader = pypdf.PdfReader(BytesIO(source_pdf_bytes))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, height = A4
    x, y = 20 * mm, height - 25 * mm
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x, y, "Signature Wrapper")
    y -= 8 * mm
    c.setFont("Helvetica", 10)
    c.drawString(x, y, f"Status: {agreement_status}")
    y -= 6 * mm
    c.drawString(x, y, f"Provider: {snapshot.get('provider_name', '')}")
    y -= 6 * mm
    c.drawString(x, y, f"Renter: {snapshot.get('renter_name', '')}")
    y -= 6 * mm
    c.drawString(x, y, f"Wrapper generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    c.save()
    buf.seek(0)
    writer.add_page(pypdf.PdfReader(buf).pages[0])

    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def content_similarity_ratio(rendered_text: str, authoritative_text: str) -> float:
    """AC-04 mode C 'Legal diff tests against authoritative form version':
    a similarity ratio in [0, 1] between the natively-rendered text and the
    counsel-approved reference text -- see
    check_content_drift for the pass/fail threshold this backs."""
    if not authoritative_text.strip():
        return 1.0
    return difflib.SequenceMatcher(None, rendered_text, authoritative_text).ratio()


DRIFT_THRESHOLD = 0.85


def check_content_drift(rendered_text: str, authoritative_text: str) -> bool:
    """True if the rendered content has drifted too far from the
    counsel-approved reference -- callers must block delivery/generation
    when this is true (see crud/leasing.py:generate_agreement_pdf's mode C
    branch), never silently deliver drifted content."""
    return content_similarity_ratio(rendered_text, authoritative_text) < DRIFT_THRESHOLD
