"""Help centre articles for signed-in users (services/help_centre.py), and
"was this helpful?" feedback on them (recorded in the audit log, where the
knowledge-base owners can review it)."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.correlation import get_correlation_id
from app.crud.audit import log_audit_event
from app.models.kb import KbDocument
from app.models.user_account import UserAccount
from app.schemas.common import CamelModel
from app.services.help_centre import list_articles

router = APIRouter(prefix="/api/users/help", tags=["help-centre"], dependencies=[Depends(get_current_user)])


class HelpArticleFeedback(CamelModel):
    helpful: bool


@router.get("/articles")
def get_help_articles(db: Session = Depends(get_db)):
    return list_articles(db)


@router.post("/articles/{slug}/feedback")
def post_help_article_feedback(
    slug: str, payload: HelpArticleFeedback, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    document = db.scalar(select(KbDocument).where(KbDocument.slug == slug, KbDocument.status == "ACTIVE"))
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")
    log_audit_event(
        db, None, "help_article.feedback", "kb_document", str(document.id), get_correlation_id(request),
        reason=f"user:{user.id} helpful:{'yes' if payload.helpful else 'no'}",
    )
    db.commit()
    return {"ok": True}
