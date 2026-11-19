from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

from app.logic import TZ, _rule_active, effective_profile, expected_current
from app.db import SessionLocal
from app.models import BrightnessProfile, LuminaireGroup, ScheduleRule


def profile(base=30, motion=100, ib=150, im=500):
    return SimpleNamespace(base_brightness_pct=base, motion_brightness_pct=motion,
                           expected_base_current_ma=ib, expected_motion_current_ma=im)

def rule(start, end, dow=None, day=None):
    return SimpleNamespace(start_time=start, end_time=end, day_of_week=dow, specific_date=day)

def test_expected_current_calibration_points():
    p = profile()
    assert expected_current(p, 30) == 150
    assert expected_current(p, 100) == 500

def test_expected_current_interpolation_and_zero():
    p = profile()
    assert expected_current(p, 65) == 325
    assert expected_current(p, 0) == 0

def test_expected_current_never_negative():
    assert expected_current(profile(base=50, motion=100, ib=10, im=500), 0) == 0

def test_expected_current_equal_points():
    assert expected_current(profile(base=50, motion=50, ib=200, im=200), 25) == 100

def test_rule_inside_one_day():
    r = rule(time(18), time(23), dow=2)
    wed = datetime(2026, 11, 18, 20, 0, tzinfo=TZ)
    assert _rule_active(r, wed)
    assert not _rule_active(r, wed.replace(hour=23, minute=30))

def test_rule_across_midnight_belongs_to_start_day():
    r = rule(time(22), time(6), dow=4)
    fri = datetime(2026, 11, 20, 23, 0, tzinfo=TZ)
    sat_morning = fri + timedelta(hours=3)
    fri_morning = datetime(2026, 11, 20, 2, 0, tzinfo=TZ)
    assert _rule_active(r, fri)
    assert _rule_active(r, sat_morning)
    assert not _rule_active(r, fri_morning)

def test_specific_date_rule():
    r = rule(time(0, 0), time(23, 59), day=date(2026, 12, 31))
    assert _rule_active(r, datetime(2026, 12, 31, 12, 0, tzinfo=TZ))
    assert not _rule_active(r, datetime(2027, 1, 1, 12, 0, tzinfo=TZ))

def test_effective_profile_specific_date_beats_weekday():
    with SessionLocal() as db:
        weekly = BrightnessProfile(name="weekly", base_brightness_pct=20, motion_brightness_pct=60,
                                   min_lux_threshold=0, expected_base_current_ma=100,
                                   expected_motion_current_ma=300)
        holiday = BrightnessProfile(name="holiday", base_brightness_pct=50, motion_brightness_pct=100,
                                    min_lux_threshold=0, expected_base_current_ma=250,
                                    expected_motion_current_ma=500)
        group = LuminaireGroup(name="g", max_current_ma=800)
        db.add_all([weekly, holiday, group])
        db.flush()
        at = datetime(2026, 12, 31, 20, 0, tzinfo=TZ)
        db.add_all([
            ScheduleRule(group_id=group.id, profile_id=weekly.id, day_of_week=at.weekday(),
                         start_time=time(0, 0), end_time=time(23, 59), priority=10),
            ScheduleRule(group_id=group.id, profile_id=holiday.id, specific_date=at.date(),
                         start_time=time(0, 0), end_time=time(23, 59), priority=0),
        ])
        db.flush()
        assert effective_profile(db, group, at).name == "holiday"
        db.rollback()
