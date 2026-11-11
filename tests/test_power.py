import asyncio
import json
from pathlib import Path

import pytest

import power

FIXTURE = (Path(__file__).parent / "fixtures" / "dpem_index.html").read_text(encoding="utf-8")


def parse(html=FIXTURE):
    return power.parse_html(html)


def test_parses_the_operational_block_of_the_dpem_site():
    snap = parse()
    assert snap["published"] == "2026-11-07T20:24+02:00"
    assert list(snap["days"]) == ["2026-11-08"]                      # лише найновіший запис; старий (07.11) ігнорується
    day = snap["days"]["2026-11-08"]
    assert sorted(day["queues"]) == sorted(power.QUEUES) and len(power.QUEUES) == 12
    assert day["queues"]["1-І"] == [[0, 90], [1110, 1350]]
    assert day["queues"]["3-ІІ"] == [[270, 510]]
    assert day["queues"]["6-ІІ"] == [[900, 1140]]


def test_levels_from_ukrenergo_command_lines():
    levels = parse()["days"]["2026-11-08"]["levels"]
    assert [(lv["s"], lv["e"], lv["n"]) for lv in levels] == [(0, 519, 2), (519, 557, 1), (540, 1440, 0)]
    assert levels[2]["text"] == "без обмежень"


def test_zero_length_interval_is_skipped_with_a_warning():
    snap = parse()
    assert snap["days"]["2026-11-08"]["queues"]["2-І"] == [[1320, 1440]]   # "з 01:00 до 01:00" на сайті - очевидна помилка
    assert any("2-І" in w and "порожній" in w for w in snap["warnings"])
    assert snap["days"]["2026-11-08"]["queues"]["2-ІІ"] == [[60, 300], [1320, 1440]]


def test_notes_keep_the_feeder_warnings_but_drop_boilerplate():
    notes = parse()["notes"]
    assert any("Протягом доби" in n for n in notes) and any("Ф-6" in n for n in notes) and any("Ф-30" in n for n in notes)
    assert not any(n.strip("! ").upper() == "УВАГА" for n in notes)
    assert not any("Переглянути графік" in n for n in notes)


def test_latin_letters_in_subqueue_names_are_normalized():
    html = FIXTURE.replace("1-ІІ підчерга", "1-II підчерга").replace("3-І підчерга", "3-I підчерга")
    queues = parse(html)["days"]["2026-11-08"]["queues"]
    assert "1-ІІ" in queues and "3-І" in queues and len(queues) == 12


def test_two_forecast_days_in_one_announcement():
    html = FIXTURE.replace("07&nbsp;листопада 2026 20:24", """
Прогнозований графік погодинних відключень на 09.11.2026р.<br>
1 черга, 1-І підчерга - з 06:00 до 10:00;<br>
07 листопада 2026 20:24""")
    days = parse(html)["days"]
    assert sorted(days) == ["2026-11-08", "2026-11-09"] and days["2026-11-09"]["queues"]["1-І"] == [[360, 600]]


def test_bad_times_and_midnight_wrap_produce_warnings_not_errors():
    html = FIXTURE.replace("з 04:30 до 08:30;<br>\n3 черга, 3-ІІ", "з 25:00 до 26:00;<br>\n3 черга, 3-ІІ").replace("з 15:00 до 19:00;", "з 22:00 до 02:00;")
    snap = parse(html)
    q = snap["days"]["2026-11-08"]["queues"]
    assert q["3-І"] == [] and q["6-І"] == [[1320, 1440]]
    assert any("3-І" in w for w in snap["warnings"]) and any("через північ" in w for w in snap["warnings"])


def test_missing_block_means_layout_changed():
    with pytest.raises(power.ParseError):
        parse("<html><body><h1>Інша сторінка</h1></body></html>")


def test_block_without_schedule_is_not_an_error():
    html = "<h3>ОПЕРАТИВНА ІНФОРМАЦІЯ ПО ПОГОДИННИМ ВІДКЛЮЧЕННЯМ!</h3><table><tr><td>Графіки не застосовуються.<br>07 листопада 2026 20:24</td></tr></table><h2>Новини</h2>"
    snap = parse(html)
    assert snap["days"] == {} and snap["published"] == "2026-11-07T20:24+02:00"


# ---------- зміни ----------

def variant(old_text, new_text):
    return parse(FIXTURE.replace(old_text, new_text))


def test_diff_detects_changed_queue_levels_new_day_and_notes():
    base = parse()
    assert power.diff(None, base) is None and power.diff(base, parse()) is None
    d = power.diff(base, variant("3 черга, 3-І підчерга - з 04:30 до 08:30;", "3 черга, 3-І підчерга - з 05:00 до 09:00;"))
    assert d["days"] == {"2026-11-08": {"queues": ["3-І"], "levels": False}} and not d["new_days"]
    d = power.diff(base, variant("з 08:39 до 09:17 - 1 черга;", "з 08:39 до 10:00 - 1 черга;"))
    assert d["days"]["2026-11-08"] == {"queues": [], "levels": True}
    d = power.diff(base, variant("07&nbsp;листопада 2026 20:24", "Прогнозований графік погодинних відключень на 09.11.2026р.<br>1 черга, 1-І підчерга - з 06:00 до 10:00;<br>07 листопада 2026 20:24"))
    assert d["new_days"] == ["2026-11-09"]
    d = power.diff(base, variant("Введені нові черги відключень з 01.10.2026 року.", "Введені нові черги відключень з 01.12.2026 року."))
    assert d["notes"] is True and not d["days"]


def test_message_for_changed_queue_lists_only_that_group_and_escapes_html():
    base = parse()
    new = variant("3 черга, 3-І підчерга - з 04:30 до 08:30;", "3 черга, 3-І підчерга - з 05:00 до 09:00;")
    text = power.format_change(base, new, power.diff(base, new))
    assert "Зміни за чергами" in text and "3 черга (3-І): 05:00–09:00" in text
    assert "1 черга" not in text and "/app" in text and "07.11 о 20:24" in text and "(нд)" in text
    new["days"]["2026-11-08"]["levels"][0]["text"] = "<b>2 черги</b>"
    assert "&lt;b&gt;2 черги" in power.format_change(base, new, {"new_days": [], "days": {"2026-11-08": {"queues": [], "levels": True}}, "notes": False})


def test_message_for_new_day_shows_grouped_full_schedule():
    base = parse()
    new = variant("07&nbsp;листопада 2026 20:24", "Прогнозований графік погодинних відключень на 09.11.2026р.<br>1 черга, 1-І підчерга - з 06:00 до 10:00;<br>1 черга, 1-ІІ підчерга - з 06:00 до 10:00;<br>07 листопада 2026 20:24")
    text = power.format_change(base, new, power.diff(base, new))
    assert "новий графік" in text and "1 черга (1-І, 1-ІІ): 06:00–10:00" in text and "Графік за чергами" in text


# ---------- оновлення з мережі ----------

def run(coro):
    return asyncio.run(coro)


def test_refresh_first_run_is_silent_then_notifies_only_on_change(monkeypatch):
    pages = {"html": FIXTURE}

    async def fake_fetch(session, url=None):
        return pages["html"]

    sent = []

    async def notifier(old, new, d):
        sent.append(d)

    monkeypatch.setattr(power, "fetch_html", fake_fetch)
    monkeypatch.setattr(power, "NOTIFIER", notifier)
    r = run(power.run_once(None))
    assert r["first"] is True and sent == [] and power.load()["snapshot"]["days"]
    assert run(power.run_once(None))["diff"] is None and sent == []             # без змін - тиша
    pages["html"] = FIXTURE.replace("з 04:30 до 08:30;<br>\n3 черга, 3-ІІ", "з 05:00 до 09:00;<br>\n3 черга, 3-ІІ")
    r = run(power.run_once(None))
    assert len(sent) == 1 and sent[0]["days"]["2026-11-08"]["queues"] == ["3-І"]
    assert power.load()["snapshot"]["days"]["2026-11-08"]["queues"]["3-І"] == [[300, 540]]


def test_failures_are_tracked_and_recovery_resets(monkeypatch):
    async def broken(session, url=None):
        raise ConnectionError("сайт недоступний")

    async def good(session, url=None):
        return FIXTURE

    monkeypatch.setattr(power, "fetch_html", broken)
    with pytest.raises(ConnectionError):
        run(power.refresh(None))
    assert power.fail_count >= 1 and "недоступний" in power.last_error
    monkeypatch.setattr(power, "fetch_html", good)
    run(power.refresh(None))
    assert power.fail_count == 0 and power.last_error is None and power.stale_seconds() < 5
