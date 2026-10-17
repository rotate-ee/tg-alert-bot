"""Налаштування, які адмін змінює з міні-застосунку без перезапуску: {"alerts_muted": bool}."""
import json
import os
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "settings.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
DEFAULTS = {"alerts_muted": False}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data.update(json.load(f))
    except (OSError, ValueError):
        pass
    return data


def get(key: str):
    return load().get(key, DEFAULTS.get(key))


def set_value(key: str, value) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    data = load()
    data[key] = type(DEFAULTS[key])(value)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
