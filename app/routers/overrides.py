import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.logic import (
    affected_luminaires, effective_profile, expected_current, new_command, release_override,
)
from app.models import LuminaireGroup, ManualOverride, User
from app.schemas import OverrideIn, OverrideOut
from app.security import get_current_user, require_roles

router = APIRouter(prefix="/overrides", tags=["overrides"])
editor = require_roles("admin", "dispatcher")
MAX_DURATION = timedelta(days=7)


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail)

@router.get("", response_model=list[OverrideOut])
def list_overrides(status_filter: str | None = None, db: Session = Depends(get_db),
                   _: User = Depends(get_current_user)):
    query = select(ManualOverride).order_by(ManualOverride.created_at.desc()).limit(200)
    if status_filter:
        query = query.where(ManualOverride.status == status_filter)
    return db.scalars(query).all()

@router.post("", response_model=OverrideOut, status_code=status.HTTP_201_CREATED)
def create_override(data: OverrideIn, db: Session = Depends(get_db), user: User = Depends(editor)):
    now = datetime.now(timezone.utc)
    if data.expires_at <= now:
        raise _unprocessable("Строк дії має бути в майбутньому")
    if data.expires_at > now + MAX_DURATION:
        raise _unprocessable("Строк дії не може перевищувати 7 діб")

    override = ManualOverride(**data.model_dump(), created_by=user.id)
    targets = affected_luminaires(db, override)
    if not targets:
        raise _unprocessable("Ціль не знайдено або в групі немає світильників")

    plan = []
    for lum in targets:
        group = db.get(LuminaireGroup, lum.group_id)
        profile = effective_profile(db, group, now)
        if profile is None:
            raise _unprocessable(f"Для групи «{group.name}» не задано профіль")
        expected_ma = expected_current(profile, data.target_brightness_pct)
        if expected_ma > group.max_current_ma:
            raise _unprocessable(
                f"Яскравість {data.target_brightness_pct} % перевищує ліміт струму групи «{group.name}»")
        plan.append((lum, expected_ma))

    db.add(override)
    db.flush()
    payload = {"brightness_pct": data.target_brightness_pct, "until": data.expires_at.isoformat()}
    for lum, expected_ma in plan:
        new_command(db, lum, "set_brightness", payload, expected_ma, user.id, override.id, now)
    audit(db, user.id, "OVERRIDE_CREATED", "manual_override", override.id,
          {"target": data.target_brightness_pct, "expires_at": data.expires_at.isoformat()})
    db.commit()
    return override

@router.delete("/{override_id}", response_model=OverrideOut)
def cancel_override(override_id: uuid.UUID, db: Session = Depends(get_db),
                    user: User = Depends(editor)):
    override = db.get(ManualOverride, override_id)
    if override is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Перевизначення не знайдено")
    if override.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "Перевизначення вже не активне")
    override.status = "cancelled"
    audit(db, user.id, "OVERRIDE_CANCELLED", "manual_override", override.id)
    release_override(db, override, datetime.now(timezone.utc))
    db.commit()
    return override