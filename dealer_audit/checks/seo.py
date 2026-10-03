"""SEO: robots/sitemap, заголовки и описания, структура, разметка, видимость в Яндексе."""
from __future__ import annotations
import re
from collections import Counter
from urllib.parse import urlparse
from ..models import Finding, PageData

CITY_STEMS = {"Чебоксары": "чебоксар", "Йошкар-Ола": "йошкар", "Новочебоксарск": "новочебоксар", "Казань": "казан"}
IMPORTANT = ("home", "catalog", "card", "model", "credit", "tradein", "contacts", "offers")


def run(data: dict, visibility: dict | None = None) -> tuple[list[Finding], dict]:
    pages: list[PageData] = [p for p in data["pages"] if p.dom and p.status and p.status < 400 and not p.kind.startswith("blocked")]
    sm, site = data["sitemap"], data["site"]
    host = urlparse(site["url"]).netloc.removeprefix("www.")
    F: list[Finding] = []

    # --- robots.txt / sitemap
    if sm["robots_status"] != 200:
        F.append(Finding("seo.no_robots", "seo", "Нет robots.txt", "Создать robots.txt с директивой Sitemap и Clean-param для UTM-меток (для Яндекса).",
                         impact=2, ease=5, confidence=0.8, evidence=[f"https://{host}/robots.txt → {sm['robots_status']}"]))
    else:
        rb = sm["robots_txt"]
        if re.search(r"(?im)^\s*disallow:\s*/\s*$", rb):
            F.append(Finding("seo.robots_disallow_all", "seo", "robots.txt закрывает сайт от индексации (Disallow: /)",
                             "Срочно проверить: если это не тестовый домен — открыть сайт для индексации.", impact=5, ease=5, confidence=0.7, evidence=[f"https://{host}/robots.txt"]))
        foreign = [s for s in sm["sitemaps"] if urlparse(s).netloc.removeprefix("www.") != host]
        if foreign:
            F.append(Finding("seo.sitemap_foreign_host", "seo", f"В robots.txt указан sitemap другого сайта: {foreign[0]}",
                             "Исправить директиву Sitemap на собственный sitemap.xml. Сейчас поисковик получает карту чужого домена, а страницы этого сайта индексируются хуже.",
                             impact=4, ease=5, confidence=0.9, evidence=[f"https://{host}/robots.txt"] + foreign[:2]))
        if "clean-param" not in rb.lower():
            F.append(Finding("seo.no_clean_param", "seo", "В robots.txt нет Clean-param для UTM-меток",
                             "Добавить Clean-param: utm_source&utm_medium&utm_campaign&utm_content&utm_term&yclid, чтобы рекламные метки не плодили дубли в Яндексе.",
                             impact=1, ease=5, confidence=0.7, evidence=[f"https://{host}/robots.txt"]))
    own_urls = [u for u in sm["urls"] if urlparse(u).netloc.removeprefix("www.") == host]
    if sm["errors"]:
        F.append(Finding("seo.sitemap_errors", "seo", "Ошибки sitemap.xml", "Исправить карту сайта: она должна отдаваться с кодом 200 и быть валидным XML.",
                         impact=3, ease=4, confidence=0.8, evidence=sm["errors"][:3]))
    foreign_sm = any(urlparse(x).netloc.removeprefix("www.") != host for x in sm["sitemaps"])
    if not sm["errors"] and not own_urls and not foreign_sm:
        F.append(Finding("seo.sitemap_empty", "seo", "sitemap.xml пустой (0 страниц этого сайта)",
                         "Генерировать sitemap автоматически: главная, каталог, все карточки авто в наличии, модели, акции. Без него новые авто дольше попадают в поиск.",
                         impact=4, ease=4, confidence=0.85, evidence=sm["sitemaps"][:2]))
    def _is_junk(u: str) -> bool:
        path = urlparse(u).path.lower()
        if re.search(r"/(news|novosti|blog|articles?|press|stati)/", path):
            return False
        return any(len(seg) <= 30 and re.search(r"(^|[_-])(dev|test|staging|tmp|copy)(?![-_]?dr)([_-]|$)", seg) for seg in path.split("/"))
    junk = [u for u in own_urls if _is_junk(u)]
    if junk:
        F.append(Finding("seo.sitemap_junk", "seo", f"В sitemap тестовые/служебные страницы: {len(junk)}",
                         "Убрать тестовые страницы из sitemap и закрыть их от индексации (noindex) или удалить.", impact=2, ease=5, confidence=0.75, evidence=junk[:5]))
    legal = [u for u in own_urls if re.search(r"soglasie|politik|agree|personal", u.lower())]
    if own_urls and len(legal) >= 10 and len(legal) / len(own_urls) > 0.1:
        F.append(Finding("seo.sitemap_legal", "seo", f"В sitemap {len(legal)} юридических страниц (согласия, политики) — {round(100*len(legal)/len(own_urls))}% карты",
                         "Исключить служебные страницы из sitemap: они тратят краулинговый бюджет и размывают релевантность сайта.", impact=1, ease=5, confidence=0.7, evidence=legal[:3]))
    # --- Мета-теги
    imp = [p for p in pages if p.kind in IMPORTANT]
    no_title = [p for p in imp if not p.title.strip()]
    bad_len = [p for p in imp if p.title and (len(p.title) < 20 or len(p.title) > 80)]
    if no_title:
        F.append(Finding("seo.no_title", "seo", f"Нет <title>: {len(no_title)} стр.", "Прописать уникальные title.", impact=4, ease=5, confidence=0.85, evidence=[p.url for p in no_title[:4]]))
    if bad_len:
        F.append(Finding("seo.title_length", "seo", f"Title слишком короткий/длинный: {len(bad_len)} стр.",
                         "Оптимально 40–70 символов: модель + ключевое слово + город + выгода.", impact=2, ease=5, confidence=0.6,
                         evidence=[f"{p.url} — «{p.title[:90]}» ({len(p.title)})" for p in bad_len[:4]]))
    dup_t = [t for t, c in Counter(p.title.strip() for p in pages if p.title.strip()).items() if c > 1]
    if dup_t:
        F.append(Finding("seo.dup_titles", "seo", f"Одинаковые title у разных страниц: {len(dup_t)} групп",
                         "Сделать title уникальными (шаблон для карточек: «Марка Модель Год — купить в <город>, цена X ₽»).", impact=3, ease=4, confidence=0.75,
                         evidence=[f"«{t[:80]}»" for t in dup_t[:3]]))
    no_desc = [p for p in imp if not p.description.strip()]
    if no_desc:
        F.append(Finding("seo.no_description", "seo", f"Нет meta description: {len(no_desc)} из {len(imp)} ключевых страниц",
                         "Прописать description 120–160 символов с выгодой и призывом. От него зависит сниппет в Яндексе и кликабельность.",
                         impact=2, ease=5, confidence=0.8, evidence=[p.url for p in no_desc[:4]]))
    dup_d = [t for t, c in Counter(p.description.strip() for p in pages if p.description.strip()).items() if c > 1]
    if dup_d:
        F.append(Finding("seo.dup_descriptions", "seo", f"Одинаковые description: {len(dup_d)} групп", "Генерировать description по шаблону с данными страницы.",
                         impact=2, ease=4, confidence=0.7, evidence=[f"«{t[:80]}»" for t in dup_d[:2]]))
    # --- Структура
    no_h1 = [p for p in imp if not p.h1]
    multi_h1 = [p for p in imp if len(p.h1) > 1]
    if no_h1:
        F.append(Finding("seo.no_h1", "seo", f"Нет H1: {len(no_h1)} стр.", "Один H1 на страницу с главным ключом (модель/категория + город).",
                         impact=3, ease=5, confidence=0.8, evidence=[p.url for p in no_h1[:4]]))
    if multi_h1:
        F.append(Finding("seo.multi_h1", "seo", f"Несколько H1 на странице: {len(multi_h1)} стр.", "Оставить один H1, остальные заменить на H2.",
                         impact=1, ease=5, confidence=0.75, evidence=[f"{p.url} — {len(p.h1)} шт." for p in multi_h1[:3]]))
    noindex = [p for p in imp if "noindex" in p.robots_meta.lower()]
    if noindex:
        F.append(Finding("seo.noindex", "seo", f"Важные страницы закрыты noindex: {len(noindex)}", "Убрать noindex со страниц, которые должны приводить трафик.",
                         impact=5, ease=5, confidence=0.8, evidence=[p.url for p in noindex[:4]]))
    canon_bad = [p for p in imp if p.canonical and urlparse(p.canonical).netloc.removeprefix("www.") not in (host, "")]
    if canon_bad:
        F.append(Finding("seo.canonical_foreign", "seo", "Canonical указывает на другой домен", "Проверить canonical: страница отдаёт вес другому сайту и может выпасть из индекса.",
                         impact=4, ease=5, confidence=0.75, evidence=[f"{p.url} → {p.canonical}" for p in canon_bad[:3]]))
    no_canon = [p for p in imp if not p.canonical]
    if len(no_canon) > len(imp) / 2 and imp:
        F.append(Finding("seo.no_canonical", "seo", f"Нет canonical на {len(no_canon)} из {len(imp)} ключевых страниц",
                         "Добавить rel=canonical: защищает от дублей с UTM/фильтрами.", impact=1, ease=5, confidence=0.7, evidence=[p.url for p in no_canon[:3]]))
    home = next((p for p in pages if p.kind == "home"), None)
    city = site.get("city", "Чебоксары")
    stem = CITY_STEMS.get(city, city.lower()[:6])
    if home and site.get("type") != "import" and stem not in (home.title + " " + " ".join(home.h1)).lower():
        F.append(Finding("seo.no_city_home", "seo", f"В title/H1 главной нет города ({city})",
                         f"Добавить город: «Официальный дилер X в {city}». Большинство запросов с покупательским намерением геозависимы («купить haval {stem}…»).",
                         impact=3, ease=5, confidence=0.7, evidence=[f"title: «{home.title[:90]}»"]))
    # --- Разметка
    if home:
        types = " ".join(home.dom["seo"]["ld_types"] + home.dom["seo"]["micro"]).lower()
        if not re.search(r"autodealer|organization|localbusiness|automotivebusiness", types):
            F.append(Finding("seo.no_org_schema", "seo", "Нет разметки Organization/AutoDealer на главной",
                             "Добавить JSON-LD AutoDealer: название, адрес, телефон, часы работы, координаты. Помогает в Яндекс Картах и в расширенных сниппетах.",
                             impact=2, ease=4, confidence=0.75, evidence=[home.url]))
        if not home.dom["seo"]["og"]:
            F.append(Finding("seo.no_og", "seo", "Нет Open Graph-разметки", "Добавить og:title/og:image: ссылки, отправленные в мессенджерах, будут с картинкой и ценой.",
                             impact=1, ease=5, confidence=0.8, evidence=[home.url]))
        if home.dom["seo"]["words"] < 250:
            F.append(Finding("seo.thin_home", "seo", f"Мало текста на главной ({home.dom['seo']['words']} слов)", "Добавить SEO-блок: модели, город, преимущества, FAQ.",
                             impact=1, ease=4, confidence=0.5, evidence=[home.url]))
    cards = [p for p in pages if p.kind == "card"]
    if cards and not any(re.search(r"car|vehicle|product|offer", " ".join(p.dom["seo"]["ld_types"] + p.dom["seo"]["micro"]).lower()) for p in cards):
        F.append(Finding("seo.no_car_schema", "seo", "В карточках авто нет микроразметки Car/Product/Offer",
                         "Добавить schema.org Car + Offer (цена, наличие, VIN, пробег) — это расширенные сниппеты с ценой в выдаче.", impact=2, ease=4, confidence=0.75, evidence=[cards[0].url]))
    alts = sum(p.dom["images"]["no_alt"] for p in pages); total = sum(p.dom["images"]["total"] for p in pages)
    if total and alts / total > 0.4:
        F.append(Finding("seo.img_alt", "seo", f"У {round(100*alts/total)}% изображений нет alt", "Генерировать alt по шаблону «Марка Модель Год — фото N».",
                         impact=1, ease=4, confidence=0.7, evidence=[f"{alts} из {total} изображений"]))
    # --- Видимость в Яндексе
    vis_stats = None
    if visibility and visibility.get("status") == "ok":
        vis_stats = visibility
        q = visibility["queries"]
        top10 = [x for x in q if x["position"] and x["position"] <= 10]
        miss = [x for x in q if not x["position"] or x["position"] > 10]
        if miss:
            F.append(Finding("seo.yandex_low_visibility", "seo", f"Сайт вне топ-10 Яндекса по {len(miss)} из {len(q)} целевых запросов",
                             "Под каждый запрос — посадочная страница (модель/категория + город), title/H1 с ключом, карточки в sitemap, отзывы на Яндекс Картах.",
                             impact=4, ease=2, confidence=0.75, evidence=[f"«{x['query']}» — {x['position'] or 'нет в топ-' + str(visibility['depth'])}" for x in miss[:6]],
                             details={"top10": len(top10)}))
    stats = {"sitemap_urls": len(own_urls), "sitemaps": sm["sitemaps"], "pages_checked": len(pages),
             "missing_description": len(no_desc), "missing_h1": len(no_h1), "yandex": visibility or {"status": "not_configured"}}
    return F, stats
