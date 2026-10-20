"""Persistent IDs for alert status messages and messages waiting for deletion."""
import json
import os
from pathlib import Path

_data_dir = os.getenv("DATA_DIR")
DATA_DIR = Path(_data_dir) if _data_dir else Path(__file__).parent / "data"
DATA_PATH = DATA_DIR / "alert_notifications.json"
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}

    active = data.get("active_message_id")
    if not isinstance(active, int) or isinstance(active, bool):
        active = None
    pending = data.get("pending_delete_ids", [])
    if not isinstance(pending, list):
        pending = []
    pending = list(dict.fromkeys(
        value for value in pending
        if isinstance(value, int) and not isinstance(value, bool) and value != active
    ))
    return {"active_message_id": active, "pending_delete_ids": pending}


def _save(data: dict) -> None:
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f)


def replace_active(message_id: int) -> None:
    """Remember the new status message and queue the previous one for deletion."""
    data = _load()
    previous = data["active_message_id"]
    if previous is not None and previous != message_id:
        data["pending_delete_ids"] = list(dict.fromkeys([*data["pending_delete_ids"], previous]))
    data["active_message_id"] = message_id
    _save(data)


def pending_deletions() -> list[int]:
    return _load()["pending_delete_ids"]


def mark_deleted(message_id: int) -> None:
    data = _load()
    data["pending_delete_ids"] = [
        pending_id for pending_id in data["pending_delete_ids"] if pending_id != message_id
    ]
    _save(data)
