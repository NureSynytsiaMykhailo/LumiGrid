import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.models import BrightnessProfile, LuminaireGroup, User
from app.schemas import GroupIn, GroupOut
from app.security import get_current_user, require_roles

router = APIRouter(prefix="/groups", tags=["groups"])
editor = require_roles("admin", "dispatcher")


def _get_or_404(db: Session, group_id: uuid.UUID) -> LuminaireGroup:
    group = db.get(LuminaireGroup, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Групу не знайдено")
    return group


def _check_profile(db: Session, profile_id: uuid.UUID | None) -> None:
    if profile_id is not None and db.get(BrightnessProfile, profile_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Профіль не існує")


@router.get("", response_model=list[GroupOut])
def list_groups(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.scalars(select(LuminaireGroup).order_by(LuminaireGroup.name)).all()


@router.get("/{group_id}", response_model=GroupOut)
def get_group(group_id: uuid.UUID, db: Session = Depends(get_db),
              _: User = Depends(get_current_user)):
    return _get_or_404(db, group_id)


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(data: GroupIn, db: Session = Depends(get_db), user: User = Depends(editor)):
    _check_profile(db, data.default_profile_id)
    group = LuminaireGroup(**data.model_dump())
    db.add(group)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Група з такою назвою вже існує")
    audit(db, user.id, "GROUP_CREATED", "luminaire_group", group.id)
    db.commit()
    return group


@router.put("/{group_id}", response_model=GroupOut)
def update_group(group_id: uuid.UUID, data: GroupIn, db: Session = Depends(get_db),
                 user: User = Depends(editor)):
    group = _get_or_404(db, group_id)
    _check_profile(db, data.default_profile_id)
    for field, value in data.model_dump().items():
        setattr(group, field, value)
    audit(db, user.id, "GROUP_UPDATED", "luminaire_group", group.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Група з такою назвою вже існує")
    return group


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: uuid.UUID, db: Session = Depends(get_db),
                 user: User = Depends(editor)):
    group = _get_or_404(db, group_id)
    db.delete(group)
    audit(db, user.id, "GROUP_DELETED", "luminaire_group", group_id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "У групі є світильники або перевизначення")