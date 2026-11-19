import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Role = Literal["admin", "dispatcher", "technician"]

class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

#users
class UserCreate(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1)
    role: Role
    locale: str = "uk"

class UserUpdate(BaseModel):
    full_name: str | None = None
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)

class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    locale: str
    is_active: bool
    created_at: datetime

#profiles
class ProfileIn(BaseModel):
    name: str = Field(min_length=1)
    base_brightness_pct: int = Field(ge=0, le=100)
    motion_brightness_pct: int = Field(ge=0, le=100)
    min_lux_threshold: float = Field(ge=0)
    expected_base_current_ma: float = Field(ge=0)
    expected_motion_current_ma: float = Field(ge=0)

    @model_validator(mode="after")
    def motion_not_below_base(self):
        if self.motion_brightness_pct < self.base_brightness_pct:
            raise ValueError("Яскравість при русі не може бути меншою за базову")
        return self

class ProfileOut(ProfileIn, ORM):
    id: uuid.UUID
    created_at: datetime

#groups
class GroupIn(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    default_profile_id: uuid.UUID | None = None
    max_current_ma: float = Field(gt=0)

class GroupOut(GroupIn, ORM):
    id: uuid.UUID
    created_at: datetime

#lamps
class LuminaireIn(BaseModel):
    group_id: uuid.UUID
    serial_number: str = Field(min_length=1)
    pole_label: str | None = None
    location_lat: float | None = Field(default=None, ge=-90, le=90)
    location_lon: float | None = Field(default=None, ge=-180, le=180)

class LuminaireUpdate(BaseModel):
    group_id: uuid.UUID | None = None
    pole_label: str | None = None
    location_lat: float | None = Field(default=None, ge=-90, le=90)
    location_lon: float | None = Field(default=None, ge=-180, le=180)

class LuminaireOut(ORM):
    id: uuid.UUID
    group_id: uuid.UUID
    serial_number: str
    pole_label: str | None
    location_lat: float | None
    location_lon: float | None
    status: str
    last_seen_at: datetime | None
    is_archived: bool

class LuminaireWithToken(LuminaireOut):
    device_token: str

class RuleIn(BaseModel):
    group_id: uuid.UUID
    profile_id: uuid.UUID
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    specific_date: date | None = None
    start_time: time
    end_time: time
    priority: int = 0

    @model_validator(mode="after")
    def check_rule(self):
        if (self.day_of_week is None) == (self.specific_date is None):
            raise ValueError("Вкажіть або day_of_week, або specific_date")
        if self.start_time == self.end_time:
            raise ValueError("Початок і кінець інтервалу не можуть збігатися")
        return self


class RuleOut(RuleIn, ORM):
    id: uuid.UUID

class OverrideIn(BaseModel):
    luminaire_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    target_brightness_pct: int = Field(ge=0, le=100)
    expires_at: AwareDatetime
    reason: str | None = None

    @model_validator(mode="after")
    def one_target(self):
        if (self.luminaire_id is None) == (self.group_id is None):
            raise ValueError("Вкажіть або luminaire_id, або group_id")
        return self


class OverrideOut(ORM):
    id: uuid.UUID
    luminaire_id: uuid.UUID | None
    group_id: uuid.UUID | None
    target_brightness_pct: int
    reason: str | None
    expires_at: datetime
    status: str
    created_by: uuid.UUID
    created_at: datetime

class CommandOut(ORM):
    id: uuid.UUID
    luminaire_id: uuid.UUID
    override_id: uuid.UUID | None
    command_type: str
    payload: dict
    expected_current_ma: float | None
    status: str
    timeout_at: datetime
    created_by: uuid.UUID | None
    created_at: datetime
    sent_at: datetime | None
    confirmed_at: datetime | None
    measured_current_ma: float | None


class FailureOut(ORM):
    id: uuid.UUID
    command_id: uuid.UUID
    luminaire_id: uuid.UUID
    reason_code: str
    details: str | None
    created_at: datetime


class DeviationOut(ORM):
    id: uuid.UUID
    luminaire_id: uuid.UUID
    expected_brightness_pct: int
    actual_brightness_pct: int
    expected_current_ma: float
    measured_current_ma: float
    status: str
    detected_at: datetime
    resolved_at: datetime | None


class DeviationUpdate(BaseModel):
    status: Literal["in_inspection", "resolved"]


class TelemetryOut(ORM):
    id: int
    luminaire_id: uuid.UUID
    recorded_at: datetime
    illuminance_lux: float
    motion_detected: bool
    current_ma: float
    actual_brightness_pct: int

class TelemetryPoint(BaseModel):
    recorded_at: AwareDatetime
    illuminance_lux: float = Field(ge=0)
    motion_detected: bool
    current_ma: float = Field(ge=0)
    actual_brightness_pct: int = Field(ge=0, le=100)


class TelemetryBatch(BaseModel):
    items: list[TelemetryPoint] = Field(min_length=1, max_length=500)


class DeviceCommand(ORM):
    id: uuid.UUID
    command_type: str
    payload: dict
    expected_current_ma: float | None
    timeout_at: datetime


class CommandResult(BaseModel):
    result: Literal["executed", "rejected"]
    measured_current_ma: float | None = Field(default=None, ge=0)
    details: str | None = None


class DeviceRule(BaseModel):
    day_of_week: int | None
    specific_date: date | None
    start_time: time
    end_time: time
    priority: int
    profile: ProfileIn


class DeviceConfig(BaseModel):
    serial_number: str
    timezone: str
    server_time: datetime
    max_current_ma: float
    default_profile: ProfileIn | None
    rules: list[DeviceRule]