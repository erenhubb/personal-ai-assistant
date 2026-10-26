"""Jarvis'in dünyaya açılan penceresi: hava durumu ve haber başlıkları (ücretsiz kaynaklar)."""

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# Open-Meteo: ücretsiz, anahtar gerektirmeyen hava durumu servisi
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
NEWS_FEEDS = [
    "https://www.ntv.com.tr/gundem.rss",
    "https://feeds.bbci.co.uk/turkce/rss.xml",
]

TOOLS = [
    {
        "name": "get_weather",
        "description": "Bir şehrin şu anki hava durumunu ve önümüzdeki günlerin tahminini getirir.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Şehir adı, ör. 'İstanbul'"},
                "days": {"type": "integer", "description": "Kaç günlük tahmin (1-7)"},
            },
            "required": ["city", "days"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_news",
        "description": "Türkiye ve dünyadan güncel haber başlıklarını getirir.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
for _tool in TOOLS:
    _tool["strict"] = True
    _tool["eager_input_streaming"] = True

# Hava durumu kodlarının Türkçe karşılıkları (WMO kodları)
WEATHER_CODES = [
    ((0,), "açık"), ((1, 2), "parçalı bulutlu"), ((3,), "kapalı"), ((45, 48), "sisli"),
    ((51, 53, 55, 56, 57), "çiseleyen yağmurlu"), ((61, 63, 65, 66, 67), "yağmurlu"),
    ((71, 73, 75, 77), "karlı"), ((80, 81, 82), "sağanak yağışlı"), ((85, 86), "kar sağanaklı"),
    ((95, 96, 99), "gök gürültülü fırtınalı"),
]


def describe(code):
    return next((text for codes, text in WEATHER_CODES if code in codes), "bilinmeyen")


def fetch_json(url, params):
    with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=10) as response:
        return json.load(response)


def weather(city, days):
    places = fetch_json(GEOCODE_URL, {"name": city, "count": 1, "language": "tr"}).get("results")
    if not places:
        return False, f"'{city}' diye bir şehir bulunamadı."
    place = places[0]
    data = fetch_json(FORECAST_URL, {
        "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto",
        "forecast_days": max(1, min(7, days)),
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
    })
    now = data["current"]
    lines = [f"{place['name']} şu an: {now['temperature_2m']:.0f}°C, {describe(now['weather_code'])}."]
    daily = data["daily"]
    for i, day in enumerate(daily["time"]):
        lines.append(f"{day}: {describe(daily['weather_code'][i])}, "
                     f"en düşük {daily['temperature_2m_min'][i]:.0f}°C, en yüksek {daily['temperature_2m_max'][i]:.0f}°C, "
                     f"yağış ihtimali %{daily['precipitation_probability_max'][i] or 0}")
    return True, "\n".join(lines)


def news():
    titles = []
    for url in NEWS_FEEDS:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=10) as response:
                root = ET.fromstring(response.read())
            # RSS'te haberler <item>, Atom'da <entry> içinde olur
            entries = [e for e in root.iter() if e.tag.split("}")[-1] in ("item", "entry")]
            for entry in entries[:6]:
                title = next((c.text for c in entry if c.tag.split("}")[-1] == "title"), "")
                titles.append((title or "").strip())
        except Exception:
            continue
    titles = [t for t in titles if t]
    if not titles:
        return False, "Haberlere şu an ulaşılamadı."
    return True, "\n".join(f"- {t}" for t in titles)


def run_tool(name, tool_input):
    """Claude'un istediği bilgi aracını çalıştırır; (başarılı_mı, sonuç_yazısı) döndürür."""
    try:
        if name == "get_weather":
            city, days = tool_input.get("city"), tool_input.get("days")
            if not isinstance(city, str) or not city.strip() or not isinstance(days, int):
                return False, "Geçersiz girdi: 'city' ve 'days' gerekli."
            return weather(city.strip(), days)
        if name == "get_news":
            return news()
        return False, f"Bilinmeyen araç: {name}"
    except Exception as e:
        return False, f"Bilgi alınamadı: {type(e).__name__}"
