"""Jarvis'in elleri: bilgisayarda uygulama, klasör, dosya ve web sitesi açar.

Güvenlik için Jarvis sadece AÇABİLİR: hiçbir şeyi silemez, değiştiremez
ve program/komut dosyası çalıştıramaz.
"""

import difflib
import json
import os
import subprocess
import threading
import time
import webbrowser
import winreg
from pathlib import Path

HOME = Path.home()

# Jarvis'in açmayı reddedeceği dosya türleri (programlar ve komut dosyaları)
BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf",
    ".wsh", ".msi", ".msp", ".scr", ".lnk", ".reg", ".hta", ".cpl", ".jar", ".py", ".pyw",
}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "AppData"}
SEARCH_DEPTH = 4
MAX_RESULTS = 15

TOOLS = [
    {
        "name": "open_app",
        "description": "Bilgisayarda yüklü bir uygulamayı açar (Spotify, Chrome, Hesap Makinesi gibi). "
                       "Uygulamanın adını Yusuf'un söylediği gibi ver.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Uygulamanın adı"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_folder",
        "description": "Bir klasörü Dosya Gezgini'nde açar. Bilinen klasörler: masaüstü, indirilenler, "
                       "belgeler, resimler, müzik, videolar, kullanıcı klasörü. Ya da tam klasör yolu ver.",
        "input_schema": {
            "type": "object",
            "properties": {"folder": {"type": "string", "description": "Klasör adı ya da tam yolu"}},
            "required": ["folder"],
            "additionalProperties": False,
        },
    },
    {
        "name": "find_files",
        "description": "Masaüstü, İndirilenler ve Belgeler'de adında verilen kelimeler geçen dosya ve "
                       "klasörleri arar, tam yollarını döndürür. Bir dosyayı açmadan önce yolunu bulmak için kullan.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Dosya adında geçen kelime(ler)"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_file",
        "description": "Bir dosyayı varsayılan programıyla açar (PDF, Word, resim, video...). "
                       "Tam yol gerekir; bilmiyorsan önce find_files kullan. Programlar ve komut dosyaları açılamaz.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Dosyanın tam yolu"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_website",
        "description": "Bir web sitesini tarayıcıda açar. Arama için arama adresini kendin oluştur, "
                       "örneğin https://www.youtube.com/results?search_query=... ya da "
                       "https://www.google.com/search?q=...",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "http:// ya da https:// ile başlayan adres"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
]
for _tool in TOOLS:
    _tool["strict"] = True
    _tool["eager_input_streaming"] = True


def normalize(text):
    """Türkçe harfleri sadeleştirip küçültür: 'Hesap Makinesi' -> 'hesap makinesi'."""
    table = str.maketrans("çğıİöşüÇĞÖŞÜâîû", "cgiiosucgosuaiu")
    return text.translate(table).casefold().strip()


# ---------- Uygulamalar ----------

_apps = None
_apps_lock = threading.Lock()


def load_apps():
    """Başlat menüsündeki uygulamaların listesini alır (ilk seferde ~2 saniye)."""
    global _apps
    with _apps_lock:
        if not _apps:
            output = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                 "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout
            _apps = {app["Name"]: app["AppID"] for app in json.loads(output or "[]")}
    return _apps


def open_app(name):
    apps = load_apps()
    wanted = normalize(name)
    by_norm = {normalize(n): n for n in apps}

    match = by_norm.get(wanted)
    if not match:
        containing = sorted((n for n in by_norm if wanted in n), key=len)
        if containing:
            match = by_norm[containing[0]]
    if not match:
        close = difflib.get_close_matches(wanted, by_norm, n=1, cutoff=0.6)
        if close:
            match = by_norm[close[0]]
    if not match:
        suggestions = difflib.get_close_matches(wanted, by_norm, n=8, cutoff=0.3)
        return False, (f"'{name}' adında bir uygulama bulunamadı. Benzerleri: "
                       + ", ".join(by_norm[s] for s in suggestions) if suggestions
                       else f"'{name}' adında bir uygulama bulunamadı.")

    os.startfile(f"shell:AppsFolder\\{apps[match]}")
    return True, f"{match} açıldı."


# ---------- Klasörler ----------

def known_folders():
    """Windows'un Masaüstü, İndirilenler gibi klasörlerinin gerçek yerlerini bulur."""
    names = {
        "masaustu": "Desktop",
        "belgeler": "Personal",
        "indirilenler": "{374DE290-123F-4565-9164-39C4925E467B}",
        "resimler": "My Pictures",
        "muzik": "My Music",
        "videolar": "My Video",
    }
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    folders = {"kullanici klasoru": HOME}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        for turkish, value_name in names.items():
            try:
                folders[turkish] = Path(os.path.expandvars(winreg.QueryValueEx(key, value_name)[0]))
            except OSError:
                pass
    return folders


def open_folder(folder):
    folders = known_folders()
    wanted = normalize(folder)
    for name, path in folders.items():
        if name in wanted or wanted in name:
            os.startfile(path)
            return True, f"{path} açıldı."

    path = Path(folder).expanduser()
    if path.is_dir():
        os.startfile(path)
        return True, f"{path} açıldı."
    return False, f"'{folder}' klasörü bulunamadı. Bilinen klasörler: " + ", ".join(folders)


# ---------- Dosyalar ----------

def find_files(query):
    words = normalize(query).split()
    if not words:
        return False, "Aranacak kelime boş."
    folders = known_folders()
    roots = [folders[k] for k in ("masaustu", "indirilenler", "belgeler") if k in folders]
    results = []
    deadline = time.time() + 4

    for root in roots:
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() > deadline or len(results) >= MAX_RESULTS:
                break
            depth = len(Path(dirpath).parts) - base_depth
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith(".") and depth < SEARCH_DEPTH]
            for name in dirnames + filenames:
                if all(w in normalize(name) for w in words):
                    results.append(str(Path(dirpath) / name))

    if not results:
        return False, f"Adında '{query}' geçen dosya bulunamadı (Masaüstü, İndirilenler, Belgeler)."
    return True, "\n".join(results[:MAX_RESULTS])


def open_file(path):
    file = Path(path).expanduser()
    if not file.is_file():
        return False, f"Dosya bulunamadı: {path}"
    if file.suffix.lower() in BLOCKED_EXTENSIONS:
        return False, f"Güvenlik için {file.suffix} dosyaları açılamıyor (programlar ve komut dosyaları)."
    os.startfile(file)
    return True, f"{file.name} açıldı."


# ---------- Web siteleri ----------

def open_website(url):
    if not url.startswith(("http://", "https://")):
        return False, "Adres http:// ya da https:// ile başlamalı."
    webbrowser.open(url)
    return True, f"{url} tarayıcıda açıldı."


# ---------- Claude'un araç çağrılarını çalıştırma ----------

HANDLERS = {
    "open_app": (open_app, "name"),
    "open_folder": (open_folder, "folder"),
    "find_files": (find_files, "query"),
    "open_file": (open_file, "path"),
    "open_website": (open_website, "url"),
}


def run_tool(name, tool_input):
    """Claude'un istediği aracı çalıştırır; (başarılı_mı, sonuç_yazısı) döndürür."""
    if name not in HANDLERS:
        return False, f"Bilinmeyen araç: {name}"
    handler, field = HANDLERS[name]
    # Girdi yarım ya da bozuk geldiyse çalıştırma, Claude'a tekrar denemesini söyle
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get(field), str) \
            or not tool_input[field].strip():
        return False, f"Geçersiz girdi: '{field}' alanı gerekli."
    try:
        return handler(tool_input[field].strip())
    except Exception as e:
        return False, f"Hata: {type(e).__name__}: {e}"
