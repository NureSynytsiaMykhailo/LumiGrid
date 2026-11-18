import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.models import BrightnessProfile, User
from app.schemas import ProfileIn, ProfileOut
from app.security import get_current_user, require_roles

router = APIRouter(prefix="/profiles", tags=["profiles"])
editor = require_roles("admin", "dispatcher")

def _get_or_404(db: Session, profile_id: uuid.UUID) -> BrightnessProfile:
    profile = db.get(BrightnessProfile, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Профіль не знайдено")
    return profile

@router.get("", response_model=list[ProfileOut])
def list_profiles(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.scalars(select(BrightnessProfile).order_by(BrightnessProfile.name)).all()

@router.get("/{profile_id}", response_model=ProfileOut)
def get_profile(profile_id: uuid.UUID, db: Session = Depends(get_db),
                _: User = Depends(get_current_user)):
    return _get_or_404(db, profile_id)

@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
def create_profile(data: ProfileIn, db: Session = Depends(get_db),
                   user: User = Depends(editor)):
    profile = BrightnessProfile(**data.model_dump(), created_by=user.id)
    db.add(profile)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Профіль з такою назвою вже існує")
    audit(db, user.id, "PROFILE_CREATED", "brightness_profile", profile.id)
    db.commit()
    return profile

@router.put("/{profile_id}", response_model=ProfileOut)
def update_profile(profile_id: uuid.UUID, data: ProfileIn, db: Session = Depends(get_db),
                   user: User = Depends(editor)):
    profile = _get_or_404(db, profile_id)
    for field, value in data.model_dump().items():
        setattr(profile, field, value)
    audit(db, user.id, "PROFILE_UPDATED", "brightness_profile", profile.id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Профіль з такою назвою вже існує")
    return profile

@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(profile_id: uuid.UUID, db: Session = Depends(get_db),
                   user: User = Depends(editor)):
    profile = _get_or_404(db, profile_id)
    db.delete(profile)
    audit(db, user.id, "PROFILE_DELETED", "brightness_profile", profile_id)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Профіль використовується групами або правилами")