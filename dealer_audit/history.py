"""История прогонов: сравнение с прошлым отчётом и учёт «возраста» карточек авто."""
from __future__ import annotations
import datetime as dt, json
from pathlib import Path


def site_dir(root: Path, key: str) -> Path:  # noqa: D401
    d = root / key; d.mkdir(parents=True, exist_ok=True); return d


def previous_report(sd: Path, current: Path | None = None) -> dict | None:
    runs = sorted([p for p in sd.iterdir() if p.is_dir() and (p / "report.json").exists() and p != current])
    return json.loads((runs[-1] / "report.json").read_text(encoding="utf-8")) if runs else None


def diff(prev: dict | None, findings: list[dict]) -> dict | None:
    if not prev:
        return None
    old = {f["id"]: f for f in prev["findings"]}
    new = {f["id"]: f for f in findings}
    return {"prev_date": prev["meta"]["date"],
            "new": [new[i] for i in new if i not in old],
            "fixed": [old[i] for i in old if i not in new],
            "persisting": [new[i] for i in new if i in old]}


def load_inventory(sd: Path) -> dict:
    p = sd / "inventory.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def update_inventory(sd: Path, inv: dict, records: list[dict]) -> None:
    today = dt.date.today().isoformat()
    for r in records:
        h = inv.get(r["url"])
        if not h or h.get("price") != r["price"]:
            inv[r["url"]] = {"first_seen": h["first_seen"] if h and h.get("price") == r["price"] else today,
                             "price": r["price"], "title": r["title"], "last_seen": today}
        else:
            h["last_seen"] = today
    (sd / "inventory.json").write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
