from dealer_audit.crawler import classify, find_card_candidates
from dealer_audit.models import Finding
from dealer_audit.history import diff


def test_classify():
    assert classify("https://x.ru/", "") == "home"
    assert classify("https://x.ru/purchase/credit", "") == "credit"
    assert classify("https://x.ru/trade-in", "") == "tradein"
    assert classify("https://x.ru/online-stock", "") == "catalog"
    assert classify("https://x.ru/soglasie-zapis-na-servis", "") == "legal"


def test_card_candidates():
    urls = [f"https://x.ru/cars/bmw-x5-202{i}" for i in range(5)] + ["https://x.ru/about/news-1", "https://x.ru/contacts"]
    assert len(find_card_candidates(urls)) == 5


def test_ice_priority():
    assert Finding("a", "seo", "t", "r", impact=5, ease=5, confidence=0.9).priority == "P0"
    assert Finding("b", "seo", "t", "r", impact=2, ease=3, confidence=0.5).priority == "P2"


def test_diff():
    prev = {"meta": {"date": "d"}, "findings": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]}
    d = diff(prev, [{"id": "b", "title": "B"}, {"id": "c", "title": "C"}])
    assert [f["id"] for f in d["fixed"]] == ["a"] and [f["id"] for f in d["new"]] == ["c"]


def test_llm_plan_drops_unfounded_items():
    """LLM-план: пункты со ссылкой на несуществующие находки отбрасываются (защита от галлюцинаций)."""
    from dealer_audit.llm import synthesize

    class FakeLLM:
        def complete(self, *a, **k):
            return ('```json {"summary": "s", "plan": ['
                    '{"finding_ids": ["seo.sitemap_empty"], "action": "ok"},'
                    '{"finding_ids": ["made.up"], "action": "галлюцинация"}]} ```')

    f = [Finding("seo.sitemap_empty", "seo", "t", "r")]
    res = synthesize(FakeLLM(), {"name": "x", "url": "https://x.ru"}, f, None)
    assert [p["action"] for p in res["plan"]] == ["ok"]


def test_sitemap_junk_rules():
    from dealer_audit.checks import seo
    from dealer_audit.models import PageData
    urls = ["https://x.ru/test-online-stock/", "https://x.ru/online-stock-dev/", "https://x.ru/test-drive-new-poer/",
            "https://x.ru/news/geely-has-passed-a-24-hour-test/", "https://x.ru/soglasie-zapis-na-test-draiv/"]
    data = {"pages": [], "site": {"url": "https://x.ru/", "type": "new"},
            "sitemap": {"robots_status": 200, "robots_txt": "Clean-param: utm", "sitemaps": ["https://x.ru/sitemap.xml"], "urls": urls, "errors": []}}
    F, _ = seo.run(data)
    junk = next(f for f in F if f.id == "seo.sitemap_junk")
    assert sorted(junk.evidence) == ["https://x.ru/online-stock-dev/", "https://x.ru/test-online-stock/"]
