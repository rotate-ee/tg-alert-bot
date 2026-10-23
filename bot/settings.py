"""Persistent settings managed by admins from the mini app."""
import json
import os
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "settings.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
# schedule_end: остання дата дії розкладу (випуск), "" - без обмеження
DEFAULTS = {"alerts_muted": False, "maintenance": False, "app_mode": "open", "maintenance_message": "", "ticker_text": "", "schedule_end": "2027-08-31", "remote_lesson_links": {}}


def load() -> dict:
    data = dict(DEFAULTS)
    stored = {}
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            stored = json.load(f)
            data.update(stored)
    except (OSError, ValueError):
        pass
    # Migrate installations that only stored the old maintenance checkbox.
    if "app_mode" not in stored:
        data["app_mode"] = "maintenance" if data.get("maintenance") else "open"
    if data.get("app_mode") not in {"open", "maintenance", "stopped"}:
        data["app_mode"] = "open"
    data["maintenance"] = data["app_mode"] == "maintenance"
    return data


def get(key: str):
    return load().get(key, DEFAULTS.get(key))


def set_value(key: str, value) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    data = load()
    if key == "app_mode":
        value = str(value)
        if value not in {"open", "maintenance", "stopped"}:
            raise ValueError("app_mode")
        data["maintenance"] = value == "maintenance"
    elif key == "maintenance":
        value = bool(value)
        data["app_mode"] = "maintenance" if value else "open"
    elif key == "maintenance_message":
        value = str(value).strip()[:300]
    elif key == "ticker_text":
        value = str(value).strip()[:500]
    if key != "maintenance":
        data[key] = type(DEFAULTS[key])(value)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
