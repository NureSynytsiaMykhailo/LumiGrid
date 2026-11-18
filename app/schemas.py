import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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