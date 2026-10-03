"""Сравнение с конкурентами: тот же сбор фактов на сайтах конкурентов → матрица возможностей → «в чём они сильнее»."""
from __future__ import annotations
from urllib.parse import urlparse
from ..models import Finding, PageData

FEATURES = [
    # key, название, функция(pages, perf) -> bool|число, вес для заявок (impact)
    ("tel_click", "Кликабельный телефон", lambda P: any(p.dom["tel_links"] > 0 for p in P), 4),
    ("phone_fold_m", "Телефон в первом экране на мобильном", lambda P: any(p.mobile and (p.mobile["tel_fold"] or p.mobile["phone_text_fold"]) for p in P if p.kind == "home"), 4),
    ("cta_fold_m", "Кнопка заявки в первом экране на мобильном", lambda P: any(p.mobile and p.mobile["cta_fold"] > 0 for p in P if p.kind == "home"), 4),
    ("sticky_m", "Закреплённая кнопка связи на мобильном", lambda P: any(p.mobile and p.mobile["mobile"]["sticky_cta"] for p in P), 3),
    ("messengers", "Мессенджеры (WhatsApp/Telegram/MAX)", lambda P: any(p.dom["messengers"][k] for p in P for k in ("whatsapp", "telegram", "max")), 4),
    ("chat", "Онлайн-чат / обратный звонок", lambda P: any(p.dom["widgets"][k] for p in P for k in ("jivo", "envybox", "bitrix24", "talkme", "callibri", "generic_chat")), 2),
    ("credit_calc", "Кредитный калькулятор", lambda P: any(p.dom["credit_calc"] for p in P), 4),
    ("tradein", "Трейд-ин на сайте", lambda P: any(p.dom["mentions"]["tradein"] for p in P), 4),
    ("tradein_calc", "Онлайн-оценка авто для трейд-ин", lambda P: any(p.dom["tradein_calc"] for p in P), 3),
    ("testdrive", "Запись на тест-драйв", lambda P: any(p.dom["mentions"]["testdrive"] for p in P), 2),
    ("booking", "Онлайн-бронирование / покупка", lambda P: any(p.dom["mentions"]["online_booking"] for p in P), 3),
    ("stock_prices", "Авто в наличии с ценами", lambda P: any(p.dom["car"].get("page_price_count", 0) >= 3 for p in P), 5),
    ("offers", "Спецпредложения и акции", lambda P: any(p.dom["mentions"]["offers"] for p in P), 2),
    ("reviews", "Отзывы клиентов на сайте", lambda P: any(p.dom["mentions"]["reviews"] for p in P), 2),
    ("lead_forms", "Формы заявки на страницах", lambda P: any(f["has_phone"] for p in P for f in p.dom["forms"]), 4),
]


def profile(data: dict) -> dict:
    P = [p for p in data["pages"] if p.dom]
    prof = {"name": data["site"].get("name") or urlparse(data["site"]["url"]).netloc, "url": data["site"]["url"], "ok": bool(P)}
    for key, _, fn, _ in FEATURES:
        try:
            prof[key] = bool(fn(P)) if P else None
        except Exception:  # noqa: BLE001
            prof[key] = None
    home_perf = next((x for x in data.get("perf", []) if "lcp" in x), None)
    prof["lcp"] = home_perf["lcp"] if home_perf else None
    return prof


def run(target: dict, comps: list[dict]) -> tuple[list[Finding], dict]:
    me = profile(target)
    others = [profile(c) for c in comps]
    others_ok = [o for o in others if o["ok"]]
    F: list[Finding] = []
    stype = target["site"].get("type")
    for key, label, _, impact in FEATURES:
        if key == "testdrive" and stype not in ("new", "group"):
            continue  # тест-драйв не применим к импорту/б/у
        have = [o["name"] for o in others_ok if o.get(key)]
        if me.get(key) is False and have:
            share = len(have) / max(1, len(others_ok))
            F.append(Finding(f"comp.{key}", "competitors", f"У конкурентов есть, у вас нет: {label.lower()}",
                             f"Есть у: {', '.join(have)}. Добавить на сайт, чтобы не проигрывать при сравнении (покупатель авто в среднем смотрит 3–5 сайтов дилеров).",
                             impact=impact, ease=3, confidence=round(0.45 + 0.25 * share, 2), evidence=[o["url"] for o in others_ok if o.get(key)][:3], source="competitor"))
    lcps = [o["lcp"] for o in others_ok if o.get("lcp")]
    if me.get("lcp") and lcps and me["lcp"] > 1.5 * min(lcps) and me["lcp"] > 2.5:
        best = min(others_ok, key=lambda o: o.get("lcp") or 99)
        F.append(Finding("comp.speed", "competitors", f"Сайт медленнее конкурентов: LCP {me['lcp']} с против {best['lcp']} с у {best['name']}",
                         "Ускорить первый экран (см. блок «Техническое состояние»).", impact=3, ease=3, confidence=0.55, evidence=[best["url"]], source="competitor"))
    strengths = [label for key, label, _, _ in FEATURES if me.get(key) and not any(o.get(key) for o in others_ok)]
    if me.get("lcp") and lcps and me["lcp"] < min(lcps):
        strengths.append(f"скорость загрузки (LCP {me['lcp']} с против {min(lcps)} с у лучшего конкурента)")
    matrix = {"features": [(k, l) for k, l, _, _ in FEATURES] + [("lcp", "Скорость, LCP mobile (с)")], "rows": [me] + others,
              "matrix_summary": {"мы": {l: me.get(k) for k, l, _, _ in FEATURES}, **{o["name"]: {l: o.get(k) for k, l, _, _ in FEATURES} for o in others_ok}},
              "our_strengths": strengths}
    return F, matrix
