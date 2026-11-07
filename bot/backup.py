"""Резервні копії даних бота: zip з усіх JSON-файлів DATA_DIR + безпечне відновлення з такого zip."""
import io
import json
import re
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import alert_log

KYIV_TZ = ZoneInfo("Europe/Kyiv")
DATA_DIR = alert_log.DATA_DIR
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.json$")
MAX_ZIP_BYTES = 5 * 1024 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024


def make_zip() -> tuple[bytes, str, list[str]]:
    """Повертає (байти zip, ім'я файлу, список файлів усередині)."""
    buf = io.BytesIO()
    names = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(DATA_DIR.glob("*.json")):
            if NAME_RE.match(path.name) and path.is_file():
                z.write(path, path.name)
                names.append(path.name)
    stamp = datetime.now(KYIV_TZ).strftime("%Y-%m-%d_%H%M")
    return buf.getvalue(), f"backup_{stamp}.zip", names


def _parse(payload: bytes) -> dict[str, bytes]:
    """Перевіряє архів і повертає {ім'я: вміст}. ValueError з кодом причини, якщо архів не підходить."""
    if len(payload) > MAX_ZIP_BYTES:
        raise ValueError("too_big")
    try:
        z = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile:
        raise ValueError("not_zip")
    files: dict[str, bytes] = {}
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if not infos:
            raise ValueError("empty")
        for info in infos:
            if not NAME_RE.match(info.filename):  # жодних шляхів, "..", не-json
                raise ValueError("bad_name")
            if info.file_size > MAX_FILE_BYTES:
                raise ValueError("too_big")
            raw = z.read(info)
            try:
                json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ValueError("bad_json")
            files[info.filename] = raw
    return files


def validate_zip(payload: bytes) -> list[str]:
    """Лише перевірка (нічого не записує). Повертає імена файлів в архіві."""
    return sorted(_parse(payload))


def restore_zip(payload: bytes) -> list[str]:
    """Відновлює файли з zip: усе спершу перевіряється, і лише потім записується (все або нічого)."""
    files = _parse(payload)
    for name, raw in files.items():
        tmp = DATA_DIR / (name + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(DATA_DIR / name)
    return sorted(files)
