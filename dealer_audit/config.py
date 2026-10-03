from __future__ import annotations
from pathlib import Path
from urllib.parse import urlparse
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_sites(path: str | None = None) -> dict:
    p = Path(path) if path else ROOT / "config" / "sites.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def resolve_site(cfg: dict, key_or_url: str) -> dict:
    """Сайт по ключу из конфига или произвольный URL (агент работает на любом сайте)."""
    sites = cfg.get("sites", {})
    if key_or_url in sites:
        s = dict(sites[key_or_url]); s["key"] = key_or_url
    else:
        url = key_or_url if key_or_url.startswith("http") else "https://" + key_or_url
        host = urlparse(url).netloc
        match = next((k for k, v in sites.items() if urlparse(v["url"]).netloc == host), None)
        if match:
            s = dict(sites[match]); s["key"] = match
        else:
            s = {"key": host.replace(".", "_"), "url": url, "name": host, "type": "new"}
    s.setdefault("city", cfg.get("defaults", {}).get("city", "Чебоксары"))
    s.setdefault("competitors", cfg.get("defaults", {}).get("competitors", []))
    s.setdefault("keywords", [])
    return s
