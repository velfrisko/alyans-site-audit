"""HTTP-клиент с ретраями: сайты периодически рвут соединение, и это не должно превращаться в ложные «битые ссылки»."""
from __future__ import annotations
import asyncio, os
import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 AlyansSiteAudit/0.1"


def client(timeout: float = 20) -> httpx.AsyncClient:
    verify = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or True
    return httpx.AsyncClient(headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"},
                             timeout=timeout, follow_redirects=True, verify=verify)


async def get(c: httpx.AsyncClient, url: str, tries: int = 3, method: str = "GET") -> tuple[int, str, str]:
    """-> (status, text, final_url). status=0 — сеть недоступна после всех попыток."""
    last = ""
    for i in range(tries):
        try:
            r = await c.request(method, url)
            if r.status_code >= 500 and i < tries - 1:
                await asyncio.sleep(1.5 * (i + 1)); continue
            return r.status_code, (r.text if method == "GET" else ""), str(r.url)
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(1.5 * (i + 1))
    return 0, last, url
