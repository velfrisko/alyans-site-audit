"""Техническое состояние: скорость, мобильная версия, битые ссылки, ошибки JS, формы."""
from __future__ import annotations
import asyncio
import re
from urllib.parse import urlparse
from ..fetch import client, get
from ..models import Finding, PageData

SKIP_EXT = re.compile(r"\.(jpg|jpeg|png|webp|gif|svg|pdf|zip|mp4|docx?|xlsx?)$", re.I)
NOISE = re.compile(r"Failed to load resource|Failed to fetch dynamically imported|MIME type|net::ERR_|WebSocket connection|favicon|yandex\.ru/metrika|mc\.yandex|google|facebook|vk\.com|top-fwz|ERR_BLOCKED|Third-party cookie|CORS policy.*(metrika|google)", re.I)


async def check_links(urls: dict[str, str], limit: int = 250, conc: int = 8) -> list[dict]:
    """urls: {url: страница-источник}. Возвращает список проблемных ссылок."""
    sem = asyncio.Semaphore(conc)
    out = []
    async with client(timeout=15) as c:
        async def one(u, src):
            async with sem:
                st, _, final = await get(c, u, tries=3)
                if st >= 400 or st == 0:
                    out.append({"url": u, "status": st, "source": src})
        await asyncio.gather(*(one(u, s) for u, s in list(urls.items())[:limit]))
        # http → https
    return out


async def http_redirect(host: str) -> bool | None:
    async with client(timeout=15) as c:
        st, _, final = await get(c, f"http://{host}/", tries=2)
        return final.startswith("https://") if st else None


def run(data: dict, linkcheck: bool = True) -> tuple[list[Finding], dict]:
    pages: list[PageData] = data["pages"]
    host = urlparse(data["site"]["url"]).netloc.removeprefix("www.")
    F: list[Finding] = []

    # --- Недоступные страницы (после повторных попыток)
    bad = [p for p in pages if p.status >= 400 or (p.error and not p.status)]
    if bad and linkcheck:  # 5xx/обрыв перепроверяем HTTP-запросом с повторами — разовый сбой не ошибка сайта
        still = asyncio.run(check_links({p.url: "" for p in bad}, limit=20))
        bad_urls = {b["url"] for b in still if b["status"] >= 400}
        bad = [p for p in bad if p.url in bad_urls or (400 <= p.status < 500)]
    if bad:
        F.append(Finding("tech.page_errors", "technical", f"Страницы с ошибкой загрузки: {len(bad)}",
                         "Исправить или настроить 301-редирект на актуальную страницу; убрать ссылки на них из меню и sitemap.",
                         impact=4, ease=4, confidence=0.75, evidence=[f"{p.url} → {p.status or p.error[:60]}" for p in bad[:5]]))
    # --- Капча антибота
    blocked = [p for p in pages if p.kind.startswith("blocked:")]
    if blocked:
        F.append(Finding("tech.captcha_shown", "technical", f"Антибот показал капчу на {len(blocked)} стр. (исключены из анализа)",
                         "Проверить настройки защиты (Yandex SmartCaptcha / DDoS-Guard): капча на карточке авто стоит заявок, если её видят реальные посетители (мобильный интернет, NAT, VPN). Агенту — добавить IP в белый список.",
                         impact=3, ease=4, confidence=0.4, evidence=[f"{p.url} ({p.kind.split(':')[1]})" for p in blocked[:4]] + [s for p in blocked[:1] for s in p.screenshots.values()]))
    # --- JS-ошибки
    js = {}
    for p in pages:
        for e in p.console_errors:
            if NOISE.search(e):
                continue
            js.setdefault(e[:160], []).append(p.url)
    exceptions = {k: v for k, v in js.items() if k.startswith("JS exception")}
    if exceptions:
        F.append(Finding("tech.js_exceptions", "technical", f"Необработанные JS-исключения: {len(exceptions)} видов",
                         "Передать разработчику: исключения могут ломать формы, калькуляторы и галереи. Подключить мониторинг ошибок (Sentry / Hawk).",
                         impact=3, ease=3, confidence=0.7, evidence=[f"{k} — {v[0]}" for k, v in list(exceptions.items())[:4]]))
    other_js = {k: v for k, v in js.items() if not k.startswith("JS exception")}
    if len(other_js) >= 3:
        F.append(Finding("tech.console_errors", "technical", f"Ошибки в консоли браузера: {len(other_js)} видов",
                         "Разобрать ошибки консоли (часто это 404 скриптов/картинок и падающие виджеты).", impact=2, ease=3, confidence=0.6,
                         evidence=[f"{k} — {v[0]}" for k, v in list(other_js.items())[:4]]))
    # --- Битые ресурсы и картинки
    res = sorted({r for p in pages for r in p.failed_resources})
    if res and linkcheck:  # перепроверяем: разовый сбой сети не считается ошибкой сайта
        still = asyncio.run(check_links({r.split(" ", 1)[1]: "" for r in res}, limit=40))
        still_urls = {b["url"] for b in still if b["status"] >= 400}
        res = [r for r in res if r.split(" ", 1)[1] in still_urls]
    if res:
        F.append(Finding("tech.failed_resources", "technical", f"Ресурсы сайта отдают ошибку (4xx/5xx): {len(res)}",
                         "Исправить пути к файлам или удалить подключения.", impact=2, ease=4, confidence=0.75, evidence=res[:5]))
    broken_imgs = sorted({i for p in pages if p.dom for i in p.dom["images"]["broken"]})
    if broken_imgs and linkcheck:  # подтверждаем HTTP-запросом
        still = asyncio.run(check_links({u: "" for u in broken_imgs}, limit=30))
        bad_set = {b["url"] for b in still if b["status"] >= 400}
        broken_imgs = [u for u in broken_imgs if u in bad_set]
    if broken_imgs:
        F.append(Finding("tech.broken_images", "technical", f"Битые изображения: {len(broken_imgs)}",
                         "Заменить или удалить; в карточках авто битое фото резко снижает доверие.", impact=3, ease=4, confidence=0.7, evidence=broken_imgs[:5]))
    # --- Битые ссылки
    broken = []
    if linkcheck:
        src = {}
        for p in pages:
            for l in p.links:
                if urlparse(l).netloc.removeprefix("www.") == host and not SKIP_EXT.search(l) and l not in src:
                    src[l] = p.url
        for u in data["sitemap"]["urls"][:150]:
            if urlparse(u).netloc.removeprefix("www.") == host:
                src.setdefault(u.rstrip("/") if urlparse(u).path not in ("", "/") else u, "sitemap.xml")
        rendered = {p.url for p in pages if p.status and p.status < 400}
        todo = {u: s for u, s in src.items() if u not in rendered}
        broken = asyncio.run(check_links(todo))
        hard = [b for b in broken if b["status"] >= 400]
        soft = [b for b in broken if b["status"] == 0]
        if hard:
            F.append(Finding("tech.broken_links", "technical", f"Битые внутренние ссылки: {len(hard)}",
                             "Исправить ссылки или настроить 301-редиректы. Битые ссылки в меню и карточках — прямые потери заявок; в sitemap — потери индексации.",
                             impact=3 if len(hard) < 5 else 4, ease=4, confidence=0.85,
                             evidence=[f"{b['url']} → {b['status']} (ссылка с {b['source']})" for b in hard[:6]], details={"broken": hard}))
        if len(soft) >= 3:
            F.append(Finding("tech.unstable", "technical", f"Сайт периодически не отвечает: {len(soft)} URL не ответили после 3 попыток",
                             "Проверить хостинг/защиту от DDoS: возможны обрывы соединения и для реальных посетителей.", impact=3, ease=3, confidence=0.4,
                             evidence=[b["url"] for b in soft[:4]]))
        redir = asyncio.run(http_redirect(host))
        if redir is False:
            F.append(Finding("tech.no_https_redirect", "technical", "HTTP-версия не перенаправляет на HTTPS",
                             "Настроить 301 с http:// на https://.", impact=2, ease=5, confidence=0.7, evidence=[f"http://{host}/"]))
    # --- Скорость (лабораторный замер, мобильный Slow 4G + CPU×4)
    perf = [x for x in data.get("perf", []) if "lcp" in x]
    for x in perf:
        lcp = x["lcp"]
        pk = next((p.kind for p in pages if p.url == x["url"]), "inner")
        if lcp > 4:
            F.append(Finding(f"tech.slow_lcp.{pk}", "technical",
                             f"Медленная загрузка на мобильном: LCP {lcp} с (норма ≤ 2,5 с)",
                             "Сжать и перевести изображения в WebP/AVIF, задать размеры, ленивая загрузка ниже первого экрана, отложить сторонние виджеты, CDN. Каждая лишняя секунда загрузки снижает конверсию.",
                             impact=4, ease=3, confidence=0.6, evidence=[f"{x['url']} — LCP {lcp} с, вес {x['weight_mb']} МБ, запросов {x.get('requests')}"]))
        elif lcp > 2.5:
            F.append(Finding(f"tech.mid_lcp.{pk}", "technical", f"Загрузка на мобильном ниже нормы: LCP {lcp} с",
                             "Оптимизировать главный баннер/фото первого экрана (preload, WebP, правильный размер).", impact=3, ease=3, confidence=0.55,
                             evidence=[f"{x['url']} — LCP {lcp} с, вес {x['weight_mb']} МБ"]))
        if x.get("weight_mb", 0) > 5:
            F.append(Finding(f"tech.heavy.{pk}", "technical", f"Тяжёлая страница: {x['weight_mb']} МБ",
                             "Цель — до 2–3 МБ на мобильном: оптимизация изображений и видео, удаление неиспользуемых скриптов.", impact=3, ease=3, confidence=0.7, evidence=[x["url"]]))
        if x.get("cls", 0) > 0.25:
            F.append(Finding(f"tech.cls.{pk}", "technical", f"Контент «прыгает» при загрузке (CLS {x['cls']})",
                             "Задать размеры изображений и баннеров, резервировать место под виджеты.", impact=2, ease=4, confidence=0.6, evidence=[x["url"]]))
    # --- Мобильная версия
    mob = [p for p in pages if p.mobile]
    if mob:
        if any(not p.mobile["mobile"]["viewport_meta"] for p in mob):
            F.append(Finding("tech.no_viewport", "technical", "Нет meta viewport — сайт не адаптирован под мобильные",
                             "Добавить <meta name=viewport content=\"width=device-width, initial-scale=1\"> и адаптивную вёрстку.", impact=5, ease=3, confidence=0.85, evidence=[mob[0].url]))
        ov = [p for p in mob if p.mobile["mobile"]["overflow_x"]]
        if ov:
            F.append(Finding("tech.mobile_overflow", "technical", f"Горизонтальная прокрутка на мобильном: {len(ov)} стр.",
                             "Найти элементы шире экрана (таблицы, баннеры, слайдеры) и ограничить ширину.", impact=3, ease=4, confidence=0.75,
                             evidence=[p.screenshots.get("mobile", p.url) for p in ov[:3]]))
        taps = [(p, p.mobile["mobile"]["small_taps"]) for p in mob if p.mobile["mobile"]["small_taps"] > 25]
        if taps:
            F.append(Finding("tech.small_taps", "technical", "Много мелких кнопок/ссылок на мобильном (<32px)",
                             "Увеличить зоны нажатия до 44–48px для кнопок и ссылок меню.", impact=2, ease=4, confidence=0.5,
                             evidence=[f"{p.url} — {n} элементов" for p, n in taps[:3]]))
    # --- Формы: техническая исправность (статическая)
    for p in pages:
        if not p.dom:
            continue
        for f in p.dom["forms"]:
            if f["has_phone"] and not f["submit_text"]:
                F.append(Finding("tech.form_no_submit", "technical", "Форма с телефоном без кнопки отправки",
                                 "Проверить форму вручную — возможно, она не отправляется.", impact=4, ease=4, confidence=0.5, evidence=[p.url]))
                break
    forms_with_js = [p for p in pages if p.dom and p.dom["forms"] and any(e.startswith("JS exception") for e in p.console_errors)]
    if forms_with_js:
        F.append(Finding("tech.forms_js_risk", "technical", "JS-исключения на страницах с формами заявок",
                         "Проверить отправку форм на этих страницах (запуск агента с флагом --test-forms).", impact=4, ease=4, confidence=0.45,
                         evidence=[p.url for p in forms_with_js[:3]]))
    stats = {"pages_rendered": len(pages), "page_errors": len(bad), "js_exceptions": len(exceptions), "console_errors": len(other_js),
             "broken_links": len([b for b in broken if b["status"] >= 400]), "perf": perf}
    return F, stats
