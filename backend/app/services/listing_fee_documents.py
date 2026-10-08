"""Listing Fee receipt and credit note PDFs (ZR-PAY-002 Section 8.5,
ZR-PAY-CFG-001 Section 7) in the Zoiko Rooms house style shared with the
agreement document (services/agreement_document/pdf.py): wordmark header,
seller / customer blocks, a details grid, an itemised charge table with the
tax split, the amount paid and the fee's terms.

Pure functions of FeeDocument: nothing here touches the database, so a
stored document always reflects the data frozen when it was issued."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Frame, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.platypus.doctemplate import BaseDocTemplate

from app.services.agreement_document.pdf import ICON_PATH, INK, LABEL_BG, MUTED, NAVY, RED, RULE

GREEN = colors.HexColor("#0F7B4A")
GREEN_BG = colors.HexColor("#E7F5EE")
AMBER = colors.HexColor("#9A5B00")
AMBER_BG = colors.HexColor("#FDF3E2")

PAGE_W, PAGE_H = A4
MARGIN_X = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X

_base = ParagraphStyle("lf-base", fontName="Helvetica", fontSize=9, leading=12.4, textColor=INK)
STYLES = {
    "body": _base,
    "small": ParagraphStyle("lf-small", parent=_base, fontSize=7.8, leading=10.4, textColor=MUTED),
    "label": ParagraphStyle("lf-label", parent=_base, fontSize=7, leading=9, textColor=MUTED),
    "value": ParagraphStyle("lf-value", parent=_base, fontSize=8.8, leading=11.4),
    "value_bold": ParagraphStyle("lf-value-bold", parent=_base, fontName="Helvetica-Bold", fontSize=8.8, leading=11.4),
    "party_name": ParagraphStyle("lf-party", parent=_base, fontName="Helvetica-Bold", fontSize=10, leading=13,
                                 textColor=NAVY),
    "title": ParagraphStyle("lf-title", parent=_base, fontName="Helvetica-Bold", fontSize=21, leading=25,
                            textColor=NAVY),
    "subtitle": ParagraphStyle("lf-subtitle", parent=_base, fontSize=9.5, leading=13, textColor=MUTED),
    "head": ParagraphStyle("lf-head", parent=_base, fontName="Helvetica-Bold", fontSize=7.4, leading=9.4,
                           textColor=colors.white),
    "head_r": ParagraphStyle("lf-head-r", parent=_base, fontName="Helvetica-Bold", fontSize=7.4, leading=9.4,
                             textColor=colors.white, alignment=TA_RIGHT),
    "cell": ParagraphStyle("lf-cell", parent=_base, fontSize=8.6, leading=11.2),
    "cell_r": ParagraphStyle("lf-cell-r", parent=_base, fontSize=8.6, leading=11.2, alignment=TA_RIGHT),
    "cell_sub": ParagraphStyle("lf-cell-sub", parent=_base, fontSize=7.4, leading=9.6, textColor=MUTED),
    "total_label": ParagraphStyle("lf-total-label", parent=_base, fontName="Helvetica-Bold", fontSize=10.5,
                                  leading=13, textColor=colors.white),
    "total_value": ParagraphStyle("lf-total-value", parent=_base, fontName="Helvetica-Bold", fontSize=13,
                                  leading=16, textColor=colors.white, alignment=TA_RIGHT),
    "section": ParagraphStyle("lf-section", parent=_base, fontName="Helvetica-Bold", fontSize=8, leading=10,
                              textColor=RED),
}


@dataclass
class Party:
    name: str
    lines: list[str] = field(default_factory=list)


@dataclass
class LineItem:
    description: str
    detail: str
    amount: float


@dataclass
class FeeDocument:
    """Everything a receipt or credit note shows -- frozen values only."""
    kind: str  # "RECEIPT" | "CREDIT_NOTE"
    number: str
    issued_at: datetime
    currency: str
    seller: Party
    customer: Party
    details: list[tuple[str, str]]
    items: list[LineItem]
    net_amount: float
    tax_label: str
    tax_amount: float
    total_amount: float
    status_label: str
    notes: list[str] = field(default_factory=list)


def _p(text: str, style: str = "body") -> Paragraph:
    return Paragraph(escape(str(text)), STYLES[style])


def _text_width(text: str, font: str, size: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(text, font, size)


def money(currency: str, amount: float) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}{currency} {abs(amount):,.2f}"


def render_fee_document_pdf(doc_model: FeeDocument) -> bytes:
    buffer = BytesIO()
    title = "Listing Fee Receipt" if doc_model.kind == "RECEIPT" else "Credit Note"
    doc = BaseDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGIN_X, rightMargin=MARGIN_X, topMargin=34 * mm, bottomMargin=22 * mm,
        title=f"{title} {doc_model.number}", author="Zoiko Rooms", subject=title,
    )
    frame = Frame(MARGIN_X, 22 * mm, CONTENT_W, PAGE_H - 56 * mm, leftPadding=0, rightPadding=0, topPadding=0,
                  bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame],
                                       onPage=lambda canvas, _d: _draw_chrome(canvas, doc_model, title))])
    story = [
        _title_block(doc_model, title), Spacer(1, 7 * mm),
        _parties(doc_model), Spacer(1, 6 * mm),
        _details(doc_model), Spacer(1, 7 * mm),
        KeepTogether([_items(doc_model), Spacer(1, 1.5 * mm), _totals(doc_model)]),
    ]
    if doc_model.notes:
        story += [Spacer(1, 8 * mm), _p("NOTES", "section"), Spacer(1, 1.5 * mm)]
        for note in doc_model.notes:
            story += [_p(note, "small"), Spacer(1, 1.2 * mm)]
    doc.build(story)
    return buffer.getvalue()


def _draw_chrome(canvas, doc_model: FeeDocument, title: str) -> None:
    canvas.saveState()
    canvas.setFillColor(RED)
    canvas.rect(MARGIN_X, PAGE_H - 12 * mm, CONTENT_W, 1.4 * mm, stroke=0, fill=1)
    y = PAGE_H - 22 * mm
    canvas.setFont("Helvetica-Bold", 15)
    canvas.setFillColor(RED)
    canvas.drawString(MARGIN_X, y, "ZOIKO")
    canvas.setFillColor(NAVY)
    canvas.setFont("Helvetica", 15)
    canvas.drawString(MARGIN_X + canvas.stringWidth("ZOIKO", "Helvetica-Bold", 15) + 0.6 * mm, y, "Rooms")
    right = MARGIN_X + CONTENT_W
    canvas.setFont("Helvetica-Bold", 7.6)
    canvas.setFillColor(NAVY)
    canvas.drawRightString(right, y + 3.2 * mm, title.upper())
    canvas.setFont("Helvetica", 7.6)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(right, y - 0.4 * mm, doc_model.number)

    fy = 11 * mm
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN_X, fy + 5 * mm, right, fy + 5 * mm)
    if ICON_PATH.exists():
        canvas.drawImage(str(ICON_PATH), MARGIN_X, fy - 1 * mm, width=4 * mm, height=4 * mm, mask="auto")
    canvas.setFont("Helvetica", 6.6)
    canvas.drawString(MARGIN_X + 5.5 * mm, fy,
                      f"{doc_model.seller.name} · {title} {doc_model.number} · Issued electronically; valid without a signature.")
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(right, fy, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def _title_block(doc_model: FeeDocument, title: str) -> Table:
    paid = doc_model.kind == "RECEIPT"
    pill_style = ParagraphStyle("lf-pill", parent=STYLES["value_bold"], textColor=GREEN if paid else AMBER)
    pill_text = doc_model.status_label.upper()
    pill = Table([[Paragraph(escape(pill_text), pill_style)]], hAlign="RIGHT",
                 colWidths=[_text_width(pill_text, "Helvetica-Bold", 8.8) + 16])
    pill.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN_BG if paid else AMBER_BG),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    left = [_p(title, "title"), _p(
        f"{doc_model.number}  ·  {'Paid' if paid else 'Issued'} {doc_model.issued_at:%d %b %Y, %H:%M} UTC", "subtitle")]
    amount_box = [Paragraph("AMOUNT PAID" if paid else "AMOUNT CREDITED",
                            ParagraphStyle("lf-label-r", parent=STYLES["label"], alignment=TA_RIGHT)),
                  Paragraph(escape(money(doc_model.currency, doc_model.total_amount)),
                            ParagraphStyle("lf-big", parent=STYLES["title"], fontSize=17, leading=21, alignment=TA_RIGHT)),
                  Spacer(1, 1.5 * mm), pill]
    table = Table([[left, amount_box]], colWidths=[CONTENT_W * 0.62, CONTENT_W * 0.38])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _party_cell(heading: str, party: Party) -> list:
    return [_p(heading, "label"), Spacer(1, 1 * mm), _p(party.name, "party_name")] + [_p(line, "value") for line in party.lines]


def _parties(doc_model: FeeDocument) -> Table:
    half = CONTENT_W / 2 - 3 * mm
    customer_heading = "BILLED TO" if doc_model.kind == "RECEIPT" else "CREDITED TO"
    table = Table([[_party_cell("ISSUED BY", doc_model.seller), "", _party_cell(customer_heading, doc_model.customer)]],
                  colWidths=[half, 6 * mm, half])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, 0), LABEL_BG), ("BACKGROUND", (2, 0), (2, 0), LABEL_BG),
        ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (1, 0), (1, 0), 0), ("RIGHTPADDING", (1, 0), (1, 0), 0),
    ]))
    return table


def _details(doc_model: FeeDocument) -> Table:
    cells = [[_p(label.upper(), "label"), _p(value, "value")] for label, value in doc_model.details]
    if len(cells) % 2:
        cells.append(["", ""])
    rows = [cells[i] + cells[i + 1] for i in range(0, len(cells), 2)]
    label_w, value_w = 30 * mm, CONTENT_W / 2 - 30 * mm
    table = Table(rows, colWidths=[label_w, value_w, label_w, value_w])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _items(doc_model: FeeDocument) -> Table:
    rows = [[_p("DESCRIPTION", "head"), _p("QTY", "head_r"), _p(f"AMOUNT ({doc_model.currency})", "head_r")]]
    for item in doc_model.items:
        rows.append([[_p(item.description, "cell"), _p(item.detail, "cell_sub")], _p("1", "cell_r"),
                     _p(f"{item.amount:,.2f}", "cell_r")])
    table = Table(rows, colWidths=[CONTENT_W - 50 * mm, 15 * mm, 35 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table


def _totals(doc_model: FeeDocument) -> Table:
    width = 85 * mm
    rows = [
        [_p("Subtotal (excluding tax)", "value"), _p(money(doc_model.currency, doc_model.net_amount), "cell_r")],
        [_p(doc_model.tax_label, "value"), _p(money(doc_model.currency, doc_model.tax_amount), "cell_r")],
        [_p("Total paid" if doc_model.kind == "RECEIPT" else "Total credited", "total_label"),
         _p(money(doc_model.currency, doc_model.total_amount), "total_value")],
    ]
    inner = Table(rows, colWidths=[width - 35 * mm, 35 * mm])
    inner.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 1), 0.5, RULE),
        ("BACKGROUND", (0, 2), (-1, 2), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 2), (-1, 2), 7), ("BOTTOMPADDING", (0, 2), (-1, 2), 8),
    ]))
    outer = Table([["", inner]], colWidths=[CONTENT_W - width, width])
    outer.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return outer
