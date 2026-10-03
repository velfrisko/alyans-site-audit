from __future__ import annotations
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from .models import CATEGORIES

ENV = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html", "j2"]))


def render_html(r: dict) -> str:
    F = r["findings"]
    by_cat = {c: [f for f in F if f["category"] == c] for c in CATEGORIES}
    return ENV.get_template("report.html.j2").render(
        m=r["meta"], scores=r["scores"], cats=CATEGORIES, counts={c: len(v) for c, v in by_cat.items()}, by_cat=by_cat,
        top=F[:10], plan=r.get("plan"), stats=r["stats"], comp=r.get("competitors"), perf=r.get("perf"), diff=r.get("diff"), pages=r["pages"])


def render_md(r: dict, short: bool = False) -> str:
    m, F = r["meta"], r["findings"]
    p0 = [f for f in F if f["priority"] == "P0"]
    lines = [f"*Аудит {m['site'].get('name') or m['site']['url']}* — {m['date']}",
             " · ".join(f"{CATEGORIES[c]}: {s}" for c, s in r["scores"].items()),
             f"Рекомендаций: {len(F)} (P0: {len(p0)})"]
    if r.get("diff"):
        d = r["diff"]
        lines.append(f"С прошлого прогона: исправлено {len(d['fixed'])}, новых {len(d['new'])}")
    lines.append("")
    lines += [f"{i+1}. [{f['priority']}] {f['title']}" for i, f in enumerate(F[: 5 if short else 15])]
    return "\n".join(lines)


def portfolio_summary(root: Path) -> Path:
    """Сводная таблица по всем сайтам холдинга: индексы, число P0 и главные проблемы — для еженедельной планёрки."""
    import json
    rows = []
    for sd in sorted(p for p in root.iterdir() if p.is_dir()):
        latest = sd / "latest.txt"
        if not latest.exists():
            continue
        rd = sd / latest.read_text().strip()
        r = json.loads((rd / "report.json").read_text(encoding="utf-8"))
        F = r["findings"]
        rows.append((r, rd.relative_to(root), F))
    lines = ["# Сводка аудита сайтов", "", "| Сайт | Дата | " + " | ".join(CATEGORIES.values()) + " | P0 | Главное |", "|" + "---|" * (len(CATEGORIES) + 4)]
    for r, rel, F in rows:
        p0 = [f for f in F if f["priority"] == "P0"]
        top = "; ".join(f["title"] for f in F[:3])
        lines.append(f"| [{r['meta']['site'].get('name') or r['meta']['site']['url']}]({rel}/report.html) | {r['meta']['date'][:10]} | "
                     + " | ".join(str(r["scores"][c]) for c in CATEGORIES) + f" | {len(p0)} | {top} |")
    out = root / "SUMMARY.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
