"""Проверка работоспособности форм реальной отправкой (только с флагом --test-forms).

ВНИМАНИЕ: создаёт тестовые заявки. Включать по согласованию с отделом продаж; в CRM их легко отфильтровать по имени «ТЕСТ АУДИТ».
Логика: заполнить видимую форму с полем телефона → нажать отправку → проверить, ушёл ли запрос на сервер (POST/XHR) и его код,
и появилось ли сообщение об успехе. Форма «сломана», если запрос не ушёл или вернул ошибку.
"""
from __future__ import annotations
import asyncio, os, re
from playwright.async_api import async_playwright

from .models import Finding

TEST_NAME = os.environ.get("FORMTEST_NAME", "ТЕСТ АУДИТ (не перезванивать)")
TEST_PHONE = os.environ.get("FORMTEST_PHONE", "9000000000")
OK_RE = re.compile(r"спасибо|заявка (принята|отправлена)|мы (свяжемся|перезвоним)|успешно", re.I)
FILL_JS = """(args) => { const [name, phone] = args;
  const forms = [...document.querySelectorAll('form')].filter(f => f.offsetParent && f.querySelector('input[type=tel], input[name*=phone i], input[placeholder*="+7"]'));
  const f = forms[0]; if (!f) return false;
  for (const i of f.querySelectorAll('input, textarea')) {
    if (['hidden','submit','button','file'].includes(i.type)) continue;
    if (i.type === 'checkbox') { if (!i.checked) i.click(); continue; }
    const set = (v) => { const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(i), 'value'); d.set.call(i, v); i.dispatchEvent(new Event('input', {bubbles: true})); i.dispatchEvent(new Event('change', {bubbles: true})); };
    if (i.type === 'tel' || /phone|tel/i.test(i.name) || /\\+7|телефон/i.test(i.placeholder)) set(phone);
    else if (i.type === 'email') set('test@example.com');
    else if (!i.value) set(name);
  }
  window.__auditForm = f; return true; }"""


async def _test(url: str) -> dict:
    async with async_playwright() as p:
        b = await p.chromium.launch(proxy={"server": os.environ["HTTPS_PROXY"]} if os.environ.get("HTTPS_PROXY") else None)
        page = await b.new_page(locale="ru-RU")
        res = {"url": url, "filled": False, "requests": [], "success_text": False}
        try:
            await page.goto(url, wait_until="load", timeout=45000)
            await page.wait_for_timeout(1500)
            res["filled"] = await page.evaluate(FILL_JS, [TEST_NAME, TEST_PHONE])
            if res["filled"]:
                page.on("response", lambda r: res["requests"].append((r.request.method, r.status, r.url[:150]))
                        if r.request.method in ("POST", "PUT") or r.request.resource_type in ("xhr", "fetch") else None)
                await page.evaluate("() => { const f = window.__auditForm; const b = f.querySelector('button[type=submit], input[type=submit], button'); b ? b.click() : f.requestSubmit(); }")
                await page.wait_for_timeout(6000)
                res["success_text"] = bool(OK_RE.search(await page.inner_text("body")))
        except Exception as e:  # noqa: BLE001
            res["error"] = str(e)[:150]
        await b.close()
        return res


def run(urls: list[str]) -> list[Finding]:
    F = []
    for u in urls:
        r = asyncio.run(_test(u))
        posts = [x for x in r["requests"] if x[0] in ("POST", "PUT")]
        if not r["filled"]:
            continue
        if not posts and not r["success_text"]:
            F.append(Finding("tech.form_not_sent", "technical", "Форма не отправила заявку при тестовой отправке",
                             "Срочно проверить форму: заявка не уходит на сервер (возможна ошибка JS, капча или валидация).",
                             impact=5, ease=4, confidence=0.7, evidence=[u], details=r))
        elif any(s >= 400 for _, s, _ in posts):
            F.append(Finding("tech.form_server_error", "technical", "Сервер вернул ошибку при отправке формы",
                             "Проверить обработчик форм и интеграцию с CRM.", impact=5, ease=4, confidence=0.75,
                             evidence=[u] + [f"{m} {s} {x}" for m, s, x in posts if s >= 400][:2], details=r))
    return F
