import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TEST_URL = os.getenv("TEST_DATABASE_URL",
                     "postgresql+psycopg2://lumigrid:lumigrid@db:5432/lumigrid_test")
os.environ["DATABASE_URL"] = TEST_URL
os.environ.setdefault("JWT_SECRET", "test-secret")

import app.models
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import User
from app.security import hash_password

PASSWORD = "pass1234"


@pytest.fixture(scope="session", autouse=True)
def database():
    url = make_url(TEST_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                             {"n": url.database})
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables(database):
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth(client):
    def _auth(role: str) -> dict:
        email = f"{role}@test.local"
        with SessionLocal() as db:
            if db.query(User).filter_by(email=email).first() is None:
                db.add(User(email=email, password_hash=hash_password(PASSWORD),
                            full_name=role, role=role))
                db.commit()
        resp = client.post("/auth/login", data={"username": email, "password": PASSWORD})
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}
    return _auth


PROFILE = {"name": "Нічний", "base_brightness_pct": 30, "motion_brightness_pct": 100,
           "min_lux_threshold": 20, "expected_base_current_ma": 150,
           "expected_motion_current_ma": 500}


@pytest.fixture
def world(client, auth):
    admin = auth("admin")
    profile = client.post("/profiles", json=PROFILE, headers=admin).json()
    group = client.post("/groups", headers=admin, json={
        "name": "Тестова група", "default_profile_id": profile["id"],
        "max_current_ma": 800}).json()
    lum = client.post("/luminaires", headers=admin, json={
        "group_id": group["id"], "serial_number": "T-0001"}).json()
    return {
        "admin": admin, "profile": profile, "group": group, "lum": lum,
        "device": {"X-Device-Serial": "T-0001", "X-Device-Token": lum["device_token"]},
    }


def point(minutes_ago: int, brightness=30, current=150, lux=0, motion=False) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {"recorded_at": ts.isoformat(), "illuminance_lux": lux,
            "motion_detected": motion, "current_ma": current,
            "actual_brightness_pct": brightness}
