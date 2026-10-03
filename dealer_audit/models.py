from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

CATEGORIES = {
    "conversion": "Конверсия",
    "cards": "Карточки автомобилей",
    "technical": "Техническое состояние",
    "seo": "SEO и видимость",
    "competitors": "Конкуренты",
}


@dataclass
class Finding:
    """Одна проблема/возможность. Все рекомендации агента сводятся к этому формату."""
    id: str                     # стабильный ключ, нужен для сравнения прогонов (diff)
    category: str               # ключ из CATEGORIES
    title: str                  # что не так (коротко)
    recommendation: str         # что сделать
    impact: int = 3             # 1..5 — влияние на заявки
    ease: int = 3               # 1..5 — 5 = сделать легко (часы), 1 = проект
    confidence: float = 0.8     # 0..1 — насколько агент уверен (автопроверка ~0.9, эвристика ~0.6, LLM ~0.5-0.7)
    evidence: list[str] = field(default_factory=list)   # URL, значения, пути к скриншотам
    source: str = "rule"        # rule | llm | competitor
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return round(self.impact * self.ease * self.confidence, 2)

    @property
    def priority(self) -> str:
        s = self.score
        return "P0" if s >= 12 else "P1" if s >= 7 else "P2"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["score"], d["priority"] = self.score, self.priority
        return d


@dataclass
class PageData:
    url: str
    final_url: str = ""
    status: int = 0
    kind: str = "other"         # home|catalog|card|credit|tradein|contacts|testdrive|model|offers|other
    title: str = ""
    description: str = ""
    h1: list[str] = field(default_factory=list)
    canonical: str = ""
    robots_meta: str = ""
    lang: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    dom: dict[str, Any] = field(default_factory=dict)      # результат JS-детекторов
    mobile: dict[str, Any] = field(default_factory=dict)   # то же для мобильной версии
    perf: dict[str, Any] = field(default_factory=dict)
    console_errors: list[str] = field(default_factory=list)
    failed_resources: list[str] = field(default_factory=list)
    screenshots: dict[str, str] = field(default_factory=dict)
    error: str = ""
