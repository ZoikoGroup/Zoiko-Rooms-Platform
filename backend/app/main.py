import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm.exc import StaleDataError

from app.api.routes import (
    admin_notifications,
    admin_contact,
    admin_users,
    analytics,
    authority,
    auth,
    bookings,
    chatbot,
    disputes,
    finance,
    guests,
    handoffs,
    feature_flags,
    identity_verification,
    property_location,
    authority_verification,
    sublet_payments,
    leasing,
    listings,
    listing_fees,
    knowledge,
    market_policy,
    market_releases,
    occupancy,
    occupancy_classification,
    party,
    payments,
    payments_overview,
    properties,
    public,
    public_assistant,
    rental_payment_returns,
    rental_payments,
    reviews,
    room_passport,
    search,
    settings as settings_routes,
    uploads,
    user_auth,
    user_chat,
    user_contact,
    user_hosting,
    user_identity,
    user_identity_flow,
    user_notifications,
    user_payments,
    user_rentals,
    user_verification,
    verification,
)
from app.core.config import settings
from app.core.correlation import correlation_id_middleware

import logging

logger = logging.getLogger("uvicorn.error")

if settings.llm_provider == "groq" and not settings.groq_api_key:
    logger.warning(
        "chatbot: GROQ_API_KEY is not set -- the admin assistant will reply with a "
        "configuration error until you add it to backend/.env and restart."
    )

async def _scheduler_loop() -> None:
    """Hourly (settings.scheduler_interval_minutes) run of
    services/scheduled_jobs.py. Runs in a worker thread so the blocking DB
    work never stalls request handling; a failed tick is logged and the loop
    carries on."""
    from app.services.scheduled_jobs import run_scheduled_jobs_once

    await asyncio.sleep(30)  # let the app finish starting up first
    while True:
        try:
            results = await asyncio.to_thread(run_scheduled_jobs_once)
            if results:
                logger.info("scheduled jobs: %s", results)
        except Exception:
            logger.exception("scheduled jobs: tick failed")
        await asyncio.sleep(max(settings.scheduler_interval_minutes, 1) * 60)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_scheduler_loop()) if settings.scheduler_enabled else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Zoiko Rooms API", lifespan=lifespan)

app.middleware("http")(correlation_id_middleware)


@app.exception_handler(StaleDataError)
async def _stale_data_error_handler(request: Request, exc: StaleDataError) -> JSONResponse:
    """AC-39: 'All material state changes are idempotent and protected by
    optimistic/version concurrency controls.' SQLAlchemy's version_id_col
    mechanism (see app/models/dispute.py's DisputeResolutionCase/Claim,
    dispute_settlement.py, dispute_external_proceeding.py, and the
    original DisputeResolutionHold) raises StaleDataError on commit when a
    session holding a stale copy of a row tries to update it after another
    session already changed it. Financial-hold routes already convert this
    locally (crud/disputes.py:_commit_hold_or_stale_conflict) for a
    slightly more specific message; this catches it everywhere else so a
    concurrent-edit race on any other versioned dispute object surfaces as
    a clean, actionable 409 instead of an unhandled 500."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "This record was modified by someone else in the meantime -- reload and retry."},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True, 
    allow_methods=["*"],
    allow_headers=["*"],
) 

Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")
 
# Identity documents live in their own directory and are deliberately never
# mounted here -- they're only reachable through the authenticated
# /api/users/identity-verifications/{id}/document and
# /api/identity-verifications/{id}/document routes.
Path(settings.identity_upload_dir).mkdir(parents=True, exist_ok=True)

# Property/lister evidence -- same never-mounted convention, reachable only
# through the authenticated property-verification document routes.
Path(settings.property_verification_upload_dir).mkdir(parents=True, exist_ok=True)

app.include_router(auth.router)
app.include_router(user_auth.router)
app.include_router(user_identity.router)
app.include_router(user_identity_flow.router)
app.include_router(user_identity_flow.veriff_webhook_router)
app.include_router(user_identity_flow.v1_router)
app.include_router(user_payments.router)
app.include_router(user_rentals.router)
app.include_router(user_hosting.router)
app.include_router(listings.router)
app.include_router(listing_fees.router)
app.include_router(listing_fees.admin_router)
app.include_router(listing_fees.webhook_router)
app.include_router(rental_payment_returns.router)
app.include_router(rental_payments.router)
app.include_router(rental_payments.recipient_router)
app.include_router(rental_payments.admin_router)
app.include_router(sublet_payments.router)
app.include_router(knowledge.router)
app.include_router(bookings.router)
app.include_router(guests.router)
app.include_router(payments.router)
app.include_router(payments_overview.router)
app.include_router(reviews.router)
app.include_router(analytics.router)
app.include_router(settings_routes.router)
app.include_router(admin_users.router)
app.include_router(public.router)
app.include_router(public_assistant.router)
app.include_router(uploads.router)
app.include_router(search.router)
app.include_router(market_releases.router)
app.include_router(market_policy.router)
app.include_router(properties.router)
app.include_router(party.router)
app.include_router(authority.router)
app.include_router(identity_verification.router)
app.include_router(property_location.router)
app.include_router(property_location.location_router)
app.include_router(property_location.admin_location_router)
app.include_router(property_location.admin_router)
app.include_router(authority_verification.router)
app.include_router(authority_verification.confirmation_router)
app.include_router(authority_verification.admin_router)
app.include_router(identity_verification.internal_router)
app.include_router(room_passport.router)
app.include_router(occupancy_classification.router)
app.include_router(leasing.router)
app.include_router(occupancy.router)
app.include_router(finance.router)
app.include_router(finance.webhook_router)
app.include_router(disputes.renter_router)
app.include_router(disputes.host_router)
app.include_router(disputes.admin_router)
app.include_router(chatbot.router)
app.include_router(user_chat.router)
app.include_router(handoffs.router)
app.include_router(feature_flags.router)
app.include_router(user_notifications.router)
app.include_router(admin_notifications.router)
app.include_router(user_contact.router)
app.include_router(admin_contact.router)
app.include_router(verification.router)
app.include_router(user_verification.router)


@app.get("/health")
def health():
    return {"status": "ok"}
