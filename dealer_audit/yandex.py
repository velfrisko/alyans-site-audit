"""Видимость в Яндексе через Yandex Cloud Search API (v2, синхронный web search).

Нужны переменные окружения YANDEX_SEARCH_API_KEY и YANDEX_FOLDER_ID (сервисный аккаунт с ролью search-api.webSearch.user).
Без ключа модуль возвращает status=not_configured, и отчёт помечает блок «не проверено» (без выдумывания позиций).
"""
from __future__ import annotations
import base64, os
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
import httpx

REGIONS = {"Чебоксары": 45, "Йошкар-Ола": 41, "Казань": 43, "Новочебоксарск": 45}
ENDPOINT = "https://searchapi.api.cloud.yandex.net/v2/web/search"


def default_queries(site: dict) -> list[str]:
    city = site.get("city", "Чебоксары")
    city_l = {"Чебоксары": "чебоксары", "Йошкар-Ола": "йошкар-ола"}.get(city, city.lower())
    if site.get("keywords"):
        return site["keywords"]
    brand = site.get("brand", "")
    if site.get("type") == "import":
        return [f"авто из сша {city_l}", f"пригнать авто из сша {city_l}", "купить bmw x5 из сша", f"авто под заказ {city_l}"]
    if site.get("type") == "used":
        return [f"авто с пробегом {city_l}", f"купить бу авто {city_l}", f"трейд ин {city_l}"]
    return [f"{brand} {city_l}", f"купить {brand} {city_l}", f"{brand} официальный дилер {city_l}", f"{brand} цена {city_l}"]


def _positions(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    return [u.text for u in root.iter("url") if u.text]


def check(site: dict, competitors: list[str] | None = None, depth: int = 50) -> dict:
    key, folder = os.environ.get("YANDEX_SEARCH_API_KEY"), os.environ.get("YANDEX_FOLDER_ID")
    if not key or not folder:
        return {"status": "not_configured", "note": "Задайте YANDEX_SEARCH_API_KEY и YANDEX_FOLDER_ID, чтобы получить позиции в Яндексе."}
    region = REGIONS.get(site.get("city", ""), 45)
    hosts = [urlparse(site["url"]).netloc.removeprefix("www.")] + [urlparse(c).netloc.removeprefix("www.") for c in (competitors or [])]
    out = {"status": "ok", "region": region, "depth": depth, "queries": []}
    with httpx.Client(timeout=30) as c:
        for q in default_queries(site):
            body = {"query": {"searchType": "SEARCH_TYPE_RU", "queryText": q}, "region": str(region), "folderId": folder,
                    "groupSpec": {"groupMode": "GROUP_MODE_FLAT", "groupsOnPage": str(depth), "docsInGroup": "1"}, "responseFormat": "FORMAT_XML"}
            try:
                r = c.post(ENDPOINT, json=body, headers={"Authorization": f"Api-Key {key}"})
                r.raise_for_status()
                urls = _positions(base64.b64decode(r.json()["rawData"]).decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                out["queries"].append({"query": q, "position": None, "error": str(e)[:120], "competitors": {}}); continue
            pos = lambda h: next((i + 1 for i, u in enumerate(urls) if urlparse(u).netloc.removeprefix("www.") == h), None)  # noqa: E731
            out["queries"].append({"query": q, "position": pos(hosts[0]), "competitors": {h: pos(h) for h in hosts[1:]}})
    return out
