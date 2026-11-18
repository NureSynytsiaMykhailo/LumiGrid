import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.models import Luminaire, LuminaireGroup, User
from app.schemas import LuminaireIn, LuminaireOut, LuminaireUpdate, LuminaireWithToken
from app.security import get_current_user, hash_token, require_roles

router = APIRouter(prefix="/luminaires", tags=["luminaires"])
editor = require_roles("admin", "dispatcher")

def _get_or_404(db: Session, luminaire_id: uuid.UUID) -> Luminaire:
    lum = db.get(Luminaire, luminaire_id)
    if lum is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Світильник не знайдено")
    return lum

def _check_group(db: Session, group_id: uuid.UUID) -> None:
    if db.get(LuminaireGroup, group_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Група не існує")

def _with_token(lum: Luminaire, token: str) -> LuminaireWithToken:
    return LuminaireWithToken(**LuminaireOut.model_validate(lum).model_dump(),
                              device_token=token)

@router.get("", response_model=list[LuminaireOut])
def list_luminaires(group_id: uuid.UUID | None = None, include_archived: bool = False,
                    db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    query = select(Luminaire).order_by(Luminaire.serial_number)
    if group_id:
        query = query.where(Luminaire.group_id == group_id)
    if not include_archived:
        query = query.where(Luminaire.is_archived.is_(False))
    return db.scalars(query).all()

@router.get("/by-serial/{serial}", response_model=LuminaireOut)
def get_by_serial(serial: str, db: Session = Depends(get_db),
                  _: User = Depends(get_current_user)):
    lum = db.scalar(select(Luminaire).where(Luminaire.serial_number == serial))
    if lum is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Світильник не знайдено")
    return lum

@router.get("/{luminaire_id}", response_model=LuminaireOut)
def get_luminaire(luminaire_id: uuid.UUID, db: Session = Depends(get_db),
                  _: User = Depends(get_current_user)):
    return _get_or_404(db, luminaire_id)

@router.post("", response_model=LuminaireWithToken, status_code=status.HTTP_201_CREATED)
def create_luminaire(data: LuminaireIn, db: Session = Depends(get_db),
                     user: User = Depends(editor)):
    _check_group(db, data.group_id)
    token = secrets.token_urlsafe(32)
    lum = Luminaire(**data.model_dump(), device_token_hash=hash_token(token))
    db.add(lum)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Серійний номер уже зайнятий")
    audit(db, user.id, "LUMINAIRE_CREATED", "luminaire", lum.id)
    db.commit()
    return _with_token(lum, token)

@router.patch("/{luminaire_id}", response_model=LuminaireOut)
def update_luminaire(luminaire_id: uuid.UUID, data: LuminaireUpdate,
                     db: Session = Depends(get_db), user: User = Depends(editor)):
    lum = _get_or_404(db, luminaire_id)
    changes = data.model_dump(exclude_unset=True)
    if "group_id" in changes and changes["group_id"] is not None:
        _check_group(db, changes["group_id"])
    for field, value in changes.items():
        setattr(lum, field, value)
    audit(db, user.id, "LUMINAIRE_UPDATED", "luminaire", lum.id, {k: str(v) for k, v in changes.items()})
    db.commit()
    return lum

@router.post("/{luminaire_id}/rotate-token", response_model=LuminaireWithToken)
def rotate_token(luminaire_id: uuid.UUID, db: Session = Depends(get_db),
                 user: User = Depends(require_roles("admin"))):
    """Випуск нового токена вузла (старий перестає діяти)."""
    from datetime import datetime, timezone
    lum = _get_or_404(db, luminaire_id)
    token = secrets.token_urlsafe(32)
    lum.device_token_hash = hash_token(token)
    lum.token_rotated_at = datetime.now(timezone.utc)
    audit(db, user.id, "TOKEN_ROTATED", "luminaire", lum.id)
    db.commit()
    return _with_token(lum, token)

@router.delete("/{luminaire_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_luminaire(luminaire_id: uuid.UUID, db: Session = Depends(get_db),
                      user: User = Depends(editor)):
    lum = _get_or_404(db, luminaire_id)
    lum.is_archived = True
    audit(db, user.id, "LUMINAIRE_ARCHIVED", "luminaire", lum.id)
    db.commit()