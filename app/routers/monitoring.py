import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.models import Command, Deviation, FailureLog, Telemetry, User
from app.schemas import CommandOut, DeviationOut, DeviationUpdate, FailureOut, TelemetryOut
from app.security import get_current_user, require_roles

router = APIRouter(tags=["monitoring"])
editor = require_roles("admin", "dispatcher")

@router.get("/commands", response_model=list[CommandOut])
def list_commands(luminaire_id: uuid.UUID | None = None, status_filter: str | None = None,
                  limit: int = Query(100, le=500), db: Session = Depends(get_db),
                  _: User = Depends(editor)):
    query = select(Command).order_by(Command.created_at.desc()).limit(limit)
    if luminaire_id:
        query = query.where(Command.luminaire_id == luminaire_id)
    if status_filter:
        query = query.where(Command.status == status_filter)
    return db.scalar(query).all()

@router.get("/failures", response_model=list[FailureOut])
def list_failures(luminaire_id: uuid.UUID | None = None, limit: int = Query(100, le=500),
                  db: Session = Depends(get_db), _: User = Depends(editor)):
    query = select(FailureLog).order_by(FailureLog.created_at.desc()).limit(limit)
    if luminaire_id:
        query = query.where(FailureLog.luminaire_id == luminaire_id)
    return db.scalars(query).all()

@router.get("/deviations", response_model=list[DeviationOut])
def list_deviations(status_filter: str | None = None, db: Session = Depends(get_db),
                    _: User = Depends(get_current_user)):
    query = select(Deviation).order_by(Deviation.detected_at.desc()).limit(200)
    if status_filter:
        query = query.where(Deviation.status == status_filter)
    return db.scalars(query).all()

@router.patch("/deviations/{deviation_id}", response_model=DeviationOut)
def update_deviation(deviation_id: uuid.UUID, data: DeviationUpdate,
                     db: Session = Depends(get_db), user: User = Depends(editor)):
    dev = db.get(Deviation, deviation_id)
    if dev is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Відхилення не знайдено")
    dev.status = data.status
    if data.status == "resolved":
        dev.resolved_at = datetime.now(timezone.utc)
    audit(db, user.id, "DEVIATION_" + data.status.upper(), "deviation", dev.id)
    db.commit()
    return dev

@router.get("/telemetry", response_model=list[TelemetryOut])
def list_telemetry(luminaire_id: uuid.UUID, limit: int = Query(100, le=1000),
                   db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.scalars(select(Telemetry).where(Telemetry.luminaire_id == luminaire_id)
                      .order_by(Telemetry.recorded_at.desc()).limit(limit)).all()