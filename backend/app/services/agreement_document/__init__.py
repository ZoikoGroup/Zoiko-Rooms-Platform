"""Zoiko Rooms Residential Occupancy Agreement -- global master template.

- template_text.py: the approved template wording (verbatim, versioned)
- facts.py: facts frozen into snapshot["document"] at generation time
- context.py: snapshot + execution state -> DocumentModel (printed strings)
- pdf.py / text.py: renderers over DocumentModel
"""

from app.services.agreement_document.context import DocumentModel, build_document_model
from app.services.agreement_document.facts import build_document_facts
from app.services.agreement_document.pdf import render_agreement_pdf
from app.services.agreement_document.text import render_agreement_text

__all__ = [
    "DocumentModel",
    "build_document_facts",
    "build_document_model",
    "render_agreement_pdf",
    "render_agreement_text",
]
