"""Email delivery and the template builders for every email this build sends.

Design, footer blocks, sending streams and the template register follow
ZR-COMMS-EMAIL-001 and live in app/services/email/. Each send_* function
below builds one registered template (body copy adapted from the spec's
production copy) and hands it to deliver().

Delivery is best-effort and never raises: a mail failure must never roll back
or corrupt the database transaction that triggered it."""

import logging
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from app.core.config import settings
from app.services.email import formatting as fmt
from app.services.email import message_log, registry, streams
from app.services.email.design import Message, render_html, render_text
from app.services.email.registry import TIER_1, TemplateSpec

logger = logging.getLogger("uvicorn.error")

# Local dev fallback only -- mirrors Django's/Rails' well-known file-based email
# backends. Used whenever EMAIL_PROVIDER is not explicitly set to "smtp" (the
# default), so nothing sends real mail until an operator opts in.
DEV_MAIL_OUTBOX_DIR = Path("dev_mail_outbox")

SUPPORT_EMAIL = "support@zoikorooms.com"

# One immediate retry for a transient SMTP failure. Kept small on purpose:
# delivery runs inside the user's request, and a FAILED record can be sent
# again later by the same dedupe key.
SMTP_ATTEMPTS = 2
SMTP_RETRY_DELAY_SECONDS = 0.5


# ---------------------------------------------------------------- delivery

def _write_to_dev_outbox(to_email: str, subject: str, text_body: str, html_body: str = "") -> None:
    DEV_MAIL_OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "@.-_" else "_" for c in to_email)
    outbox_file = DEV_MAIL_OUTBOX_DIR / f"{safe_name}.txt"
    outbox_file.write_text(
        "This file is a local development mail outbox entry, not a sent email.\n"
        f"To: {to_email}\n"
        f"Generated at: {datetime.now(timezone.utc).isoformat()}\n"
        f"Subject: {subject}\n\n"
        f"{text_body}\n",
        encoding="utf-8",
    )
    if html_body:
        # Open in a browser to review the rendered design.
        (DEV_MAIL_OUTBOX_DIR / f"{safe_name}.html").write_text(html_body, encoding="utf-8")


def _send_via_smtp(
    to_email: str, subject: str, html_body: str, text_body: str, *,
    sender: str | None = None, reply_to: str = "", headers: dict[str, str] | None = None,
) -> bool:
    return _send_via_smtp_detailed(
        to_email, subject, html_body, text_body, sender=sender, reply_to=reply_to, headers=headers,
    )[0]


def _send_via_smtp_detailed(
    to_email: str, subject: str, html_body: str, text_body: str, *,
    sender: str | None = None, reply_to: str = "", headers: dict[str, str] | None = None,
) -> tuple[bool, str, bool]:
    """(sent, error, retryable)."""
    if not settings.smtp_host or not settings.smtp_username or not settings.smtp_password:
        logger.error(
            "mailer: EMAIL_PROVIDER=smtp but SMTP_HOST/SMTP_USERNAME/SMTP_PASSWORD are not fully "
            "configured -- email to %s was not sent.", to_email,
        )
        return False, "SMTP is not configured", False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender or settings.email_from
    message["To"] = to_email
    if reply_to:
        message["Reply-To"] = reply_to
    for name, value in (headers or {}).items():
        message[name] = value
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        if settings.smtp_use_ssl:
            # Implicit TLS (e.g. port 465) -- the socket is SSL-wrapped before any
            # SMTP command is sent, so STARTTLS is neither needed nor valid here.
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
                smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls()
                smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        return True, "", False
    except Exception as exc:
        logger.exception("mailer: failed to send email to %s via SMTP", to_email)
        return False, f"{type(exc).__name__}: {exc}", True


def deliver(to_email: str, message: Message) -> bool:
    """Renders a registered template, records it (Section 1.3), and sends it
    on its stream -- unless its dedupe key was already delivered, in which
    case nothing is sent. Never raises."""
    spec = message.spec
    try:
        subject = message.subject
        html_body, text_body = render_html(message), render_text(message)
    except Exception:
        logger.exception("mailer: failed to render %s for %s", spec.template_id, to_email)
        return False

    record_id, should_send = message_log.claim(
        template_id=spec.template_id, template_version=spec.version, variant=message.variant, tier=spec.tier,
        stream=spec.stream.key, recipient_email=to_email,
        dedupe_key=f"{message.dedupe_key}|{to_email.strip().lower()}" if message.dedupe_key else None,
        related_entity_type=message.related_entity_type, related_entity_id=message.related_entity_id,
        body_hash=message_log.content_hash(text_body),
    )
    if not should_send:
        logger.info("mailer: %s to %s already sent -- duplicate suppressed", spec.template_id, to_email)
        return True

    headers = {
        "X-Zoiko-Template": f"{spec.template_id}@{spec.version}",
        "X-Zoiko-Message-Class": f"tier-{spec.tier}",
    }
    if message.variant:
        headers["X-Zoiko-Template-Variant"] = message.variant
    if message.unsubscribe_url:
        # RFC 8058 one-click unsubscribe (Section 3.2); the URL also accepts POST.
        headers["List-Unsubscribe"] = f"<{message.unsubscribe_url}>"
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    if settings.email_provider != "smtp":
        try:
            _write_to_dev_outbox(to_email, subject, text_body, html_body)
        except Exception as exc:
            logger.exception("mailer: failed to write dev outbox entry for %s", to_email)
            message_log.complete(record_id, status="FAILED", provider="file", attempts=1, error=str(exc))
            return False
        message_log.complete(record_id, status="OUTBOX", provider="file", attempts=1)
        return True

    attempts, error = 0, ""
    for attempt in range(1, SMTP_ATTEMPTS + 1):
        attempts = attempt
        sent, error, retryable = _send_via_smtp_detailed(
            to_email, subject, html_body, text_body,
            sender=streams.sender_for(spec.stream), reply_to=streams.reply_to_for(spec.stream), headers=headers,
        )
        if sent:
            message_log.complete(record_id, status="SENT", provider="smtp", attempts=attempts)
            return True
        if not retryable:
            break
        if attempt < SMTP_ATTEMPTS:
            time.sleep(SMTP_RETRY_DELAY_SECONDS)
    message_log.complete(record_id, status="FAILED", provider="smtp", attempts=attempts, error=error)
    return False


def send_email(
    to_email: str, subject: str, *, heading: str, body_lines: list[str],
    cta_label: str | None = None, cta_url: str | None = None,
) -> bool:
    """Ad-hoc service email in the standard design, for one-off messages that
    have no registered template yet. Prefer a registered template."""
    spec = TemplateSpec(
        "ZR-EML-GEN-000", "1.0.0", "General service message", TIER_1, streams.TRANSACTIONS,
        (), cta_label, subject.replace("{", "{{").replace("}", "}}"), "",
    )
    return deliver(to_email, Message(spec=spec, heading=heading, intro=list(body_lines), cta_url=cta_url or ""))


def _url(path: str) -> str:
    return f"{settings.frontend_url}{path}"


# ------------------------------------------------- A -- Account and security

def send_welcome_email(to_email: str, full_name: str) -> None:
    """ZR-EML-ID-002, seeker-first variant."""
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-ID-002"), variant="seeker-first", dedupe_key="ID-002:welcome",
        heading="Welcome to Zoiko Rooms", first_name=fmt.first_name(full_name),
        intro=[
            "Welcome to Zoiko Rooms — a global platform dedicated to individual private rooms, generally rented "
            "for 30 nights or longer.",
            "You can search without creating duplicate profiles, add provider or organization roles when needed, and "
            "keep one clear record from discovery through move-out.",
            "Choose the path that matches what you need today. You can change or add roles later from your account.",
        ],
        cta_url=_url("/account"),
    ))


def send_password_reset_email(to_email: str, reset_link: str, expires_minutes: int, full_name: str = "") -> None:
    """ZR-EML-ID-006, "started" variant: a password-reset link."""
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-ID-006"), variant="started",
        heading="Continue your account recovery", first_name=fmt.first_name(full_name),
        intro=[
            "Your account recovery request is now started.",
            f"Use the secure button below to choose a new password. The link expires in {expires_minutes} minutes and "
            "can be used only once.",
        ],
        outro=[
            "If you did not request this, you can ignore the email. Your password and account remain unchanged.",
        ],
        cta_url=reset_link,
    ))


def _send_step_up_code(to_email: str, full_name: str, code: str, expires_minutes: int, *, action: str, variant: str) -> None:
    """ZR-EML-ID-003, six-digit one-time code / step-up variant. The code is
    never placed in the subject or preheader."""
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-ID-003"), variant=variant,
        heading="Your one-time security code", first_name=fmt.first_name(full_name),
        intro=[f"Use this code to {action}. The code expires in {expires_minutes} minutes and can be used only once."],
        code=code,
        outro=[
            "Zoiko Rooms will never ask you to forward this email or share a sign-in code, password, passkey or "
            "payment details.",
            "If you did not request this, you can ignore the email. Your account remains unchanged, and no change "
            "takes effect without this code.",
        ],
    ))


def send_payout_beneficiary_verification_code_email(to_email: str, full_name: str, code: str, expires_minutes: int) -> None:
    _send_step_up_code(to_email, full_name, code, expires_minutes,
                       action="confirm your payout account", variant="step-up-payout")


def send_rental_payment_instruction_verification_code_email(to_email: str, full_name: str, code: str, expires_minutes: int) -> None:
    _send_step_up_code(to_email, full_name, code, expires_minutes,
                       action="confirm the payment instructions renters will see", variant="step-up-payment-instructions")


# -------------------------------------------------------- C -- Verification

def _send_identity_status(
    to_email: str, full_name: str, *, status_display: str, variant: str, still_required: str,
    reference: str, note: str = "",
) -> None:
    """ZR-EML-VER-001."""
    facts = [
        ("Checks completed", "Identity document review"),
        ("Still required", still_required),
        ("Reference", reference),
    ]
    if note:
        facts.append(("Reviewer note", note))
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-VER-001"), variant=variant,
        dedupe_key=f"VER-001:{reference}:{variant}" if reference != "Not available" else "",
        related_entity_type="identity_verification", related_entity_id=reference,
        subject_vars={"verification_status_display": status_display},
        heading=f"Your identity check is {status_display}", first_name=fmt.first_name(full_name),
        intro=[f"Your identity check is now {status_display}."],
        facts=facts,
        outro=[
            "Open the secure verification page to review the evidence scope, submit any missing item or request a "
            "review. Do not email identity documents to Zoiko Rooms.",
        ],
        cta_url=_url("/account/identity"),
    ))


def send_identity_verification_approved_email(to_email: str, full_name: str, verification_id: int | None = None) -> None:
    _send_identity_status(to_email, full_name, status_display="approved", variant="approved",
                          still_required="Nothing further for identity",
                          reference=fmt.reference("IDV", verification_id))


def send_identity_verification_rejected_email(to_email: str, full_name: str, notes: str = "", verification_id: int | None = None) -> None:
    _send_identity_status(to_email, full_name, status_display="unable to verify", variant="unable-to-verify",
                          still_required="A new identity document", reference=fmt.reference("IDV", verification_id),
                          note=notes)


def send_identity_verification_additional_evidence_email(to_email: str, full_name: str, notes: str = "", verification_id: int | None = None) -> None:
    _send_identity_status(to_email, full_name, status_display="waiting for more information",
                          variant="additional-information-required",
                          still_required="Additional or different identity evidence",
                          reference=fmt.reference("IDV", verification_id), note=notes)


def send_listing_published_email(
    to_email: str, full_name: str, listing_name: str, *,
    listing_id: str = "", market_name: str = "", min_stay_nights: int | None = None,
) -> None:
    """ZR-EML-LST-002, "published" variant."""
    where = f" in {market_name}" if market_name else ""
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-LST-002"), variant="published",
        dedupe_key=f"LST-002:{listing_id}:{datetime.now(timezone.utc).date().isoformat()}" if listing_id else "",
        related_entity_type="listing", related_entity_id=listing_id,
        heading="Your room listing is live", first_name=fmt.first_name(full_name),
        intro=[f"{listing_name} is now live{where}."],
        facts=[
            ("Availability", "Open for applications"),
            ("Minimum stay", f"{min_stay_nights} nights" if min_stay_nights else "As set on the listing"),
            ("Listing reference", listing_id or "Not available"),
        ],
        outro=[
            "Review the public page for accuracy. Keep availability, price, household information and Room Passport "
            "evidence current. Material changes may require a new review before publication continues.",
        ],
        cta_url=_url("/account/host/listings"),
    ))


def send_listing_rejected_email(to_email: str, full_name: str, listing_name: str, reason: str = "") -> None:
    """ZR-EML-LST-001, "rejected with approved reason and appeal path" variant."""
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-LST-001"), variant="rejected",
        subject_vars={"room_label": listing_name, "listing_review_status": "not approved"},
        heading="Your listing was not approved", first_name=fmt.first_name(full_name),
        intro=[f"Your listing for {listing_name} has been rejected."],
        facts=[("Reason", reason or "See the listing workspace for the review notes")],
        outro=[
            "Open the listing workspace to review field-level guidance, evidence requirements and the publication "
            "checklist. A listing cannot publish until provider authority, Room Passport, classification and market "
            "requirements are satisfied.",
        ],
        cta_url=_url("/account/host/listings"),
    ))


# ------------------------------------------- B -- Search and saved alerts

def _search_summary(city: str, min_price: float | None, max_price: float | None, room_type: str | None) -> str:
    parts = [f"private rooms in {city}"]
    if room_type:
        parts.append(room_type.replace("_", " "))
    return ", ".join(parts)


def _budget(min_price: float | None, max_price: float | None) -> str:
    if min_price is None and max_price is None:
        return "Any"
    if min_price is not None and max_price is not None:
        return f"{min_price:,.0f} – {max_price:,.0f}"
    return f"From {min_price:,.0f}" if min_price is not None else f"Up to {max_price:,.0f}"


def send_alert_confirmation_email(
    to_email: str, city: str, unsubscribe_url: str, *,
    min_price: float | None = None, max_price: float | None = None, room_type: str | None = None,
) -> None:
    """ZR-EML-MKT-001, "search created" variant."""
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-MKT-001"), variant="search-created",
        heading="Your search is saved",
        intro=[f"Your search for {_search_summary(city, min_price, max_price, room_type)} has been saved."],
        facts=[
            ("Alert frequency", "As soon as a matching room is published"),
            ("Move-in window", "Any"),
            ("Stay length", "30 nights or longer"),
            ("Budget", _budget(min_price, max_price)),
        ],
        outro=[
            "You can change filters, pause alerts or delete the search at any time. Search alerts reflect current "
            "listing data and do not reserve a room.",
        ],
        cta_url=_url(f"/find-a-room?city={city}"),
        unsubscribe_url=unsubscribe_url,
    ))


def send_alert_match_email(to_email: str, city: str, listing_names: list[str], unsubscribe_url: str) -> None:
    """ZR-EML-MKT-002, "immediate alert" variant."""
    count = len(listing_names)
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-MKT-002"), variant="immediate-alert",
        subject_vars={"match_count": count},
        heading=f"{count} new room{'s' if count != 1 else ''} match your search",
        intro=[
            f"We found {count} new or updated private room{'s' if count != 1 else ''} matching your search for {city}.",
        ],
        items=list(listing_names),
        outro=[
            "Each result shows the current Room Passport evidence, provider-authority status, availability and terms. "
            "Verification is evidence-specific, so review what was checked and what remains provider-declared.",
            "Rooms can change quickly. Opening a result does not reserve it.",
        ],
        cta_url=_url(f"/find-a-room?city={city}"),
        unsubscribe_url=unsubscribe_url,
    ))


# ------------------------------------------------ E -- Applications and offers

def send_application_decided_email(
    to_email: str, full_name: str, listing_name: str, approved: bool, *, application_id: int | None = None,
) -> None:
    """ZR-EML-APP-004, "selected for next step" / "declined" variants."""
    decision = "approved" if approved else "declined"
    next_step = (
        "The host will prepare an offer for you to review in Zoiko Rooms." if approved
        else "You can continue searching and apply for other rooms."
    )
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-APP-004"), variant="selected-for-next-step" if approved else "declined",
        dedupe_key=f"APP-004:{application_id}:{decision}" if application_id else "",
        related_entity_type="application", related_entity_id=str(application_id or ""),
        subject_vars={"listing_short_title": listing_name},
        heading=f"Update on your application for {listing_name}", first_name=fmt.first_name(full_name),
        intro=[f"Your application for {listing_name} is now {decision}."],
        facts=[
            ("Decision date", fmt.day(datetime.now(timezone.utc))),
            ("Next step", next_step),
            ("Application reference", fmt.reference("APP", application_id)),
        ],
        outro=[
            "Open the application record for the complete decision explanation and any permitted review or appeal "
            "route. Zoiko Rooms records approved reason codes so consequential decisions remain auditable.",
        ],
        cta_url=_url("/account/applications"),
    ))


def send_offer_issued_email(
    to_email: str, full_name: str, listing_name: str, *, offer_id: int, terms_version: int,
    move_in=None, rent_text: str = "", deposit_text: str = "", term_months: int | None = None,
    amended: bool = False, host_note: str = "",
) -> None:
    """ZR-EML-OFR-001, "final offer" / "amended offer" variants. Sent when the
    host sends an offer, revises terms on a sent offer, or accepts the
    renter's counter-proposal (which becomes the amended terms)."""
    facts = [
        ("Move-in date", fmt.day(move_in) if move_in else "See the offer"),
        ("Rent", rent_text or "See the offer"),
        ("Deposit", deposit_text or "See the offer"),
    ]
    if term_months:
        facts.append(("Term", f"{term_months} months"))
    facts += [
        ("Required before acceptance", "Review the full terms and accept, decline or propose different terms"),
        ("Offer expires", "No deadline set \u2014 the host may change or withdraw it until you respond"),
    ]
    if host_note:
        facts.append(("Host's note", host_note))
    offer_type = "updated" if amended else "rental"
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-OFR-001"), variant="amended-offer" if amended else "final-offer",
        subject_vars={"offer_type_display": offer_type, "listing_short_title": listing_name},
        heading=f"You have {'an updated' if amended else 'a rental'} offer for {listing_name}",
        first_name=fmt.first_name(full_name),
        intro=[f"You have received {'an updated' if amended else 'a rental'} offer for {listing_name}."],
        facts=facts,
        outro=[
            "Review the full offer, fees, deposit pathway, required disclosures and cancellation terms in Zoiko Rooms "
            "before accepting.",
        ],
        cta_url=_url("/account/applications"),
        dedupe_key=f"OFR-001:{offer_id}:v{terms_version}", related_entity_type="offer", related_entity_id=str(offer_id),
    ))


# OFR-002 variants: the spec's accepted/declined/withdrawn/expired, plus two
# counter-proposal variants this build adds (renter proposed different terms;
# host declined that proposal). A counter the host accepts is sent as
# OFR-001 "amended offer" instead -- the terms changed.
_OFFER_OUTCOME_NEXT_STEP = {
    "accepted": "The host prepares the rental agreement for both of you to review and sign.",
    "accepted-host-copy": "Create and send the rental agreement from the offer panel.",
    "declined": "The offer is closed. You can continue searching and apply for other rooms.",
    "declined-host-copy": "The offer is closed. The room stays available for other applicants.",
    "counter-proposal-received": "Review the renter's proposal and accept it, decline it or send revised terms.",
    "counter-proposal-declined": "The original terms stand. You can accept, decline or propose different terms.",
}


def send_offer_outcome_email(
    to_email: str, full_name: str, listing_name: str, *, offer_id: int, variant: str, status_display: str,
    recipient_is_host: bool = False, detail_facts: list[tuple[str, str]] | None = None, event_key: str = "",
) -> None:
    """ZR-EML-OFR-002."""
    facts = [
        ("Effective", fmt.now()),
        ("Next step", _OFFER_OUTCOME_NEXT_STEP.get(variant, "Open the offer for the current status.")),
        ("Offer reference", fmt.reference("OFR", offer_id)),
    ] + list(detail_facts or [])
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-OFR-002"), variant=variant,
        subject_vars={"listing_short_title": listing_name, "offer_status_display": status_display},
        heading=f"The offer for {listing_name} is {status_display}", first_name=fmt.first_name(full_name),
        intro=[f"The rental offer for {listing_name} is now {status_display}."],
        facts=facts,
        outro=[
            "Acceptance does not confirm the booking until every required agreement, payment, eligibility and "
            "verification condition has been satisfied.",
        ],
        cta_url=_url("/account/host/applications" if recipient_is_host else "/account/applications"),
        dedupe_key=f"OFR-002:{offer_id}:{variant}:{event_key}", related_entity_type="offer",
        related_entity_id=str(offer_id),
    ))


# ----------------------------------------------- F -- Agreements and booking

def send_agreement_ready_email(
    to_email: str, full_name: str, listing_name: str, *, agreement_id: int, version_no: int,
    agreement_type: str, required_signers: str, deadline: datetime | None = None,
) -> None:
    """ZR-EML-AGR-001, "applicant agreement" variant."""
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-AGR-001"), variant="applicant-agreement",
        heading="Your room agreement is ready to review", first_name=fmt.first_name(full_name),
        intro=[f"The agreement for {listing_name} is ready for your review."],
        facts=[
            ("Agreement type", agreement_type),
            ("Version", f"{version_no}.0"),
            ("Required signers", required_signers),
            ("Deadline", fmt.moment(deadline) if deadline else "No deadline set"),
            ("Agreement reference", fmt.reference("ZR-AGR", agreement_id)),
        ],
        outro=[
            "Review the full document, fees, deposit treatment, notice terms and required disclosures in the secure "
            "agreement page. Do not sign from an emailed attachment.",
        ],
        cta_url=_url("/account/applications"),
        dedupe_key=f"AGR-001:{agreement_id}:v{version_no}", related_entity_type="agreement",
        related_entity_id=str(agreement_id),
    ))


def send_signature_status_email(
    to_email: str, full_name: str, listing_name: str, *, agreement_id: int, variant: str,
    signature_status: str, completed_signers: str, pending_signers: str, deadline: datetime | None = None,
    event_key: str = "", recipient_is_host: bool = False,
) -> None:
    """ZR-EML-AGR-002: "signature requested" (the other party signed),
    "reminder" (deadline near), "all signers completed"."""
    all_done = variant == "all-signers-completed"
    deadline_text = fmt.moment(deadline) if deadline else "No deadline set"
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-AGR-002"), variant=variant,
        subject_vars={"listing_short_title": listing_name, "signature_deadline_local": deadline_text},
        subject_override=f"Agreement for {listing_name} is signed by everyone" if all_done else "",
        heading=(f"Everyone has signed the agreement for {listing_name}" if all_done
                 else f"Sign the agreement for {listing_name}"),
        first_name=fmt.first_name(full_name),
        intro=[f"Your signature for the agreement relating to {listing_name} is {signature_status}."],
        facts=[
            ("Completed signers", completed_signers or "None yet"),
            ("Still required", pending_signers or "None"),
            ("Deadline", deadline_text),
            ("Agreement reference", fmt.reference("ZR-AGR", agreement_id)),
        ],
        outro=[
            "Open the secure signing page to review the exact document version and complete the required "
            "authentication. The booking cannot be confirmed until all required conditions are met.",
        ],
        cta_url=_url("/account/host/applications" if recipient_is_host else "/account/applications"),
        dedupe_key=f"AGR-002:{agreement_id}:{variant}:{event_key}", related_entity_type="agreement",
        related_entity_id=str(agreement_id),
    ))


def send_agreement_executed_email(
    to_email: str, full_name: str, listing_name: str, *,
    agreement_id: int | None = None, move_in=None, term_summary: str = "", payment_summary: str = "",
    recipient_is_host: bool = False,
) -> None:
    """ZR-EML-BKG-002: the agreement reaches SIGNED only once every signature
    and initial payment has cleared -- i.e. the booking is confirmed."""
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-BKG-002"), variant="provider-copy" if recipient_is_host else "direct-consumer-booking",
        dedupe_key=f"BKG-002:{agreement_id}" if agreement_id else "",
        related_entity_type="agreement", related_entity_id=str(agreement_id or ""),
        heading="Your room booking is confirmed", first_name=fmt.first_name(full_name),
        intro=[f"Your booking for {listing_name} is confirmed."],
        facts=[
            ("Move-in", fmt.day(move_in) if move_in else "See the booking page"),
            ("Occupancy term", term_summary or "See the signed agreement"),
            ("Amount paid or authorized", payment_summary or "See the payment record"),
            ("Booking reference", fmt.reference("ZR-AGR", agreement_id)),
        ],
        outro=[
            "Open the booking page for the signed agreement, payment record, protected location details, move-in "
            "checklist and support routes.",
        ],
        cta_url=_url("/account/host/listings" if recipient_is_host else "/account/applications"),
    ))


# ------------------------------- G -- Payments, deposits, refunds, payouts

def send_payment_due_email(
    to_email: str, full_name: str, *, obligation_id: int, purpose: str, amount: float, currency: str,
    due_date, payee: str, protection: str = "",
) -> None:
    """ZR-EML-PAY-001 -- a non-rent obligation (e.g. the deposit) due soon."""
    amount_text = fmt.money(amount, currency)
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-PAY-001"), variant=purpose.lower().replace(" ", "-"),
        subject_vars={"payment_purpose_display": purpose},
        heading=f"Payment due for {purpose}", first_name=fmt.first_name(full_name),
        intro=[f"A payment of {amount_text} is due for {purpose}."],
        facts=[
            ("Due date", fmt.day(due_date)),
            ("Payee or service provider", payee),
            ("Payment reference", fmt.reference("RPO", obligation_id)),
            ("Protection or release conditions", protection or "Shown in your rental agreement"),
        ],
        outro=[
            "This payment is made directly to the payee named above -- Zoiko Rooms does not receive or hold it. Use "
            "the payment page to review the instructions and record the payment. Never send card details or bank "
            "credentials by email.",
        ],
        cta_url=_url("/account/payments"),
        dedupe_key=f"PAY-001:{obligation_id}:due-soon", related_entity_type="rental_payment_obligation",
        related_entity_id=str(obligation_id),
    ))


def send_rent_reminder_email(
    to_email: str, full_name: str, *, obligation_id: int, amount: float, currency: str, due_date,
    room_label: str, payee: str, payment_method: str = "", autopay_status: str = "",
) -> None:
    """ZR-EML-RENT-001."""
    amount_text = fmt.money(amount, currency)
    due_text = fmt.day(due_date)
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-RENT-001"), variant="due-soon",
        subject_vars={"amount_localized": amount_text, "due_date_local": due_text},
        heading=f"Rent of {amount_text} is due on {due_text}", first_name=fmt.first_name(full_name),
        intro=[f"Your next rent payment of {amount_text} for {room_label} is due on {due_text}."],
        facts=[
            ("Payee", payee),
            ("Payment method", payment_method or "Paid directly to the payee using their payment instructions"),
            ("Autopay status", autopay_status or "Not set up"),
            ("Rent reference", fmt.reference("RPO", obligation_id)),
        ],
        outro=[
            "Open the rent schedule to review the payment instructions, record a payment you have made, or review "
            "support options. Zoiko Rooms does not receive or hold rent.",
        ],
        cta_url=_url("/account/payments"),
        dedupe_key=f"RENT-001:{obligation_id}:due-soon", related_entity_type="rental_payment_obligation",
        related_entity_id=str(obligation_id),
    ))


_METHOD_LABELS = {
    "CARD": "Card", "BANK_TRANSFER": "Bank transfer", "EXTERNAL": "Payment recorded outside Zoiko Rooms",
    "CASH": "Cash", "UPI": "UPI", "WALLET": "Wallet",
}


def send_payment_confirmed_email(
    to_email: str, full_name: str, amount: float, currency: str, *,
    payment_id: int | None = None, paid_at: datetime | None = None, method_class: str = "",
    purpose: str = "your rental",
) -> None:
    """ZR-EML-PAY-002, "captured" variant."""
    amount_text = fmt.money(amount, currency)
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-PAY-002"), variant="captured",
        dedupe_key=f"PAY-002:{payment_id}" if payment_id else "",
        related_entity_type="simulated_payment", related_entity_id=str(payment_id or ""),
        subject_vars={"amount_localized": amount_text},
        heading=f"Payment received: {amount_text}", first_name=fmt.first_name(full_name),
        intro=[f"We recorded your payment of {amount_text} for {purpose}."],
        facts=[
            ("Paid", fmt.moment(paid_at) if paid_at else fmt.now()),
            ("Method", _METHOD_LABELS.get(method_class, method_class.replace("_", " ").title() or "Not available")),
            ("Provider", "The payment provider identified at checkout"),
            ("Receipt reference", fmt.reference("PAY", payment_id)),
            ("Transaction status", "Captured"),
        ],
        outro=[
            "Open the receipt for the itemized amount, fees, tax treatment where applicable, and the authoritative "
            "transaction record.",
        ],
        cta_url=_url("/account/payments"),
    ))


def send_deposit_status_email(
    to_email: str, full_name: str, amount: float, released: bool, *,
    held_amount: float | None = None, currency: str = "", deposit_id: int | None = None, room_label: str = "",
) -> None:
    """ZR-EML-DEP-003, "completed" variant."""
    label = room_label or "your rental"
    held = held_amount if held_amount is not None else amount
    returned = amount if released else 0.0
    deductions = max(0.0, (held or 0.0) - returned)
    status_display = "completed — deposit released" if released else "completed — deposit forfeited"
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-DEP-003"), variant="released" if released else "forfeited",
        dedupe_key=f"DEP-003:{deposit_id}:{'released' if released else 'forfeited'}" if deposit_id else "",
        related_entity_type="deposit_record", related_entity_id=str(deposit_id or ""),
        subject_vars={"room_or_booking_label": label},
        heading="Deposit settlement update", first_name=fmt.first_name(full_name),
        intro=[f"The deposit settlement for {label} is now {status_display}."],
        facts=[
            ("Deposit held or covered", fmt.money(held, currency)),
            ("Deductions", fmt.money(deductions, currency)),
            ("Amount to return", fmt.money(returned, currency)),
            ("Reference", fmt.reference("DEP", deposit_id)),
        ],
        outro=[
            "Open the secure settlement page to review evidence, accept, dispute or track payment. The email summary "
            "is not a substitute for the full statement or scheme process.",
        ],
        cta_url=_url("/account/payments"),
    ))


def send_refund_completed_email(
    to_email: str, full_name: str, amount: float, currency: str, *,
    refund_id: int | None = None, reason: str = "", initiated_at: datetime | None = None,
) -> None:
    """ZR-EML-RFD-001, "completed" variant."""
    amount_text = fmt.money(amount, currency)
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-RFD-001"), variant="completed",
        dedupe_key=f"RFD-001:{refund_id}:completed" if refund_id else "",
        related_entity_type="refund_request", related_entity_id=str(refund_id or ""),
        subject_vars={"amount_localized": amount_text, "refund_status_display": "completed"},
        heading=f"Your refund of {amount_text} is completed", first_name=fmt.first_name(full_name),
        intro=[f"Your refund of {amount_text} for {reason or 'your payment'} is now completed."],
        facts=[
            ("Refund destination", "The original payment method"),
            ("Initiated", fmt.moment(initiated_at) if initiated_at else fmt.now()),
            ("Refund reference", fmt.reference("RFD", refund_id)),
        ],
        outro=[
            "Your bank or payment provider may take additional time to display the funds. The secure refund record "
            "shows the authoritative status and any required action.",
        ],
        cta_url=_url("/account/payments"),
    ))


def send_payout_paid_email(
    to_email: str, full_name: str, amount: float, currency: str, period_key: str, *,
    payout_id: int | None = None, paid_at: datetime | None = None, deductions_summary: str = "",
) -> None:
    """ZR-EML-PYO-001, "settled" variant."""
    amount_text = fmt.money(amount, currency)
    deliver(to_email, Message(
        spec=registry.get("ZR-EML-PYO-001"), variant="settled",
        dedupe_key=f"PYO-001:{payout_id}:paid" if payout_id else "",
        related_entity_type="payout_record", related_entity_id=str(payout_id or ""),
        subject_vars={"amount_localized": amount_text, "payout_status_display": "paid"},
        heading=f"Payout of {amount_text} is paid", first_name=fmt.first_name(full_name),
        intro=[f"Your payout of {amount_text} for {period_key} is now paid."],
        facts=[
            ("Destination", "Your verified payout account"),
            ("Completed date", fmt.day(paid_at) if paid_at else fmt.day(datetime.now(timezone.utc))),
            ("Fees or deductions", deductions_summary or "Shown in your payout statement"),
            ("Payout reference", fmt.reference("PYO", payout_id)),
        ],
        outro=["Open the payout record for the ledger breakdown, partner status and any required action."],
        cta_url=_url("/account/host/listings"),
    ))
