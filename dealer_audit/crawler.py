"""Сбор фактов о сайте: sitemap → обход → рендер страниц в браузере (desktop + mobile) → замеры скорости.

Принцип: краулер ничего не «оценивает», он только собирает факты. Оценку делают checks/* и LLM.
"""
from __future__ import annotations
import asyncio, os, re, time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse, urljoin, urldefrag
from xml.etree import ElementTree as ET

from playwright.async_api import async_playwright, Browser

from .fetch import client, get, UA
from .models import PageData

DETECT_JS = (Path(__file__).parent / "detectors.js").read_text(encoding="utf-8")

KIND_RULES = [
    ("legal", r"soglasie|politik|agree|personal|privacy|legal|pravov|cookie|user-agreement|oferta"),
    ("credit", r"kredit|credit|finans|rassroch|installment"),
    ("tradein", r"trade-?in|trejd|obmen|vykup|ocenk"),
    ("contacts", r"contact|kontakt"),
    ("testdrive", r"test-?dri|test-?dra[ij]v"),
    ("offers", r"offers?/?$|akci|special|spec-?pred|sale|promo"),
    ("catalog", r"online-stock/?$|/stock/?$|nalich|/cars/?$|catalog/?$|/used/?$|probeg|/avto/?$|/auto/?$|/new/?$|vitrin|showroom"),
    ("model", r"/models?/[^/]+/?$|/model/[^/]+"),
    ("service", r"service|servis|remont|/to/|maintenance|zapchast|parts"),
]
CARD_PARENT_HINT = re.compile(r"car|auto|avto|stock|nalich|used|probeg|catalog|vehicle|vitrin|showroom|offer|new|sale|vin", re.I)
KEY_KINDS = ["home", "catalog", "card", "credit", "tradein", "contacts", "testdrive", "offers", "model"]
TRANSIENT = re.compile(r"Failed to fetch dynamically imported module|ChunkLoadError|Loading chunk \d+ failed", re.I)

MOBILE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
              user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
DESKTOP = dict(viewport={"width": 1440, "height": 900}, user_agent=UA)

PERF_INIT = """
window.__perf = {lcp: 0, cls: 0};
try { new PerformanceObserver(l => { for (const e of l.getEntries()) window.__perf.lcp = e.startTime; }).observe({type: 'largest-contentful-paint', buffered: true}); } catch (e) {}
try { new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__perf.cls += e.value; }).observe({type: 'layout-shift', buffered: true}); } catch (e) {}
"""
PERF_READ = """() => { const n = performance.getEntriesByType('navigation')[0] || {}; const fcp = (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime || 0;
 return {ttfb: n.responseStart || 0, fcp, lcp: window.__perf.lcp || fcp, cls: +(window.__perf.cls || 0).toFixed(3), dcl: n.domContentLoadedEventEnd || 0, load: n.loadEventEnd || 0,
 requests: performance.getEntriesByType('resource').length + 1}; }"""


def norm(u: str) -> str:
    u = urldefrag(u)[0]
    return u.rstrip("/") if urlparse(u).path not in ("", "/") else u.split("?")[0]


def classify(url: str, home: str) -> str:
    p = urlparse(url)
    if p.path in ("", "/") and not p.query:
        return "home"
    path = p.path.lower()
    if re.search(r"/(news|novosti|blog|articles?|press)/", path):
        return "other"
    for kind, rx in KIND_RULES:
        if re.search(rx, path):
            return kind
    return "other"


def same_site(url: str, host: str) -> bool:
    h = urlparse(url).netloc.lower().removeprefix("www.")
    return h == host.removeprefix("www.")


def find_card_candidates(urls: list[str]) -> list[str]:
    """Карточки авто = много «братьев» под одним родительским путём, slug с цифрами/годом/id."""
    groups: dict[str, list[str]] = defaultdict(list)
    for u in urls:
        path = urlparse(u).path.rstrip("/")
        if path.count("/") < 2:
            continue
        parent, slug = path.rsplit("/", 1)
        if re.search(r"\d", slug) or slug.count("-") >= 3:
            groups[parent].append(u)
    best = []
    for parent, items in groups.items():
        if len(items) >= 3 and CARD_PARENT_HINT.search(parent) and not re.search(r"/(about|news|blog|press|events|articles?)\b", parent):
            best.extend(items)
    return list(dict.fromkeys(best))


async def read_sitemaps(c, base: str) -> dict:
    """Читает robots.txt и sitemap (включая sitemap-index). Возвращает urls и диагностику для SEO-проверок."""
    info = {"robots_status": 0, "robots_txt": "", "sitemaps": [], "sitemap_hosts": [], "urls": [], "errors": []}
    st, txt, _ = await get(c, urljoin(base, "/robots.txt"))
    info["robots_status"], info["robots_txt"] = st, txt[:5000] if st == 200 else ""
    sm = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", info["robots_txt"]) or [urljoin(base, "/sitemap.xml")]
    queue, seen = list(sm), set()
    while queue and len(seen) < 15:
        s = queue.pop(0)
        if s in seen:
            continue
        seen.add(s)
        info["sitemaps"].append(s)
        info["sitemap_hosts"].append(urlparse(s).netloc)
        st, body, _ = await get(c, s)
        if st != 200:
            info["errors"].append(f"{s} → HTTP {st}"); continue
        try:
            root = ET.fromstring(body.encode("utf-8"))
        except ET.ParseError:
            info["errors"].append(f"{s} → невалидный XML"); continue
        locs = [e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]
        if root.tag.endswith("sitemapindex"):
            queue.extend(locs)
        else:
            info["urls"].extend(locs)
    info["urls"] = list(dict.fromkeys(info["urls"]))
    return info


class Crawler:
    def __init__(self, site: dict, out_dir: Path, max_pages: int = 25, max_cards: int = 8, light: bool = False, log=print):
        self.site, self.out, self.log = site, out_dir, log
        self.base = site["url"].rstrip("/") + "/"
        self.host = urlparse(self.base).netloc.lower()
        self.max_pages, self.max_cards, self.light = max_pages, max_cards, light
        (self.out / "shots").mkdir(parents=True, exist_ok=True)

    def proxy(self):
        p = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        return {"server": p} if p else None

    async def render(self, browser: Browser, url: str, mode: str = "desktop", shot: bool = False, tries: int = 2) -> PageData:
        pd = PageData(url=url)
        for attempt in range(tries):
            ctx = await browser.new_context(**(MOBILE if mode == "mobile" else DESKTOP), locale="ru-RU", ignore_https_errors=False)
            page = await ctx.new_page()
            console, failed = [], []
            page.on("console", lambda m: console.append(m.text[:300]) if m.type == "error" else None)
            page.on("pageerror", lambda e: console.append(f"JS exception: {str(e)[:300]}"))
            page.on("response", lambda r: failed.append(f"{r.status} {r.url[:200]}") if r.status >= 400 and same_site(r.url, self.host) else None)
            try:
                t0 = time.time()
                resp = await page.goto(url, wait_until="load", timeout=45000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:  # noqa: BLE001 — виджеты держат сеть, это нормально
                    pass
                pd.status = resp.status if resp else 0
                pd.final_url = page.url
                pd.perf["load_s"] = round(time.time() - t0, 2)
                # прокрутка вниз — подгружает lazy-блоки (каталоги, формы в подвале)
                await page.evaluate("async()=>{for(let i=0;i<6;i++){window.scrollBy(0,innerHeight);await new Promise(r=>setTimeout(r,200));}window.scrollTo({top:0,behavior:'instant'});}")
                await page.wait_for_timeout(900)
                facts = await page.evaluate(DETECT_JS)
                meta = await page.evaluate("""() => ({title: document.title, description: (document.querySelector('meta[name=description]')||{}).content||'',
                    h1: [...document.querySelectorAll('h1')].map(h=>h.innerText.trim().slice(0,150)), canonical: (document.querySelector('link[rel=canonical]')||{}).href||'',
                    robots: (document.querySelector('meta[name=robots]')||{}).content||'', lang: document.documentElement.lang||'',
                    links: [...new Set([...document.querySelectorAll('a[href]')].map(a=>a.href))], text: document.body ? document.body.innerText.slice(0, 20000) : ''})""")
                if mode == "desktop" and self._probe_modal_needed(facts):
                    facts["modal_form"] = await self.probe_modal_form(page)
                if mode == "mobile":
                    pd.mobile = facts
                else:
                    pd.dom = facts
                pd.title, pd.description, pd.h1 = meta["title"], meta["description"], meta["h1"]
                pd.canonical, pd.robots_meta, pd.lang, pd.text = meta["canonical"], meta["robots"], meta["lang"], meta["text"]
                pd.links = [norm(l) for l in meta["links"] if l.startswith("http")]
                pd.console_errors, pd.failed_resources = console[:30], list(dict.fromkeys(failed))[:30]
                if shot:
                    fn = f"{re.sub(r'[^a-z0-9]+', '_', urlparse(url).path.lower()).strip('_') or 'home'}_{mode}.jpg"[:90]
                    await page.screenshot(path=str(self.out / "shots" / fn), type="jpeg", quality=55)
                    pd.screenshots[mode] = f"shots/{fn}"
                transient = any(TRANSIENT.search(e) for e in console) or pd.status >= 500 or facts.get("captcha")
                if facts.get("captcha"):
                    pd.error = "captcha"
                await ctx.close()
                if transient and attempt < tries - 1:
                    self.log(f"   ↻ повтор {url} ({'капча антибота' if facts.get('captcha') else 'сбой загрузки'}, проверяем воспроизводимость)")
                    await asyncio.sleep(8 if facts.get("captcha") else 2); continue
                if not facts.get("captcha"):
                    pd.error = ""
                return pd
            except Exception as e:  # noqa: BLE001
                pd.error = f"{type(e).__name__}: {str(e)[:200]}"
                await ctx.close()
                if attempt < tries - 1:
                    await asyncio.sleep(2)
        return pd

    @staticmethod
    def _probe_modal_needed(facts: dict) -> bool:
        return not any(f["has_phone"] and f["visible"] for f in facts.get("forms", [])) and facts.get("cta_count", 0) > 0

    async def probe_modal_form(self, page) -> dict | None:
        """Многие формы открываются только по клику (поп-ап). Кликаем кнопки-CTA (не ссылки на другие страницы) и ищем появившуюся форму."""
        n = await page.evaluate(r"""() => { const RE = /(заявк|перезвон|звонок|консультац|записат|тест[- ]?драйв|получить|рассчита|оценить|заказать|связаться|узнать)/i;
            const els = [...document.querySelectorAll('button, a, [role=button]')].filter(e => { const r = e.getBoundingClientRect(); const h = e.getAttribute('href');
              return r.width > 0 && r.height > 0 && RE.test((e.innerText || '').slice(0, 50)) && (e.tagName !== 'A' || !h || h.startsWith('#') || h.startsWith('javascript')); });
            els.slice(0, 3).forEach((e, i) => e.setAttribute('data-audit-cta', i)); return Math.min(3, els.length); }""")
        url0 = page.url
        for i in range(n):
            try:
                await page.locator(f'[data-audit-cta="{i}"]').first.click(timeout=3000)
                await page.wait_for_timeout(1500)
                res = await page.evaluate(r"""() => { const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
                    const lab = i => { let t = i.getAttribute('aria-label') || i.placeholder || ''; let p = i.parentElement; for (let k = 0; k < 3 && p && !t; k++) { const x = (p.innerText || '').trim(); if (x && x.length < 60) t = x; p = p.parentElement; } return t; };
                    const tel = [...document.querySelectorAll('input')].filter(vis).find(i => i.type === 'tel' || /phone|tel/i.test(i.name || '') || /телефон|\+7/i.test(lab(i)));
                    if (!tel) return null; const f = tel.closest('form') || tel.parentElement.parentElement.parentElement;
                    const inputs = [...f.querySelectorAll('input,select,textarea')].filter(x => !['hidden','submit','button'].includes(x.type) && vis(x));
                    const t = (f.innerText || '').toLowerCase();
                    return {visible_fields: inputs.length, field_names: inputs.slice(0, 10).map(x => (x.name || x.placeholder || x.type).slice(0, 30)),
                            consent: /персональн|согласи|политик/.test(t) || !!f.querySelector('input[type=checkbox]')}; }""")
                if res:
                    res["trigger"] = await page.locator(f'[data-audit-cta="{i}"]').first.inner_text(timeout=1000)
                    await page.keyboard.press("Escape")
                    return res
                if page.url != url0:
                    await page.go_back(timeout=15000)
            except Exception:  # noqa: BLE001
                continue
        return None

    async def measure_perf(self, browser: Browser, url: str) -> dict:
        for _ in range(2):  # повтор, если замер сорвался (обрыв соединения даёт «0 МБ, 1 запрос»)
            res = await self._perf_once(browser, url)
            if res.get("weight_mb", 0) >= 0.05 and not res.get("error"):
                return res
        return res

    async def _perf_once(self, browser: Browser, url: str) -> dict:
        """Лабораторный замер: мобильный, Slow 4G (~1.6 Мбит/с, RTT 150 мс), CPU ×4 — как в Lighthouse."""
        ctx = await browser.new_context(**MOBILE, locale="ru-RU")
        page = await ctx.new_page()
        await page.add_init_script(PERF_INIT)
        cdp = await ctx.new_cdp_session(page)
        total = {"bytes": 0}
        await cdp.send("Network.enable")
        await cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 150, "downloadThroughput": 1.6 * 1024 * 1024 / 8, "uploadThroughput": 750 * 1024 / 8})
        await cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
        cdp.on("Network.loadingFinished", lambda e: total.__setitem__("bytes", total["bytes"] + e.get("encodedDataLength", 0)))
        res = {"url": url}
        try:
            await page.goto(url, wait_until="load", timeout=90000)
            await page.wait_for_timeout(3000)
            res.update(await page.evaluate(PERF_READ))
        except Exception as e:  # noqa: BLE001
            res["error"] = f"{type(e).__name__}: {str(e)[:150]}"
        res["weight_mb"] = round(total["bytes"] / 1024 / 1024, 2)
        for k in ("ttfb", "fcp", "lcp", "dcl", "load"):
            if k in res:
                res[k] = round(res[k] / 1000, 2)
        await ctx.close()
        return res

    async def _many(self, browser, items, mode, conc=3):
        """Параллельный рендер (по 4 вкладки) — прогон сайта занимает минуты, а не десятки минут."""
        sem = asyncio.Semaphore(conc)
        async def one(u, k, shot):
            async with sem:
                self.log(f"→ {mode[:3]} {k:9s} {u}")
                pd = await self.render(browser, u, mode, shot=shot)
                pd.kind = k
                return pd
        return await asyncio.gather(*(one(u, k, sh) for u, k, sh in items))

    async def run(self) -> dict:
        t0 = time.time()
        async with client() as c:
            self.log(f"→ robots.txt и sitemap: {self.host}")
            sm = await read_sitemaps(c, self.base)
        result = {"site": self.site, "sitemap": sm, "pages": [], "perf": [], "discovered": 0}
        async with async_playwright() as p:
            browser = await p.chromium.launch(proxy=self.proxy())
            self.log("→ главная")
            home = await self.render(browser, self.base, "desktop", shot=True)
            home.kind = "home"
            if home.final_url and urlparse(home.final_url).netloc:
                self.host = urlparse(home.final_url).netloc.lower()
            internal = [l for l in home.links if same_site(l, self.host)]
            pool = list(dict.fromkeys(internal + [norm(u) for u in sm["urls"] if same_site(u, self.host)]))
            # каталоги (явные + родительские пути карточек-кандидатов)
            catalogs = [u for u in pool if classify(u, self.base) == "catalog"]
            for cu in find_card_candidates(pool):
                parent = cu.rsplit("/", 1)[0]
                if parent not in catalogs and parent.rstrip("/") != self.base.rstrip("/"):
                    catalogs.append(parent)
            catalogs = list(dict.fromkeys(catalogs))[: 1 if self.light else 3]
            cat_pages = list(await self._many(browser, [(u, "catalog", True) for u in catalogs], "desktop"))
            for cp in cat_pages:
                pool.extend(l for l in cp.links if same_site(l, self.host))
            pool = list(dict.fromkeys(pool))
            result["discovered"] = len(pool)
            cards = find_card_candidates(pool)
            n_cards = 3 if self.light else self.max_cards
            step = max(1, len(cards) // n_cards) if cards else 1
            card_sample = cards[::step][:n_cards]
            picked: dict[str, str] = {}
            for u in pool:
                k = classify(u, self.base)
                if k in ("credit", "tradein", "contacts", "testdrive", "offers") and k not in picked.values():
                    picked[u] = k
            for u in [u for u in pool if classify(u, self.base) == "model"][:2]:
                picked[u] = "model"
            for u in card_sample:
                picked[u] = "card"
            budget = max(0, (8 if self.light else self.max_pages) - 1 - len(cat_pages))
            todo = list(picked.items())[:budget]
            others = [u for u in pool if u not in picked and u not in cards and u not in catalogs and classify(u, self.base) == "other"]
            todo += [(u, "other") for u in others[: max(0, budget - len(todo))]]
            items, n_shot = [], 0
            for u, k in todo:
                shot = k != "other" and (k != "card" or n_shot < 2)
                n_shot += k == "card"
                items.append((u, k, shot))
            pages = [home] + cat_pages + list(await self._many(browser, items, "desktop"))
            for pd in pages:  # страницы, закрытые капчей, исключаем из анализа (иначе ложные «нет кнопки», «нет цены»)
                if pd.dom and pd.dom.get("captcha"):
                    pd.kind = f"blocked:{pd.kind}"
            for pd in pages:  # валидация карточек по содержимому
                car = pd.dom.get("car", {}) if pd.dom else {}
                looks_card = car.get("price_count", 0) >= 1 and (len(car.get("specs", [])) >= 2 or car.get("vin") or car.get("year"))
                if pd.kind == "card" and not looks_card:
                    pd.kind = "card?"
            # мобильная версия ключевых страниц
            mob = [pd for pd in pages if pd.kind in (("home",) if self.light else ("home", "catalog", "credit", "tradein", "contacts"))][:5]
            mob += [] if self.light else [pd for pd in pages if pd.kind == "card"][:2]
            mres = await self._many(browser, [(pd.url, pd.kind, True) for pd in mob], "mobile")
            for pd, m in zip(mob, mres):
                if m.mobile and not m.mobile.get("captcha"):
                    pd.mobile, pd.screenshots["mobile"] = m.mobile, m.screenshots.get("mobile", "")
            # скорость: по одному замеру за раз (параллельные замеры искажают друг друга)
            perf_targets = [home.url] + ([] if self.light else [pd.url for pd in pages if pd.kind == "card"][:1] + [pd.url for pd in cat_pages][:1])
            for u in perf_targets:
                self.log(f"→ скорость (mobile, Slow 4G) {u}")
                result["perf"].append(await self.measure_perf(browser, u))
            await browser.close()
        result["pages"] = pages
        result["elapsed_s"] = round(time.time() - t0, 1)
        return result
