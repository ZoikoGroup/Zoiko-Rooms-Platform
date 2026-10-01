"""ZR-COMMS-EMAIL-001 Section 05 -- reusable footer, disclosure and warning
blocks. Text is verbatim from the approved communications baseline; change it
only through a new template version (Section 8.2)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Block:
    key: str
    title: str
    text: str
    # Visual accent only -- meaning is always carried by the title text too
    # (Section 4.1: "Status meaning is never conveyed by color alone").
    tone: str  # "teal" | "warning" | "neutral"

    @property
    def show_title(self) -> bool:
        # Disclosure/warning blocks are labeled; the service and marketing
        # footers are plain closing text (their titles are internal names).
        return self.tone != "neutral"


SERVICE = Block(
    "service", "Service footer",
    "You are receiving this because of activity on your Zoiko Rooms account or rental journey. This is a service "
    "message and does not change your marketing preferences. Manage account notifications in Zoiko Rooms.",
    "neutral",
)
SECURITY = Block(
    "security", "Security warning",
    "Zoiko Rooms will never ask you by email to share your password, passkey, one-time code, full payment card "
    "number, bank login details or API secret.",
    "warning",
)
VERIFICATION = Block(
    "verification", "Verification disclosure",
    "Verification confirms only the evidence and checks expressly identified in the relevant provider record or "
    "Room Passport. It is not a guarantee of safety, legality, suitability, property condition or future conduct.",
    "teal",
)
PAYMENT = Block(
    "payment", "Payment disclosure",
    "Where available, payment, deposit, guarantor, insurance or protection services are provided by the regulated "
    "or authorized provider identified during the transaction. Applicable fees, protection and release conditions "
    "are shown before commitment.",
    "teal",
)
SAFETY = Block(
    "safety", "Safety boundary",
    "Zoiko Rooms Support is not an emergency service. If anyone may be in immediate danger, contact local emergency "
    "services. Use the secure case page for evidence and updates.",
    "warning",
)
DECISION = Block(
    "decision", "Decision notice",
    "The secure decision record contains the complete reason, evidence scope, effective date and any review or "
    "appeal route. This email is a summary and must not expose another person's private information.",
    "teal",
)
MARKETING = Block(
    "marketing", "Marketing footer",
    "You are receiving this because you opted in to Zoiko Rooms updates. Manage preferences or unsubscribe at any "
    "time. {sender_postal_address}.",
    "neutral",
)
CORPORATE_IDENTITY = (
    "© 2026 Zoiko Rooms. Zoiko Rooms is a trading name of Zoiko Realty Group Inc., a Zoiko Group company. "
    "Products and availability vary by jurisdiction."
)

BLOCKS: dict[str, Block] = {b.key: b for b in (SERVICE, SECURITY, VERIFICATION, PAYMENT, SAFETY, DECISION, MARKETING)}
