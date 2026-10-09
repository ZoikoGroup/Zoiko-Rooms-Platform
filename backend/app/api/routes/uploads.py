from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.core.image_uploads import save_listing_images
from app.db.session import get_db

router = APIRouter(prefix="/api/uploads", tags=["uploads"], dependencies=[Depends(get_current_admin)])


@router.post("/images")
async def upload_images(files: list[UploadFile], db: Session = Depends(get_db)):
    urls = await save_listing_images(db, files)
    return {"urls": urls}
