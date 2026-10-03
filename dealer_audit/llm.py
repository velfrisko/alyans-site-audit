"""LLM-слой со сменным провайдером: Anthropic Claude, любой OpenAI-совместимый API (OpenAI, OpenRouter, DeepSeek, локальный Ollama), YandexGPT.

Роль LLM в агенте ограничена и проверяема:
  1) UX-ревью скриншотов глазами покупателя (то, что правилами не формализовать);
  2) сведение находок в план действий на языке бизнеса.
LLM не придумывает факты: каждый пункт плана обязан ссылаться на id находок, собранных проверками; пункты без ссылок отбрасываются.
"""
from __future__ import annotations
import base64, json, os, re
from pathlib import Path
import httpx

from .models import Finding, CATEGORIES


class LLM:
    def __init__(self, provider: str | None = None):
        p = (provider or os.environ.get("LLM_PROVIDER") or "auto").lower()
        if p == "auto":
            p = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai" if os.environ.get("OPENAI_API_KEY") else \
                "yandex" if os.environ.get("YANDEX_LLM_API_KEY") else "none"
        self.provider = p
        self.model = os.environ.get("LLM_MODEL") or {"anthropic": "claude-sonnet-4-5", "openai": "gpt-4.1-mini",
                                                       "yandex": f"gpt://{os.environ.get('YANDEX_FOLDER_ID', '')}/yandexgpt/latest"}.get(p, "")
        self.vision = p in ("anthropic", "openai") and os.environ.get("LLM_VISION", "1") != "0"
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    @property
    def enabled(self) -> bool:
        return self.provider != "none"

    def complete(self, system: str, user: str, images: list[Path] | None = None, max_tokens: int = 3000) -> str:
        images = images if self.vision else []
        self.usage["calls"] += 1
        if self.provider == "anthropic":
            content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(p.read_bytes()).decode()}} for p in images or []]
            content.append({"type": "text", "text": user})
            r = httpx.post(os.environ.get("LLM_BASE_URL", "https://api.anthropic.com") + "/v1/messages", timeout=180,
                           headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                           json={"model": self.model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": content}]})
            r.raise_for_status(); j = r.json()
            self.usage["input_tokens"] += j.get("usage", {}).get("input_tokens", 0); self.usage["output_tokens"] += j.get("usage", {}).get("output_tokens", 0)
            return "".join(b.get("text", "") for b in j["content"])
        # OpenAI-совместимые (включая YandexGPT: https://llm.api.cloud.yandex.net/v1)
        if self.provider == "yandex":
            base, key = os.environ.get("LLM_BASE_URL", "https://llm.api.cloud.yandex.net/v1"), os.environ["YANDEX_LLM_API_KEY"]
        else:
            base, key = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"), os.environ["OPENAI_API_KEY"]
        content = [{"type": "text", "text": user}] + [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()}} for p in images or []]
        r = httpx.post(base.rstrip("/") + "/chat/completions", timeout=180, headers={"Authorization": f"Bearer {key}"},
                       json={"model": self.model, "max_tokens": max_tokens, "temperature": 0.2,
                             "messages": [{"role": "system", "content": system}, {"role": "user", "content": content if images else user}]})
        r.raise_for_status(); j = r.json()
        u = j.get("usage", {}); self.usage["input_tokens"] += u.get("prompt_tokens", 0); self.usage["output_tokens"] += u.get("completion_tokens", 0)
        return j["choices"][0]["message"]["content"]


def _json(text: str):
    m = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    text = m.group(1) if m else text
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [0])
    return json.loads(text[start:])


SYSTEM = ("Ты — эксперт по конверсии сайтов автодилеров (CRO) и интернет-маркетингу в России. "
          "Отвечаешь строго в JSON, по-русски, конкретно и проверяемо. Не придумываешь факты, которых нет на скриншотах или в данных.")


def ux_review(llm: LLM, data: dict, out_dir: Path) -> list[Finding]:
    """Визуальный разбор ключевых экранов: главная (desktop/mobile), каталог, карточка (mobile)."""
    shots = []
    for kind, mode in [("home", "desktop"), ("home", "mobile"), ("card", "mobile"), ("catalog", "desktop"), ("card", "desktop")]:
        p = next((x for x in data["pages"] if x.kind == kind and x.screenshots.get(mode)), None)
        if p:
            shots.append((f"{kind}/{mode}", p.url, out_dir / p.screenshots[mode]))
    shots = [s for s in shots if s[2].exists()][:4]
    if not shots or not llm.vision:
        return []
    legend = "\n".join(f"Скриншот {i+1}: {name} — {url}" for i, (name, url, _) in enumerate(shots))
    user = f"""Сайт: {data['site'].get('name', '')} ({data['site']['url']}), тип: {data['site'].get('type')}, город: {data['site'].get('city')}.
{legend}

Оцени экраны глазами покупателя авто: понятно ли предложение, видна ли цена/выгода, насколько легко оставить заявку, позвонить или написать, есть ли доверие (гарантии, отзывы, реальные фото), визуальный шум, читаемость на мобильном.
Верни JSON-массив до 6 элементов: [{{"title": "проблема коротко", "recommendation": "что сделать конкретно", "category": "conversion|cards|technical|seo",
"impact": 1-5, "ease": 1-5, "confidence": 0.3-0.7, "screenshot": номер}}]. Только проблемы, видимые на скриншотах."""
    try:
        items = _json(llm.complete(SYSTEM, user, [s[2] for s in shots]))
    except Exception as e:  # noqa: BLE001
        return [Finding("llm.error", "technical", "LLM-разбор скриншотов не выполнен", f"Ошибка провайдера: {str(e)[:150]}", impact=1, ease=5, confidence=0.1, source="llm")]
    out = []
    for i, it in enumerate(items if isinstance(items, list) else []):
        try:
            k = max(1, min(len(shots), int(it.get("screenshot", 1)))) - 1
            out.append(Finding(f"llm.ux.{i}", it.get("category", "conversion") if it.get("category") in CATEGORIES else "conversion",
                               str(it["title"])[:160], str(it["recommendation"])[:500], impact=max(1, min(5, int(it.get("impact", 3)))),
                               ease=max(1, min(5, int(it.get("ease", 3)))), confidence=min(0.7, float(it.get("confidence", 0.5))),
                               evidence=[shots[k][1], str(shots[k][2].relative_to(out_dir))], source="llm"))
        except Exception:  # noqa: BLE001
            continue
    return out


def synthesize(llm: LLM, site: dict, findings: list[Finding], comp: dict | None) -> dict | None:
    """План действий для руководителя. Валидация: каждый пункт ссылается на существующие id находок."""
    top = sorted(findings, key=lambda f: -f.score)[:30]
    compact = [{"id": f.id, "cat": f.category, "title": f.title, "rec": f.recommendation[:200], "score": f.score} for f in top]
    comp_s = json.dumps(comp.get("matrix_summary", {}), ensure_ascii=False) if comp else "нет данных"
    user = f"""Сайт {site.get('name')} ({site['url']}). Находки автоматических проверок (id, категория, проблема, рекомендация, приоритет):
{json.dumps(compact, ensure_ascii=False)}
Сравнение с конкурентами: {comp_s}

Сделай план для коммерческого директора дилера. Верни JSON:
{{"summary": "3–4 предложения: главное, что мешает получать заявки",
 "plan": [{{"finding_ids": ["id", ...], "action": "что сделать (одна фраза)", "why": "почему это даст заявки", "owner": "маркетинг|разработчик|контент-менеджер|отдел продаж", "effort": "часы|дни|недели"}}]}}
5–8 пунктов, по убыванию эффекта. Объединяй связанные находки. Используй только id из списка."""
    try:
        res = _json(llm.complete(SYSTEM, user, max_tokens=2500))
    except Exception as e:  # noqa: BLE001
        return {"summary": f"LLM-сводка не построена: {str(e)[:120]}", "plan": []}
    ids = {f.id for f in findings}
    res["plan"] = [p for p in res.get("plan", []) if p.get("finding_ids") and all(i in ids for i in p["finding_ids"])]
    return res
