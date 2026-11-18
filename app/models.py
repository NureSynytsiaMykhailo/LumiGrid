"""ORM-моделі LumiGrid. Відповідають SQL-схемі з діаграм."""
import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    REAL, BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    Index, Integer, SmallInteger, Text, Time, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

TS = DateTime(timezone=True)


def uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True,
                         default=uuid.uuid4, server_default=func.gen_random_uuid())


def fk(table: str, nullable: bool = False, ondelete: str = "RESTRICT"):
    return mapped_column(UUID(as_uuid=True), ForeignKey(f"{table}.id", ondelete=ondelete),
                         nullable=nullable)


def created():
    return mapped_column(TS, server_default=func.now())


def one_of(column: str, *values: str, name: str) -> CheckConstraint:
    items = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({items})", name=name)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (one_of("role", "admin", "dispatcher", "technician", name="ck_users_role"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(Text, default="uk", server_default="uk")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = created()


class LlmDraft(Base):
    __tablename__ = "llm_drafts"
    __table_args__ = (
        one_of("validation_status", "pending", "valid", "invalid", name="ck_llm_validation"),
        one_of("status", "draft", "approved", "rejected", name="ck_llm_status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    requested_by: Mapped[uuid.UUID | None] = fk("users", nullable=True)
    model_name: Mapped[str] = mapped_column(Text)
    context_json: Mapped[dict] = mapped_column(JSONB)
    response_json: Mapped[dict | None] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)
    proposed_profile: Mapped[dict | None] = mapped_column(JSONB)
    validation_status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    validation_errors: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, default="draft", server_default="draft")
    reviewed_by: Mapped[uuid.UUID | None] = fk("users", nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = created()


class BrightnessProfile(Base):
    __tablename__ = "brightness_profiles"
    __table_args__ = (
        CheckConstraint("base_brightness_pct BETWEEN 0 AND 100", name="ck_prof_base"),
        CheckConstraint("motion_brightness_pct BETWEEN 0 AND 100", name="ck_prof_motion"),
        CheckConstraint("motion_brightness_pct >= base_brightness_pct", name="ck_prof_order"),
        CheckConstraint("min_lux_threshold >= 0", name="ck_prof_lux"),
        CheckConstraint("expected_base_current_ma >= 0", name="ck_prof_base_ma"),
        CheckConstraint("expected_motion_current_ma >= 0", name="ck_prof_motion_ma"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True)
    base_brightness_pct: Mapped[int] = mapped_column(SmallInteger)
    motion_brightness_pct: Mapped[int] = mapped_column(SmallInteger)
    min_lux_threshold: Mapped[float] = mapped_column(REAL)
    expected_base_current_ma: Mapped[float] = mapped_column(REAL)
    expected_motion_current_ma: Mapped[float] = mapped_column(REAL)
    llm_draft_id: Mapped[uuid.UUID | None] = fk("llm_drafts", nullable=True, ondelete="SET NULL")
    created_by: Mapped[uuid.UUID | None] = fk("users", nullable=True)
    created_at: Mapped[datetime] = created()


class LuminaireGroup(Base):
    __tablename__ = "luminaire_groups"
    __table_args__ = (CheckConstraint("max_current_ma > 0", name="ck_group_max_ma"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    default_profile_id: Mapped[uuid.UUID | None] = fk("brightness_profiles", nullable=True)
    max_current_ma: Mapped[float] = mapped_column(REAL)
    created_at: Mapped[datetime] = created()


class Luminaire(Base):
    __tablename__ = "luminaires"
    __table_args__ = (
        one_of("status", "normal", "deviation", "fault", "offline", name="ck_lum_status"),
        CheckConstraint("location_lat BETWEEN -90 AND 90", name="ck_lum_lat"),
        CheckConstraint("location_lon BETWEEN -180 AND 180", name="ck_lum_lon"),
        Index("ix_luminaires_group", "group_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    group_id: Mapped[uuid.UUID] = fk("luminaire_groups")
    serial_number: Mapped[str] = mapped_column(Text, unique=True)
    pole_label: Mapped[str | None] = mapped_column(Text)
    location_lat: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    location_lon: Mapped[float | None] = mapped_column(DOUBLE_PRECISION)
    device_token_hash: Mapped[str] = mapped_column(Text)
    token_rotated_at: Mapped[datetime] = created()
    status: Mapped[str] = mapped_column(Text, default="offline", server_default="offline")
    last_seen_at: Mapped[datetime | None] = mapped_column(TS)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = created()


class ScheduleRule(Base):
    __tablename__ = "schedule_rules"
    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_rule_dow"),
        CheckConstraint("(day_of_week IS NULL) <> (specific_date IS NULL)", name="ck_rule_day_or_date"),
        CheckConstraint("start_time <> end_time", name="ck_rule_times"),
        Index("ix_rules_group", "group_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    group_id: Mapped[uuid.UUID] = fk("luminaire_groups", ondelete="CASCADE")
    profile_id: Mapped[uuid.UUID] = fk("brightness_profiles")
    day_of_week: Mapped[int | None] = mapped_column(SmallInteger)
    specific_date: Mapped[date | None] = mapped_column(Date)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)  # end < start = через північ
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ManualOverride(Base):
    __tablename__ = "manual_overrides"
    __table_args__ = (
        one_of("status", "active", "expired", "cancelled", name="ck_ovr_status"),
        CheckConstraint("target_brightness_pct BETWEEN 0 AND 100", name="ck_ovr_pct"),
        CheckConstraint("(luminaire_id IS NULL) <> (group_id IS NULL)", name="ck_ovr_target"),
        CheckConstraint("expires_at > created_at", name="ck_ovr_expiry"),
        Index("ix_overrides_active", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    luminaire_id: Mapped[uuid.UUID | None] = fk("luminaires", nullable=True)
    group_id: Mapped[uuid.UUID | None] = fk("luminaire_groups", nullable=True)
    target_brightness_pct: Mapped[int] = mapped_column(SmallInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TS)
    status: Mapped[str] = mapped_column(Text, default="active", server_default="active")
    created_by: Mapped[uuid.UUID] = fk("users")
    created_at: Mapped[datetime] = created()


class Command(Base):
    __tablename__ = "commands"
    __table_args__ = (
        one_of("command_type", "set_brightness", "sync_schedule", "reboot", name="ck_cmd_type"),
        one_of("status", "pending", "sent", "executed", "expired", "rejected", name="ck_cmd_status"),
        Index("ix_commands_poll", "luminaire_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    luminaire_id: Mapped[uuid.UUID] = fk("luminaires")
    override_id: Mapped[uuid.UUID | None] = fk("manual_overrides", nullable=True, ondelete="SET NULL")
    command_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    expected_current_ma: Mapped[float | None] = mapped_column(REAL)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    timeout_at: Mapped[datetime] = mapped_column(TS)
    created_by: Mapped[uuid.UUID | None] = fk("users", nullable=True)  # None = від розкладу
    created_at: Mapped[datetime] = created()
    sent_at: Mapped[datetime | None] = mapped_column(TS)
    confirmed_at: Mapped[datetime | None] = mapped_column(TS)
    measured_current_ma: Mapped[float | None] = mapped_column(REAL)


class FailureLog(Base):
    __tablename__ = "failure_logs"
    __table_args__ = (
        one_of("reason_code", "TIMEOUT", "CURRENT_MISMATCH", "DEVICE_REJECTED", "OFFLINE",
               name="ck_fail_reason"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    command_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("commands.id", ondelete="RESTRICT"), unique=True)
    luminaire_id: Mapped[uuid.UUID] = fk("luminaires")
    reason_code: Mapped[str] = mapped_column(Text)
    details: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class Telemetry(Base):
    __tablename__ = "telemetry"
    __table_args__ = (
        UniqueConstraint("luminaire_id", "recorded_at", name="uq_telemetry_node_time"),
        CheckConstraint("illuminance_lux >= 0", name="ck_tel_lux"),
        CheckConstraint("current_ma >= 0", name="ck_tel_ma"),
        CheckConstraint("actual_brightness_pct BETWEEN 0 AND 100", name="ck_tel_pct"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    luminaire_id: Mapped[uuid.UUID] = fk("luminaires")
    recorded_at: Mapped[datetime] = mapped_column(TS)
    received_at: Mapped[datetime] = created()
    illuminance_lux: Mapped[float] = mapped_column(REAL)
    motion_detected: Mapped[bool] = mapped_column(Boolean)
    current_ma: Mapped[float] = mapped_column(REAL)
    actual_brightness_pct: Mapped[int] = mapped_column(SmallInteger)


class Deviation(Base):
    __tablename__ = "deviations"
    __table_args__ = (
        one_of("status", "open", "in_inspection", "resolved", name="ck_dev_status"),
        Index("ix_deviations_status", "status", "detected_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    luminaire_id: Mapped[uuid.UUID] = fk("luminaires")
    llm_draft_id: Mapped[uuid.UUID | None] = fk("llm_drafts", nullable=True, ondelete="SET NULL")
    expected_brightness_pct: Mapped[int] = mapped_column(SmallInteger)
    actual_brightness_pct: Mapped[int] = mapped_column(SmallInteger)
    expected_current_ma: Mapped[float] = mapped_column(REAL)
    measured_current_ma: Mapped[float] = mapped_column(REAL)
    status: Mapped[str] = mapped_column(Text, default="open", server_default="open")
    detected_at: Mapped[datetime] = created()
    resolved_at: Mapped[datetime | None] = mapped_column(TS)


class MaintenanceAct(Base):
    __tablename__ = "maintenance_acts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)  # від Android
    luminaire_id: Mapped[uuid.UUID] = fk("luminaires")
    technician_id: Mapped[uuid.UUID] = fk("users")
    deviation_id: Mapped[uuid.UUID | None] = fk("deviations", nullable=True, ondelete="SET NULL")
    work_description: Mapped[str] = mapped_column(Text)
    created_offline_at: Mapped[datetime] = mapped_column(TS)
    synced_at: Mapped[datetime] = created()


class MaintenancePhoto(Base):
    __tablename__ = "maintenance_photos"

    id: Mapped[uuid.UUID] = uuid_pk()
    act_id: Mapped[uuid.UUID] = fk("maintenance_acts", ondelete="CASCADE")
    file_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = fk("users", nullable=True, ondelete="SET NULL")
    updated_at: Mapped[datetime] = created()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = fk("users", nullable=True, ondelete="SET NULL")
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created()