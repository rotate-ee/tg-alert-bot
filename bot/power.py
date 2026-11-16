"""
Графіки відключень світла (ГПВ) з сайту ДПЕМ ПрАТ "Атомсервіс" (info.dpem.mk.ua).

Сторінка з таблицею графіка вантажиться скриптом, тому читаємо оперативний блок
"ОПЕРАТИВНА ІНФОРМАЦІЯ ПО ПОГОДИННИМ ВІДКЛЮЧЕННЯМ" на головній: це звичайний текст

    з 00:00 до 08:39 - 2 черги;                       <- режим обмежень за вказівкою Укренерго
    з 09:00 до окремої команди - без обмежень.
    Прогнозований графік погодинних відключень на 08.11.2026р.
    1 черга, 1-І підчерга - з 00:00 до 01:30, з 18:30 до 22:30;    <- графік підчерги

Результат (snapshot) - словник, що зберігається в power.json, віддається API і порівнюється
з попереднім, щоб надіслати повідомлення в чат при зміні графіка.
"""
import asyncio
import hashlib
import html as htmllib
import json
import logging
import os
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import config

log = logging.getLogger("power")
KYIV_TZ = ZoneInfo("Europe/Kyiv")
_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "power.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)

QUEUES = [f"{n}-{roman}" for roman in ("І", "ІІ") for n in range(1, 7)]  # 1-І..6-І, 1-ІІ..6-ІІ
MONTHS = {"січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6, "липня": 7,
          "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12}
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]

START_RE = re.compile(r"ОПЕРАТИВНА\s+ІНФОРМАЦІЯ\s+ПО\s+ПОГОДИННИМ\s+ВІДКЛЮЧЕННЯМ", re.I)
STOP_RE = re.compile(r"^\s*Новини\s*$", re.M)
TS_RE = re.compile(r"^(\d{1,2})\s+([А-Яа-яІіЇїЄєҐґ]+)\s+(\d{4})\s+(\d{1,2}):(\d{2})\s*$")
DATE_PART = r"(\d{1,2})\.(\d{1,2})\.(\d{4})"
APPLIED_HEAD_RE = re.compile(r"погодинних\s+відключень\s+на\s*:?\s*" + DATE_PART, re.I)
FORECAST_HEAD_RE = re.compile(r"Прогнозован\w*\s+графік\w*[^\n]*?\bна\s*:?\s*" + DATE_PART, re.I)
# Час набирають вручну: нуль буває літерою О/o, двокрапка - крапкою або схожим символом (∶ ：), "до" - з латинською "o".
_T = r"([0-9OОоo]{1,2})\s*[:.\u2236\uff1a\ua789]\s*([0-9OОоo]{2})"
LEVEL_RE = re.compile(r"^[зЗ]\s*" + _T + r"\s*(?:д[оo]|[пp][оo]|[-–—])\s*(?:" + _T + r"|окремої\s+команди)\s*[-–—]\s*(.+?)\s*[;.]?$", re.I)
FORECAST_RE = re.compile(r"^(\d)\s*черг\w*\s*,\s*(\d)\s*[-–—]\s*([IІ]{1,2}|[12])\s*підчерг\w*\s*[-–—]\s*(.*?)\s*[;.]?$", re.I)
INTERVAL_RE = re.compile(r"з\s*(\d{1,2}):(\d{2})\s*(?:до|по|[-–—])\s*(\d{1,2}):(\d{2})", re.I)
ATTENTION_ONLY_RE = re.compile(r"^\W*УВАГА\W*$", re.I)
VIEW_LINK_RE = re.compile(r"Переглянути\s+графік", re.I)

# Хід оновлення (для API й попередження адмінам)
last_ok: datetime | None = None
last_error: str | None = None
fail_count = 0
_started = datetime.now(KYIV_TZ)
NOTIFIER = None  # async def (old_snapshot, new_snapshot, diff) - ставить main.py
_lock = asyncio.Lock()


class ParseError(Exception):
    pass


# ---------- парсинг ----------

def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h[1-6]|table)>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = htmllib.unescape(raw).replace("\xa0", " ").replace("\u200b", "")
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _label(queue_no: str, roman: str) -> str | None:
    roman = roman.upper()
    count = roman.count("І") + roman.count("I") if roman not in ("1", "2") else int(roman)
    return f"{int(queue_no)}-{'І' * count}" if 1 <= count <= 2 else None


def _minutes(h: str, m: str) -> int | None:
    h, m = int(h.translate(HOMOGLYPH_ZERO)), int(m.translate(HOMOGLYPH_ZERO))
    if m > 59 or h > 24 or (h == 24 and m):
        return None
    return h * 60 + m


def _merge(intervals: list[list[int]]) -> list[list[int]]:
    out: list[list[int]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


# Час шукаємо як "ГГ:ХХ" (або "ГГ.ХХ"), а слова навколо ("з", "до", тире) ігноруємо: на сайті трапляються
# схожі символи (латинська "o" замість "о", цифра "3" замість "з", кириллична "О" в "00" тощо).
TIME_TOKEN_RE = re.compile(r"(?<![\d:.])" + _T + r"(?!\d)")
HOMOGLYPH_ZERO = str.maketrans({"O": "0", "О": "0", "o": "0", "о": "0"})
END_OF_DAY_RE = re.compile(r"кінця\s+(?:доби|дня)", re.I)
NO_OUTAGE_RE = re.compile(r"без\s+відключ|відсутн|не\s+передбач|немає|не\s+плану", re.I)
# Рядок підчерги з пошкодженими словами ("пiдчерга" з латинською i тощо): шукаємо "N-І ... - інтервали"
LOOSE_FORECAST_RE = re.compile(r"(?<!\d)([1-6])\s*[-–—]\s*(І{1,2}|I{1,2}|[12])(?![\wА-Яа-яІіЇї])[^-–—]*[-–—]\s*(.+)$", re.I)


def _intervals(text: str, warnings: list[str], label: str) -> list[list[int]]:
    tokens = []
    for m in TIME_TOKEN_RE.finditer(text):
        tokens.append((m.group(0), _minutes(m.group(1).translate(HOMOGLYPH_ZERO), m.group(2).translate(HOMOGLYPH_ZERO))))
    found = []
    for k in range(0, len(tokens), 2):
        raw_start, start = tokens[k]
        if k + 1 < len(tokens):
            raw_end, end = tokens[k + 1]
        else:  # "з 20:30 до кінця доби"
            raw_end, end = "кінця доби", (1440 if END_OF_DAY_RE.search(text) else None)
        shown = f"з {raw_start} до {raw_end}"
        if start is None or end is None:
            warnings.append(f"{label}: некоректний час «{shown}»")
        elif start == end:
            warnings.append(f"{label}: порожній інтервал «{shown}» (початок дорівнює кінцю), пропущено")
        elif end == 0:
            found.append([start, 1440])  # "до 00:00" - до кінця доби
        elif end < start:
            warnings.append(f"{label}: інтервал через північ «{shown}», враховано до 24:00")
            found.append([start, 1440])
        else:
            found.append([start, end])
    if not found and not warnings_for(label, warnings) and text.strip() and not NO_OUTAGE_RE.search(text):
        # нічого не розібрали і сайт не сказав "відключень немає": не губимо мовчки, а показуємо адміну символи
        warnings.append(f"{label}: не вдалося розібрати «{text.strip()[:60]}» ({ascii(text.strip()[:40])})")
    return _merge(found)


def warnings_for(label: str, warnings: list[str]) -> bool:
    return any(w.startswith(label + ":") for w in warnings)


def _iso(d: str, m: str, y: str) -> str:
    return date(int(y), int(m), int(d)).isoformat()


def parse_html(raw: str) -> dict:
    """Розбирає головну сторінку ДПЕМ. ParseError, якщо оперативного блока немає (змінилась верстка)."""
    text = html_to_text(raw)
    start = START_RE.search(text)
    if not start:
        raise ParseError("block_not_found")
    rest = text[start.end():]
    stop = STOP_RE.search(rest)
    lines = [ln.strip() for ln in (rest[:stop.start()] if stop else rest).split("\n") if ln.strip()]

    published = None
    for i, ln in enumerate(lines):  # перший запис - найновіший; його закінчує рядок із датою публікації
        m = TS_RE.match(ln)
        if m and m.group(2).lower() in MONTHS:
            published = datetime(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)),
                                 int(m.group(4)), int(m.group(5)), tzinfo=KYIV_TZ).isoformat(timespec="minutes")
            lines = lines[:i]
            break

    days: dict[str, dict] = {}
    warnings: list[str] = []
    intro: list[str] = []
    notes: list[str] = []
    applied_date = forecast_date = None
    seen_forecast = False

    def day(key: str) -> dict:
        return days.setdefault(key, {"levels": [], "queues": {}})

    for ln in lines:
        if not re.search(r"\w", ln):  # самотня пунктуація (напр., "!" після заголовка)
            continue
        m = FORECAST_HEAD_RE.search(ln)
        if m:
            forecast_date = _iso(*m.groups())
            day(forecast_date)
            continue
        m = APPLIED_HEAD_RE.search(ln)
        if m and not LEVEL_RE.match(ln):
            applied_date = _iso(*m.groups())
            day(applied_date)
            intro.append(ln)
            continue
        m = LEVEL_RE.match(ln)
        if m:
            target = applied_date or forecast_date
            if target is None:
                warnings.append(f"Режим без дати: «{ln[:60]}»")
                continue
            s = _minutes(m.group(1), m.group(2))
            e = _minutes(m.group(3), m.group(4)) if m.group(3) else 1440
            text = m.group(5).strip()
            count = re.match(r"(\d+)\s*черг", text, re.I)
            n = int(count.group(1)) if count else (0 if "без обмежень" in text.lower() else None)
            if s is None or e is None or e <= s:
                warnings.append(f"Режим: некоректний час у «{ln[:60]}»")
                continue
            day(target)["levels"].append({"s": s, "e": e, "n": n, "text": text})
            continue
        m = FORECAST_RE.match(ln)
        loose = None
        if not m and (forecast_date or applied_date) and TIME_TOKEN_RE.search(ln):
            loose = LOOSE_FORECAST_RE.search(ln)
        if (m or loose) and (forecast_date or applied_date):
            seen_forecast = True
            no, roman, tail = (m.group(2), m.group(3), m.group(4)) if m else (loose.group(1), loose.group(2), loose.group(3))
            label = _label(no, roman)
            if label is None:
                warnings.append(f"Невідома підчерга: «{ln[:50]}»")
                continue
            day(forecast_date or applied_date)["queues"][label] = _intervals(tail, warnings, label)
            continue
        if VIEW_LINK_RE.search(ln) or ATTENTION_ONLY_RE.match(ln):
            continue
        (notes if seen_forecast else intro).append(ln)

    return {"published": published, "days": days, "intro": intro[:6], "notes": notes[:40], "warnings": warnings[:20]}


# ---------- збереження, порівняння ----------

def schedule_signature(snapshot: dict) -> str:
    core = {d: {"levels": v["levels"], "queues": v["queues"]} for d, v in sorted(snapshot["days"].items())}
    return hashlib.sha1(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def notes_signature(snapshot: dict) -> str:
    return hashlib.sha1("\n".join(snapshot.get("notes", [])).encode()).hexdigest()


def diff(old: dict | None, new: dict) -> dict | None:
    """None - змін немає. Інакше: нові дні, зміни режиму/черг по днях, зміна приміток."""
    if old is None:
        return None
    out = {"new_days": [], "removed_days": [], "days": {}, "notes": False}
    for d, v in new["days"].items():
        if d not in old["days"]:
            out["new_days"].append(d)
            continue
        o = old["days"][d]
        changed_q = [q for q in QUEUES if v["queues"].get(q) != o["queues"].get(q)]
        levels = v["levels"] != o["levels"]
        if changed_q or levels:
            out["days"][d] = {"queues": changed_q, "levels": levels}
    out["removed_days"] = [d for d in old["days"] if d not in new["days"]]
    out["notes"] = notes_signature(old) != notes_signature(new)
    return out if (out["new_days"] or out["days"] or out["notes"]) else None


def load() -> dict | None:
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save(data: dict) -> None:
    tmp = DATA_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DATA_PATH)


def stale_seconds() -> int:
    """Скільки секунд минуло з останнього успішного читання сайту (або з запуску бота, якщо ще не було)."""
    ref = last_ok
    if ref is None:
        stored = load()
        ref = datetime.fromisoformat(stored["fetched"]) if stored and stored.get("fetched") else _started
    return max(0, int((datetime.now(KYIV_TZ) - ref).total_seconds()))


# ---------- форматування повідомлення в чат ----------

def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _group_lines(queues: dict, only: list[str] | None = None) -> list[str]:
    """Об'єднує підчерги з однаковим графіком: '1 черга (1-І, 1-ІІ): 00:00–01:30, 18:30–22:30'."""
    groups: dict[tuple, list[str]] = {}
    for q in QUEUES:
        if q in queues and (only is None or q in only):
            groups.setdefault((q.split("-")[0], json.dumps(queues[q])), []).append(q)
    lines = []
    for (no, _), labels in groups.items():
        ivs = queues[labels[0]]
        text = ", ".join(f"{_hhmm(s)}–{_hhmm(e)}" for s, e in ivs) or "без відключень"
        lines.append(f"{'🔴' if ivs else '🟢'} {no} черга ({', '.join(labels)}): {text}")
    return lines


def _level_text(lv: dict) -> str:
    end = "окремої команди" if lv["e"] >= 1440 else _hhmm(lv["e"])
    return f"з {_hhmm(lv['s'])} до {end} — {lv['text']}"


def _cancel_note(old_day: dict | None, new_day: dict) -> str | None:
    """Окремий рядок, коли обмеження скасували ("без обмежень") або знову ввели."""
    def free_from(day):
        starts = [lv["s"] for lv in (day or {}).get("levels", []) if lv["n"] == 0]
        return min(starts) if starts else None
    was, now = free_from(old_day), free_from(new_day)
    if now is not None and was is None:
        whole = any(lv["n"] == 0 and lv["s"] == 0 and lv["e"] >= 1440 for lv in new_day["levels"])
        when = "На весь день" if whole else f"З {_hhmm(now)}"
        return f"✅ {when} обмежень немає: відключення за графіком не застосовуються (графік лишається на випадок їх повернення)"
    if was is not None and now is None:
        return "⚠️ Обмеження знову застосовуються: діє графік нижче"
    return None


def format_change(old: dict | None, new: dict, d: dict) -> str | None:
    """HTML-повідомлення для чату про зміну графіка (None - нічого розповідати)."""
    from html import escape
    blocks = []
    dates = sorted(set(d["new_days"]) | set(d["days"]))[:2]
    for key in dates:
        day = new["days"][key]
        wd = WEEKDAYS[date.fromisoformat(key).weekday()]
        title = f"<b>На {key[8:10]}.{key[5:7]} ({wd})</b>" + (" · <i>новий графік</i>" if key in d["new_days"] else "")
        lines = [title]
        info = d["days"].get(key, {"queues": [], "levels": True})
        note = _cancel_note((old or {}).get("days", {}).get(key), day) if (key in d["new_days"] or info["levels"]) else None
        if note:
            lines.append(escape(note))
        if day["levels"] and (key in d["new_days"] or info["levels"]):
            lines += ["<u>Режим обмежень</u>"] + [escape(_level_text(lv)) for lv in day["levels"]]
        only = None if key in d["new_days"] else info["queues"]
        qlines = _group_lines(day["queues"], only)
        if qlines:
            lines += ["<u>Зміни за чергами</u>" if only is not None else "<u>Графік за чергами</u>"] + [escape(x) for x in qlines]
        blocks.append("\n".join(lines))
    if not blocks and d["notes"] and new.get("notes"):
        blocks.append("<b>Оновлено примітки</b>\n" + "\n".join(escape(x) for x in new["notes"][:6]))
    if not blocks:
        return None
    pub = ""
    if new.get("published"):
        p = datetime.fromisoformat(new["published"])
        pub = f"\n<i>Опубліковано на сайті ДПЕМ {p:%d.%m о %H:%M}</i>"
    text = "⚡️ <b>Графік відключень світла оновлено</b>" + pub + "\n\n" + "\n\n".join(blocks)
    text += "\n\nСвоя черга та графіки: /app → «Світло». Протягом доби графік може змінюватись."
    return text[:3900]


# ---------- завантаження і оновлення ----------

async def fetch_html(session, url: str | None = None) -> str:
    import aiohttp
    async with session.get(url or config.POWER_URL, timeout=aiohttp.ClientTimeout(total=20),
                           headers={"User-Agent": "Mozilla/5.0 (compatible; school-class-bot)"}) as resp:
        resp.raise_for_status()
        return (await resp.read()).decode(resp.charset or "utf-8", errors="replace")


async def refresh(session) -> dict:
    """Читає сайт, оновлює power.json. Повертає {"snapshot","diff","first","previous"}.
    Кидає виняток при мережевій/парсингу помилці (стан збою ведеться тут)."""
    global last_ok, last_error, fail_count
    async with _lock:
        try:
            snapshot = parse_html(await fetch_html(session))
        except Exception as e:
            fail_count += 1
            last_error = f"{type(e).__name__}: {e}"[:200]
            raise
        stored = load()
        previous = stored["snapshot"] if stored else None
        now = datetime.now(KYIV_TZ)
        change = diff(previous, snapshot)
        _save({"snapshot": snapshot, "fetched": now.isoformat(timespec="seconds"),
               "sig": schedule_signature(snapshot)})
        last_ok, last_error, fail_count = now, None, 0
        return {"snapshot": snapshot, "previous": previous, "diff": change, "first": previous is None}


async def run_once(session) -> dict:
    """refresh + сповіщення (якщо задано NOTIFIER). Перший прочитаний графік не сповіщається."""
    result = await refresh(session)
    if result["diff"] and NOTIFIER:
        try:
            await NOTIFIER(result["previous"], result["snapshot"], result["diff"])
        except Exception:
            log.exception("Не вдалося повідомити про зміну графіка світла")
    return result
