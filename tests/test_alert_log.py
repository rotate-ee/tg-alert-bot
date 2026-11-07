import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import alert_log

K = ZoneInfo("Europe/Kyiv")


def write(entries):
    alert_log.DATA_PATH.write_text(json.dumps(entries), encoding="utf-8")


def test_legacy_entries_get_ids_and_timezone():
    write([{"start": "2026-10-30T08:10:00+02:00", "end": "2026-10-30T08:50:00+02:00", "level": "red"},
           {"start": "2026-10-31 12:00", "end": "2026-10-31 12:20", "level": "yellow"}])
    data = alert_log.all_entries()
    assert all(e["id"] and e["is_false"] is False for e in data)
    assert data[1]["start"].endswith(("+02:00", "+03:00"))  # "наївна" дата отримала київський пояс


def test_update_validates_range_and_level():
    e = alert_log.add("2026-10-30 08:00", "2026-10-30 08:30", "red")
    with pytest.raises(ValueError, match="range"):
        alert_log.update(e["id"], end="2026-10-30 07:00")
    with pytest.raises(ValueError, match="level"):
        alert_log.update(e["id"], level="blue")
    updated = alert_log.update(e["id"], level="yellow", is_false=True)
    assert updated["level"] == "yellow" and updated["is_false"] is True


def test_cannot_add_alert_in_the_future():
    soon = (datetime.now(K) + timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
    with pytest.raises(ValueError, match="future"):
        alert_log.add(soon, soon, "red")


def test_open_alert_stays_last_after_adding_past_entry():
    alert_log.on_started("red")
    open_id = alert_log.all_entries()[-1]["id"]
    now = datetime.now(K)
    alert_log.add((now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"), (now - timedelta(minutes=50)).strftime("%Y-%m-%d %H:%M"), "yellow")
    assert alert_log.all_entries()[-1]["id"] == open_id
    alert_log.on_ended()
    assert alert_log.get(open_id)["end"] is not None


def test_delete_and_toggle_false_by_id():
    a = alert_log.add("2026-10-01 08:00", "2026-10-01 08:10", "red")
    assert alert_log.toggle_false(a["id"]) is True
    alert_log.delete(a["id"])
    with pytest.raises(KeyError):
        alert_log.delete(a["id"])


def test_months_and_clear_month_keeps_open_alert():
    alert_log.add("2026-09-03 08:00", "2026-09-03 08:20", "red")
    alert_log.add("2026-09-10 08:00", "2026-09-10 08:20", "red")
    alert_log.add("2026-10-05 08:00", "2026-10-05 08:20", "red")
    assert [(m["month"], m["count"]) for m in alert_log.months()] == [("2026-10", 1), ("2026-09", 2)]
    assert alert_log.clear_month("2026-09") == (2, False)
    assert [m["month"] for m in alert_log.months()] == ["2026-10"]
    with pytest.raises(ValueError):
        alert_log.clear_month("вересень")


def test_clear_month_does_not_remove_ongoing():
    alert_log.on_started("red")
    month = alert_log.all_entries()[-1]["start"][:7]
    assert alert_log.clear_month(month, keep_open=True) == (0, True)
    assert alert_log.clear_month(month, keep_open=False)[0] == 1
