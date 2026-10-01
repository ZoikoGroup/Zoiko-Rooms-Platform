"""PDF rendering of the Zoiko Rooms Residential Occupancy Agreement (global
master template v1.0) -- layout follows the approved template document:
cover header + Agreement Summary, sections 02-06 (clauses 1-42, verbatim
from template_text.py), Schedules A-D and the execution certificate.

Pure function of DocumentModel (context.py): nothing here touches the
database, so a frozen agreement version always renders identically."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.services.agreement_document import template_text as T
from app.services.agreement_document.context import DocumentModel

NAVY = colors.HexColor("#13306B")
RED = colors.HexColor("#E0201B")
INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#6B7280")
RULE = colors.HexColor("#D9DEE8")
LABEL_BG = colors.HexColor("#F3F5F9")
CREAM = colors.HexColor("#FFF7EC")
PINK = colors.HexColor("#FDF1F1")
SKY = colors.HexColor("#F1F5FB")

PAGE_W, PAGE_H = A4
MARGIN_X = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X
ICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "zoikorooms-icon.png"

_base = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=12.2, textColor=INK)
STYLES = {
    "body": _base,
    "small": ParagraphStyle("small", parent=_base, fontSize=8, leading=10.4),
    "cell": ParagraphStyle("cell", parent=_base, fontSize=8.2, leading=10.4),
    "cell_label": ParagraphStyle("cell_label", parent=_base, fontSize=7.4, leading=9.6, textColor=MUTED),
    "cell_head": ParagraphStyle("cell_head", parent=_base, fontName="Helvetica-Bold", fontSize=7.4, leading=9.4,
                                textColor=colors.white),
    "cell_bold": ParagraphStyle("cell_bold", parent=_base, fontName="Helvetica-Bold", fontSize=8.2, leading=10.4),
    "title": ParagraphStyle("title", parent=_base, fontSize=25, leading=29, textColor=NAVY),
    "subtitle": ParagraphStyle("subtitle", parent=_base, fontSize=11.5, leading=15, textColor=MUTED),
    "section_no": ParagraphStyle("section_no", parent=_base, fontSize=8, leading=10, textColor=RED),
    "section": ParagraphStyle("section", parent=_base, fontSize=17, leading=21, textColor=NAVY),
    "clause_title": ParagraphStyle("clause_title", parent=_base, fontSize=10.6, leading=13.5, textColor=NAVY,
                                   spaceBefore=5),
    "subhead": ParagraphStyle("subhead", parent=_base, fontSize=10, leading=13, textColor=NAVY, spaceBefore=4),
    "callout_title": ParagraphStyle("callout_title", parent=_base, fontSize=8.8, leading=11.5, textColor=NAVY),
    "closing": ParagraphStyle("closing", parent=_base, fontSize=8.6, leading=11, textColor=NAVY, alignment=TA_CENTER),
    "closing_sub": ParagraphStyle("closing_sub", parent=_base, fontSize=7.4, leading=9.5, textColor=MUTED,
                                  alignment=TA_CENTER),
}


def _p(text: str, style: str = "body") -> Paragraph:
    return Paragraph(escape(str(text)), STYLES[style])


def render_agreement_pdf(model: DocumentModel) -> bytes:
    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=MARGIN_X, rightMargin=MARGIN_X, topMargin=30 * mm, bottomMargin=20 * mm,
        title=f"{T.TEMPLATE_TITLE} {model.agreement_ref}",
        author="Zoiko Rooms", subject=f"{T.TEMPLATE_SUBTITLE} — v{model.version_label}",
    )
    frame = Frame(MARGIN_X, 20 * mm, CONTENT_W, PAGE_H - 50 * mm, id="body", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)

    def first_page(canvas, _doc):
        _draw_chrome(canvas, model, first=True)

    def later_page(canvas, _doc):
        _draw_chrome(canvas, model, first=False)

    doc.addPageTemplates([
        PageTemplate(id="first", frames=[frame], onPage=first_page),
        PageTemplate(id="later", frames=[frame], onPage=later_page),
    ])
    from reportlab.platypus import NextPageTemplate

    story = [NextPageTemplate("later")]
    story += _cover(model)
    for number, title, clauses in T.SECTIONS:
        story += _section(number, title)
        for clause_no, clause_title, text in clauses:
            story.append(KeepTogether([_p(f"{clause_no}. {clause_title}", "clause_title"), _p(text)]))
            if clause_no == 40:
                story.append(Spacer(1, 3 * mm))
                story.append(_callout(T.PRODUCTION_CONTROL_TITLE, T.PRODUCTION_CONTROL, PINK, RED))
    story += _schedule_a(model)
    story += _schedule_b(model)
    story += _schedule_c(model)
    story += _schedule_d(model)

    doc.build(story)
    return buffer.getvalue()


# ---------------------------------------------------------------- page chrome

def _draw_chrome(canvas, model: DocumentModel, *, first: bool) -> None:
    canvas.saveState()
    top = PAGE_H - 12 * mm
    if first:
        canvas.setFillColor(RED)
        canvas.rect(MARGIN_X, top, CONTENT_W, 1.4 * mm, stroke=0, fill=1)
    # Wordmark: "ZOIKO" bold navy + "Rooms" red, mirroring the brand mark.
    y = PAGE_H - 22 * mm
    canvas.setFont("Helvetica-Bold", 15)
    canvas.setFillColor(RED)
    canvas.drawString(MARGIN_X, y, "ZOIKO")
    canvas.setFillColor(NAVY)
    canvas.setFont("Helvetica", 15)
    canvas.drawString(MARGIN_X + canvas.stringWidth("ZOIKO", "Helvetica-Bold", 15) + 0.6 * mm, y, "Rooms")
    right = MARGIN_X + CONTENT_W
    if first:
        canvas.setFont("Helvetica", 7.6)
        canvas.setFillColor(RED)
        canvas.drawRightString(right, y + 4 * mm, T.TEMPLATE_KICKER[0])
        canvas.setFillColor(NAVY)
        canvas.drawRightString(right, y + 0.6 * mm, T.TEMPLATE_KICKER[1])
    else:
        canvas.setFont("Helvetica", 7.6)
        canvas.setFillColor(NAVY)
        canvas.drawRightString(right, y + 2 * mm, T.TEMPLATE_TITLE.upper())

    # Footer
    fy = 11 * mm
    if ICON_PATH.exists():
        canvas.drawImage(str(ICON_PATH), MARGIN_X, fy - 1 * mm, width=4 * mm, height=4 * mm, mask="auto")
    canvas.setFont("Helvetica", 6.6)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN_X + 5.5 * mm, fy, model.footer)
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(right - 4 * mm, fy - 2.5 * mm, "Page")
    canvas.setFont("Helvetica-Bold", 10)
    canvas.setFillColor(INK)
    canvas.drawRightString(right, fy - 2.5 * mm, str(canvas.getPageNumber()))
    canvas.restoreState()


# -------------------------------------------------------------- building blocks

def _section(number: str, title: str) -> list:
    rule = Table([[""]], colWidths=[CONTENT_W], rowHeights=[0.8 * mm])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RED)]))
    return [
        CondPageBreak(45 * mm),
        Spacer(1, 6 * mm),
        _p(number, "section_no"),
        _p(title, "section"),
        Spacer(1, 1 * mm),
        rule,
        Spacer(1, 2 * mm),
    ]


def _callout(title: str, text: str, background, bar) -> Table:
    inner = [_p(title, "callout_title"), _p(text, "small")]
    table = Table([["", inner]], colWidths=[2.4 * mm, CONTENT_W - 2.4 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), bar),
        ("BACKGROUND", (1, 0), (1, 0), background),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (1, 0), (1, 0), 8),
        ("TOPPADDING", (1, 0), (1, 0), 6),
        ("BOTTOMPADDING", (1, 0), (1, 0), 7),
    ]))
    return table


def _grid(rows: list[list], col_widths: list[float], *, header: bool, label_cols: tuple[int, ...] = ()) -> Table:
    data = []
    for r, row in enumerate(rows):
        cells = []
        for c, value in enumerate(row):
            if header and r == 0:
                style = "cell_head"
            elif c in label_cols:
                style = "cell_label"
            else:
                style = "cell"
            cells.append(value if isinstance(value, Paragraph) else _p(value, style))
        data.append(cells)
    table = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
    for c in label_cols:
        style.append(("BACKGROUND", (c, 1 if header else 0), (c, -1), LABEL_BG))
    table.setStyle(TableStyle(style))
    return table


def _quarter(n: int = 4) -> list[float]:
    return [CONTENT_W / n] * n


# ------------------------------------------------------------------- cover

def _cover(m: DocumentModel) -> list:
    meta = _grid(
        [["AGREEMENT ID", "VERSION", "AGREEMENT TYPE", "STATUS"],
         [m.agreement_ref, m.version_label, m.agreement_type, m.status_label]],
        _quarter(), header=True,
    )
    summary = _grid(
        [[label.upper(), value] for label, value in m.summary_rows],
        [CONTENT_W * 0.45, CONTENT_W * 0.55], header=False, label_cols=(0,),
    )
    return [
        _p(T.TEMPLATE_TITLE, "title"),
        _p(T.TEMPLATE_SUBTITLE, "subtitle"),
        Spacer(1, 5 * mm),
        meta,
        *_section("01", "Agreement Summary")[1:],
        summary,
        Spacer(1, 5 * mm),
        _callout(T.MANDATORY_LAW_CONTROL_TITLE, T.MANDATORY_LAW_CONTROL, CREAM, RED),
    ]


# --------------------------------------------------------------- schedules

def _schedule_a(m: DocumentModel) -> list:
    widths = [CONTENT_W * 0.2, CONTENT_W * 0.3, CONTENT_W * 0.2, CONTENT_W * 0.3]
    details = _grid([[a.upper(), b, c.upper(), d] for a, b, c, d in m.schedule_a_rows], widths,
                    header=False, label_cols=(0, 2))
    financial = _grid(
        [["ITEM", "AMOUNT / RULE", "RESPONSIBLE PARTY", "NOTES"]]
        + [[_p(item, "cell_bold"), amount, party, notes] for item, amount, party, notes in m.financial_rows],
        _quarter(), header=True,
    )
    return [
        *_section("A", T.SCHEDULE_A_TITLE),
        details,
        Spacer(1, 3 * mm),
        _p("Financial terms", "subhead"),
        Spacer(1, 1 * mm),
        financial,
    ]


def _schedule_b(m: DocumentModel) -> list:
    utilities = _grid(
        [["COST / SERVICE", "HOST", "RENTER", "INCLUDED / NOTES"]]
        + [[_p(name, "cell_bold"), host, renter, notes] for name, host, renter, notes in m.utility_rows],
        _quarter(), header=True,
    )
    condition = _grid(
        [["AREA / ITEM", "CONDITION", "EVIDENCE REF.", "NOTES"]]
        + [[_p(area, "cell_bold"), cond, ev, notes] for area, cond, ev, notes in m.condition_rows],
        _quarter(), header=True,
    )
    rules = _grid(
        [["TOPIC", "AGREED RULE", "LEGAL CONTROL"]]
        + [[_p(topic, "cell_bold"), rule, control] for topic, rule, control in m.house_rule_rows],
        [CONTENT_W * 0.3, CONTENT_W * 0.4, CONTENT_W * 0.3], header=True,
    )
    return [
        *_section("B", T.SCHEDULE_B_TITLE),
        _p("Utilities and recurring charges", "subhead"),
        Spacer(1, 1 * mm),
        utilities,
        Spacer(1, 3 * mm),
        KeepTogether([_p("Move-in condition record", "subhead"), Spacer(1, 1 * mm), condition]),
        Spacer(1, 3 * mm),
        KeepTogether([_p("Property-specific rules", "subhead"), Spacer(1, 1 * mm), rules]),
    ]


def _schedule_c(m: DocumentModel) -> list:
    rows = _grid(
        [["JURISDICTIONAL CONTROL", "SYSTEM OUTPUT"]]
        + [[_p(label, "cell_bold"), value] for label, value in m.schedule_c_rows],
        [CONTENT_W * 0.45, CONTENT_W * 0.55], header=True,
    )
    precedence = " ".join(f"<b>{i}.</b> {escape(item)}{';' if i < len(T.ORDER_OF_PRECEDENCE) else ''}"
                          for i, item in enumerate(T.ORDER_OF_PRECEDENCE, start=1))
    return [
        *_section("C", T.SCHEDULE_C_TITLE),
        _callout(T.SCHEDULE_C_PACK_TITLE, T.SCHEDULE_C_PACK_TEXT, SKY, NAVY),
        Spacer(1, 2 * mm),
        _p(m.jurisdiction_pack_note, "small"),
        Spacer(1, 2 * mm),
        rows,
        Spacer(1, 3 * mm),
        KeepTogether([_p(T.ORDER_OF_PRECEDENCE_TITLE, "subhead"), Paragraph(precedence, STYLES["body"])]),
    ]


def _signature_cell(block) -> list:
    lines = [
        ("LEGAL NAME", block.legal_name),
        (block.capacity_label.upper(), block.capacity),
        ("SIGNATURE", block.method),
        ("SIGNED AT", block.signed_at),
        ("AUTHENTICATION", block.authentication),
        ("SIGNATURE REF.", block.reference),
    ]
    return [
        Paragraph(f'<font size="6.8" color="#6B7280">{escape(label)}</font>&nbsp;&nbsp;{escape(value)}', STYLES["cell"])
        for label, value in lines
    ]


def _schedule_d(m: DocumentModel) -> list:
    host, renter = m.signatures
    signatures = Table(
        [[_p("HOST", "cell_bold"), _p("RENTER", "cell_bold")],
         [_signature_cell(host), _signature_cell(renter)]],
        colWidths=[CONTENT_W / 2] * 2,
    )
    signatures.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, RULE),
        ("BACKGROUND", (0, 0), (-1, 0), LABEL_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    certificate = _grid(
        [["EVIDENCE", "RECORDED VALUE"]]
        + [[_p(label, "cell_bold"), value] for label, value in m.certificate_rows],
        [CONTENT_W * 0.45, CONTENT_W * 0.55], header=True,
    )
    integrity = Table(
        [["", [_p(T.DOCUMENT_INTEGRITY_TITLE, "subhead"), _p(T.DOCUMENT_INTEGRITY, "small")]]],
        colWidths=[CONTENT_W * 0.45, CONTENT_W * 0.55],
    )
    integrity.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), NAVY),
        ("BACKGROUND", (1, 0), (1, 0), SKY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (1, 0), (1, 0), 8),
        ("BOTTOMPADDING", (1, 0), (1, 0), 8),
    ]))
    return [
        *_section("D", T.SCHEDULE_D_TITLE),
        _p(T.SCHEDULE_D_INTRO),
        Spacer(1, 3 * mm),
        signatures,
        Spacer(1, 5 * mm),
        KeepTogether([_p("Execution certificate", "subhead"), Spacer(1, 1 * mm), certificate, integrity]),
        Spacer(1, 8 * mm),
        _p(T.CLOSING_LINE, "closing"),
        _p(T.CLOSING_SUBLINE, "closing_sub"),
    ]
