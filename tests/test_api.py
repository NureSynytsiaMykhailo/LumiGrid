from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, Command, FailureLog, ManualOverride
from app.worker import run_maintenance
from tests.conftest import PROFILE, point


def future(hours=1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

def test_login_wrong_password(client, auth):
    auth("admin")
    resp = client.post("/auth/login", data={"username": "admin@test.local", "password": "bad"})
    assert resp.status_code == 401

def test_protected_endpoint_requires_token(client):
    assert client.get("/luminaires").status_code == 401

def test_technician_cannot_manage(client, auth):
    tech = auth("technician")
    assert client.get("/luminaires", headers=tech).status_code == 200
    assert client.get("/users", headers=tech).status_code == 403
    assert client.post("/profiles", json=PROFILE, headers=tech).status_code == 403

def test_profile_validation(client, auth):
    bad = {**PROFILE, "base_brightness_pct": 80, "motion_brightness_pct": 40}
    assert client.post("/profiles", json=bad, headers=auth("admin")).status_code == 422

def test_device_auth(client, world):
    assert client.get("/device/commands", headers=world["device"]).status_code == 200
    wrong = {**world["device"], "X-Device-Token": "wrong"}
    assert client.get("/device/commands", headers=wrong).status_code == 401
    assert client.get("/device/commands").status_code == 422  # немає заголовків

def test_token_rotation_invalidates_old(client, world):
    resp = client.post(f"/luminaires/{world['lum']['id']}/rotate-token", headers=world["admin"])
    assert resp.status_code == 200
    assert client.get("/device/commands", headers=world["device"]).status_code == 401
    new = {**world["device"], "X-Device-Token": resp.json()["device_token"]}
    assert client.get("/device/commands", headers=new).status_code == 200

def test_telemetry_duplicates_ignored(client, world):
    body = {"items": [point(5)]}
    first = client.post("/device/telemetry", json=body, headers=world["device"]).json()
    again = client.post("/device/telemetry", json=body, headers=world["device"]).json()
    assert first["stored"] == 1 and again["stored"] == 0

def make_override(client, world, pct=80, expires=None):
    return client.post("/overrides", headers=world["admin"], json={
        "luminaire_id": world["lum"]["id"], "target_brightness_pct": pct,
        "expires_at": expires or future(), "reason": "тест"})

def test_override_expiry_validation(client, world):
    assert make_override(client, world, expires=future(-1)).status_code == 422
    assert make_override(client, world, expires=future(24 * 8)).status_code == 422

def test_override_respects_group_current_limit(client, world):
    group = client.post("/groups", headers=world["admin"], json={
        "name": "Слабка", "default_profile_id": world["profile"]["id"],
        "max_current_ma": 300}).json()
    lum = client.post("/luminaires", headers=world["admin"], json={
        "group_id": group["id"], "serial_number": "T-0002"}).json()
    resp = client.post("/overrides", headers=world["admin"], json={
        "luminaire_id": lum["id"], "target_brightness_pct": 100, "expires_at": future()})
    assert resp.status_code == 422

def test_override_creates_command_and_confirmation_by_current(client, world):
    assert make_override(client, world, pct=65).status_code == 201
    cmds = client.get("/device/commands", headers=world["device"]).json()
    assert len(cmds) == 1 and cmds[0]["command_type"] == "set_brightness"
    assert cmds[0]["expected_current_ma"] == 325

    ok = client.post(f"/device/commands/{cmds[0]['id']}/confirm", headers=world["device"],
                     json={"result": "executed", "measured_current_ma": 330})
    assert ok.json()["status"] == "executed"
    repeat = client.post(f"/device/commands/{cmds[0]['id']}/confirm", headers=world["device"],
                         json={"result": "executed", "measured_current_ma": 0})
    assert repeat.json()["status"] == "executed"

def test_current_mismatch_goes_to_failure_log(client, world):
    make_override(client, world, pct=65)
    cmd = client.get("/device/commands", headers=world["device"]).json()[0]
    resp = client.post(f"/device/commands/{cmd['id']}/confirm", headers=world["device"],
                       json={"result": "executed", "measured_current_ma": 0})
    assert resp.json()["status"] == "rejected"
    failures = client.get("/failures", headers=world["admin"]).json()
    assert [f["reason_code"] for f in failures] == ["CURRENT_MISMATCH"]

def test_device_rejection_logged(client, world):
    make_override(client, world)
    cmd = client.get("/device/commands", headers=world["device"]).json()[0]
    client.post(f"/device/commands/{cmd['id']}/confirm", headers=world["device"],
                json={"result": "rejected", "details": "перегрів"})
    failures = client.get("/failures", headers=world["admin"]).json()
    assert failures[0]["reason_code"] == "DEVICE_REJECTED"

def test_command_timeout_creates_failure(client, world):
    make_override(client, world)
    with SessionLocal() as db:
        cmd = db.scalars(select(Command)).one()
        cmd.timeout_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    run_maintenance()
    with SessionLocal() as db:
        assert db.scalars(select(Command)).one().status == "expired"
        assert db.scalars(select(FailureLog)).one().reason_code in ("TIMEOUT", "OFFLINE")

def test_expired_override_is_cancelled_and_audited(client, world):
    override_id = make_override(client, world).json()["id"]
    with SessionLocal() as db:
        override = db.get(ManualOverride, override_id)
        override.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        override.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.commit()
    run_maintenance()
    with SessionLocal() as db:
        assert db.get(ManualOverride, override_id).status == "expired"
        actions = [a.action for a in db.scalars(select(AuditLog))]
        assert "OVERRIDE_CREATED" in actions and "OVERRIDE_EXPIRED" in actions
        types = [c.command_type for c in db.scalars(select(Command))]
        assert "sync_schedule" in types

def test_cancel_override(client, world):
    override_id = make_override(client, world).json()["id"]
    resp = client.delete(f"/overrides/{override_id}", headers=world["admin"])
    assert resp.json()["status"] == "cancelled"
    assert client.delete(f"/overrides/{override_id}", headers=world["admin"]).status_code == 409

def test_deviation_after_consecutive_bad_measurements(client, world):
    dev = world["device"]
    bad = [point(m, brightness=0, current=0) for m in (3, 2, 1)]
    client.post("/device/telemetry", json={"items": bad}, headers=dev)
    deviations = client.get("/deviations", headers=world["admin"]).json()
    assert len(deviations) == 1 and deviations[0]["expected_brightness_pct"] == 30
    lum = client.get(f"/luminaires/{world['lum']['id']}", headers=world["admin"]).json()
    assert lum["status"] == "deviation"

def test_no_deviation_when_only_some_measurements_bad(client, world):
    items = [point(3, 0, 0), point(2), point(1)]
    client.post("/device/telemetry", json={"items": items}, headers=world["device"])
    assert client.get("/deviations", headers=world["admin"]).json() == []

def test_status_returns_to_normal(client, world):
    dev = world["device"]
    client.post("/device/telemetry", headers=dev,
                json={"items": [point(m + 10, 0, 0) for m in (3, 2, 1)]})
    client.post("/device/telemetry", headers=dev,
                json={"items": [point(m) for m in (3, 2, 1)]})
    lum = client.get(f"/luminaires/{world['lum']['id']}", headers=world["admin"]).json()
    assert lum["status"] == "normal"

def test_daylight_expects_lamp_off(client, world):
    items = [point(m, brightness=0, current=0, lux=500) for m in (3, 2, 1)]
    client.post("/device/telemetry", json={"items": items}, headers=world["device"])
    assert client.get("/deviations", headers=world["admin"]).json() == []

def test_deviation_can_be_resolved(client, world):
    client.post("/device/telemetry", headers=world["device"],
                json={"items": [point(m, 0, 0) for m in (3, 2, 1)]})
    dev_id = client.get("/deviations", headers=world["admin"]).json()[0]["id"]
    resp = client.patch(f"/deviations/{dev_id}", json={"status": "resolved"}, headers=world["admin"])
    assert resp.json()["status"] == "resolved" and resp.json()["resolved_at"]

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64

def act_body(world, act_id="11111111-1111-4111-8111-111111111111"):
    return {"id": act_id, "luminaire_id": world["lum"]["id"],
            "work_description": "Замінено драйвер",
            "created_offline_at": datetime.now(timezone.utc).isoformat()}

def test_act_sync_is_idempotent(client, world, auth):
    tech = auth("technician")
    first = client.post("/acts", json=act_body(world), headers=tech)
    again = client.post("/acts", json=act_body(world), headers=tech)
    assert first.status_code == 201 and again.status_code == 200
    assert len(client.get("/acts", headers=tech).json()) == 1

def test_only_technician_creates_acts(client, world):
    resp = client.post("/acts", json=act_body(world), headers=world["admin"])
    assert resp.status_code == 201                      # admin дозволений
    other = client.post("/acts", json=act_body(world, "22222222-2222-4222-8222-222222222222"),
                        headers={"Authorization": "Bearer bad"})
    assert other.status_code == 401

def test_dispatcher_cannot_create_act(client, world, auth):
    assert client.post("/acts", json=act_body(world), headers=auth("dispatcher")).status_code == 403

def test_act_unknown_luminaire(client, world, auth):
    body = {**act_body(world), "luminaire_id": "99999999-9999-4999-8999-999999999999"}
    assert client.post("/acts", json=body, headers=auth("technician")).status_code == 422

def test_photo_upload_and_dedup(client, world, auth, monkeypatch, tmp_path):
    monkeypatch.setattr("app.routers.acts.UPLOAD_DIR", tmp_path)
    tech = auth("technician")
    act_id = client.post("/acts", json=act_body(world), headers=tech).json()["id"]
    files = {"file": ("a.png", PNG, "image/png")}
    first = client.post(f"/acts/{act_id}/photos", files=files, headers=tech)
    again = client.post(f"/acts/{act_id}/photos", files=files, headers=tech)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    act = client.get(f"/acts/{act_id}", headers=tech).json()
    assert len(act["photos"]) == 1
    photo = client.get(f"/acts/{act_id}/photos/{act['photos'][0]['id']}", headers=tech)
    assert photo.status_code == 200 and photo.content == PNG

def test_photo_rejects_non_image(client, world, auth, monkeypatch, tmp_path):
    monkeypatch.setattr("app.routers.acts.UPLOAD_DIR", tmp_path)
    tech = auth("technician")
    act_id = client.post("/acts", json=act_body(world), headers=tech).json()["id"]
    resp = client.post(f"/acts/{act_id}/photos", headers=tech,
                       files={"file": ("x.png", b"not an image", "image/png")})
    assert resp.status_code == 415
