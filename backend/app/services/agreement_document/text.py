"""Linear plain-text rendering of the same DocumentModel the PDF uses
(ZR-ENG-CLR-004 AC-28: screen readers / clients that can't render PDF).
Same content, same order, no layout."""

from __future__ import annotations

from app.services.agreement_document import template_text as T
from app.services.agreement_document.context import DocumentModel


def render_agreement_text(m: DocumentModel) -> str:
    out: list[str] = []

    def heading(text: str) -> None:
        out.extend(["", text.upper(), "=" * len(text)])

    out.append(f"ZOIKO ROOMS -- {T.TEMPLATE_TITLE.upper()} ({T.TEMPLATE_SUBTITLE}) -- accessible text version")
    out.append(f"Agreement ID: {m.agreement_ref} | Version: {m.version_label} | Type: {m.agreement_type} | Status: {m.status_label}")

    heading("01 Agreement Summary")
    out.extend(f"{label}: {value}" for label, value in m.summary_rows)
    out.extend(["", f"{T.MANDATORY_LAW_CONTROL_TITLE}: {T.MANDATORY_LAW_CONTROL}"])

    for number, title, clauses in T.SECTIONS:
        heading(f"{number} {title}")
        for clause_no, clause_title, text in clauses:
            out.extend(["", f"{clause_no}. {clause_title}", text])
            if clause_no == 40:
                out.append(f"{T.PRODUCTION_CONTROL_TITLE}: {T.PRODUCTION_CONTROL}")

    heading(T.SCHEDULE_A_TITLE)
    for a, b, c, d in m.schedule_a_rows:
        out.append(f"{a}: {b}")
        out.append(f"{c}: {d}")
    out.extend(["", "Financial terms"])
    out.extend(f"- {item}: {amount}; responsible: {party}; {notes}" for item, amount, party, notes in m.financial_rows)

    heading(T.SCHEDULE_B_TITLE)
    out.append("Utilities and recurring charges")
    for name, host, renter, notes in m.utility_rows:
        payer = " and ".join(p for p, flag in (("Host", host), ("Renter", renter)) if flag) or "-"
        out.append(f"- {name}: paid by {payer}; {notes or '-'}")
    out.extend(["", "Move-in condition record"])
    out.extend(f"- {area}: {cond}; evidence: {ev}" for area, cond, ev, _ in m.condition_rows)
    out.extend(["", "Property-specific rules"])
    out.extend(f"- {topic}: {rule} ({control})" for topic, rule, control in m.house_rule_rows)

    heading(T.SCHEDULE_C_TITLE)
    out.append(f"{T.SCHEDULE_C_PACK_TITLE}: {T.SCHEDULE_C_PACK_TEXT}")
    out.append(m.jurisdiction_pack_note)
    out.extend(f"- {label}: {value}" for label, value in m.schedule_c_rows)
    out.append(f"{T.ORDER_OF_PRECEDENCE_TITLE}: " + "; ".join(
        f"{i}. {item}" for i, item in enumerate(T.ORDER_OF_PRECEDENCE, start=1)
    ))

    heading(T.SCHEDULE_D_TITLE)
    out.append(T.SCHEDULE_D_INTRO)
    for block in m.signatures:
        out.extend([
            "", block.role_label.upper(),
            f"Legal name: {block.legal_name}",
            f"{block.capacity_label}: {block.capacity}",
            f"Signature: {block.method}",
            f"Signed at: {block.signed_at}",
            f"Authentication: {block.authentication}",
            f"Signature ref.: {block.reference}",
        ])
    out.extend(["", "Execution certificate"])
    out.extend(f"- {label}: {value}" for label, value in m.certificate_rows)
    out.extend(["", f"{T.DOCUMENT_INTEGRITY_TITLE}: {T.DOCUMENT_INTEGRITY}", "", T.CLOSING_LINE, T.CLOSING_SUBLINE])
    return "\n".join(out)
