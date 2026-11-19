import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.db import get_db
from app.models import BrightnessProfile, LuminaireGroup, ScheduleRule, User
from app.schemas import RuleIn, RuleOut
from app.security import get_current_user, require_roles

router = APIRouter(prefix="/rules", tags=["rules"])
editor = require_roles("admin", "dispatcher")

def _validate_refs(db: Session, data: RuleIn) -> None:
    if db.get(LuminaireGroup, data.group_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Група не існує")
    if db.get(BrightnessProfile, data.profile_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Профіль не існує")

def _get_or_404(db: Session, rule_id: uuid.UUID) -> ScheduleRule:
    rule = db.get(ScheduleRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Правило не знайдено")
    return rule

@router.get("", response_model=list[RuleOut])
def list_rules(group_id: uuid.UUID | None = None, db: Session = Depends(get_db),
               _: User = Depends(get_current_user)):
    query = select(ScheduleRule)
    if group_id:
        query = query.where(ScheduleRule.group_id == group_id)
    return db.scalars(query.order_by(ScheduleRule.start_time)).all()

@router.post("", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(data: RuleIn, db: Session = Depends(get_db), user: User = Depends(editor)):
    _validate_refs(db, data)
    rule = ScheduleRule(**data.model_dump())
    db.add(rule)
    db.flush()
    audit(db, user.id, "RULE_CREATED", "schedule_rule", rule.id)
    db.commit()
    return rule

@router.put("/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: uuid.UUID, data: RuleIn, db: Session = Depends(get_db),
                user: User = Depends(editor)):
    rule = _get_or_404(db, rule_id)
    _validate_refs(db, data)
    for field, value in data.model_dump().items():
        setattr(rule, field, value)
    audit(db, user.id, "RULE_UPDATED", "schedule_rule", rule.id)
    db.commit()
    return rule

@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(editor)):
    rule = _get_or_404(db, rule_id)
    db.delete(rule)
    audit(db, user.id, "RULE_DELETED", "schedule_rule", rule_id)
    db.commit()