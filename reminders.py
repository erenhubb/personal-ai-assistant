"""Jarvis'in ajandası: hatırlatmaları kaydeder, zamanı gelince haber verir."""

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

FILE = Path(__file__).parent / "hatirlatmalar.json"
TIME_FORMAT = "%Y-%m-%d %H:%M"
CHECK_SECONDS = 20
LATE_MINUTES = 5  # Bundan daha geç kalındıysa "kaçırdığın hatırlatma" denir
REPEATS = {"none": None, "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}

TOOLS = [
    {
        "name": "add_reminder",
        "description": "Bir hatırlatma kurar. Zamanı Eren'in mesajındaki şu anki tarih ve saate göre hesapla. "
                       "Her hafta tekrar eden şeyler (ör. ders) için repeat=weekly, her gün için daily kullan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Hatırlatılacak şey, ör. 'Dişçi randevusu'"},
                "when": {"type": "string", "description": "Ne zaman, 'YYYY-MM-DD HH:MM' biçiminde"},
                "repeat": {"type": "string", "enum": ["none", "daily", "weekly"]},
            },
            "required": ["text", "when", "repeat"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_reminders",
        "description": "Kurulu bütün hatırlatmaları numaralarıyla listeler.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "delete_reminder",
        "description": "Numarası verilen hatırlatmayı siler. Numarayı bilmiyorsan önce list_reminders kullan.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "Hatırlatmanın numarası"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
]
for _tool in TOOLS:
    _tool["strict"] = True
    _tool["eager_input_streaming"] = True

_lock = threading.Lock()


def _load():
    if FILE.exists():
        return json.loads(FILE.read_text(encoding="utf-8"))
    return []


def _save(items):
    FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def add(text, when, repeat):
    try:
        moment = datetime.strptime(when, TIME_FORMAT)
    except ValueError:
        return False, "Zaman 'YYYY-MM-DD HH:MM' biçiminde olmalı."
    if moment <= datetime.now() and repeat == "none":
        return False, "Bu zaman geçmişte kalıyor."
    with _lock:
        items = _load()
        new_id = max((r["id"] for r in items), default=0) + 1
        items.append({"id": new_id, "text": text, "when": moment.strftime(TIME_FORMAT), "repeat": repeat})
        _save(items)
    repeat_text = {"none": "", "daily": " (her gün)", "weekly": " (her hafta)"}[repeat]
    return True, f"{new_id} numaralı hatırlatma kuruldu: {text}, {moment:%d.%m.%Y %H:%M}{repeat_text}."


def list_all():
    with _lock:
        items = sorted(_load(), key=lambda r: r["when"])
    if not items:
        return True, "Hiç hatırlatma yok."
    lines = [f"{r['id']}. {r['when']} - {r['text']}"
             + ("" if r["repeat"] == "none" else f" ({'her gün' if r['repeat'] == 'daily' else 'her hafta'})")
             for r in items]
    return True, "\n".join(lines)


def delete(reminder_id):
    with _lock:
        items = _load()
        kept = [r for r in items if r["id"] != reminder_id]
        if len(kept) == len(items):
            return False, f"{reminder_id} numaralı hatırlatma yok."
        _save(kept)
    return True, f"{reminder_id} numaralı hatırlatma silindi."


def pop_due():
    """Zamanı gelen hatırlatmaları döndürür; tekrarlıları bir sonraki zamana kurar, diğerlerini siler."""
    now = datetime.now()
    due = []
    with _lock:
        items = _load()
        kept = []
        for r in items:
            moment = datetime.strptime(r["when"], TIME_FORMAT)
            if moment > now:
                kept.append(r)
                continue
            late = now - moment > timedelta(minutes=LATE_MINUTES)
            due.append({**r, "late": late})
            step = REPEATS[r["repeat"]]
            if step:
                while moment <= now:
                    moment += step
                kept.append({**r, "when": moment.strftime(TIME_FORMAT)})
        if due:
            _save(kept)
    return due


def start_watcher(announce):
    """Arka planda zamanı gelen hatırlatmaları kontrol eder ve announce(hatırlatma) çağırır."""
    def loop():
        while True:
            for reminder in pop_due():
                try:
                    announce(reminder)
                except Exception as e:
                    print(f"(Hatırlatma söylenemedi: {e})")
            time.sleep(CHECK_SECONDS)

    threading.Thread(target=loop, daemon=True).start()


def run_tool(name, tool_input):
    """Claude'un istediği hatırlatma aracını çalıştırır; (başarılı_mı, sonuç_yazısı) döndürür."""
    if not isinstance(tool_input, dict):
        return False, "Geçersiz girdi."
    if name == "add_reminder":
        text, when, repeat = tool_input.get("text"), tool_input.get("when"), tool_input.get("repeat")
        if not isinstance(text, str) or not text.strip() or not isinstance(when, str) or repeat not in REPEATS:
            return False, "Geçersiz girdi: 'text', 'when' ve 'repeat' gerekli."
        return add(text.strip(), when.strip(), repeat)
    if name == "list_reminders":
        return list_all()
    if name == "delete_reminder":
        reminder_id = tool_input.get("id")
        if not isinstance(reminder_id, int):
            return False, "Geçersiz girdi: 'id' gerekli."
        return delete(reminder_id)
    return False, f"Bilinmeyen araç: {name}"
