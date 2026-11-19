from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.config import settings
from app.models import (
    AppSetting, BrightnessProfile, Command, Deviation, FailureLog, Luminaire,
    LuminaireGroup, ManualOverride, ScheduleRule, Telemetry,
)

TZ = ZoneInfo(settings.timezone)

DEFAULT = {
    "deviation_window": 3, #кількість послідовних вимірювань мають розходитись
    "brightness_tolerance_pct": 10, #допуск яскравості, в пунктах відсотка
    "current_tolerance_ratio": 0.2, #допуск струму, частка від очікуваного
    "current_tolerance_floor_ma": 20, #мінімальний допуск струму, мА
    "command_timeout_seconds": 60, #час очікування підтвердження команди
    "offline_after_seconds": 120, #через скільки без зв'язку вузол «offline»
}

def get_setting(db: Session, key: str):
    row = db.get(AppSetting, key)
    return row.value if row is not None else DEFAULT[key]

#розклад
def _day_matches(rule: ScheduleRule, day) -> bool:
    if rule.specific_date is not None:
        return rule.specific_date == day
    return rule.day_of_week == day.weekday()

def _rule_active(rule: ScheduleRule, local: datetime) -> bool:
    t = local.time()
    if rule.start_time < rule.end_time:
        return rule.start_time <= t < rule.end_time and _day_matches(rule, local.date())
    if t >= rule.start_time:
        return _day_matches(rule, local.date())
    if t < rule.end_time:
        return _day_matches(rule, local.date() - timedelta(days=1))
    return False

def effective_profile(db: Session, group: LuminaireGroup, at: datetime) -> BrightnessProfile | None:
    local = at.astimezone(TZ)
    rules = db.scalars(select(ScheduleRule).where(ScheduleRule.group_id == group.id)).all()
    active = [r for r in rules if _rule_active(r, local)]
    if active:
        best = max(active, key=lambda r: (r.specific_date is not None, r.priority))
        return db.get(BrightnessProfile, best.profile_id)
    if group.default_profile_id:
        return db.get(BrightnessProfile, group.default_profile_id)
    return None

#модель струму
def expected_current(profile: BrightnessProfile, pct: float) -> float:
    """
    Очікуваний струм (мА) для заданої яскравості.
    Лінійна інтерполяція/екстраполяція за двома калібрувальними точками профілю:
    (базова яскравість, базовий струм) і (яскравість при русі, струм при русі).
    """
    pb, pm = profile.base_brightness_pct, profile.motion_brightness_pct
    ib, im = profile.expected_base_current_ma, profile.expected_motion_current_ma
    if pm != pb:
        slope = (im - ib) / (pm - pb)
    elif pb:
        slope = ib / pb
    else:
        slope = 0.0
    return max(0.0, ib + slope * (pct - pb))

def current_tolerance(db: Session, expected_ma: float) -> float:
    return max(get_setting(db, "current_tolerance_floor_ma"),
               get_setting(db, "current_tolerance_ratio") * expected_ma)

#очікуваний стан вузла
def affected_luminaires(db: Session, override: ManualOverride) -> list[Luminaire]:
    if override.luminaire_id:
        lum = db.get(Luminaire, override.luminaire_id)
        return [lum] if lum and not lum.is_archived else []
    return list(db.scalars(select(Luminaire).where(
        Luminaire.group_id == override.group_id, Luminaire.is_archived.is_(False)
    )))

def active_override(db: Session, lum: Luminaire, at: datetime) -> ManualOverride | None:
    return db.scalars(
        select(ManualOverride)
        .where(ManualOverride.status == "active", ManualOverride.expires_at > at,
               (ManualOverride.luminaire_id == lum.id) | (ManualOverride.group_id == lum.group_id))
        .order_by(ManualOverride.luminaire_id.is_not(None).desc(), ManualOverride.created_at.desc())
    ).first()

def expected_state(db: Session, lum: Luminaire, group: LuminaireGroup, motion: bool,
                   lux: float, at: datetime) -> tuple[int, BrightnessProfile] | None:
    profile = effective_profile(db, group, at)
    if profile is None:
        return None
    override = active_override(db, lum, at)
    if override:
        return override.target_brightness_pct, profile
    daylight = profile.min_lux_threshold > 0 and lux >= profile.min_lux_threshold
    if daylight:
        return 0, profile
    return (profile.motion_brightness_pct if motion else profile.base_brightness_pct), profile

#окоманди
def new_command(db: Session, lum: Luminaire, command_type: str, payload: dict,
                expected_ma: float | None, created_by, override_id, now: datetime) -> Command:
    cmd = Command(
        luminaire_id=lum.id, override_id=override_id, command_type=command_type,
        payload=payload, expected_current_ma=expected_ma, created_by=created_by,
        timeout_at=now + timedelta(seconds=get_setting(db, "command_timeout_seconds")))
    db.add(cmd)
    return cmd

def register_failure(db: Session, cmd: Command, reason: str, details: str) -> None:
    exists = db.scalar(select(FailureLog.id).where(FailureLog.command_id == cmd.id))
    if exists is None:
        db.add(FailureLog(command_id=cmd.id, luminaire_id=cmd.luminaire_id,
                          reason_code=reason, details=details))

def release_override(db: Session, override: ManualOverride, now: datetime) -> None:
    for lum in affected_luminaires(db, override):
        new_command(db, lum, "sync_schedule", {}, None, None, override.id, now)

#виявлення відхилень
def check_deviation(db: Session, lum: Luminaire, now: datetime) -> None:
    """Подія створюється, коли N останніх вимірювань поспіль розходяться з планом."""
    window = get_setting(db, "deviation_window")
    rows = db.scalars(select(Telemetry).where(Telemetry.luminaire_id == lum.id)
                      .order_by(Telemetry.recorded_at.desc()).limit(window)).all()
    if len(rows) < window:
        return
    group = db.get(LuminaireGroup, lum.group_id)
    tol_pct = get_setting(db, "brightness_tolerance_pct")
    results = []
    for r in rows:
        state = expected_state(db, lum, group, r.motion_detected, r.illuminance_lux, r.recorded_at)
        if state is None:
            return
        pct, profile = state
        exp_ma = expected_current(profile, r.actual_brightness_pct)
        bad = (abs(r.actual_brightness_pct - pct) > tol_pct
               or abs(r.current_ma - exp_ma) > current_tolerance(db, exp_ma))
        results.append((bad, r, pct, profile))

    if all(item[0] for item in results):
        open_dev = db.scalar(select(Deviation.id).where(
            Deviation.luminaire_id == lum.id,
            Deviation.status.in_(("open", "in_inspection"))).limit(1))
        if open_dev is None:
            _, r, pct, profile = results[0]
            db.add(Deviation(
                luminaire_id=lum.id, expected_brightness_pct=pct,
                actual_brightness_pct=r.actual_brightness_pct,
                expected_current_ma=expected_current(profile, pct),
                measured_current_ma=r.current_ma, detected_at=now))
            audit(db, None, "DEVIATION_DETECTED", "luminaire", lum.id)
        lum.status = "deviation"
    elif not any(item[0] for item in results) and lum.status == "deviation":
        lum.status = "normal"

#фонве обслуговування
def expire_overrides(db: Session, now: datetime) -> None:
    items = db.scalars(select(ManualOverride).where(
        ManualOverride.status == "active", ManualOverride.expires_at <= now
    )).all()
    for o in items:
        o.status = "expired"
        audit(db, None, "OVERRIDE_EXPIRED", "manual_override", o.id)
        release_override(db, o, now)

def expire_commands(db: Session, now: datetime) -> None:
    items = db.scalars(select(Command).where(
        Command.status.in_(("pending", "sent")), Command.timeout_at <= now)).all()
    for cmd in items:
        cmd.status = "expired"
        lum = db.get(Luminaire, cmd.luminaire_id)
        reason = "OFFLINE" if lum.status == "offline" else "TIMEOUT"
        register_failure(db, cmd, reason, "Підтвердження не отримано до завершення строку очікування")


def mark_offline(db: Session, now: datetime) -> None:
    limit = now - timedelta(seconds=get_setting(db, "offline_after_seconds"))
    for lum in db.scalars(select(Luminaire).where(
            Luminaire.is_archived.is_(False), Luminaire.status != "offline",
            Luminaire.last_seen_at < limit)):
        lum.status = "offline"