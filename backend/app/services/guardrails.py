"""Deterministic AI guardrails (ZR-AI-PG-001, ZR-AI-ARCH-001).

System-prompt wording is not a security boundary. These server-side,
deterministic controls backstop the assistant prompts so that risk
classification and the "no determinations" rule are enforced in code, not just
relied on from the model:

* ``classify_risk`` / ``risk_topic_name`` -- R0-R4 risk classification,
  mirroring the client-side ``src/lib/risk.ts`` so the server is authoritative
  and the client trusts its value.
* ``classify_action_tier`` -- A1/A2/A3 action tier for a turn.
* ``scan_for_determination`` -- a deterministic output post-processor that flags
  responses which assert an eligibility/compliance/approval/tenancy decision.

Everything here is pure (no I/O) and unit-testable. The chat routes surface the
results back to the client (risk_tier / risk) and audit any block.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import re


# ---------------------------------------------------------------------------
# Risk classes (R0-R4), mirroring src/lib/risk.ts
# ---------------------------------------------------------------------------


class RiskClass(str, Enum):
    R0 = "R0"  # informational
    R1 = "R1"  # account-sensitive (retained for compat; not currently emitted)
    R2 = "R2"  # consequential
    R3 = "R3"  # determination / regulated
    R4 = "R4"  # immediate danger / safety crisis


HIGH_CONSEQUENCE_TERMS: list[tuple[str, re.Pattern]] = [
    ("compliance", re.compile(r"\bcomplian\w*\b", re.I)),
    (
        "right-to-rent",
        re.compile(r"\bright[\s-]?to[\s-]?rent\b|\bright of rent\b|\bimmigration\b|\bvisa\b|\bborder\b", re.I),
    ),
    ("deposit", re.compile(r"\bdeposit\b", re.I)),
    ("payment", re.compile(r"\bpay\w*\b|\bbill\b|\bcharge\b|\binvoice\b|\bowe\b", re.I)),
    ("agreement", re.compile(r"\bagreement\b|\bcontract\b|\btenan\w*\b|\bsignature\b|\bsign\b", re.I)),
    (
        "eligibility",
        re.compile(r"\beligib\w*\b|\bqualif\w*\b|\bapproved?\b|\brejection\b|\breject\b", re.I),
    ),
    ("dispute", re.compile(r"\bdispute\b|\bcomplain\w*\b|\bappeal\b", re.I)),
    (
        "discrimination",
        re.compile(r"\bdiscrimination\b|\bharass\w*\b|\bbias\b|\bfair\w*\b", re.I),
    ),
    (
        "safety",
        re.compile(r"\bsafe\w*\b|\bdanger\b|\bemergency\b|\bthreat\w*\b|\bcrisis\b", re.I),
    ),
    ("deadline", re.compile(r"\bdeadline\b|\bnotice period\b|\bnotice\b", re.I)),
    ("eviction", re.compile(r"\bevict\w*\b|\bhomeless\w*\b|\blandlord\b", re.I)),
]

# R3: the user asks the assistant to make a determination or override an
# authoritative outcome.
DETERMINATION_PATTERN = re.compile(
    r"\b(approve me|override|decide|should i (be|get)|is this (compliant|legal)|"
    r"am i (eligible|approved)|do i have the right|rule on|make the decision|determine)\b",
    re.I,
)

# R4: immediate danger / safety crisis.
CRISIS_PATTERN = re.compile(
    r"\b(i'?m (in )?danger|hurting (myself|me)|kill (myself|me)|being (abused|attacked)|"
    r"physical (danger|threat)|my (life|safety) is (in )?danger)\b",
    re.I,
)


def classify_risk(text: str) -> RiskClass:
    if not text:
        return RiskClass.R0
    if CRISIS_PATTERN.search(text):
        return RiskClass.R4
    if DETERMINATION_PATTERN.search(text):
        return RiskClass.R3
    if any(re.search(t, text) for _, t in HIGH_CONSEQUENCE_TERMS):
        return RiskClass.R2
    return RiskClass.R0


def risk_topic_name(text: str) -> str:
    if not text:
        return ""
    for name, pattern in HIGH_CONSEQUENCE_TERMS:
        if pattern.search(text):
            return name
    return ""


# ---------------------------------------------------------------------------
# Action tiers (A1-A3)
# ---------------------------------------------------------------------------


class ActionTier(str, Enum):
    A1 = "A1"  # informational / safe to surface as-is
    A2 = "A2"  # needs clarification before it can be relied on
    A3 = "A3"  # requires human confirmation; never surface a determination


# Jurisdiction-dependent legal guidance asked without an explicit market/locality.
_JURISDICTION_LEGAL = re.compile(
    r"\b(legal|law|lawful|statut|regulat|tenant\s+right|deposit\s+(law|cap|limit)|"
    r"notice\s+period|evict|right\s+to\s+rent|immigration)\b",
    re.I,
)
_MARKET_HINT = re.compile(
    r"\b(in|for|under|uk|england|wales|scotland|india|in\b|dubai|uae|us|usa|"
    r"new\s+york|california|england|mumbai|delhi|bangalore|chennai|pune|"
    r"kolkata|hyderabad)\b",
    re.I,
)


def classify_action_tier(text: str) -> ActionTier:
    """Deterministic A1-A3 tier for a turn, derived from risk and phrasing."""
    if not text:
        return ActionTier.A1
    risk = classify_risk(text)
    # Immediate danger and determination/authority requests are A3.
    if risk == RiskClass.R4 or risk == RiskClass.R3:
        return ActionTier.A3
    # High-consequence topics where the user could expect a decision are A3.
    topic = risk_topic_name(text)
    if topic in {"compliance", "right-to-rent", "eligibility", "eviction", "dispute", "discrimination"}:
        return ActionTier.A3
    # Jurisdiction-dependent legal question with no market context -> A2.
    if _JURISDICTION_LEGAL.search(text) and not _MARKET_HINT.search(text):
        return ActionTier.A2
    return ActionTier.A1


# ---------------------------------------------------------------------------
# "No determinations" output scanner
# ---------------------------------------------------------------------------

# Phrases where the assistant may (wrongly) assert an authoritative decision.
_DETERMINATION_ASSERT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\byou (are|'re|have been) (approved|accepted|eligible|compliant|qualified)\b", re.I),
    re.compile(r"\byou qualify\b", re.I),
    re.compile(r"\byou (are|'re) entitled\b", re.I),
    re.compile(r"\byour (application|request|tenancy) (is|has been) (approved|accepted|granted)\b", re.I),
    re.compile(r"\byou (have|will) (the )?right to rent\b", re.I),
    re.compile(r"\bapproval (is|has been) (granted|confirmed)\b", re.I),
    re.compile(r"\bthis (is|counts as) (compliant|legal|approved)\b", re.I),
]

DETERMINATION_NOTICE = (
    "I can't confirm or determine an eligibility, compliance, approval, or tenancy "
    "decision. Only the authoritative record in Zoiko Rooms (or a qualified human "
    "review) can establish that. Please check your dashboard or reach a member of "
    "our team to confirm the current status."
)


@dataclass(frozen=True)
class DeterminationCheck:
    blocked: bool
    matched: str = ""

    def __bool__(self) -> bool:
        return self.blocked


def scan_for_determination(text: str) -> DeterminationCheck:
    """Return blocked=True if the text asserts an authoritative decision.

    This is a deterministic backstop: it does not fully understand language, it
    only flags known decision-assertion phrases so the route can refuse to
    surface them as authoritative and can append a corrective notice.
    """
    if not text:
        return DeterminationCheck(blocked=False)
    lowered = text.strip().lower()
    for pattern in _DETERMINATION_ASSERT_PATTERNS:
        match = pattern.search(lowered)
        if match:
            return DeterminationCheck(blocked=True, matched=match.group(0))
    return DeterminationCheck(blocked=False)


# Compiled user-topic classification used to decide whether an A3/handoff flag
# should be set on a response before it is surfaced.
def should_require_confirmation(text: str) -> bool:
    return classify_action_tier(text) == ActionTier.A3
