import hashlib
import secrets

import bcrypt

from app.db import SessionLocal
from app.models import BrightnessProfile, Luminaire, LuminaireGroup, User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def main() -> None:
    with SessionLocal() as db:
        if db.query(User).first():
            print("База вже заповнена")
            return

        users = [
            User(email="admin@lumigrid.local", password_hash=hash_password("admin123"),
                 full_name="Адміністратор", role="admin"),
            User(email="dispatcher@lumigrid.local", password_hash=hash_password("disp123"),
                 full_name="Диспетчер", role="dispatcher"),
            User(email="tech@lumigrid.local", password_hash=hash_password("tech123"),
                 full_name="Технік", role="technician"),
        ]
        db.add_all(users)
        db.flush()

        night = BrightnessProfile(
            name="Нічний", base_brightness_pct=30, motion_brightness_pct=100,
            min_lux_threshold=20, expected_base_current_ma=150,
            expected_motion_current_ma=500, created_by=users[1].id)
        db.add(night)
        db.flush()

        group = LuminaireGroup(name="вул. Сумська — ділянка 1", max_current_ma=800,
                               default_profile_id=night.id)
        db.add(group)
        db.flush()

        for i in (1, 2):
            token = secrets.token_urlsafe(32)
            db.add(Luminaire(
                group_id=group.id, serial_number=f"LG-000{i}", pole_label=f"Опора {i}",
                location_lat=49.99 + i / 1000, location_lon=36.23,
                device_token_hash=hashlib.sha256(token.encode()).hexdigest()))
            print(f"LG-000{i}  токен вузла: {token}")

        db.commit()
        print("Готово")


if __name__ == "__main__":
    main()