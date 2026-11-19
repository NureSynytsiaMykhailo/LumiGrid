import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.models import Deviation, Luminaire, MaintenanceAct, MaintenancePhoto, User
from app.schemas import ActIn, ActOut, PhotoOut
from app.security import get_current_user, require_roles

router = APIRouter(prefix="/acts", tags=["acts"])
author = require_roles("technician", "admin")

UPLOAD_DIR = Path("uploads")
MAX_PHOTO_BYTES = 5 * 1024 * 1024
SIGNATURES = {b"\xff\xd8\xff": ".jpg", b"\x89PNG\r\n\x1a\n": ".png"}


def _act_out(db: Session, act: MaintenanceAct) -> ActOut:
    photos = db.scalars(select(MaintenancePhoto).where(MaintenancePhoto.act_id == act.id)
                        .order_by(MaintenancePhoto.created_at)).all()
    return ActOut.model_validate(act).model_copy(
        update={"photos": [PhotoOut.model_validate(p) for p in photos]})


def _get_or_404(db: Session, act_id: uuid.UUID) -> MaintenanceAct:
    act = db.get(MaintenanceAct, act_id)
    if act is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Акт не знайдено")
    return act


@router.post("", response_model=ActOut, status_code=status.HTTP_201_CREATED)
def sync_act(data: ActIn, response: Response, db: Session = Depends(get_db),
             user: User = Depends(author)):
    existing = db.get(MaintenanceAct, data.id)
    if existing is not None:
        if existing.technician_id != user.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "Ідентифікатор акта вже зайнятий")
        response.status_code = status.HTTP_200_OK
        return _act_out(db, existing)

    if db.get(Luminaire, data.luminaire_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Світильник не існує")
    if data.deviation_id and db.get(Deviation, data.deviation_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Відхилення не існує")

    act = MaintenanceAct(**data.model_dump(), technician_id=user.id)
    db.add(act)
    audit(db, user.id, "ACT_SYNCED", "maintenance_act", act.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        response.status_code = status.HTTP_200_OK
        return _act_out(db, _get_or_404(db, data.id))
    return _act_out(db, act)


@router.post("/{act_id}/photos", response_model=PhotoOut, status_code=status.HTTP_201_CREATED)
def upload_photo(act_id: uuid.UUID, file: UploadFile, response: Response,
                 db: Session = Depends(get_db), user: User = Depends(author)):
    act = _get_or_404(db, act_id)
    if act.technician_id != user.id and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Чужий акт")

    content = file.file.read(MAX_PHOTO_BYTES + 1)
    if len(content) > MAX_PHOTO_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Фото більше 5 МБ")
    ext = next((e for sig, e in SIGNATURES.items() if content.startswith(sig)), None)
    if ext is None:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Дозволені лише JPEG і PNG")

    relative = f"{act_id}/{hashlib.sha256(content).hexdigest()}{ext}"
    existing = db.scalar(select(MaintenancePhoto).where(
        MaintenancePhoto.act_id == act_id, MaintenancePhoto.file_path == relative))
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing

    target = UPLOAD_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    photo = MaintenancePhoto(act_id=act_id, file_path=relative)
    db.add(photo)
    db.commit()
    return photo


@router.get("", response_model=list[ActOut])
def list_acts(luminaire_id: uuid.UUID | None = None, limit: int = Query(50, le=200),
              db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    query = select(MaintenanceAct).order_by(MaintenanceAct.created_offline_at.desc()).limit(limit)
    if luminaire_id:
        query = query.where(MaintenanceAct.luminaire_id == luminaire_id)
    return [_act_out(db, act) for act in db.scalars(query)]


@router.get("/{act_id}", response_model=ActOut)
def get_act(act_id: uuid.UUID, db: Session = Depends(get_db),
            _: User = Depends(get_current_user)):
    return _act_out(db, _get_or_404(db, act_id))


@router.get("/{act_id}/photos/{photo_id}")
def download_photo(act_id: uuid.UUID, photo_id: uuid.UUID, db: Session = Depends(get_db),
                   _: User = Depends(get_current_user)):
    photo = db.get(MaintenancePhoto, photo_id)
    if photo is None or photo.act_id != act_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Фото не знайдено")
    return FileResponse(UPLOAD_DIR / photo.file_path)