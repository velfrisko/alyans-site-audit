"""Карточки автомобилей: полнота, единообразие, устаревшие позиции."""
from __future__ import annotations
import datetime as dt
import statistics
from ..models import Finding, PageData

MIN_PHOTOS = 5


def card_record(p: PageData) -> dict:
    c, im = p.dom["car"], p.dom["images"]
    price = c["prices"][0] if c["prices"] else None
    has_cta = p.dom["cta_count"] > 0
    rec = {
        "url": p.url, "title": (p.h1[0] if p.h1 else p.title)[:120], "price": price,
        "photos": im["gallery"], "specs": len(c["specs"]), "year": c["year"], "mileage": c["mileage"], "vin": c["vin"],
        "in_stock": c["avail"]["in_stock"], "in_transit": c["avail"]["in_transit"], "on_order": c["avail"]["on_order"],
        "sold_label": c["avail"]["sold"], "price_on_request": c["price_on_request"], "cta": has_cta,
        "credit": p.dom["mentions"]["credit"], "tradein": p.dom["mentions"]["tradein"],
    }
    checks = [rec["photos"] >= MIN_PHOTOS, bool(price) and not rec["price_on_request"], rec["specs"] >= 3,
              rec["in_stock"] or rec["in_transit"] or rec["on_order"], bool(rec["year"]), has_cta]
    rec["completeness"] = round(100 * sum(checks) / len(checks))
    return rec


def run(data: dict, history: dict | None = None) -> tuple[list[Finding], dict]:
    pages: list[PageData] = data["pages"]
    site = data["site"]
    F: list[Finding] = []
    cards = [p for p in pages if p.kind == "card" and p.dom]
    catalogs = [p for p in pages if p.kind == "catalog" and p.dom]
    empty = [p for p in catalogs if p.dom["car"].get("page_price_count", 0) == 0 and p.dom["mentions"]["stock"]]
    if not cards and empty:
        F.append(Finding("cards.empty_catalog", "cards", "Страница «Авто в наличии» загрузилась без единого автомобиля и цены",
                         ("Проверить витрину наличия: за 10+ секунд после загрузки на странице нет ни одного авто с ценой (виджет склада не отработал или пуст). "
                          "Если витрина грузится внешним виджетом — проверить его доступность и скорость; при пустом складе показывать «авто в пути» и форму предзаказа, а не пустой экран."),
                         impact=5, ease=3, confidence=0.5, evidence=[p.url for p in empty[:2]] + [s for p in empty[:1] for s in p.screenshots.values()]))
    if not cards:
        stock_hint = any(p.dom and p.dom["mentions"]["stock"] for p in pages)
        F.append(Finding("cards.no_card_pages", "cards", "Не найдены отдельные страницы карточек авто с ценой",
                         ("Авто в наличии показываются без отдельных URL (или их нет). Каждому авто в наличии нужна своя страница: "
                          "фото, цена, комплектация, VIN/год, кнопки заявки. Тогда карточки попадут в поиск Яндекса, их можно отправить клиенту ссылкой и запускать на них рекламу."),
                         impact=4, ease=2, confidence=0.55 if stock_hint else 0.45, evidence=[p.url for p in catalogs[:2]] or [site["url"]]))
        return F, {"cards_analyzed": 0, "records": []}

    recs = [card_record(p) for p in cards]
    n = len(recs)
    ev = lambda rs: [r["url"] for r in rs[:4]]  # noqa: E731

    few = [r for r in recs if r["photos"] < MIN_PHOTOS]
    if few:
        F.append(Finding("cards.few_photos", "cards", f"Мало фото: {len(few)} из {n} карточек меньше {MIN_PHOTOS} фото",
                         "Минимум 10–15 фото: экстерьер со всех сторон, салон, приборная панель (пробег), багажник, дефекты. Карточки с полной галереей получают больше заявок.",
                         impact=4, ease=3, confidence=0.6, evidence=ev(few), details={"photos": {r["url"]: r["photos"] for r in few}}))
    noprice = [r for r in recs if not r["price"] or r["price_on_request"]]
    if noprice:
        F.append(Finding("cards.no_price", "cards", f"Цена не указана или «по запросу»: {len(noprice)} из {n}",
                         "Указывать цену (можно «от» и цену с учётом выгоды). Карточки без цены покупатель пролистывает и уходит к конкуренту.",
                         impact=5, ease=4, confidence=0.75, evidence=ev(noprice)))
    nospec = [r for r in recs if r["specs"] < 3]
    if nospec:
        F.append(Finding("cards.no_specs", "cards", f"Нет комплектации/характеристик: {len(nospec)} из {n}",
                         "Добавить блок: двигатель, мощность, КПП, привод, комплектация, цвет, ключевые опции.",
                         impact=3, ease=3, confidence=0.6, evidence=ev(nospec)))
    noavail = [r for r in recs if not (r["in_stock"] or r["in_transit"] or r["on_order"])]
    if noavail:
        F.append(Finding("cards.no_availability", "cards", f"Не указан статус наличия: {len(noavail)} из {n}",
                         "Показывать «В наличии / В пути до DD.MM / Под заказ». Статус — главный вопрос покупателя, его отсутствие порождает лишние звонки или уход.",
                         impact=3, ease=4, confidence=0.6, evidence=ev(noavail)))
    sold = [r for r in recs if r["sold_label"]]
    if sold:
        F.append(Finding("cards.sold_listed", "cards", f"В каталоге есть проданные/забронированные авто: {len(sold)}",
                         "Снимать проданные авто автоматически (выгрузка из DMS/1С) или выводить блок «Похожие авто в наличии».",
                         impact=3, ease=3, confidence=0.5, evidence=ev(sold)))
    if site.get("type") == "new":
        year_now = dt.date.today().year
        old = [r for r in recs if r["year"] and int(r["year"]) <= year_now - 2]
        if old:
            F.append(Finding("cards.old_model_year", "cards", f"Новые авто старого модельного года ({', '.join(sorted({r['year'] for r in old}))}): {len(old)}",
                             "Проверить актуальность позиций, отметить «спеццена на авто прошлого года».", impact=2, ease=4, confidence=0.5, evidence=ev(old)))
    if site.get("type") in ("used", "import"):
        novin = [r for r in recs if not r["vin"]]
        if novin and len(novin) == n:
            F.append(Finding("cards.no_vin", "cards", "В карточках авто с пробегом/импорта нет VIN или отчёта об истории",
                             "Показывать VIN (частично) и ссылку на отчёт (Автотека/Автокод). Снимает главный страх покупателя б/у авто.",
                             impact=3, ease=4, confidence=0.55, evidence=ev(novin)))
    # Единообразие
    if n >= 3:
        photos = [r["photos"] for r in recs]
        if max(photos) >= 3 * max(1, min(photos)) and max(photos) - min(photos) >= 6:
            F.append(Finding("cards.inconsistent_photos", "cards", f"Карточки заполнены неравномерно: от {min(photos)} до {max(photos)} фото",
                             "Ввести стандарт карточки (чек-лист фото и полей) и проверять его перед публикацией.", impact=2, ease=4, confidence=0.6, evidence=ev(recs)))
        filled = [r["completeness"] for r in recs]
        if statistics.pstdev(filled) > 18:
            F.append(Finding("cards.inconsistent_fields", "cards", f"Разная полнота карточек: {min(filled)}–{max(filled)}%",
                             "Сделать обязательные поля в CMS (цена, наличие, комплектация, ≥10 фото).", impact=2, ease=3, confidence=0.6, evidence=ev(sorted(recs, key=lambda r: r["completeness"])[:3])))
    titles = {}
    for r in recs:
        titles.setdefault((r["title"].lower(), r["price"]), []).append(r)
    dups = [v for v in titles.values() if len(v) > 1]
    if dups:
        F.append(Finding("cards.duplicates", "cards", f"Дубли карточек (одинаковые название и цена): {sum(len(v) for v in dups)}",
                         "Удалить дубли или различать их (цвет, VIN). Дубли путают покупателя и вредят SEO.", impact=2, ease=4, confidence=0.4,
                         evidence=[r["url"] for v in dups for r in v][:4]))
    # «Зависшие» авто — по истории прогонов
    stale = []
    if history:
        today = dt.date.today()
        for r in recs:
            h = history.get(r["url"])
            if h and h.get("first_seen"):
                days = (today - dt.date.fromisoformat(h["first_seen"])).days
                if days >= 45 and h.get("price") == r["price"]:
                    stale.append({**r, "days": days})
        if stale:
            F.append(Finding("cards.stale", "cards", f"Авто висят на сайте 45+ дней без изменения цены: {len(stale)}",
                             "Пересмотреть цену или поднять в спецпредложения; проверить, не продано ли авто.", impact=3, ease=4, confidence=0.7,
                             evidence=[f"{s['url']} — {s['days']} дн." for s in stale[:4]]))
    if catalogs:
        cp = catalogs[0]
        if cp.dom["car"].get("page_price_count", 0) == 0:
            F.append(Finding("cards.catalog_no_prices", "cards", "В списке авто (каталоге) не видно цен",
                             "Выводить цену и «от X ₽/мес» в каждой плитке каталога.", impact=4, ease=4, confidence=0.6, evidence=[cp.url]))
    stats = {"cards_analyzed": n, "avg_completeness": round(sum(r["completeness"] for r in recs) / n), "avg_photos": round(sum(r["photos"] for r in recs) / n, 1),
             "records": recs, "stale": len(stale)}
    return F, stats
