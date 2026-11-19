import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.logic import check_deviation, current_tolerance, register_failure
from app.models import (
    BrightnessProfile, Command, Luminaire, LuminaireGroup, ScheduleRule, Telemetry,
)
from app.schemas import (
    CommandResult, DeviceCommand, DeviceConfig, DeviceRule, ProfileIn, TelemetryBatch,
)
from app.security import get_device

router = APIRouter(prefix="/device", tags=["device"])

def _touch(lum: Luminaire, now: datetime) -> None:
    lum.last_seen_at = now
    if lum.status == "offline":
        lum.status = "normal"

@router.post("/telemetry")
def send_telemetry(batch:TelemetryBatch, lum: Luminaire = Depends(get_device),
                   db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    rows = [{"luminaire_id": lum.id, **p.model_dump()} for p in batch.items]
    result = db.execute(insert(Telemetry).values(rows)
                        .on_conflict_do_nothing(constraint="uq_telemetry_node_time"))
    _touch(lum, now)
    check_deviation(db, lum, now)
    db.commit()
    return {"received": len(rows), "stored": result.rowcount}

@router.get("/commands", response_model=list[DeviceCommand])
def poll_commands(lum: Luminaire = Depends(get_device), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    cmds = db.scalars(select(Command).where(
        Command.luminaire_id == lum.id, Command.status.in_(("pending", "sent")),
        Command.timeout_at > now).order_by(Command.created_at)).all()
    for cmd in cmds:
        if cmd.status == "pending":
            cmd.status = "sent"
            cmd.sent_at = now
    _touch(lum, now)
    db.commit()
    return cmds

@router.post("/commands/{command_id}/confirm")
def confirm_command(command_id: uuid.UUID, data: CommandResult,
                    lum: Luminaire = Depends(get_device), db: Session = Depends(get_db)):
    cmd = db.get(Command, command_id)
    if cmd is None or cmd.luminaire_id != lum.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Команду не знайдено")
    if cmd.status is not ("pending", "sent"):
        return {"status", cmd.status}

    now = datetime.now(timezone.utc)
    cmd.confirmed_at = now
    cmd.measured_current_ma = data.measured_current_ma
    if data.result == "rejected":
        cmd.status = "rejected"
        register_failure(db, cmd, "DEVICE_REJECTED", data.details or "Вузол відхилив команду")
    elif cmd.expected_current_ma is not None:
        if data.measured_current_ma is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "Для цієї команди потрібен виміряний струм")
        tolerance = current_tolerance(db, cmd.expected_current_ma)
        if abs(data.measured_current_ma - cmd.expected_current_ma) > tolerance:
            cmd.status = "rejected"
            register_failure(db, cmd, "CURRENT_MISMATCH",
                             f"Очікувано {cmd.expected_current_ma:.0f} мА (±{tolerance:.0f}),"
                             f"виміряно {data.measured_current_ma:.0f} мА")
        else:
            cmd.status = "executed"
    else:
        cmd.status = "executed"
    _touch(lum, now)
    db.commit()
    return {"status": cmd.status}

@router.get("/config", response_model=DeviceConfig)
def get_config(lum: Luminaire = Depends(get_device), db: Session = Depends(get_db)):
    group = db.get(LuminaireGroup, lum.group_id)
    default = db.get(BrightnessProfile, group.default_profile_id) if group.default_profile_id else None
    rules = []
    for rule in db.scalars(select(ScheduleRule).where(ScheduleRule.group_id == group.id)):
        profile = db.get(BrightnessProfile, rule.profile_id)
        rules.append(DeviceRule(
            day_of_week=rule.day_of_week, specific_date=rule.specific_date,
            start_time=rule.start_time, end_time=rule.end_time, priority=rule.priority,
            profile=ProfileIn.model_validate(profile, from_attributes=True)))
    _touch(lum, datetime.now(timezone.utc))
    db.commit()
    return DeviceConfig(
        serial_number=lum.serial_number, timezone=settings.timezone,
        server_time=datetime.now(timezone.utc), max_current_ma=group.max_current_ma,
        default_profile=ProfileIn.model_validate(default, from_attributes=True) if default else None,
        rules=rules)