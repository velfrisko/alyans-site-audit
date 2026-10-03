"""Оркестратор: сбор фактов → проверки → конкуренты → LLM → приоритизация → отчёт → история."""
from __future__ import annotations
import asyncio, datetime as dt, json, os, pickle
from dataclasses import asdict
from pathlib import Path

from . import history, yandex
from .checks import cards, competitors, conversion, seo, technical
from .crawler import Crawler
from .llm import LLM, synthesize, ux_review
from .models import CATEGORIES, Finding
from .report import render_html, render_md


_COMP_CACHE: dict[str, dict] = {}  # конкуренты общие для многих сайтов — в одном запуске сканируем каждого один раз


COMP_TO_RULE = {"phone_fold_m": "conv.mobile_no_phone_fold", "cta_fold_m": "conv.no_cta_fold_mobile", "sticky_m": "conv.no_sticky_mobile",
                "credit_calc": "conv.no_credit_calc", "chat": "conv.no_chat_widget", "messengers": "conv.no_messengers", "tradein": "conv.no_tradein",
                "tradein_calc": "conv.no_tradein_valuation", "testdrive": "conv.no_testdrive", "lead_forms": "conv.no_lead_forms"}


def merge_competitor_evidence(findings: list[dict]) -> list[dict]:
    """Если проблема уже найдена правилом, сравнение с конкурентами не дублирует её, а усиливает:
    добавляет «есть у конкурентов» в рекомендацию и повышает уверенность."""
    by_id = {f["id"]: f for f in findings}
    out = []
    for f in findings:
        rule = COMP_TO_RULE.get(f["id"].removeprefix("comp."))
        if f["id"].startswith("comp.") and rule in by_id:
            r = by_id[rule]
            if "Есть у конкурентов" not in r["recommendation"]:
                r["recommendation"] += " Есть у конкурентов: " + f["recommendation"].split(".")[0].removeprefix("Есть у: ") + "."
                r["evidence"] = r["evidence"] + f["evidence"][:2]
                r["confidence"] = min(0.95, round(r["confidence"] + 0.1, 2))
                r["score"] = round(r["impact"] * r["ease"] * r["confidence"], 2)
                r["priority"] = "P0" if r["score"] >= 12 else "P1" if r["score"] >= 7 else "P2"
            continue
        out.append(f)
    return sorted(out, key=lambda f: (-f["score"], f["category"]))


def category_scores(findings: list[dict]) -> dict:
    out = {}
    for cat in CATEGORIES:
        pen = sum(f["impact"] * f["confidence"] * 4.5 for f in findings if f["category"] == cat)
        out[cat] = max(0, round(100 - pen))
    return out


def audit(site: dict, root: Path, max_pages: int = 25, with_competitors: bool = True, linkcheck: bool = True,
          llm_provider: str | None = None, test_forms: bool = False, log=print) -> Path:
    started = dt.datetime.now()
    sd = history.site_dir(root, site["key"])
    out = sd / started.strftime("%Y-%m-%d_%H%M")
    out.mkdir(parents=True, exist_ok=True)
    log(f"=== Аудит {site.get('name', site['url'])} → {out}")

    data = asyncio.run(Crawler(site, out, max_pages=max_pages, log=log).run())

    h0 = data["pages"][0] if data["pages"] else None
    unavailable = not h0 or h0.status >= 400 or not h0.status or bool((h0.dom or {}).get("captcha"))
    comp_data = []
    if with_competitors and site.get("competitors") and not unavailable:
        for c in site["competitors"]:
            cs = {"key": "comp", "url": c["url"] if isinstance(c, dict) else c, "name": c.get("name") if isinstance(c, dict) else None}
            log(f"=== Конкурент {cs['url']}")
            if cs["url"] in _COMP_CACHE:
                comp_data.append(_COMP_CACHE[cs["url"]]); continue
            cache_f = root / "_competitors" / (cs["url"].split("//")[-1].strip("/").replace("/", "_") + f"_{started:%Y-%m-%d}.pkl")
            if cache_f.exists():  # конкурентов сканируем не чаще раза в сутки
                _COMP_CACHE[cs["url"]] = pickle.loads(cache_f.read_bytes())
                comp_data.append(_COMP_CACHE[cs["url"]]); log(f"=== Конкурент {cs['url']} (из кэша за сегодня)"); continue
            try:
                host = cs["url"].split("//")[-1].split("/")[0]
                comp_data.append(asyncio.run(Crawler(cs, out / "competitors" / host, light=True, log=log).run()))
                _COMP_CACHE[cs["url"]] = comp_data[-1]
                cache_f.parent.mkdir(parents=True, exist_ok=True)
                cache_f.write_bytes(pickle.dumps(comp_data[-1]))
            except Exception as e:  # noqa: BLE001
                log(f"   конкурент недоступен: {e}")
    log("=== Проверки")
    inv = history.load_inventory(sd)
    comp_urls = [c["site"]["url"] for c in comp_data]
    vis = yandex.check(site, comp_urls)
    F: list[Finding] = []
    stats = {}
    home = h0
    if unavailable:
        # Главная недоступна агенту (антибот/5xx): не делаем выводов о контенте, чтобы не выдать ложные рекомендации
        log("!!! Главная недоступна агенту — анализ контента пропущен")
        F.append(Finding("tech.site_unavailable", "technical", f"Сайт не отдал главную страницу агенту (HTTP {home.status if home else 0}{', капча' if home and (home.dom or {}).get('captcha') else ''})",
                         "Проверить доступность и настройки антибота (SmartCaptcha / DDoS-защита): если сайт блокирует автоматическую проверку, он может блокировать и часть посетителей. Для регулярного аудита добавить IP агента в белый список и повторить прогон.",
                         impact=5, ease=4, confidence=0.6, evidence=[site["url"]] + list(home.screenshots.values() if home else [])))
        for pg in data["pages"]:
            pg.dom = {}  # контент страницы-заглушки не анализируем
        f, st = seo.run(data, vis)  # robots.txt и sitemap проверяются по HTTP и остаются валидными
        F += f; stats["seo"] = st
        comp_data = []
        checks_plan = []
    else:
        checks_plan = None
    for name, fn in ([] if checks_plan == [] else [("conversion", lambda: conversion.run(data)), ("cards", lambda: cards.run(data, inv)),
                     ("technical", lambda: technical.run(data, linkcheck)), ("seo", lambda: seo.run(data, vis))]):
        try:
            f, st = fn()
            F += f; stats[name] = st
        except Exception as e:  # noqa: BLE001 — одна упавшая проверка не должна ронять весь отчёт
            log(f"   проверка {name} упала: {e}")
            stats[name] = {"error": str(e)}
    if test_forms:
        from . import formtest
        targets = [p.url for p in data["pages"] if p.dom and any(f["has_phone"] and f["visible"] for f in p.dom["forms"])][:3]
        log(f"=== Тестовая отправка форм: {len(targets)} стр.")
        F += formtest.run(targets)
    comp_matrix = None
    if comp_data:
        f, comp_matrix = competitors.run(data, comp_data)
        F += f
    llm = LLM(llm_provider)
    plan = None
    if llm.enabled:
        log(f"=== LLM ({llm.provider}: {llm.model})")
        F += ux_review(llm, data, out)
    # дедупликация по id
    uniq: dict[str, Finding] = {}
    for f in F:
        uniq.setdefault(f.id, f)
    F = sorted(uniq.values(), key=lambda f: (-f.score, f.category))
    if llm.enabled:
        plan = synthesize(llm, site, F, comp_matrix)
    findings = merge_competitor_evidence([f.to_dict() for f in F])
    prev = history.previous_report(sd, out)
    report = {
        "meta": {"site": site, "date": started.strftime("%Y-%m-%d %H:%M"), "elapsed_s": round((dt.datetime.now() - started).total_seconds()),
                 "pages_rendered": len(data["pages"]), "urls_discovered": data["discovered"], "llm": {"provider": llm.provider, "model": llm.model, "usage": llm.usage},
                 "competitors": comp_urls, "version": "0.1.0"},
        "scores": category_scores(findings), "findings": findings, "plan": plan, "stats": stats, "competitors": comp_matrix,
        "perf": data["perf"], "diff": history.diff(prev, findings),
        "pages": [{"url": p.url, "kind": p.kind, "status": p.status, "title": p.title, "screenshots": p.screenshots, "error": p.error} for p in data["pages"]],
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "raw_pages.json").write_text(json.dumps([asdict(p) for p in data["pages"]], ensure_ascii=False, default=str)[:30_000_000], encoding="utf-8")
    (out / "report.html").write_text(render_html(report), encoding="utf-8")
    (out / "summary.md").write_text(render_md(report), encoding="utf-8")
    history.update_inventory(sd, inv, stats.get("cards", {}).get("records", []))
    (sd / "latest.txt").write_text(out.name, encoding="utf-8")
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        from .notify import telegram
        telegram(render_md(report, short=True))
    log(f"=== Готово: {out / 'report.html'}  ({len(findings)} рекомендаций)")
    return out


def rebuild(report_dir: Path) -> None:
    """Пересобрать отчёт из report.json (после обновления правил слияния или шаблона) без повторного обхода сайта."""
    r = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    r["findings"] = merge_competitor_evidence(r["findings"])
    r["scores"] = category_scores(r["findings"])
    sd = report_dir.parent
    r["diff"] = history.diff(history.previous_report(sd, report_dir), r["findings"])
    (report_dir / "report.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (report_dir / "report.html").write_text(render_html(r), encoding="utf-8")
    (report_dir / "summary.md").write_text(render_md(r), encoding="utf-8")
