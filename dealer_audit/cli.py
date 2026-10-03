"""CLI: python -m dealer_audit run <ключ|URL> [...]  |  python -m dealer_audit run --all  |  python -m dealer_audit list"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
from .config import ROOT, load_sites, resolve_site


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Подхватывает ключи из .env (без внешних зависимостей). Уже заданные переменные окружения не перезаписываются."""
    import os
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.split(" #")[0].strip().strip('"').strip("'")
            if v and k.strip() not in os.environ:
                os.environ[k.strip()] = v


def main(argv=None):
    load_dotenv()
    ap = argparse.ArgumentParser(prog="dealer_audit", description="ИИ-агент аудита сайтов автодилера")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="запустить аудит")
    r.add_argument("targets", nargs="*", help="ключи из config/sites.yaml или URL")
    r.add_argument("--all", action="store_true", help="все сайты из конфига")
    r.add_argument("--config", default=None)
    r.add_argument("--out", default=str(ROOT / "reports"))
    r.add_argument("--max-pages", type=int, default=25)
    r.add_argument("--no-competitors", action="store_true")
    r.add_argument("--no-linkcheck", action="store_true")
    r.add_argument("--test-forms", action="store_true", help="реально отправить тестовые заявки (по согласованию с продажами!)")
    r.add_argument("--llm", default=None, help="anthropic | openai | yandex | none (по умолчанию — по найденному ключу)")
    sub.add_parser("list", help="список сайтов из конфига")
    sm = sub.add_parser("summary", help="сводка по всем сайтам (последние прогоны) → SUMMARY.md")
    sm.add_argument("--out", default=str(ROOT / "reports"))
    rb = sub.add_parser("rebuild", help="пересобрать HTML-отчёт из report.json")
    rb.add_argument("dirs", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "summary":
        from .report import portfolio_summary
        print(portfolio_summary(Path(a.out)))
        return 0
    if a.cmd == "rebuild":
        from .agent import rebuild
        for d in a.dirs:
            rebuild(Path(d)); print("ok", d)
        return 0
    cfg = load_sites(getattr(a, "config", None))
    if a.cmd == "list":
        for k, v in cfg["sites"].items():
            print(f"{k:18s} {v['url']:42s} {v.get('type', '')}")
        return 0
    targets = list(cfg["sites"]) if a.all else a.targets
    if not targets:
        ap.error("укажите сайт (ключ или URL) или --all")
    from .agent import audit
    failed = 0
    for t in targets:
        try:
            audit(resolve_site(cfg, t), Path(a.out), max_pages=a.max_pages, with_competitors=not a.no_competitors,
                  linkcheck=not a.no_linkcheck, llm_provider=a.llm, test_forms=a.test_forms)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"!!! {t}: {type(e).__name__}: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
