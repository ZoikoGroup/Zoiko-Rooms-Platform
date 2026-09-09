from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    admin_notifications,
    admin_contact,
    admin_users,
    analytics,
    authority,
    auth,
    bookings,
    chatbot,
    finance,
    guests,
    handoffs,
    feature_flags,
    identity_verification,
    leasing,
    listings,
    knowledge,
    market_releases,
    occupancy,
    occupancy_classification,
    payments,
    properties,
    public,
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
    user_notifications,
    user_payments,
    user_rentals,
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

app = FastAPI(title="Zoiko Rooms API")

app.middleware("http")(correlation_id_middleware)

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

app.include_router(auth.router)
app.include_router(user_auth.router)
app.include_router(user_identity.router)
app.include_router(user_payments.router)
app.include_router(user_rentals.router)
app.include_router(user_hosting.router)
app.include_router(listings.router)
app.include_router(knowledge.router)
app.include_router(bookings.router)
app.include_router(guests.router)
app.include_router(payments.router)
app.include_router(reviews.router)
app.include_router(analytics.router)
app.include_router(settings_routes.router)
app.include_router(admin_users.router)
app.include_router(public.router)
app.include_router(uploads.router)
app.include_router(search.router)
app.include_router(market_releases.router)
app.include_router(properties.router)
app.include_router(authority.router)
app.include_router(identity_verification.router)
app.include_router(room_passport.router)
app.include_router(occupancy_classification.router)
app.include_router(leasing.router)
app.include_router(occupancy.router)
app.include_router(finance.router)
app.include_router(chatbot.router)
app.include_router(user_chat.router)
app.include_router(handoffs.router)
app.include_router(feature_flags.router)
app.include_router(user_notifications.router)
app.include_router(admin_notifications.router)
app.include_router(user_contact.router)
app.include_router(admin_contact.router)


@app.get("/health")
def health():
    return {"status": "ok"}
