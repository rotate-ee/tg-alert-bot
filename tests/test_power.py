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


# ---------- схожі символи та різні записи часу (регресія: «5-І: не вдалося розібрати «з 20:30 до 24:00»») ----------

@pytest.mark.parametrize("text", [
    "з 20:30 до 24:00",
    "з 20:30 дo 24:00",          # латинська o в «до»
    "3 20:30 до 24:00",          # цифра 3 замість «з»
    "з 20.30 до 24.00",          # крапка замість двокрапки
    "з 20:30 24:00",             # без слова «до»
    "з 20:30 до 24:ОО",          # кириллична О замість нуля
    "з 20:30 до 00:00",          # північ = кінець доби
    "з 20:30 до кінця доби",
    "з 20 : 30  до  24 : 00",
])
def test_end_of_day_interval_is_understood_in_any_spelling(text):
    warnings = []
    assert power._intervals(text, warnings, "5-І") == [[1230, 1440]]
    assert not any("не вдалося" in w for w in warnings)


def test_midnight_end_is_not_reported_as_a_problem():
    warnings = []
    assert power._intervals("з 20:30 до 00:00", warnings, "5-І") == [[1230, 1440]] and warnings == []


def test_several_intervals_with_mixed_spellings():
    assert power._intervals("з 00:00 дo 01:30, 3 18.30 до 22:30;", [], "1-І") == [[0, 90], [1110, 1350]]


def test_unparseable_text_is_reported_with_exact_characters_not_lost_silently():
    warnings = []
    assert power._intervals("з вечора до ранку", warnings, "4-І") == []
    assert len(warnings) == 1 and "не вдалося розібрати" in warnings[0] and "\\u0432" in warnings[0]   # видно коди символів
    assert power._intervals("відключень не передбачено", [], "4-І") == []                                # це не помилка


def test_queue_line_with_corrupted_words_is_still_recognized():
    html = FIXTURE.replace("5 черга, 5-І підчерга - з 11:30 до 15:30;", "5 черга, 5-І пiдчерга - з 20:30 дo 24:00;")   # латинська i та o
    snap = parse(html)
    assert snap["days"]["2026-11-08"]["queues"]["5-І"] == [[1230, 1440]]
    assert not any("5-І" in w for w in snap["warnings"])


def test_real_sample_unchanged_after_parser_rewrite():
    q = parse()["days"]["2026-11-08"]["queues"]
    assert q["1-І"] == [[0, 90], [1110, 1350]] and q["2-ІІ"] == [[60, 300], [1320, 1440]] and q["2-І"] == [[1320, 1440]]


# ---------- сайт набирають вручну: "схожі" символи не повинні ламати розбір ----------

@pytest.mark.parametrize("variant", [
    "з 20:30 до 24:00",
    "з 2О:30 до 24:00",          # кирилична "О" замість нуля
    "з 20:30 до 24:OO",          # латинські "OO" замість нулів
    "з 20:30 дo 24:00",          # латинська "o" в слові "до"
    "з 20.30 до 24.00",          # крапка замість двокрапки
    "з 20\u223630 до 24\u223600",  # "∶" (знак відношення) замість ":"
    "з 20：30 до 24：00",         # повноширинна двокрапка
    "з\u00a020:30\u00a0до\u00a024:00",
    "З 20:30 ДО 24:00",
])
def test_end_of_day_interval_survives_lookalike_characters(variant):
    html = FIXTURE.replace("5 черга, 5-І підчерга - з 11:30 до 15:30;", f"5 черга, 5-І підчерга - {variant};")
    snap = parse(html)
    assert snap["days"]["2026-11-08"]["queues"]["5-І"] == [[1230, 1440]]       # "до кінця доби"
    assert not any("5-І" in w for w in snap["warnings"])


def test_levels_also_tolerate_lookalikes():
    html = FIXTURE.replace("з 00:00 до 08:39 - 2 черги;", "з 0О:00 дo 08.39 - 2 черги;")
    assert parse(html)["days"]["2026-11-08"]["levels"][0]["e"] == 519


def test_unparseable_text_is_reported_with_character_codes():
    html = FIXTURE.replace("5 черга, 5-І підчерга - з 11:30 до 15:30;", "5 черга, 5-І підчерга - з вечора до ранку\u2026;")
    warnings = [w for w in parse(html)["warnings"] if "5-І" in w]
    assert warnings and "не вдалося розібрати" in warnings[0] and "\\u2026" in warnings[0]   # адмін бачить код дивного символу


def test_no_outage_wording_is_not_a_warning():
    html = FIXTURE.replace("5 черга, 5-І підчерга - з 11:30 до 15:30;", "5 черга, 5-І підчерга - відключень немає;")
    snap = parse(html)
    assert snap["days"]["2026-11-08"]["queues"]["5-І"] == [] and not any("5-І" in w for w in snap["warnings"])


# ---------- скасування обмежень ----------

LEVELS_BLOCK = "з 00:00 до 08:39 - 2 черги;<br>\nз 08:39 до 09:17 - 1 черга;<br>\nз 09:00 до окремої команди - без обмежень.<br>"


def with_levels(text):
    assert LEVELS_BLOCK in FIXTURE
    return parse(FIXTURE.replace(LEVELS_BLOCK, text))


def test_message_says_when_restrictions_are_cancelled_or_return():
    active = with_levels("з 00:00 до окремої команди - 2 черги.<br>")
    whole_day = with_levels("з 00:00 до окремої команди - без обмежень.<br>")
    from_nine = with_levels("з 00:00 до 09:00 - 2 черги;<br>\nз 09:00 до окремої команди - без обмежень.<br>")
    text = power.format_change(active, whole_day, power.diff(active, whole_day))
    assert "На весь день обмежень немає" in text and "графік лишається" in text
    text = power.format_change(active, from_nine, power.diff(active, from_nine))
    assert "З 09:00 обмежень немає" in text
    text = power.format_change(whole_day, active, power.diff(whole_day, active))
    assert "знову застосовуються" in text


def test_cancel_note_is_not_repeated_when_only_a_queue_changes():
    whole_day = with_levels("з 00:00 до окремої команди - без обмежень.<br>")
    changed = parse(FIXTURE.replace(LEVELS_BLOCK, "з 00:00 до окремої команди - без обмежень.<br>").replace("з 04:30 до 08:30;<br>\n3 черга, 3-ІІ", "з 05:00 до 09:00;<br>\n3 черга, 3-ІІ"))
    text = power.format_change(whole_day, changed, power.diff(whole_day, changed))
    assert "3 черга (3-І)" in text and "обмежень немає" not in text


def test_new_day_that_is_already_cancelled_mentions_it():
    base = parse()
    new = parse(FIXTURE.replace("07&nbsp;листопада 2026 20:24", "Графіки погодинних відключень на: 09.11.2026<br>з 00:00 до окремої команди - без обмежень.<br>Прогнозований графік погодинних відключень на 09.11.2026р.<br>1 черга, 1-І підчерга - з 06:00 до 10:00;<br>07 листопада 2026 20:24"))
    text = power.format_change(base, new, power.diff(base, new))
    assert "На весь день обмежень немає" in text and "новий графік" in text
